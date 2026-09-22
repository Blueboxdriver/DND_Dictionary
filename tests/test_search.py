from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dndref.importer import import_dataset, load_dataset
from dndref.search import (
    SearchCategory,
    SearchMode,
    SearchQuery,
    get_entry_detail,
    normalize_name,
    search,
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


def imported_database(tmp_path: Path, *datasets: Path) -> Database:
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    for dataset in datasets:
        import_dataset(database, load_dataset(dataset))
    return database


def names(page) -> list[str]:
    return [result.name for result in page.results]


def test_name_normalization_is_deterministic() -> None:
    assert normalize_name("  Mĕlf’s\t  Ácid   Arrow  ") == "melf's acid arrow"
    assert normalize_name("Fire—Ball") == "fire—ball"


def test_empty_browse_category_subtitles_and_category_isolation(tmp_path: Path) -> None:
    database = imported_database(tmp_path, copy_fixture(tmp_path))

    items = search(database, SearchQuery("items"))
    assert names(items) == ["Adventuring Pack", "Chain Shirt", "Rapier", "Star Map"]
    assert items.total_count == 4
    assert items.results[2].subtitle == "Martial Melee"
    assert items.results[2].source_label == "Example Core Rules"

    spells = search(database, SearchQuery(SearchCategory.SPELLS))
    assert spells.total_count == 2
    assert all(result.category is SearchCategory.SPELLS for result in spells.results)
    assert search(database, SearchQuery("items", "wizard")).total_count == 0


def test_names_mode_requires_all_terms_and_supports_fire_ball_substring_semantics(
    tmp_path: Path,
) -> None:
    dataset = copy_fixture(tmp_path)
    items = read_json(dataset, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["name"] = "Fireball"
    write_json(dataset, "items.json", items)
    database = imported_database(tmp_path, dataset)

    assert names(search(database, SearchQuery("items", "fire ball"))) == ["Fireball"]
    assert names(search(database, SearchQuery("items", "fire sword"))) == []


def test_names_ranking_is_exact_then_prefix_then_other_match(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    items = read_json(dataset, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["name"] = "Rapier"
    items["items"][1]["name"] = "Rapier of Dawn"
    items["items"][2]["name"] = "Dawn Rapier"
    write_json(dataset, "items.json", items)
    database = imported_database(tmp_path, dataset)

    assert names(search(database, SearchQuery("items", "rapier")))[:3] == [
        "Rapier",
        "Rapier of Dawn",
        "Dawn Rapier",
    ]


@pytest.mark.parametrize(
    "text",
    [
        '"magic missile"',
        "acid (arrow)",
        "+1 sword",
        "fire-ball",
        "melf's",
        "a:b*c -d",
    ],
)
def test_all_text_punctuation_is_literal_and_parser_safe(tmp_path: Path, text: str) -> None:
    database = imported_database(tmp_path, copy_fixture(tmp_path))

    page = search(database, SearchQuery("spells", text, SearchMode.ALL_TEXT))
    assert page.total_count >= 0


def test_all_text_indexes_sections_and_category_specific_content(tmp_path: Path) -> None:
    database = imported_database(tmp_path, copy_fixture(tmp_path))

    checks = [
        ("spells", "powdered glass", "Comet Burst"),
        ("spells", "damage increases", "Comet Burst"),
        ("items", "finesse", "Rapier"),
        ("items", "starlit guidance", "Star Map"),
        ("feats", "scholarly text", "Quick Study"),
        ("classes", "spellcasting", "Wizard"),
        ("classes", "star sage", "Wizard"),
        ("classes", "astral reading", "Wizard"),
    ]
    for category, text, expected in checks:
        page = search(database, SearchQuery(category, text, SearchMode.ALL_TEXT))
        assert names(page) == [expected], (category, text)


def test_all_text_name_matches_rank_before_body_only_matches(tmp_path: Path) -> None:
    first = copy_fixture(tmp_path, "first")
    second = copy_fixture(tmp_path, "second")
    manifest = read_json(second, "manifest.json")
    assert isinstance(manifest, dict)
    manifest["dataset_id"] = "other-pack"
    items = read_json(second, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["name"] = "Finesse"
    write_json(second, "manifest.json", manifest)
    write_json(second, "items.json", items)
    database = imported_database(tmp_path, first, second)

    page = search(database, SearchQuery("items", "finesse", SearchMode.ALL_TEXT))
    assert names(page)[:2] == ["Finesse", "Rapier"]
    assert page.results[0].stable_id.startswith("other-pack:")


def test_pagination_is_sql_paged_and_stable(tmp_path: Path) -> None:
    database = imported_database(tmp_path, copy_fixture(tmp_path))
    first = search(database, SearchQuery("items", limit=2))
    second = search(database, SearchQuery("items", offset=2, limit=2))
    final = search(database, SearchQuery("items", offset=4, limit=2))

    assert first.total_count == second.total_count == final.total_count == 4
    assert names(first) + names(second) == names(search(database, SearchQuery("items")))
    assert final.results == ()
    with pytest.raises(ValueError):
        SearchQuery("items", offset=-1)
    with pytest.raises(ValueError):
        SearchQuery("items", limit=201)


def test_fts_and_content_are_replaced_together_on_update_and_removal(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    database = imported_database(tmp_path, dataset)
    spells = read_json(dataset, "spells.json")
    assert isinstance(spells, list)
    spells[0]["description"] = "A luminescent bolt crosses the battlefield."
    spells.pop()
    write_json(dataset, "spells.json", spells)
    import_dataset(database, load_dataset(dataset))

    assert (
        search(database, SearchQuery("spells", "luminescent", SearchMode.ALL_TEXT)).total_count
        == 1
    )
    assert (
        search(database, SearchQuery("spells", "powdered glass", SearchMode.ALL_TEXT)).total_count
        == 0
    )
    with database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM entry_search WHERE dataset_id = ?", ("example-5e",)
        ).fetchone()[0] == 7


def test_detail_lookup_returns_structured_data_and_missing_is_none(tmp_path: Path) -> None:
    database = imported_database(tmp_path, copy_fixture(tmp_path))

    detail = get_entry_detail(database, "example-5e:class/wizard")
    assert detail is not None
    assert detail.category is SearchCategory.CLASSES
    assert detail.fields["features"]
    assert detail.fields["subclasses"][0]["name"] == "Star Sage"
    assert get_entry_detail(database, "example-5e:missing") is None


def test_upgrade_from_milestone_4_backfills_fts(tmp_path: Path) -> None:
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    source = Path("src/dndref/storage/migrations")
    for filename in ("001_initial.sql", "002_content.sql"):
        shutil.copy(source / filename, old_migrations / filename)

    database_path = tmp_path / "data" / "dndref.sqlite3"
    old_database = Database(database_path, old_migrations)
    old_database.initialize()
    import_dataset(old_database, load_dataset(copy_fixture(tmp_path)))

    upgraded = Database(database_path)
    assert upgraded.initialize() == (3,)
    assert search(upgraded, SearchQuery("items", "finesse", SearchMode.ALL_TEXT)).total_count == 1
    with upgraded.connection() as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'entry_search'"
        ).fetchone() is not None
