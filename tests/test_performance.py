from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from dndref.importer import import_dataset, load_dataset
from dndref.performance import PerformanceProfiler
from dndref.personal import PersonalDataService
from dndref.search import SearchCategory, SearchQuery, SearchService
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp

FIXTURE = Path(__file__).parent / "fixtures" / "dataset"
PRODUCTION = Path("src/dndref/datasets/official-5etools-2024")


def _monster(index: int, source: str) -> dict[str, object]:
    return {
        "local_key": f"monster/performance-{index}",
        "name": f"Performance Monster {index}",
        "description": "A fixture monster for list-query profiling.",
        "source": source,
        "size": "Medium",
        "creature_type": "Beast",
        "armor_class": "12",
        "hit_points": 10,
        "hit_points_text": "10",
        "hit_dice": "3d8",
        "speed": {"walk": "30 ft."},
        "abilities": {"str": 12, "dex": 12, "con": 10, "int": 6, "wis": 10, "cha": 6},
        "challenge_rating": "1/4",
        "abilities_and_actions": [
            {
                "section": "actions",
                "name": f"Action {ability}",
                "description": "This child detail must stay out of list summaries.",
                "display_order": ability,
            }
            for ability in range(8)
        ],
    }


def _database(
    tmp_path: Path, *, extra_spells: int = 0, monster_count: int = 0,
    profiler: PerformanceProfiler | None = None,
) -> Database:
    dataset = tmp_path / "dataset"
    shutil.copytree(FIXTURE, dataset)
    if extra_spells:
        path = dataset / "spells.json"
        spells = json.loads(path.read_text(encoding="utf-8"))
        for index in range(extra_spells):
            extra = dict(spells[0])
            extra.update(
                local_key=f"spell/performance-{index}",
                name=f"Performance Spell {index}",
            )
            spells.append(extra)
        path.write_text(json.dumps(spells), encoding="utf-8")
    if monster_count:
        manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
        source = str(manifest["sources"][0]["key"])
        (dataset / "monsters.json").write_text(
            json.dumps([_monster(index, source) for index in range(monster_count)]),
            encoding="utf-8",
        )
    database = Database(tmp_path / "dndref.sqlite3", profiler=profiler)
    import_dataset(database, load_dataset(dataset))
    return database


@pytest.mark.parametrize(
    "category",
    [
        SearchCategory.ITEMS,
        SearchCategory.SPELLS,
        SearchCategory.FEATS,
        SearchCategory.CLASSES,
        SearchCategory.SUBCLASSES,
        SearchCategory.MONSTERS,
        SearchCategory.CONDITIONS,
        SearchCategory.RULES,
    ],
)
def test_category_query_statement_count_is_bounded(
    tmp_path: Path, category: SearchCategory
) -> None:
    profiler = PerformanceProfiler()
    database = _database(tmp_path, monster_count=24, profiler=profiler)

    page = SearchService(database).search_grouped(SearchQuery(category, limit=20))

    assert profiler.statement_count(f"category_query.{category.value}") <= 4
    if category is SearchCategory.MONSTERS:
        assert page.total_count == 24
        assert len(page.results) == 20
        assert not hasattr(page.results[0], "abilities_and_actions")


def test_category_query_statement_count_does_not_grow_with_result_count(
    tmp_path: Path,
) -> None:
    counts = []
    for name, extra_spells in (("small", 0), ("large", 40)):
        profiler = PerformanceProfiler()
        database = _database(tmp_path / name, extra_spells=extra_spells, profiler=profiler)
        SearchService(database).search_grouped(
            SearchQuery(SearchCategory.SPELLS, limit=20)
        )
        counts.append(profiler.statement_count("category_query.spells"))

    assert counts[0] == counts[1]


def test_scoped_universal_search_queries_only_the_selected_category(
    tmp_path: Path,
) -> None:
    profiler = PerformanceProfiler()
    database = _database(tmp_path, monster_count=2, profiler=profiler)
    service = SearchService(database)

    service.search_all("monster:performance")
    service.search_all("spell:spark")

    assert profiler.statement_count("universal_search.monsters") == 2
    assert profiler.statement_count("universal_search.spells") == 2
    for category in SearchCategory:
        if category not in {SearchCategory.MONSTERS, SearchCategory.SPELLS}:
            assert profiler.statement_count(f"universal_search.{category.value}") == 0


