"""Monster schema, import, search, filtering, and terminal presentation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dndref.cli import initialize_application
from dndref.config import ApplicationPaths
from dndref.importer import (
    DatasetLoadError,
    import_dataset,
    legacy_monsterless_hash,
    load_dataset,
)
from dndref.models.monster import cr_value
from dndref.search import SearchCategory, SearchMode, SearchQuery, SearchService, SourceIdentity
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp, render_detail

FIXTURE = Path("tests/fixtures/dataset")
PRODUCTION = Path("src/dndref/datasets/official-5etools-2024")


def monster(key: str, name: str, source: str, cr: str, **overrides) -> dict:
    value = {
        "local_key": key, "name": name, "description": "A test stat block.",
        "source": source, "size": "Small", "creature_type": "Humanoid",
        "armor_class": "15 (leather armor)", "hit_points": 7,
        "hit_points_text": "7", "hit_dice": "2d6",
        "speed": {"walk": "30 ft.", "climb": "20 ft."},
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "challenge_rating": cr,
        "abilities_and_actions": [
            {"section": "traits", "name": "Nimble Escape", "description": "The goblin hides.",
             "display_order": 0},
            {"section": "actions", "name": "Multiattack", "description": "Two claw attacks.",
             "display_order": 0},
            {"section": "reactions", "name": "Parry", "description": "The goblin parries.",
             "display_order": 0},
            {"section": "legendary_actions", "name": "Tail Attack",
             "description": "The goblin attacks.", "display_order": 0, "cost": 2},
            {"section": "spellcasting", "name": "Spellcasting",
             "description": "At will: Mage Hand. 1/day: Fireball.", "display_order": 0},
        ],
    }
    value.update(overrides)
    return value


def pack(tmp_path: Path) -> Path:
    root = tmp_path / "pack"
    shutil.copytree(FIXTURE, root)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["sources"] = [
        {"key": "old", "title": "Old Book", "edition": "2014"},
        {"key": "new", "title": "New Book", "edition": "2024"},
    ]
    # Existing fixture entries retain their source identity.
    manifest["sources"].append({"key": "example-core", "title": "Example Core", "edition": "2024"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    monsters = [
        monster("monster/old/goblin", "Goblin", "old", "1/4"),
        monster("monster/new/goblin", "Goblin", "new", "1/2", size="Medium"),
        monster("monster/new/goblin-chief", "Goblin", "new", "2", variant="Chief"),
        monster("monster/new/rat", "Rat", "new", "0", size="Tiny", creature_type="Beast",
                abilities_and_actions=[], hit_points=None, hit_points_text="Variable"),
        monster("monster/new/imp", "Imp", "new", "1/8", creature_type="Fiend"),
        monster("monster/new/guard", "Guard", "new", "1", creature_type="Humanoid"),
    ]
    (root / "monsters.json").write_text(json.dumps(monsters))
    return root


def test_monster_import_idempotency_foreign_keys_and_migration(tmp_path: Path) -> None:
    root = pack(tmp_path)
    old = tmp_path / "migrations"
    old.mkdir()
    for path in Path("src/dndref/storage/migrations").glob("00[1-4]_*.sql"):
        shutil.copy(path, old / path.name)
    db = Database(tmp_path / "reference.sqlite3", old)
    assert db.initialize() == (1, 2, 3, 4)
    assert import_dataset(db, load_dataset(FIXTURE)).added == 8
    db = Database(db.path)
    assert db.initialize() == (5,)
    assert import_dataset(db, load_dataset(root)).added == 6
    assert import_dataset(db, load_dataset(root)).is_noop
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM monsters").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM monster_abilities").fetchone()[0] == 25
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        indexed = connection.execute(
            "SELECT COUNT(*) FROM entry_search WHERE entry_search MATCH 'nimble'"
        ).fetchone()[0]
        assert indexed == 5


@pytest.mark.parametrize("cr", ["0", "1/8", "1/4", "1/2", "1", "2"])
def test_cr_is_exact_and_ordered(cr: str) -> None:
    assert str(cr_value(cr)) == cr
    assert sorted(["2", "1/2", "0", "1/8", "1", "1/4"], key=cr_value) == [
        "0", "1/8", "1/4", "1/2", "1", "2"]


def test_search_filters_variants_details_and_sources(tmp_path: Path) -> None:
    db = Database(tmp_path / "reference.sqlite3")
    import_dataset(db, load_dataset(pack(tmp_path)))
    service = SearchService(db)
    query = SearchQuery("monster", "Goblin")
    assert service.search(query).total_count == 3
    assert service.search_grouped(query).total_count == 3
    assert service.search(SearchQuery("monsters", "Fiend")).total_count == 1
    assert service.search(SearchQuery("monsters", "Humanoid", SearchMode.ALL_TEXT)).total_count == 4
    assert service.search(
        SearchQuery("monsters", "Nimble Escape", SearchMode.ALL_TEXT)
    ).total_count == 5
    assert service.search(SearchQuery("monsters", "Goblin", editions=("2014",))).total_count == 1
    assert service.search(
        SearchQuery("monsters", "Goblin", sources=(SourceIdentity("example-5e", "new"),))
    ).total_count == 2
    assert service.search(SearchQuery("monsters", "", challenge_ratings=("1/2",),
                                      creature_types=("Humanoid",), sizes=("Medium",),
                                      editions=("2024",))).total_count == 1
    assert service.list_monster_facets()["cr"] == ("0", "1/8", "1/4", "1/2", "1", "2")
    detail = service.get_entry_detail("example-5e:monster/new/goblin")
    assert detail is not None
    assert detail.fields["edition"] == "2024"
    assert detail.fields["speed"]["climb"] == "20 ft."
    rendered = render_detail(detail)
    assert "## Reactions" in rendered and "## Legendary Actions" in rendered
    assert "2 Actions" in rendered and "At will: Mage Hand" in rendered
    sparse = service.get_entry_detail("example-5e:monster/new/rat")
    assert sparse is not None and "## Actions" not in render_detail(sparse)
    with db.connection() as connection:
        from dndref.storage.repository import list_source_contents
        sources = list_source_contents(connection)
        assert any(info.counts.get(SearchCategory.MONSTERS) for info in sources)


@pytest.mark.asyncio
async def test_startup_upgrades_previous_bundled_monsters_after_migration_005(
    tmp_path: Path,
) -> None:
    paths = ApplicationPaths.for_root(tmp_path / "app")
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for migration in Path("src/dndref/storage/migrations").glob("00[1-4]_*.sql"):
        shutil.copy(migration, old_migrations / migration.name)

    old_pack = tmp_path / "old-official"
    shutil.copytree(PRODUCTION, old_pack)
    (old_pack / "monsters.json").unlink()
    db = Database(paths.database_path, old_migrations)
    assert db.initialize() == (1, 2, 3, 4)
    previous = load_dataset(old_pack)
    import_dataset(db, previous)
    with db.connection() as connection:
        connection.execute(
            "UPDATE datasets SET content_hash = ? WHERE dataset_id = ?",
            (legacy_monsterless_hash(previous), "official-5etools-2024"),
        )
        connection.commit()

    initialize_application(paths)

    db = Database(paths.database_path)
    service = SearchService(db)
    monsters = service.search(SearchQuery(SearchCategory.MONSTERS))
    assert monsters.total_count == 503
    assert monsters.results[0].name == "Aarakocra Aeromancer"
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM monsters").fetchone()[0] == 503
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    async with BrowserApp(db).run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("6")
        await pilot.pause(0.3)
        assert pilot.app.category is SearchCategory.MONSTERS
        assert pilot.app.state.total_count == 503
        assert pilot.app.state.results[0].name == "Aarakocra Aeromancer"
        assert len(pilot.app.query_one("#result-list").children) == 50


def test_monster_replacement_refreshes_fts_and_children(tmp_path: Path) -> None:
    root = pack(tmp_path)
    db = Database(tmp_path / "reference.sqlite3")
    import_dataset(db, load_dataset(root))
    path = root / "monsters.json"
    monsters = json.loads(path.read_text())
    monsters[0]["abilities_and_actions"][0]["name"] = "Shadow Slip"
    monsters.pop()
    path.write_text(json.dumps(monsters))
    report = import_dataset(db, load_dataset(root))
    assert (report.changed, report.removed) == (1, 1)
    service = SearchService(db)
    assert service.search(
        SearchQuery("monsters", "Shadow Slip", SearchMode.ALL_TEXT)
    ).total_count == 1
    assert service.search(SearchQuery("monsters", "Guard")).total_count == 0
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM monsters").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM monster_abilities").fetchone()[0] == 20
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("change", [
    {"name": ""}, {"source": "missing"}, {"challenge_rating": "1/3"},
    {"hit_points": -1}, {"abilities": {"str": 10}},
    {"speed": {"walk": ""}},
    {"abilities_and_actions": [{"section": "actions", "name": "X", "description": "Y",
                               "display_order": -1}]},
])
def test_malformed_monster_is_rejected(tmp_path: Path, change: dict) -> None:
    root = pack(tmp_path)
    path = root / "monsters.json"
    records = json.loads(path.read_text())
    records[0].update(change)
    path.write_text(json.dumps(records))
    with pytest.raises(DatasetLoadError):
        load_dataset(root)


def test_monster_source_requires_edition_and_orders_are_unique(tmp_path: Path) -> None:
    root = pack(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0].pop("edition")
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(DatasetLoadError, match="canonical edition"):
        load_dataset(root)
    manifest["sources"][0]["edition"] = "2014"
    manifest_path.write_text(json.dumps(manifest))
    monsters_path = root / "monsters.json"
    monsters = json.loads(monsters_path.read_text())
    monsters[0]["abilities_and_actions"].append(
        {"section": "actions", "name": "Duplicate", "description": "Another action.",
         "display_order": 0}
    )
    monsters_path.write_text(json.dumps(monsters))
    with pytest.raises(DatasetLoadError, match="ordering"):
        load_dataset(root)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24), (60, 20)])
async def test_monster_layout_and_keyboard(size: tuple[int, int], tmp_path: Path) -> None:
    db = Database(tmp_path / "reference.sqlite3")
    import_dataset(db, load_dataset(pack(tmp_path)))
    async with BrowserApp(db).run_test(size=size) as pilot:
        await pilot.pause(0.25)
        await pilot.press("6")
        await pilot.pause(0.25)
        assert pilot.app.category is SearchCategory.MONSTERS
        assert pilot.app.state.total_count == 5
        assert pilot.app.query_one("#detail-scroll").display
        if size[0] < 80:
            assert str(pilot.app.query_one("#tab-subclasses").label) == "Subcls"
        await pilot.press("c")
        assert pilot.app.screen.__class__.__name__ == "ChoiceScreen"
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_monster_filter_dialogs_combine(tmp_path: Path) -> None:
    db = Database(tmp_path / "reference.sqlite3")
    import_dataset(db, load_dataset(pack(tmp_path)))
    async with BrowserApp(db).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.25)
        await pilot.press("6")
        await pilot.pause(0.25)
        for key, steps in (("c", 1), ("t", 1), ("z", 3)):
            await pilot.press(key)
            await pilot.press(*(["j"] * steps), "enter")
            await pilot.pause(0.2)
        assert pilot.app.state.challenge_rating == "0"
        assert pilot.app.state.creature_type == "Beast"
        assert pilot.app.state.size == "Tiny"
        assert pilot.app.state.total_count == 1
