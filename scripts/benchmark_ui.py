"""Report repeatable database and headless Textual navigation timings."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import tracemalloc
from pathlib import Path

from dndref.config import ApplicationPaths, Config
from dndref.performance import PerformanceProfiler
from dndref.personal import PersonalDataService
from dndref.search import (
    EntrySummary,
    GroupedEntrySummary,
    SearchCategory,
    SearchMode,
    SearchQuery,
    SearchService,
)
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp


class BenchmarkApp(BrowserApp):
    def __init__(self, *args, **kwargs) -> None:
        self.completed_searches: list[int] = []
        super().__init__(*args, **kwargs)

    async def _handle_search_result(self, page) -> None:
        await super()._handle_search_result(page)
        self.completed_searches.append(page.request_id)


def _summary_identity(summary: EntrySummary | GroupedEntrySummary) -> str:
    if isinstance(summary, GroupedEntrySummary):
        return summary.primary.identity
    return summary.identity


def _parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = value.lower().split("x", 1)
        parsed = (int(width), int(height))
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT") from exc
    if min(parsed) < 1:
        raise argparse.ArgumentTypeError("size dimensions must be positive")
    return parsed


async def _wait_for_search(pilot, app: BenchmarkApp, previous: int) -> None:
    deadline = time.perf_counter() + 15
    while len(app.completed_searches) <= previous:
        if time.perf_counter() >= deadline:
            raise RuntimeError("timed out waiting for category results")
        await pilot.pause(0.002)


async def _wait_for_detail(pilot, app: BenchmarkApp, identity: str) -> None:
    deadline = time.perf_counter() + 15
    while app._detail_loaded_for != identity:
        if time.perf_counter() >= deadline:
            raise RuntimeError(f"timed out waiting for detail {identity}")
        await pilot.pause(0.002)


async def _wait_for_statement_count(
    pilot, profiler: PerformanceProfiler, name: str, previous: int
) -> None:
    deadline = time.perf_counter() + 15
    while profiler.statement_count(name) <= previous:
        if time.perf_counter() >= deadline:
            raise RuntimeError(f"timed out waiting for profiled operation {name}")
        await pilot.pause(0.002)


def _time_operation(label: str, operation, iterations: int) -> tuple[float, ...]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000)
    median = statistics.median(samples[1:] or samples)
    print(f"{label}: first={samples[0]:.2f} ms warm_median={median:.2f} ms")
    return tuple(samples)


def _detail_identities(database: Database, service: SearchService) -> dict[str, str]:
    identities: dict[str, str] = {}
    for label, category, query_text in (
        ("simple spell", SearchCategory.SPELLS, "Fireball"),
        ("class", SearchCategory.CLASSES, ""),
        ("subclass", SearchCategory.SUBCLASSES, ""),
    ):
        page = service.search_grouped(SearchQuery(category, query_text, limit=1))
        if page.results:
            identities[label] = _summary_identity(page.results[0])
    with database.connection() as connection:
        monster = connection.execute(
            "SELECT e.dataset_id||':'||e.local_key FROM entries e "
            "JOIN monsters m ON m.entry_id=e.id WHERE e.kind='monster' "
            "ORDER BY (SELECT COUNT(*) FROM monster_abilities a "
            "WHERE a.monster_id=m.entry_id) DESC, e.normalized_name LIMIT 1"
        ).fetchone()
        long_rule = connection.execute(
            "SELECT e.dataset_id||':'||e.local_key FROM entries e "
            "WHERE e.kind='rule' ORDER BY length(e.description) + "
            "(SELECT COALESCE(SUM(length(s.body)),0) FROM entry_sections s "
            "WHERE s.entry_id=e.id) DESC LIMIT 1"
        ).fetchone()
    if monster is not None:
        identities["complex monster"] = str(monster[0])
    if long_rule is not None:
        identities["long rule"] = str(long_rule[0])
    return identities


def _benchmark_service(database: Database, service: SearchService, iterations: int) -> None:
    print("DB category queries (includes model conversion and grouping)")
    for category in SearchCategory:
        query = SearchQuery(category, mode=SearchMode.NAMES, limit=BrowserApp.PAGE_SIZE)
        _time_operation(
            f"category_query.{category.value}",
            lambda query=query: service.search_grouped(query),
            iterations,
        )

    print("DB filtered and universal searches")
    facets = service.list_monster_facets()
    if facets["cr"]:
        query = SearchQuery(
            SearchCategory.MONSTERS,
            challenge_ratings=(facets["cr"][0],),
            limit=BrowserApp.PAGE_SIZE,
        )
        _time_operation(
            "category_query.monsters.filtered",
            lambda: service.search_grouped(query),
            iterations,
        )
    for text in ("", "monster:dragon", "spell:fireball"):
        _time_operation(
            f"universal_search.{text or 'all'}",
            lambda text=text: service.search_all(text, limit=50),
            iterations,
        )

    print("DB source and personal views")
    personal = PersonalDataService(database)
    _time_operation("source_browser", service.list_source_contents, iterations)
    _time_operation("personal.favorites", personal.list_favorites, iterations)
    _time_operation("personal.collections", personal.list_collections, iterations)

    print("DB detail records")
    for label, identity in _detail_identities(database, service).items():
        _time_operation(
            f"detail_query.{label}",
            lambda identity=identity: service.get_entry_detail(identity),
            iterations,
        )


async def _benchmark_ui(
    database: Database,
    profiler: PerformanceProfiler,
    size: tuple[int, int],
) -> None:
    app = BenchmarkApp(
        database,
        paths=ApplicationPaths.for_root(database.path.parent.parent),
        config=Config(),
        profiler=profiler,
    )
    started = time.perf_counter()
    async with app.run_test(size=size) as pilot:
        await _wait_for_search(pilot, app, 0)
        initial_category = app.category
        print(
            f"ui.startup_and_first_{app.category.value}: "
            f"{(time.perf_counter() - started) * 1000:.2f} ms"
        )
        for category in BrowserApp.CATEGORIES:
            if category is initial_category:
                continue
            before = len(app.completed_searches)
            started = time.perf_counter()
            app._switch_category(category)
            await _wait_for_search(pilot, app, before)
            elapsed = (time.perf_counter() - started) * 1000
            print(f"ui.category_first.{category.value}: {elapsed:.2f} ms")
        for category in (SearchCategory.ITEMS, SearchCategory.MONSTERS, SearchCategory.SPELLS):
            if category is app.category:
                continue
            before = len(app.completed_searches)
            started = time.perf_counter()
            app._switch_category(category)
            await _wait_for_search(pilot, app, before)
            elapsed = (time.perf_counter() - started) * 1000
            print(f"ui.category_warm.{category.value}: {elapsed:.2f} ms")

        if app.category is not SearchCategory.MONSTERS:
            before = len(app.completed_searches)
            app._switch_category(SearchCategory.MONSTERS)
            await _wait_for_search(pilot, app, before)
        facets = app.search_service.list_monster_facets()
        if facets["cr"]:
            app.state.challenge_rating = facets["cr"][0]
            before = len(app.completed_searches)
            started = time.perf_counter()
            app._apply_filter_change()
            await _wait_for_search(pilot, app, before)
            print(f"ui.filtered_category.monsters: {(time.perf_counter() - started) * 1000:.2f} ms")
        app.state.challenge_rating = None
        search_input = app._query_widget("#search-input")
        search_input.value = "dragon"
        before = len(app.completed_searches)
        started = time.perf_counter()
        await _wait_for_search(pilot, app, before)
        elapsed = (time.perf_counter() - started) * 1000
        print(f"ui.category_search_refresh.monsters: {elapsed:.2f} ms")

        if app.state.results:
            selected = app.state.results[0]
            identity = _summary_identity(selected)
            if identity == app._detail_loaded_for and len(app.state.results) > 1:
                selected = app.state.results[1]
                identity = _summary_identity(selected)
            started = time.perf_counter()
            app._select_summary(selected)
            await _wait_for_detail(pilot, app, identity)
            print(f"ui.detail_open: {(time.perf_counter() - started) * 1000:.2f} ms")

        # Opening another category's entry exercises Back restoration to the
        # existing category state and its cached summary page.
        page = app.search_service.search_grouped(
            SearchQuery(SearchCategory.SPELLS, "", limit=1)
        )
        if page.results:
            before = len(app.completed_searches)
            started = time.perf_counter()
            app._navigate_to_identity(_summary_identity(page.results[0]))
            await _wait_for_search(pilot, app, before)
            app.action_back()
            await _wait_for_search(pilot, app, before + 1)
            print(f"ui.detail_to_category_back: {(time.perf_counter() - started) * 1000:.2f} ms")

        for label, operation in (
            ("source_browser", app.action_browse_sources),
            ("favorites", app.action_favorites),
            ("collections", app.action_collections),
        ):
            started = time.perf_counter()
            operation()
            await pilot.pause(0.02)
            print(f"ui.open_{label}: {(time.perf_counter() - started) * 1000:.2f} ms")
            app.pop_screen()
            await pilot.pause(0.01)

        started = time.perf_counter()
        app.action_universal_search()
        await pilot.pause(0.02)
        print(f"ui.open_universal_search: {(time.perf_counter() - started) * 1000:.2f} ms")
        universal_screen = app.screen
        universal_input = universal_screen.query_one("#universal-input")
        for label, query, operation_name in (
            ("first", "monster:dragon", "universal_search.monsters"),
            ("repeat", "monster:dragons", "universal_search.monsters"),
            ("repeat", "monster:dragon", "universal_search.monsters"),
        ):
            before = profiler.statement_count(operation_name)
            started = time.perf_counter()
            universal_input.value = query
            await _wait_for_statement_count(pilot, profiler, operation_name, before)
            await pilot.pause(0.03)
            print(
                f"ui.universal_search.{label}.{query}: "
                f"{(time.perf_counter() - started) * 1000:.2f} ms"
            )

        before = profiler.statement_count("universal_search.spells")
        started = time.perf_counter()
        for query in ("s", "sp", "spe", "spell:fireball"):
            universal_input.value = query
            await pilot.pause(0.025)
        await _wait_for_statement_count(
            pilot, profiler, "universal_search.spells", before
        )
        await pilot.pause(0.03)
        print(
            f"ui.universal_search.rapid_typing: "
            f"{(time.perf_counter() - started) * 1000:.2f} ms; "
            f"scoped SQL statements="
            f"{profiler.statement_count('universal_search.spells') - before}"
        )
        app.pop_screen()
        await pilot.pause(0.01)


async def _benchmark_category_cache_memory(
    database: Database,
    size: tuple[int, int],
) -> None:
    """Report retained Python allocations after warming every category page."""
    app_database = Database(database.path)
    app = BenchmarkApp(
        app_database,
        paths=ApplicationPaths.for_root(database.path.parent.parent),
        config=Config(),
    )
    tracemalloc.start()
    try:
        async with app.run_test(size=size) as pilot:
            await _wait_for_search(pilot, app, 0)
            first_category = app.category
            first_bytes = tracemalloc.get_traced_memory()[0]
            for category in BrowserApp.CATEGORIES:
                if category is first_category:
                    continue
                before = len(app.completed_searches)
                app._switch_category(category)
                await _wait_for_search(pilot, app, before)
            all_category_bytes = tracemalloc.get_traced_memory()[0]
            print(
                "ui.category_cache_memory: "
                f"{(all_category_bytes - first_bytes) / 1024:.1f} KiB retained Python heap "
                f"after warming {len(BrowserApp.CATEGORIES)} category pages"
            )
    finally:
        tracemalloc.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "database",
        nargs="?",
        type=Path,
        default=ApplicationPaths.default().database_path,
        help="existing dndref SQLite database (defaults to the application database)",
    )
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--size", type=_parse_size, default=(100, 30))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if not args.database.is_file():
        parser.error(f"database does not exist: {args.database}")

    profiler = PerformanceProfiler()
    database = Database(args.database, profiler=profiler)
    service = SearchService(database)
    _benchmark_service(database, service, args.iterations)
    asyncio.run(_benchmark_ui(database, profiler, args.size))
    asyncio.run(_benchmark_category_cache_memory(database, args.size))
    print(profiler.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
