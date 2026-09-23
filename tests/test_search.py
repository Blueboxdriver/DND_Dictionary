from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dndref.importer import import_dataset, load_dataset
from dndref.search import (
    GroupedEntrySummary,
    SearchCategory,
    SearchMode,
    SearchQuery,
    SearchService,
    SourceIdentity,
    get_entry_detail,
    list_available_editions,
    list_available_sources,
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


def configure_source(
    dataset: Path, *, dataset_id: str, key: str, title: str, edition: str | None
) -> None:
    manifest = read_json(dataset, "manifest.json")
    assert isinstance(manifest, dict)
    old_key = str(manifest["sources"][0]["key"])
    manifest["dataset_id"] = dataset_id
    manifest["sources"] = [{"key": key, "title": title, "edition": edition}]
    write_json(dataset, "manifest.json", manifest)

    def replace_source(value: object) -> object:
        if isinstance(value, dict):
            return {
                name: key if name == "source" and item == old_key else replace_source(item)
                for name, item in value.items()
            }
        if isinstance(value, list):
            return [replace_source(item) for item in value]
        return value

    for filename in ("items.json", "spells.json", "feats.json", "classes.json"):
        write_json(dataset, filename, replace_source(read_json(dataset, filename)))


def grouped_fixture_database(tmp_path: Path, *, extra_names: int = 0) -> Database:
    first = copy_fixture(tmp_path, "phb")
    second = copy_fixture(tmp_path, "srd")
    configure_source(first, dataset_id="phb", key="book", title="Player's Handbook", edition="2024")
    configure_source(second, dataset_id="srd", key="book", title="SRD", edition="2014")
    for dataset, wording in ((first, "phb wording"), (second, "srd wording")):
        spells = read_json(dataset, "spells.json")
        assert isinstance(spells, list)
        spells[0]["description"] = wording
        for index in range(extra_names):
            extra = dict(spells[0])
            extra.update(local_key=f"spell/extra-{index:02d}", name=f"Extra {index:02d}")
            spells.append(extra)
        write_json(dataset, "spells.json", spells)
    return imported_database(tmp_path, first, second)


def test_grouped_search_keeps_matching_variants_and_source_preference(tmp_path: Path) -> None:
    service = SearchService(grouped_fixture_database(tmp_path))
    phb = SourceIdentity("phb", "book")
    srd = SourceIdentity("srd", "book")
    query = SearchQuery("spells", "Spark", editions=("2024", "2014"))
    raw = service.search(query)
    page = service.search_grouped(query, (phb, srd))
    assert raw.total_count == 2
    assert page.total_count == 1
    group = page.results[0]
    assert isinstance(group, GroupedEntrySummary)
    assert group.primary.source_identity == phb
    assert group.alternates[0].source_identity == srd
    assert group.identity == "group:spells:spark"
    assert service.get_entry_detail(group.primary.identity).description == "phb wording"
    assert service.get_entry_detail(group.alternates[0].identity).description == "srd wording"
    reversed_page = service.search_grouped(query, (srd, phb))
    assert reversed_page.results[0].primary.source_identity == srd
    unknown_first = service.search_grouped(query, (SourceIdentity("missing", "source"), phb))
    assert unknown_first.results[0].primary.source_identity == phb
    browse = SearchQuery("spells")
    assert [result.name for result in service.search_grouped(browse).results] == list(
        dict.fromkeys(result.name for result in service.search(browse).results)
    )
    only_srd = service.search_grouped(
        SearchQuery("spells", "srd wording", SearchMode.ALL_TEXT), (phb, srd)
    )
    assert only_srd.total_count == 1
    assert only_srd.results[0].source_identity == srd
    phb_only = service.search_grouped(SearchQuery("spells", "Spark", sources=(phb,)))
    assert phb_only.results[0].source_identity == phb
    assert (
        service.search_grouped(SearchQuery("spells", "Spark", editions=("2024",))).total_count
        == 1
    )


def test_grouped_pagination_pages_groups_not_rows(tmp_path: Path) -> None:
    service = SearchService(grouped_fixture_database(tmp_path, extra_names=7))
    raw = service.search(SearchQuery("spells", limit=2))
    assert raw.total_count == 18
    pages = [
        service.search_grouped(SearchQuery("spells", offset=offset, limit=2))
        for offset in (0, 2, 4, 6, 8)
    ]
    assert all(page.total_count == 9 for page in pages)
    names = [result.name for page in pages for result in page.results]
    assert names == sorted(names)
    assert len(names) == len(set(names)) == 9
    assert [len(page.results) for page in pages] == [2, 2, 2, 2, 1]
    assert all(
        isinstance(result, GroupedEntrySummary) and len(result.alternates) == 1
        for page in pages
        for result in page.results
    )


def test_same_source_name_collision_stays_separate(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path, "same-source")
    spells = read_json(dataset, "spells.json")
    assert isinstance(spells, list)
    second_spark = dict(spells[0])
    second_spark.update(local_key="spell/spark-other", description="Different spell")
    spells.append(second_spark)
    write_json(dataset, "spells.json", spells)
    service = SearchService(imported_database(tmp_path, dataset))
    page = service.search_grouped(SearchQuery("spells", "Spark"))
    assert page.total_count == 2
    assert all(not isinstance(result, GroupedEntrySummary) for result in page.results)
    assert {result.identity for result in page.results} == {
        "example-5e:spell/spark", "example-5e:spell/spark-other"
    }


def test_same_name_in_different_categories_never_groups(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    items = read_json(dataset, "items.json")
    assert isinstance(items, dict)
    items["items"][0]["name"] = "Spark"
    write_json(dataset, "items.json", items)
    service = SearchService(imported_database(tmp_path, dataset))
    item = service.search_grouped(SearchQuery("items", "Spark"))
    spell = service.search_grouped(SearchQuery("spells", "Spark"))
    assert item.total_count == spell.total_count == 1
    assert item.results[0].category is SearchCategory.ITEMS
    assert spell.results[0].category is SearchCategory.SPELLS


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


def test_query_filters_normalize_values_and_preserve_request_id() -> None:
    source = SourceIdentity("pack", "book")
    query = SearchQuery(
        "spells",
        editions=("2024", "2024", "2014"),
        sources=(source, source),
        request_id="filter-change-1",
    )
    assert query.editions == ("2024", "2014")
    assert query.sources == (source,)
    assert query.request_id == "filter-change-1"
    assert SearchQuery("spells").editions == ()
    assert SearchQuery("spells").sources == ()
    with pytest.raises(ValueError):
        SourceIdentity("pack", " ")


def test_edition_source_filters_browse_search_modes_and_paginate_in_sql(tmp_path: Path) -> None:
    first = copy_fixture(tmp_path, "first")
    second = copy_fixture(tmp_path, "second")
    configure_source(first, dataset_id="pack-a", key="core", title="Shared Book", edition="2024")
    configure_source(second, dataset_id="pack-b", key="core", title="Shared Book", edition="2014")
    database = imported_database(tmp_path, first, second)
    pack_a = SourceIdentity("pack-a", "core")
    pack_b = SourceIdentity("pack-b", "core")
    same_title_sources = list_available_sources(database, "spells")
    assert [option.identity for option in same_title_sources] == [pack_b, pack_a]
    assert {option.title for option in same_title_sources} == {"Shared Book"}

    assert search(database, SearchQuery("spells")).total_count == 4
    assert search(database, SearchQuery("spells", editions=("2024",))).total_count == 2
    assert search(database, SearchQuery("spells", editions=("2014", "2024"))).total_count == 4
    assert search(database, SearchQuery("spells", sources=(pack_a,))).total_count == 2
    assert search(database, SearchQuery("spells", sources=(pack_a, pack_b))).total_count == 4
    assert search(
        database, SearchQuery("spells", editions=("2024",), sources=(pack_b,))
    ).total_count == 0
    assert search(
        database, SearchQuery("spells", editions=("2024",), sources=(pack_a,))
    ).total_count == 2
    assert [
        search(database, SearchQuery(category, editions=("2024",), sources=(pack_a,))).total_count
        for category in ("items", "spells", "feats", "classes")
    ] == [4, 2, 1, 1]

    page = search(database, SearchQuery("spells", editions=("2024",), limit=1, request_id=17))
    next_page = search(
        database, SearchQuery("spells", editions=("2024",), offset=1, limit=1)
    )
    assert page.total_count == next_page.total_count == 2
    assert page.request_id == 17
    assert len(page.results) == len(next_page.results) == 1
    assert page.results[0].source_identity == pack_a
    assert page.results[0].source_edition == "2024"

    # The source restriction composes with both name matching and body-only FTS.
    assert search(database, SearchQuery("spells", "Spark")).total_count == 2
    assert search(
        database, SearchQuery("spells", "Spark", editions=("2024",))
    ).total_count == 1
    assert search(
        database, SearchQuery("spells", "Comet", editions=("2014",))
    ).total_count == 1
    assert search(
        database, SearchQuery("spells", "ark", sources=(pack_a,))
    ).total_count == 1
    body_results = search(
        database,
        SearchQuery("spells", "bright mote", SearchMode.ALL_TEXT, sources=(pack_b,)),
    )
    assert body_results.total_count == 1
    assert body_results.results[0].source_identity == pack_b
    multi_source_fts = search(
        database,
        SearchQuery("spells", "bright mote", SearchMode.ALL_TEXT, sources=(pack_a, pack_b)),
    )
    assert multi_source_fts.total_count == 2
    body_items = search(
        database,
        SearchQuery("items", "starlit guidance", SearchMode.ALL_TEXT, sources=(pack_a,)),
    )
    assert names(body_items) == ["Star Map"]


def test_unknown_editions_are_unfiltered_but_not_specific_edition_matches(tmp_path: Path) -> None:
    dataset = copy_fixture(tmp_path)
    configure_source(dataset, dataset_id="custom", key="core", title="Custom Rules", edition=None)
    database = imported_database(tmp_path, dataset)

    assert search(database, SearchQuery("spells")).total_count == 2
    assert search(database, SearchQuery("spells", editions=("2024",))).total_count == 0
    assert search(database, SearchQuery("spells", editions=("2014",))).total_count == 0
    assert list_available_editions(database) == ()
    assert list_available_sources(database)[0].edition is None


def test_filter_discovery_uses_searchable_entries_and_scopes_category(tmp_path: Path) -> None:
    first = copy_fixture(tmp_path, "first")
    second = copy_fixture(tmp_path, "second")
    configure_source(first, dataset_id="pack-2024", key="core", title="Core", edition="2024")
    configure_source(second, dataset_id="pack-2014", key="old", title="Old Core", edition="2014")
    spells = read_json(second, "spells.json")
    assert isinstance(spells, list)
    write_json(second, "spells.json", [])
    database = imported_database(tmp_path, first, second)

    editions = list_available_editions(database)
    assert [(option.value, option.label) for option in editions] == [
        ("2014", "2014 / 5e"),
        ("2024", "2024 / 5.5e"),
    ]
    assert [option.value for option in list_available_editions(database, "spells")] == ["2024"]
    assert [option.title for option in list_available_sources(database, "spells")] == ["Core"]
    assert [
        option.identity
        for option in list_available_sources(database, "items", editions=("2014", "2024"))
    ] == [SourceIdentity("pack-2014", "old"), SourceIdentity("pack-2024", "core")]
    assert [
        option.identity
        for option in list_available_sources(database, "items", editions=("2024",))
    ] == [SourceIdentity("pack-2024", "core")]


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
    with old_database.connection() as connection:
        connection.execute("UPDATE sources SET edition = '2024 rules'")
        connection.commit()

    upgraded = Database(database_path)
    assert upgraded.initialize() == (3, 4, 5)
    assert search(upgraded, SearchQuery("items", "finesse", SearchMode.ALL_TEXT)).total_count == 1
    with upgraded.connection() as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'entry_search'"
        ).fetchone() is not None
        assert connection.execute("SELECT edition FROM sources").fetchone()[0] == "2024"
        assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 8
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
