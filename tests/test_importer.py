from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dndref.importer import (
    AssetStore,
    DatasetImportError,
    DatasetLoadError,
    format_report,
    import_dataset,
    load_dataset,
)
from dndref.storage.database import Database

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "dataset"


def copy_fixture(tmp_path: Path, name: str = "dataset") -> Path:
    destination = tmp_path / name
    shutil.copytree(FIXTURE_DIR, destination)
    return destination


def read_json(path: Path, filename: str) -> object:
    return json.loads((path / filename).read_text(encoding="utf-8"))


def write_json(path: Path, filename: str, value: object) -> None:
    (path / filename).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_loader_parses_items_wrapper_and_validates_fixture(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)

    loaded = load_dataset(dataset)

    assert loaded.dataset_id == "example-5e"
    assert loaded.entry_count == 8
    assert len(loaded.pack.items.properties) == 1


def test_loader_reports_missing_malformed_and_invalid_files(tmp_path: Path) -> None:
    missing = copy_fixture(tmp_path, "missing")
    (missing / "classes.json").unlink()
    with pytest.raises(DatasetLoadError, match="Missing required file: classes.json"):
        load_dataset(missing)

    malformed = copy_fixture(tmp_path, "malformed")
    (malformed / "spells.json").write_text("[", encoding="utf-8")
    with pytest.raises(DatasetLoadError, match="Malformed JSON in spells.json"):
        load_dataset(malformed)

    invalid = copy_fixture(tmp_path, "invalid")
    spells = read_json(invalid, "spells.json")
    assert isinstance(spells, list)
    spells[0]["level"] = 10
    write_json(invalid, "spells.json", spells)
    with pytest.raises(DatasetLoadError, match=r"(?s)Invalid spells.json.*level"):
        load_dataset(invalid)


