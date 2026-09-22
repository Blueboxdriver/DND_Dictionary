from __future__ import annotations

import importlib
import json
import sqlite3
from importlib import resources
from pathlib import Path

from dndref import __version__
from dndref.bundled import BUNDLED_DATASET_DIRECTORIES, bundled_dataset_paths
from dndref.cli import initialize_application
from dndref.config import ApplicationPaths
from dndref.importer import load_dataset
from dndref.search import SearchQuery, SearchService
from dndref.storage.database import Database

EXPECTED = {
    "srd-5.2.1": ("srd-5-2-1", 0, 339, 17, 0),
    "official-5etools-2024": ("official-5etools-2024", 2541, 444, 179, 13),
}


def test_release_metadata_and_console_target() -> None:
    assert __version__ == "0.1.0"
    module_name, function_name = "dndref.__main__", "main"
    assert callable(getattr(importlib.import_module(module_name), function_name))


def test_bundled_resources_include_runtime_files_and_inventories() -> None:
    with bundled_dataset_paths() as paths:
        assert {path.name for path in paths} == set(BUNDLED_DATASET_DIRECTORIES)
        for path in paths:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            dataset_id, items, spells, feats, classes = EXPECTED[path.name]
            loaded = load_dataset(path)
            assert manifest["dataset_id"] == dataset_id
            assert len(loaded.pack.items.items) == items
            assert len(loaded.pack.spells) == spells
            assert len(loaded.pack.feats) == feats
            assert len(loaded.pack.classes) == classes


def test_first_run_installs_bundled_data_and_second_run_is_idempotent(tmp_path: Path) -> None:
    paths = ApplicationPaths.for_root(tmp_path / "app")

    initialize_application(paths, image_mode="off")
    with sqlite3.connect(paths.database_path) as connection:
        counts = connection.execute(
            "SELECT dataset_id, COUNT(*) FROM entries GROUP BY dataset_id ORDER BY dataset_id"
        ).fetchall()
    assert counts == [("official-5etools-2024", 3177), ("srd-5-2-1", 356)]

    initialize_application(paths, image_mode="off")
    with sqlite3.connect(paths.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 3533

    service = SearchService(Database(paths.database_path))
    results = service.search(SearchQuery("items", "Longsword"))
    assert results.total_count >= 1


def test_packaged_resources_are_resource_traversable() -> None:
    with resources.as_file(resources.files("dndref").joinpath("datasets")) as dataset_root:
        assert dataset_root.is_dir()
        assert (dataset_root / "srd-5.2.1" / "manifest.json").is_file()