def test_personal_list_resolution_uses_batched_metadata_queries(tmp_path: Path) -> None:
    profiler = PerformanceProfiler()
    database = _database(tmp_path, profiler=profiler)
    search = SearchService(database)
    personal = PersonalDataService(database)
    first = search.get_entry_detail("example-5e:spell/spark")
    second = search.get_entry_detail("example-5e:spell/comet-burst")
    assert first is not None and second is not None

    personal.set_favorite(first, True)
    before_one = profiler.statement_count("personal.favorites")
    assert len(personal.list_favorites()) == 1
    one_favorite_queries = profiler.statement_count("personal.favorites") - before_one

    personal.set_favorite(second, True)
    before_many = profiler.statement_count("personal.favorites")
    assert len(personal.list_favorites()) == 2
    many_favorite_queries = profiler.statement_count("personal.favorites") - before_many

    assert one_favorite_queries == many_favorite_queries
    assert many_favorite_queries <= 4


class _TrackingBrowserApp(BrowserApp):
    def __init__(self, *args, **kwargs) -> None:
        self.completed_searches = 0
        super().__init__(*args, **kwargs)

    async def _handle_search_result(self, page) -> None:
        await super()._handle_search_result(page)
        self.completed_searches += 1


class _SqlTraceDatabase(Database):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.statements: list[str] = []

    def connect(self):
        connection = super().connect()
        connection.set_trace_callback(self.statements.append)
        return connection


async def _wait_for_search(pilot, app: _TrackingBrowserApp, previous: int) -> None:
    deadline = time.monotonic() + 15
    while app.completed_searches <= previous:
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for category results")
        await pilot.pause(0.005)


def test_production_category_browsing_does_not_read_or_load_builder_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dndref.importer import import_dataset, load_dataset

    loaded = load_dataset(PRODUCTION)
    base_database = Database(tmp_path / "production.sqlite3")
    base_database.initialize()
    import_dataset(base_database, loaded)
    with base_database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM character_builder_owners "
            "WHERE dataset_id='official-5etools-2024'"
        ).fetchone()[0] == 2104

    def fail_if_dataset_loads(*_args, **_kwargs):
        raise AssertionError("category browsing attempted to load dataset JSON")

    monkeypatch.setattr("dndref.importer.load_dataset", fail_if_dataset_loads)
    database = _SqlTraceDatabase(base_database.path)
    service = SearchService(database)
    for category in SearchCategory:
        page = service.search_grouped(SearchQuery(category, limit=20))
        assert page.total_count > 0

    builder_tables = (
        "character_builder_",
        "character_rule_",
        "class_progression_events",
        "class_spellcasting",
        "class_spell_slots",
    )
    assert not any(
        table in statement.casefold()
        for statement in database.statements
        for table in builder_tables
    )


@pytest.mark.asyncio
async def test_category_cache_reuses_pages_and_invalidates_on_dataset_change(
    tmp_path: Path,
) -> None:
    profiler = PerformanceProfiler()
    database = _database(tmp_path, profiler=profiler)
    app = _TrackingBrowserApp(database, profiler=profiler)

    async with app.run_test(size=(80, 24)) as pilot:
        await _wait_for_search(pilot, app, 0)

        before = app.completed_searches
        app._switch_category(SearchCategory.ITEMS)
        await _wait_for_search(pilot, app, before)
        item_queries = profiler.statement_count("category_query.items")
        assert item_queries > 0

        before = app.completed_searches
        app._switch_category(SearchCategory.SPELLS)
        await _wait_for_search(pilot, app, before)
        before = app.completed_searches
        app._switch_category(SearchCategory.ITEMS)
        await _wait_for_search(pilot, app, before)
        assert profiler.statement_count("category_query.items") == item_queries

        with database.connection() as connection:
            connection.execute(
                "UPDATE datasets SET content_hash=content_hash || '-changed' "
                "WHERE dataset_id='example-5e'"
            )
            connection.commit()

        before = app.completed_searches
        app._switch_category(SearchCategory.SPELLS)
        await _wait_for_search(pilot, app, before)
        before = app.completed_searches
        app._switch_category(SearchCategory.ITEMS)
        await _wait_for_search(pilot, app, before)

        assert profiler.statement_count("category_query.items") > item_queries
