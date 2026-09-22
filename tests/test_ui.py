from __future__ import annotations

import json
import shutil
import time
from dataclasses import replace
from pathlib import Path

import pytest

from dndref.config import Config, UIConfig
from dndref.importer import import_dataset, load_dataset
from dndref.search import (
    EntrySummary,
    SearchCategory,
    SearchPage,
    SearchQuery,
    get_entry_detail,
)
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp, render_detail
from dndref.ui.class_detail import progression_table
from dndref.ui.screens import AboutScreen, HelpScreen

FIXTURE = Path("tests/fixtures/dataset")


def populated_database(tmp_path: Path, dataset: Path = FIXTURE) -> Database:
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    import_dataset(database, load_dataset(dataset))
    return database


def dual_fixture_database(tmp_path: Path) -> Database:
    first = tmp_path / "srd-pack"
    second = tmp_path / "official-pack"
    shutil.copytree(FIXTURE, first)
    shutil.copytree(FIXTURE, second)
    first_manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    first_manifest.update(dataset_id="srd-5-2-1", title="SRD Test Pack")
    second_manifest.update(dataset_id="official-5etools-2024", title="Official Test Pack")
    (first / "manifest.json").write_text(json.dumps(first_manifest), encoding="utf-8")
    (second / "manifest.json").write_text(json.dumps(second_manifest), encoding="utf-8")
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    import_dataset(database, load_dataset(first))
    import_dataset(database, load_dataset(second))
    return database


def representative_dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "class-presentation-dataset"
    shutil.copytree(FIXTURE, dataset, dirs_exist_ok=True)
    classes_path = dataset / "classes.json"
    classes = json.loads(classes_path.read_text(encoding="utf-8"))
    character_class = classes[0]
    character_class["features"].extend(
        [
            {
                "feature_key": "class/wizard/scholar",
                "level": 2,
                "title": "Scholar",
                "description": (
                    "Your careful study gives you a reliable method for recalling arcane details."
                ),
                "display_order": 1,
            },
            {
                "feature_key": "class/wizard/ability-score-improvement",
                "level": 4,
                "title": "Ability Score Improvement",
                "description": "You improve one ability score or learn a permitted talent.",
                "display_order": 1,
            },
        ]
    )
    character_class["progression"]["columns"] = [
        {"key": "proficiency_bonus", "label": "PB", "display_order": 0},
        *character_class["progression"]["columns"],
        {"key": "rituals", "label": "Rituals Known", "display_order": 3},
    ]
    character_class["progression"]["levels"] = [
        {
            "level": 1,
            "values": {
                "proficiency_bonus": "+2",
                "spell_slots": "2 first-level",
                "arcane_recovery": "1/day",
                "rituals": "2",
            },
        },
        {
            "level": 2,
            "values": {
                "proficiency_bonus": "+2",
                "spell_slots": "3 first-level",
                "arcane_recovery": "1/day",
                "rituals": "3",
            },
        },
        {
            "level": 3,
            "values": {
                "proficiency_bonus": "+2",
                "spell_slots": "4 first-level, 2 second-level",
                "arcane_recovery": "1/day",
                "rituals": "4",
            },
        },
        {
            "level": 4,
            "values": {
                "proficiency_bonus": "+2",
                "spell_slots": "4 first-level, 3 second-level",
                "arcane_recovery": "1/day",
                "rituals": "5",
            },
        },
    ]
    character_class["subclasses"].append(
        {
            "subclass_key": "subclass/wizard/ritual-scholar",
            "name": "Ritual Scholar",
            "introduction": (
                "Ritual Scholars preserve patient, methodical techniques for magic that rewards "
                "preparation and careful notes."
            ),
            "source": "example-core",
            "features": [
                {
                    "feature_key": "subclass/wizard/ritual-scholar/prepared-diagrams",
                    "level": 2,
                    "title": "Prepared Diagrams",
                    "description": (
                        "Your diagrams help you recognize the shape of a ritual before you "
                        "begin it."
                    ),
                    "display_order": 1,
                },
                {
                    "feature_key": "subclass/wizard/ritual-scholar/deep-annotation",
                    "level": 4,
                    "title": "Deep Annotation",
                    "description": (
                        "Your annotations preserve a useful observation for later study."
                    ),
                    "display_order": 1,
                },
            ],
        }
    )
    classes_path.write_text(json.dumps(classes, indent=2) + "\n", encoding="utf-8")
    return dataset


