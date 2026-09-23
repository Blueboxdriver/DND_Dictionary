"""Milestone 13: subclass browsing, relationships, and navigation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dndref.importer import DatasetLoadError, import_dataset, load_dataset
from dndref.search import (
    GroupedEntrySummary,
    SearchCategory,
    SearchMode,
    SearchQuery,
    SearchService,
    SourceIdentity,
)
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp, render_detail

FIXTURE = Path("tests/fixtures/dataset")


def _pack(
    tmp_path: Path,
    dataset_id: str,
    edition: str,
    class_source: str,
    subclass_source: str,
    *,
    extra: bool = False,
) -> Path:
    path = tmp_path / dataset_id
    shutil.copytree(FIXTURE, path)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["dataset_id"] = dataset_id
    manifest["sources"] = [
        {"key": class_source, "title": f"Class Book {class_source}", "edition": edition},
        {"key": subclass_source, "title": f"Subclass Book {subclass_source}", "edition": edition},
    ]
    (path / "manifest.json").write_text(json.dumps(manifest))
    for filename in ("items.json", "spells.json", "feats.json"):
        text = (path / filename).read_text().replace("example-core", class_source)
        (path / filename).write_text(text)
    classes = json.loads((path / "classes.json").read_text())
    parent = classes[0]
    parent["name"] = "Fighter"
    parent["source"] = class_source
    parent["subclasses"][0]["name"] = "Battle Master"
    parent["subclasses"][0]["source"] = subclass_source
    parent["subclasses"][0]["introduction"] = f"Tactics from {subclass_source}."
    feature = parent["subclasses"][0]["features"][0]
    feature.update(level=7, title="Know Your Enemy", display_order=0)
    if extra:
        second = dict(feature)
        second.update(
            feature_key="feature/combat-superiority",
            level=3,
            title="Combat Superiority",
            display_order=1,
        )
        third = dict(feature)
        third.update(
            feature_key="feature/student-of-war", level=3, title="Student of War", display_order=0
        )
        fourth = dict(feature)
        fourth.update(
            feature_key="feature/improved-superiority",
            level=10,
            title="Improved Combat Superiority",
            display_order=0,
        )
        parent["subclasses"][0]["features"] = [feature, second, fourth, third]
        parent["subclasses"].append(
            {
                "subclass_key": "subclass/echo-knight",
                "name": "Echo Knight",
                "introduction": "A sparse subclass.",
                "source": subclass_source,
                "features": [],
            }
        )
    (path / "classes.json").write_text(json.dumps(classes))
    return path


def _database(tmp_path: Path) -> Database:
    paths = (
        _pack(tmp_path, "rules-2024-a", "2024", "A", "B", extra=True),
        _pack(tmp_path, "rules-2024-c", "2024", "C", "D"),
        _pack(tmp_path, "rules-2014", "2014", "E", "F"),
    )
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    for path in paths:
        import_dataset(database, load_dataset(path))
    return database


def test_cross_source_relationships_and_edition_boundary(tmp_path: Path) -> None:
    service = SearchService(_database(tmp_path))
    class_a = service.search(
        SearchQuery(
            "classes",
            "Fighter",
            editions=("2024",),
            sources=(SourceIdentity("rules-2024-a", "A"),),
        )
    ).results[0]
    class_2014 = service.search(SearchQuery("classes", "Fighter", editions=("2014",))).results[0]
    detail_2024 = service.get_entry_detail(class_a.identity)
    detail_2014 = service.get_entry_detail(class_2014.identity)
    assert detail_2024 is not None and detail_2014 is not None
    assert [
        (row["name"], row["source_label"]) for row in detail_2024.fields["compatible_subclasses"]
    ] == [
        ("Battle Master", "Subclass Book B"),
        ("Battle Master", "Subclass Book D"),
        ("Echo Knight", "Subclass Book B"),
    ]
    assert [
        (row["name"], row["source_label"]) for row in detail_2014.fields["compatible_subclasses"]
    ] == [("Battle Master", "Subclass Book F")]
    assert len(service.list_compatible_subclasses(class_a.identity)) == 3
    assert len(service.list_compatible_subclasses(class_2014.identity)) == 1


def test_subclass_search_filters_grouping_and_source_browser(tmp_path: Path) -> None:
    service = SearchService(_database(tmp_path))
    assert service.search(SearchQuery("subclasses", "Fighter")).total_count == 4
    assert service.search(SearchQuery("subclasses", "Battle Master")).total_count == 3
    assert (
        service.search(SearchQuery("subclasses", "Superiority", SearchMode.ALL_TEXT)).total_count
        == 1
    )
    assert service.search(SearchQuery("subclasses", editions=("2024",))).total_count == 3
    assert service.search(SearchQuery("subclasses", editions=("2014",))).total_count == 1
    assert (
        service.search(
            SearchQuery(
                "subclasses",
                sources=(SourceIdentity("rules-2024-a", "B"),),
            )
        ).total_count
        == 2
    )
    assert (
        service.search(
            SearchQuery(
                "subclasses",
                editions=("2024",),
                parent_class="Fighter",
            )
        ).total_count
        == 3
    )
    page = service.search_grouped(SearchQuery("subclasses", "Battle Master"))
    assert page.total_count == 2
    group = next(row for row in page.results if isinstance(row, GroupedEntrySummary))
    assert {variant.source_label for variant in group.variants} == {
        "Subclass Book B",
        "Subclass Book D",
    }
    assert group.primary.source_edition == "2024"
    assert (
        next(row for row in page.results if not isinstance(row, GroupedEntrySummary)).source_edition
        == "2014"
    )
    assert service.search_grouped(SearchQuery("subclasses", limit=1)).total_count >= 3
    assert {
        option.value for option in service.list_available_editions(SearchCategory.SUBCLASSES)
    } == {"2014", "2024"}
    assert {
        option.identity.source_key
        for option in service.list_available_sources(SearchCategory.SUBCLASSES, ("2024",))
    } == {"B", "D"}
    source_b = next(
        row for row in service.list_source_contents() if row.source.identity.source_key == "B"
    )
    assert source_b.counts[SearchCategory.SUBCLASSES] == 2


def test_subclass_features_and_parent_navigation_identity(tmp_path: Path) -> None:
    service = SearchService(_database(tmp_path))
    master = service.search(
        SearchQuery(
            "subclasses",
            "Battle Master",
            editions=("2024",),
            sources=(SourceIdentity("rules-2024-a", "B"),),
        )
    ).results[0]
    detail = service.get_entry_detail(master.identity)
    assert detail is not None
    assert detail.fields["parent_class"] == "Fighter"
    assert detail.fields["edition"] == "2024"
    assert [(feature["level"], feature["title"]) for feature in detail.fields["features"]] == [
        (3, "Student of War"),
        (3, "Combat Superiority"),
        (7, "Know Your Enemy"),
        (10, "Improved Combat Superiority"),
    ]
    rendered = render_detail(detail)
    assert (
        rendered.index("## Level 3") < rendered.index("## Level 7") < rendered.index("## Level 10")
    )
    parent = service.get_entry_detail(detail.fields["parent_class_identity"])
    assert parent is not None and parent.fields["edition"] == "2024"
    old = service.search(SearchQuery("subclasses", editions=("2014",))).results[0]
    old_detail = service.get_entry_detail(old.identity)
    assert old_detail is not None
    old_parent = service.get_entry_detail(old_detail.fields["parent_class_identity"])
    assert old_parent is not None and old_parent.fields["edition"] == "2014"
    empty = service.search(SearchQuery("subclasses", "Echo Knight")).results[0]
    empty_detail = service.get_entry_detail(empty.identity)
    assert empty_detail is not None and empty_detail.fields["features"] == ()
    assert "No subclass features are recorded" in render_detail(empty_detail)


def test_invalid_subclass_edition_is_rejected_and_reimport_is_idempotent(tmp_path: Path) -> None:
    path = _pack(tmp_path, "bad-pack", "2024", "A", "B")
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["sources"][1]["edition"] = "2014"
    (path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(DatasetLoadError, match="same edition"):
        load_dataset(path)
    manifest["sources"][1]["edition"] = "2024"
    (path / "manifest.json").write_text(json.dumps(manifest))
    database = Database(tmp_path / "idempotent.db")
    loaded = load_dataset(path)
    import_dataset(database, loaded)
    import_dataset(database, loaded)
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM subclasses").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM subclass_features").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_invalid_subclass_feature_level_is_rejected(tmp_path: Path) -> None:
    path = _pack(tmp_path, "bad-level", "2024", "A", "B")
    classes = json.loads((path / "classes.json").read_text())
    classes[0]["subclasses"][0]["features"][0]["level"] = "unknown"
    (path / "classes.json").write_text(json.dumps(classes))
    with pytest.raises(DatasetLoadError, match="level"):
        load_dataset(path)


def test_subclass_identity_includes_parent_record_key(tmp_path: Path) -> None:
    path = _pack(tmp_path, "shared-subclass-key", "2024", "A", "B")
    classes = json.loads((path / "classes.json").read_text())
    second = json.loads(json.dumps(classes[0]))
    second["local_key"] = "class/other-fighter"
    second["name"] = "Other Fighter"
    classes.append(second)
    (path / "classes.json").write_text(json.dumps(classes))
    database = Database(tmp_path / "identities.db")
    import_dataset(database, load_dataset(path))
    service = SearchService(database)
    results = service.search(SearchQuery("subclasses", "Battle Master")).results
    assert len(results) == 2
    assert results[0].identity != results[1].identity
    assert {
        service.get_entry_detail(result.identity).fields["parent_class"] for result in results
    } == {"Fighter", "Other Fighter"}


@pytest.mark.asyncio
async def test_standalone_subclass_category_and_parent_filter(tmp_path: Path) -> None:
    database = _database(tmp_path)
    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("5")
        await pilot.pause(0.3)
        app = pilot.app
        assert app.category is SearchCategory.SUBCLASSES
        assert app.state.total_count == 2
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("j", "enter")
        await pilot.pause(0.3)
        assert app.state.parent_class == "Fighter"
        assert app.state.total_count == 2
        assert "Class: Fighter" in str(app.query_one("#list-heading").render())


@pytest.mark.asyncio
async def test_class_subclass_parent_navigation_with_source_filter_at_all_sizes(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    for size in ((140, 40), (100, 30), (80, 24), (60, 20)):
        async with BrowserApp(database).run_test(size=size) as pilot:
            await pilot.pause(0.3)
            await pilot.press("4")
            await pilot.pause(0.3)
            app = pilot.app
            app.state.sources = (SourceIdentity("rules-2024-a", "A"),)
            app._apply_filter_change()
            await pilot.pause(0.3)
            if size[0] == 60:
                await pilot.press("enter")
                await pilot.pause()
            assert app.query_one("#class-detail").display
            subclass_list = app.query_one("#subclass-list")
            assert len(subclass_list.children) == 3
            subclass_list.index = 1
            subclass_list.focus()
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app._current_detail is not None
            assert app._current_detail.category is SearchCategory.SUBCLASSES
            assert app._current_detail.source_label == "Subclass Book D"
            await pilot.press("c")
            await pilot.pause(0.2)
            assert app._current_detail is not None
            assert app._current_detail.category is SearchCategory.CLASSES
            assert app._current_detail.fields["edition"] == "2024"


@pytest.mark.asyncio
async def test_subclass_parent_back_forward_restores_exact_nonfirst_detail(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    async with BrowserApp(database).run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("5")
        await pilot.pause(0.3)
        app = pilot.app
        assert app.state.total_count >= 2
        origin = app.state.results[1]
        origin_id = (
            origin.primary.identity
            if isinstance(origin, GroupedEntrySummary)
            else origin.identity
        )
        app.query_one("#result-list").index = 1
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.25)
        assert app._current_detail is not None
        assert app._current_detail.identity == origin_id
        assert app._current_detail.category is SearchCategory.SUBCLASSES
        assert app.state.list_index == 1
        await pilot.press("c")
        await pilot.pause(0.6)
        parent = app._current_detail
        assert parent is not None and parent.category is SearchCategory.CLASSES
        parent_id = parent.identity
        await pilot.press("alt+left")
        await pilot.pause(0.5)
        assert app._current_detail is not None
        assert app._current_detail.identity == origin_id
        assert app._current_detail.category is SearchCategory.SUBCLASSES
        assert app.state.selected_id == origin.identity
        assert app.state.list_index == 1
        assert app.query_one("#result-list").index == 1
        await pilot.press("alt+right")
        await pilot.pause(0.5)
        assert app._current_detail is not None
        assert app._current_detail.identity == parent_id