def test_first_import_noop_and_replacement_counts(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    database = Database(tmp_path / "data" / "dndref.sqlite3")

    first = import_dataset(database, load_dataset(dataset))
    second = import_dataset(database, load_dataset(dataset))
    assert (first.added, first.changed, first.removed) == (8, 0, 0)
    assert second.is_noop
    assert "no changes" in format_report(second)

    items = read_json(dataset, "items.json")
    assert isinstance(items, dict)
    items["items"].pop()
    items["items"][0]["name"] = "Traveling Pack"
    write_json(dataset, "items.json", items)
    replacement = import_dataset(database, load_dataset(dataset))
    assert (replacement.added, replacement.changed, replacement.removed) == (0, 1, 1)

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 7
        assert connection.execute(
            "SELECT name FROM entries WHERE dataset_id = ? AND local_key = ?",
            ("example-5e", "item/adventuring-pack"),
        ).fetchone()[0] == "Traveling Pack"


def test_unrelated_dataset_is_preserved(tmp_path: Path) -> None:
    first = copy_fixture(tmp_path, "first")
    second = copy_fixture(tmp_path, "second")
    manifest = read_json(second, "manifest.json")
    assert isinstance(manifest, dict)
    manifest["dataset_id"] = "other-pack"
    write_json(second, "manifest.json", manifest)

    database = Database(tmp_path / "data" / "dndref.sqlite3")
    import_dataset(database, load_dataset(first))
    import_dataset(database, load_dataset(second))

    items = read_json(first, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["name"] = "Changed Only In First"
    write_json(first, "items.json", items)
    import_dataset(database, load_dataset(first))

    with database.connection() as connection:
        assert connection.execute(
            "SELECT name FROM entries WHERE dataset_id = ? AND local_key = ?",
            ("other-pack", "item/adventuring-pack"),
        ).fetchone()[0] == "Adventuring Pack"


def test_failed_apply_rolls_back_existing_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = copy_fixture(tmp_path)
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    original = load_dataset(dataset)
    import_dataset(database, original)

    items = read_json(dataset, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["name"] = "Would Not Persist"
    write_json(dataset, "items.json", items)
    replacement = load_dataset(dataset)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced persistence failure")

    monkeypatch.setattr("dndref.importer.apply_dataset", fail)
    with pytest.raises(DatasetImportError, match="database unchanged"):
        import_dataset(database, replacement)

    with database.connection() as connection:
        assert connection.execute(
            "SELECT name FROM entries WHERE dataset_id = ? AND local_key = ?",
            ("example-5e", "item/adventuring-pack"),
        ).fetchone()[0] == "Adventuring Pack"
        assert connection.execute(
            "SELECT content_hash FROM datasets WHERE dataset_id = ?", ("example-5e",)
        ).fetchone()[0] == original.content_hash


def test_assets_are_validated_staged_and_deduplicated(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    images = dataset / "images"
    images.mkdir()
    (images / "test.png").write_bytes(b"test image bytes")
    items = read_json(dataset, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["image"] = "images/test.png"
    write_json(dataset, "items.json", items)

    database = Database(tmp_path / "data" / "dndref.sqlite3")
    loaded = load_dataset(dataset)
    import_dataset(database, loaded, AssetStore(tmp_path / "asset-store"))
    staged = list((tmp_path / "asset-store").rglob("*") )
    assert len([path for path in staged if path.is_file()]) == 1

    with database.connection() as connection:
        image = connection.execute(
            "SELECT relative_path, content_hash, stored_path FROM images"
        ).fetchone()
        assert tuple(image)[:2] == ("images/test.png", loaded.assets[0].content_hash)
        assert (tmp_path / "asset-store" / image[2]).is_file()


def test_missing_asset_is_rejected_before_database_creation(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    items = read_json(dataset, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["image"] = "images/missing.webp"
    write_json(dataset, "items.json", items)

    with pytest.raises(DatasetLoadError, match="Missing image asset"):
        load_dataset(dataset)


def test_asset_path_traversal_and_cross_dataset_reference_are_rejected(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    items = read_json(dataset, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["image"] = "../outside.png"
    write_json(dataset, "items.json", items)
    with pytest.raises(DatasetLoadError, match="image reference"):
        load_dataset(dataset)

    dataset = copy_fixture(tmp_path, "cross")
    spells = read_json(dataset, "spells.json")
    assert isinstance(spells, list)
    spells[0]["class_references"] = ["other-pack:class/wizard"]
    write_json(dataset, "spells.json", spells)
    with pytest.raises(DatasetLoadError, match="cross-dataset reference"):
        load_dataset(dataset)


def test_dry_run_does_not_create_database_or_assets(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    database = Database(tmp_path / "data" / "dndref.sqlite3")

    report = import_dataset(database, load_dataset(dataset), dry_run=True)

    assert report.added == 8
    assert not database.path.exists()
    assert not (tmp_path / "data" / "assets").exists()


def test_relationships_and_foreign_keys_are_persisted(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    import_dataset(database, load_dataset(dataset))

    with database.connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM spell_classes").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM item_property_links").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM class_features").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM subclass_features").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM class_progression_values").fetchone()[0]
            == 4
        )


def test_cli_validate_import_and_dry_run(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
            "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        }
    )

    validate = subprocess.run(
        [sys.executable, "-m", "dndref", "validate", str(dataset)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    dry_run = subprocess.run(
        [sys.executable, "-m", "dndref", "import", str(dataset), "--dry-run"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert validate.returncode == 0
    assert "Validated dataset" in validate.stdout
    assert dry_run.returncode == 0
    assert "8 added" in dry_run.stdout
    assert not (tmp_path / "xdg-data" / "dndref" / "dndref.sqlite3").exists()

    imported = subprocess.run(
        [sys.executable, "-m", "dndref", "import", str(dataset)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert imported.returncode == 0
    assert "8 added" in imported.stdout

    invalid = copy_fixture(tmp_path, "invalid-cli")
    (invalid / "classes.json").unlink()
    failed = subprocess.run(
        [sys.executable, "-m", "dndref", "import", str(invalid)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert failed.returncode != 0
    assert "Missing required file" in failed.stderr