@pytest.mark.asyncio
async def test_browser_starts_with_spells_and_detail(tmp_path: Path) -> None:
    database = populated_database(tmp_path)

    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.5)
        assert pilot.app.category is SearchCategory.SPELLS
        assert [result.name for result in pilot.app.state.results] == ["Comet Burst", "Spark"]
        assert pilot.app.state.selected_id == "example-5e:spell/comet-burst"
        assert pilot.app._detail_loaded_for == pilot.app.state.selected_id


@pytest.mark.asyncio
async def test_category_search_state_is_preserved(tmp_path: Path) -> None:
    database = populated_database(tmp_path)

    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("ctrl+f")
        await pilot.press(*list("spark"))
        await pilot.pause(0.3)
        await pilot.press("escape")
        await pilot.press("1")
        await pilot.pause(0.3)
        assert pilot.app.category is SearchCategory.ITEMS
        assert pilot.app.state.query == ""
        assert len(pilot.app.state.results) == 4

        await pilot.press("ctrl+f")
        await pilot.press(*list("rapier"))
        await pilot.pause(0.3)
        await pilot.press("escape")
        await pilot.press("2")
        await pilot.pause(0.3)
        assert pilot.app.category is SearchCategory.SPELLS
        assert pilot.app.state.query == "spark"
        assert [result.name for result in pilot.app.state.results] == ["Spark"]
        assert pilot.app.state.selected_id == "example-5e:spell/spark"


@pytest.mark.asyncio
async def test_search_modes_and_shortcuts_are_isolated_from_input(tmp_path: Path) -> None:
    database = populated_database(tmp_path)

    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("ctrl+f")
        await pilot.press(*list("qjk1234?"))
        await pilot.pause(0.2)
        assert pilot.app.category is SearchCategory.SPELLS
        assert pilot.app.state.query == "qjk1234?"

        await pilot.press("f2")
        await pilot.pause(0.2)
        assert pilot.app.mode.value == "all_text"
        assert pilot.app.query_one("#mode-label").renderable == "All text"
        await pilot.press("f1")
        assert isinstance(pilot.app.screen, HelpScreen)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_result_navigation_detail_and_help(tmp_path: Path) -> None:
    database = populated_database(tmp_path)

    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        first = pilot.app.state.selected_id
        await pilot.press("down")
        await pilot.pause(0.3)
        assert pilot.app.state.selected_id != first
        await pilot.press("j")
        await pilot.press("home")
        await pilot.press("end")
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(pilot.app.screen, HelpScreen)
        await pilot.press("escape")
        assert not isinstance(pilot.app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_about_data_lists_both_packs_and_restores_focus(tmp_path: Path) -> None:
    app = BrowserApp(
        dual_fixture_database(tmp_path),
        config=Config(ui=UIConfig(images="off")),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.4)
        pilot.app.action_show_about()
        await pilot.pause(0.2)
        assert isinstance(pilot.app.screen, AboutScreen)
        assert {dataset.dataset_id for dataset in pilot.app.screen.datasets} == {
            "official-5etools-2024",
            "srd-5-2-1",
        }
        await pilot.press("escape")
        await pilot.pause()
        assert pilot.app.screen.focused.id == "result-list"


@pytest.mark.asyncio
async def test_wide_layout_does_not_reserve_empty_artwork_panel(tmp_path: Path) -> None:
    async with BrowserApp(
        populated_database(tmp_path),
        config=Config(ui=UIConfig(images="off")),
    ).run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.4)
        assert pilot.app.query_one("#image-panel").display is False
        await pilot.resize_terminal(100, 30)
        await pilot.pause()
        assert pilot.app.query_one("#image-panel").display is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "mode"),
    [((140, 40), "split"), ((100, 30), "split"), ((80, 24), "split"), ((60, 20), "stacked")],
)
async def test_planned_layout_modes(size: tuple[int, int], mode: str, tmp_path: Path) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=size) as pilot:
        await pilot.pause(0.2)
        assert pilot.app.layout_mode == mode


