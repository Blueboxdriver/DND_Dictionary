from __future__ import annotations

import json
import shutil
from pathlib import Path

from dndref.importer import import_dataset, load_dataset
from dndref.personal import PersonalDataService
from dndref.search import SearchService
from dndref.storage.database import MIGRATIONS_DIR, Database

FIXTURE = Path(__file__).parent / "fixtures" / "dataset"


def _dataset(tmp_path: Path) -> Path:
    path = tmp_path / "dataset"
    shutil.copytree(FIXTURE, path)
    return path


def _initialized(tmp_path: Path) -> tuple[Database, Path]:
    path = _dataset(tmp_path)
    database = Database(tmp_path / "dndref.sqlite3")
    import_dataset(database, load_dataset(path))
    return database, path


def test_migration_005_upgrade_and_fresh_schema(tmp_path: Path) -> None:
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for migration in MIGRATIONS_DIR.glob("00[1-5]_*.sql"):
        shutil.copy(migration, old_migrations)
    path = _dataset(tmp_path)
    database_path = tmp_path / "upgrade.sqlite3"
    old_database = Database(database_path, old_migrations)
    import_dataset(old_database, load_dataset(path))
    with old_database.connection() as db:
        before = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    database = Database(database_path)
    assert database.initialize() == (6,)
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == before
        assert (
            db.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
            == 6
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "user_favorites",
            "user_collections",
            "user_collection_entries",
            "user_tags",
            "user_entry_tags",
            "user_notes",
        } <= tables


def test_personal_data_persists_across_import_removal_and_reappearance(tmp_path: Path) -> None:
    database, dataset = _initialized(tmp_path)
    service = PersonalDataService(database)
    detail = SearchService(database).get_entry_detail("example-5e:spell/spark")
    assert detail is not None

    service.set_favorite(detail, True)
    service.set_favorite(detail, True)
    collection_id = service.create_collection("Current Campaign")
    service.set_collection_membership(collection_id, detail, True)
    service.set_collection_membership(collection_id, detail, True)
    service.add_tag(detail.identity, " Boss  ")
    service.add_tag(detail.identity, "boss")
    service.add_tag(detail.identity, "campaign  one")
    service.save_note(detail, "line one\nline two")
    service = PersonalDataService(Database(database.path))

    assert service.is_favorite(detail.identity)
    assert len(service.list_favorites()) == 1
    assert len(service.list_collection_entries(collection_id)) == 1
    assert service.tags_for(detail.identity) == ("boss", "campaign one")
    assert service.note_for(detail.identity) == "line one\nline two"

    spells = json.loads((dataset / "spells.json").read_text())
    restored_spell = next(spell for spell in spells if spell["local_key"] == "spell/spark")
    spells = [spell for spell in spells if spell["local_key"] != "spell/spark"]
    (dataset / "spells.json").write_text(json.dumps(spells))
    import_dataset(database, load_dataset(dataset))
    assert service.list_favorites()[0].missing
    assert service.list_collection_entries(collection_id)[0].missing
    assert service.note_for(detail.identity) == "line one\nline two"

    restored_spell["name"] = "Spark Restored"
    restored_spell["description"] = "restored"
    spells.append(restored_spell)
    (dataset / "spells.json").write_text(json.dumps(spells))
    import_dataset(database, load_dataset(dataset))
    restored = service.list_favorites()[0]
    assert restored.identity == detail.identity
    assert restored.name == "Spark Restored"
    assert not restored.missing
    assert service.tags_for(detail.identity) == ("boss", "campaign one")


def test_collections_notes_and_exact_ids_do_not_touch_reference_data(tmp_path: Path) -> None:
    database, _ = _initialized(tmp_path)
    service = PersonalDataService(database)
    search = SearchService(database)
    first = search.get_entry_detail("example-5e:spell/spark")
    second = search.get_entry_detail("example-5e:spell/comet-burst")
    assert first is not None and second is not None
    with database.connection() as db:
        reference_before = db.execute(
            "SELECT id,name,content_hash FROM entries ORDER BY id"
        ).fetchall()

    cid = service.create_collection("Wizard Build", "Campaign list")
    service.rename_collection(cid, "Arcane Notes", "Renamed")
    service.set_collection_membership(cid, first, True)
    service.set_collection_membership(cid, second, True)
    other = service.create_collection("Other")
    service.set_collection_membership(other, first, True)
    service.add_tag(first.identity, "utility")
    service.add_tag(second.identity, "utility")
    service.save_note(first, "remember\nthis")
    assert {r.identity for r in service.list_collection_entries(cid, tag="UTILITY")} == {
        first.identity,
        second.identity,
    }
    assert service.list_tags() == (("utility", 2),)
    service.save_note(first, " \n ")
    assert service.note_for(first.identity) is None
    service.delete_collection(cid)
    assert service.collection_ids_for(first.identity) == (other,)

    with database.connection() as db:
        assert [
            tuple(row) for row in db.execute("SELECT id,name,content_hash FROM entries ORDER BY id")
        ] == [tuple(row) for row in reference_before]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_same_named_entry_in_another_dataset_keeps_separate_favorite_identity(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "variants.sqlite3"
    database = Database(database_path)
    for dataset_id, edition in (("pack-2014", "2014"), ("pack-2024", "2024")):
        path = tmp_path / dataset_id
        shutil.copytree(FIXTURE, path)
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["dataset_id"] = dataset_id
        manifest["sources"][0]["edition"] = edition
        (path / "manifest.json").write_text(json.dumps(manifest))
        import_dataset(database, load_dataset(path))

    service = PersonalDataService(database)
    first = SearchService(database).get_entry_detail("pack-2014:spell/spark")
    second = SearchService(database).get_entry_detail("pack-2024:spell/spark")
    assert first is not None and second is not None
    assert first.name == second.name
    service.set_favorite(first, True)
    favorites = service.list_favorites()
    assert len(favorites) == 1
    assert favorites[0].identity == "pack-2014:spell/spark"
    assert favorites[0].edition == "2014"