@pytest.mark.asyncio
async def test_narrow_enter_and_escape_detail_flow(tmp_path: Path) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(60, 20)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("enter")
        await pilot.pause()
        assert pilot.app.narrow_detail_open is True
        assert pilot.app.query_one("#list-pane").display is False
        await pilot.press("escape")
        assert pilot.app.narrow_detail_open is False
        assert pilot.app.query_one("#list-pane").display is True


@pytest.mark.asyncio
async def test_empty_database_and_tiny_terminal_are_readable(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    database.initialize()
    async with BrowserApp(database).run_test(size=(49, 15)) as pilot:
        await pilot.pause(0.3)
        assert pilot.app.layout_mode == "compact"
        assert pilot.app.query_one("#too-small").display is True
        assert pilot.app.state.results == []
        await pilot.press("?")
        assert isinstance(pilot.app.screen, HelpScreen)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_stale_search_completion_cannot_replace_newer_results(tmp_path: Path) -> None:
    database = populated_database(tmp_path)
    app = BrowserApp(database)

    def delayed_search(query: SearchQuery) -> SearchPage:
        if query.text == "old":
            time.sleep(0.2)
        result = EntrySummary(
            stable_id=f"test:{query.text or 'empty'}",
            category=query.category,
            name=query.text or "Empty",
            subtitle="test",
            source_label="test",
            dataset_id="test",
            local_key=query.text or "empty",
            dataset_title="test",
        )
        return SearchPage((result,), 1, 0, 50, query.request_id)

    app.search_service.search = delayed_search  # type: ignore[method-assign]
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("ctrl+f")
        await pilot.press(*list("old"))
        await pilot.pause(0.13)
        pilot.app.query_one("#search-input").value = ""
        await pilot.pause()
        await pilot.press(*list("new"))
        await pilot.pause(0.45)
        assert pilot.app.state.query == "new"
        assert [result.name for result in pilot.app.state.results] == ["new"]


def test_detail_renderer_covers_all_categories(tmp_path: Path) -> None:
    database = populated_database(tmp_path)
    identities = {
        SearchCategory.ITEMS: "example-5e:item/rapier",
        SearchCategory.SPELLS: "example-5e:spell/comet-burst",
        SearchCategory.FEATS: "example-5e:feat/quick-study",
        SearchCategory.CLASSES: "example-5e:class/wizard",
    }
    expected = {
        SearchCategory.ITEMS: "Weapon details",
        SearchCategory.SPELLS: "At higher levels",
        SearchCategory.FEATS: "Annotation",
        SearchCategory.CLASSES: "Features",
    }
    for category, identity in identities.items():
        detail = get_entry_detail(database, identity)
        assert detail is not None
        rendered = render_detail(detail)
        assert detail.name in rendered
        assert expected[category] in rendered


def test_class_renderer_presents_metadata_progression_features_and_subclasses(
    tmp_path: Path,
) -> None:
    detail = get_entry_detail(
        populated_database(tmp_path, representative_dataset(tmp_path)),
        "example-5e:class/wizard",
    )
    assert detail is not None

    rendered = render_detail(detail)
    assert "### Class Metadata" in rendered
    assert "**Hit Die:** d6" in rendered
    assert "### Starting Equipment" in rendered
    assert "### Multiclassing" in rendered
    assert "## Class Progression" in rendered
    assert "| Lvl | Features | PB | Spell Slots | Arcane Recovery | Rituals Known |" in rendered
    assert "| 4 | Ability Score Improvement | +2 |" in rendered
    assert rendered.index("Spellcasting") < rendered.index("Scholar")
    assert "## Subclasses" in rendered
    assert "### Star Sage" in rendered
    assert "### Ritual Scholar" in rendered
    assert "#### Level 4" in rendered
    assert "Prepared Diagrams" in rendered
    assert "Source: Example Core Rules" in rendered


def test_class_renderer_omits_missing_optional_fields(tmp_path: Path) -> None:
    detail = get_entry_detail(
        populated_database(tmp_path, representative_dataset(tmp_path)),
        "example-5e:class/wizard",
    )
    assert detail is not None
    fields = dict(detail.fields)
    fields.update(
        tool_proficiencies=None,
        spellcasting_ability=None,
        starting_equipment=None,
    )

    rendered = render_detail(replace(detail, fields=fields))
    assert "**Tools:**" not in rendered
    assert "**Spellcasting Ability:**" not in rendered
    assert "### Starting Equipment" not in rendered


def test_progression_table_uses_dynamic_columns_and_does_not_duplicate_pb(
    tmp_path: Path,
) -> None:
    detail = get_entry_detail(
        populated_database(tmp_path, representative_dataset(tmp_path)),
        "example-5e:class/wizard",
    )
    assert detail is not None

    columns, rows = progression_table(detail)
    assert [label for _key, label in columns].count("PB") == 1
    assert [label for _key, label in columns] == [
        "Lvl",
        "Features",
        "PB",
        "Spell Slots",
        "Arcane Recovery",
        "Rituals Known",
    ]
    assert len(rows) == 4
    assert rows[0][2] == "+2"
    assert rows[2][4] == "1/day"
    assert rows[3][1] == "Ability Score Improvement"


@pytest.mark.asyncio
async def test_class_progression_table_focus_and_horizontal_navigation(tmp_path: Path) -> None:
    async with BrowserApp(
        populated_database(tmp_path, representative_dataset(tmp_path))
    ).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("4")
        await pilot.pause(0.4)
        table = pilot.app.query_one("#progression-table")
        assert table.row_count == 4
        assert table.max_scroll_x > 0

        table.focus()
        await pilot.press("l")
        await pilot.pause()
        right_offset = table.scroll_x
        assert right_offset > 0
        await pilot.press("h")
        await pilot.pause()
        assert table.scroll_x < right_offset


@pytest.mark.asyncio
async def test_progression_navigation_when_horizontal_scroll_is_unavailable(tmp_path: Path) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("4")
        await pilot.pause(0.4)
        table = pilot.app.query_one("#progression-table")
        table.styles.overflow_x = "hidden"
        table.focus()
        await pilot.pause()
        assert not table.allow_horizontal_scroll
        await pilot.press("h", "l", "left", "right")
        assert pilot.app.state.selected_id is not None


@pytest.mark.asyncio
async def test_class_detail_responsive_sizes_and_narrow_flow(tmp_path: Path) -> None:
    for size in ((140, 40), (100, 30), (80, 24)):
        async with BrowserApp(
            populated_database(tmp_path, representative_dataset(tmp_path))
        ).run_test(size=size) as pilot:
            await pilot.pause(0.4)
            await pilot.press("4")
            await pilot.pause(0.4)
            assert pilot.app.query_one("#class-detail").display is True
            assert pilot.app.query_one("#progression-table").row_count == 4

    async with BrowserApp(
        populated_database(tmp_path, representative_dataset(tmp_path))
    ).run_test(size=(60, 20)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("4")
        await pilot.pause(0.4)
        assert pilot.app.query_one("#detail-pane").display is False
        await pilot.press("enter")
        await pilot.pause()
        assert pilot.app.narrow_detail_open is True
        assert pilot.app.query_one("#class-detail").display is True
        await pilot.press("escape")
        assert pilot.app.narrow_detail_open is False
        assert pilot.app.query_one("#list-pane").display is True
