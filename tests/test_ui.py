from __future__ import annotations

import json
import shutil
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from textual.widgets import Input, Label, ListView, Static, TextArea

from dndref.config import Config, ContentConfig, FilterPreset, UIConfig
from dndref.images import DecodedImage, ImageAdapter
from dndref.importer import import_dataset, load_dataset
from dndref.search import (
    EditionOption,
    EntrySummary,
    GroupedEntrySummary,
    SearchCategory,
    SearchPage,
    SearchQuery,
    SourceIdentity,
    get_entry_detail,
)
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp, render_detail
from dndref.ui.class_detail import progression_table
from dndref.ui.launchers import CommandPaletteScreen, LaunchRow, UniversalSearchScreen
from dndref.ui.screens import (
    AboutScreen,
    ChoiceScreen,
    CollectionChooserScreen,
    CollectionListScreen,
    FilterScreen,
    HelpScreen,
    PersonalEntriesScreen,
    SourceBrowserScreen,
    TextEntryScreen,
)

FIXTURE = Path("tests/fixtures/dataset")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24), (60, 20)])
async def test_search_launchers_fit_and_isolate_typed_shortcut_letters(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    database = populated_database(tmp_path)
    async with BrowserApp(database).run_test(size=size) as pilot:
        await pilot.pause(0.3)
        original_category = pilot.app.category
        original_detail = pilot.app._detail_loaded_for
        shortcut_letters = list("qjk1234?estcpbri")
        await pilot.press("ctrl+k")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, UniversalSearchScreen)
        search_input = pilot.app.screen.query_one("#universal-input", Input)
        assert search_input.region.width > 0 and search_input.region.height > 0
        await pilot.press(*shortcut_letters)
        await pilot.pause(0.3)
        assert pilot.app.category is original_category
        assert pilot.app._detail_loaded_for == original_detail
        await pilot.press("escape")
        assert pilot.app.screen is pilot.app.screen_stack[0]

        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, CommandPaletteScreen)
        palette_input = pilot.app.screen.query_one("#palette-input", Input)
        assert palette_input.region.width > 0 and palette_input.region.height > 0
        await pilot.press(*shortcut_letters)
        await pilot.pause(0.3)
        assert pilot.app.category is original_category
        assert pilot.app._detail_loaded_for == original_detail
        await pilot.press("escape")
        assert pilot.app.screen is pilot.app.screen_stack[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24), (60, 20)])
async def test_help_and_footer_fit_supported_terminal_sizes(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=size) as pilot:
        await pilot.pause(0.4)
        root = pilot.app.screen_stack[0]
        footer = root.query_one("#footer", Static)
        footer_lines = str(footer.renderable).splitlines()
        assert any(
            hint in str(footer.renderable) for hint in ("Ctrl+K", "Esc Back", "Type to search")
        )
        assert "Ctrl+P" in str(footer.renderable)
        assert all(len(line) <= size[0] - 4 for line in footer_lines)

        current_detail = pilot.app._current_detail.identity
        root.query_one("#search-input", Input).focus()
        await pilot.pause()
        search_footer = str(footer.renderable)
        assert "Type to search" in search_footer
        assert "Esc Close" in search_footer
        assert all(len(line) <= size[0] - 4 for line in search_footer.splitlines())
        await pilot.press("escape")

        if size[0] < 80:
            await pilot.press("enter")
            await pilot.pause()
        else:
            root.query_one("#detail-scroll").focus()
            await pilot.pause()
        detail_footer = str(footer.renderable)
        assert "Esc Back" in detail_footer
        assert "Ctrl+P" in detail_footer
        assert all(len(line) <= size[0] - 4 for line in detail_footer.splitlines())
        await pilot.press("escape")

        await pilot.press("?")
        await pilot.pause(0.1)
        screen = pilot.app.screen
        assert isinstance(screen, HelpScreen)
        assert screen.query_one("#help-copy").region.width > 0
        assert screen.query_one("#help-copy").region.height > 0
        title = screen.query_one("#help-title").region
        copy = screen.query_one("#help-copy").region
        help_footer = screen.query_one("#help-footer").region
        assert title.bottom <= copy.y
        assert copy.bottom <= help_footer.y
        assert help_footer.bottom <= size[1]
        assert "## Getting Started" in HelpScreen.KEYBOARD_HELP
        assert "## Advanced" in HelpScreen.KEYBOARD_HELP
        await pilot.press("escape")
        assert pilot.app.screen is root
        assert pilot.app._current_detail.identity == current_detail


@pytest.mark.asyncio
async def test_commands_show_common_actions_first_and_select_category_and_filters(
    tmp_path: Path,
) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        palette = pilot.app.screen
        assert isinstance(palette, CommandPaletteScreen)
        rows = palette.query_one("#palette-list", ListView).children
        commands = [row.option.payload for row in rows if isinstance(row, LaunchRow)]
        assert commands[:9] == [
            "universal-search",
            "favorites",
            "collections",
            "recent",
            "sources",
            "images",
            "change-category",
            "filters",
            "image-diagnostics",
        ]
        assert "favorite-entry" in commands

        palette.query_one("#palette-list", ListView).index = commands.index("change-category")
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, ChoiceScreen)
        categories = pilot.app.screen.query_one("#choice-list", ListView)
        categories.index = 5  # Monsters
        await pilot.press("enter")
        await pilot.pause(0.25)
        assert pilot.app.category is SearchCategory.MONSTERS

        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        palette = pilot.app.screen
        assert isinstance(palette, CommandPaletteScreen)
        rows = palette.query_one("#palette-list", ListView).children
        filter_index = next(
            index
            for index, row in enumerate(rows)
            if isinstance(row, LaunchRow) and row.option.payload == "filters"
        )
        palette.query_one("#palette-list", ListView).index = filter_index
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, ChoiceScreen)
        options = pilot.app.screen.query_one("#choice-list", ListView)
        assert len(options.children) == 6
        options.index = 1  # Source
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, FilterScreen)
        assert pilot.app.screen.kind == "source"
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_personal_actions_are_in_commands_and_text_fields_are_safe(
    tmp_path: Path,
) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        app = pilot.app
        detail = app._current_detail
        assert detail is not None
        app.personal_data.create_collection("Campaign")

        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        palette = app.screen
        assert isinstance(palette, CommandPaletteScreen)
        rows = palette.query_one("#palette-list", ListView).children
        membership_index = next(
            index
            for index, row in enumerate(rows)
            if isinstance(row, LaunchRow) and row.option.payload == "entry-collection"
        )
        palette.query_one("#palette-list", ListView).index = membership_index
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(app.screen, CollectionChooserScreen)
        await pilot.press("space", "enter")
        await pilot.pause(0.2)
        assert app.personal_data.collection_ids_for(detail.identity)

        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        palette = app.screen
        assert isinstance(palette, CommandPaletteScreen)
        rows = palette.query_one("#palette-list", ListView).children
        tags_index = next(
            index
            for index, row in enumerate(rows)
            if isinstance(row, LaunchRow) and row.option.payload == "edit-tags"
        )
        palette.query_one("#palette-list", ListView).index = tags_index
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(app.screen, TextEntryScreen)
        assert isinstance(app.screen.query_one("#note-input"), TextArea)
        await pilot.press(*list("qjkestcpbri"))
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        assert "qjkestcpbri" in app.personal_data.tags_for(detail.identity)
        assert app.screen is app.screen_stack[0]

        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        palette = app.screen
        assert isinstance(palette, CommandPaletteScreen)
        rows = palette.query_one("#palette-list", ListView).children
        note_index = next(
            index
            for index, row in enumerate(rows)
            if isinstance(row, LaunchRow) and row.option.payload == "edit-note"
        )
        palette.query_one("#palette-list", ListView).index = note_index
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(app.screen, TextEntryScreen)
        await pilot.press("q", "j", "k", "escape")
        assert app.screen is app.screen_stack[0]
        assert app.personal_data.note_for(detail.identity) is None


@pytest.mark.asyncio
async def test_collection_names_use_enter_and_do_not_run_application_shortcuts(
    tmp_path: Path,
) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        pilot.app.action_collections()
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, CollectionListScreen)
        await pilot.press("a")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, TextEntryScreen)
        name_input = pilot.app.screen.query_one("#note-input", Input)
        await pilot.press("q", "j", "k")
        assert name_input.value == "qjk"
        assert isinstance(pilot.app.screen, TextEntryScreen)
        await pilot.press("enter")
        await pilot.pause(0.15)
        assert any(
            collection.name == "qjk" for collection in pilot.app.personal_data.list_collections()
        )


@pytest.mark.asyncio
async def test_escape_from_detail_returns_to_results_and_top_level_escape_is_safe(
    tmp_path: Path,
) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        root = pilot.app.screen_stack[0]
        root.query_one("#detail-scroll").focus()
        await pilot.pause()
        await pilot.press("escape")
        assert root.focused.id == "result-list"
        await pilot.press("escape")
        assert pilot.app.screen is root
        assert pilot.app.is_running


@pytest.mark.asyncio
async def test_escape_from_detail_uses_back_history_when_available(tmp_path: Path) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        app = pilot.app
        first_id = app.state.selected_id
        await pilot.press("enter")
        await pilot.press("down", "enter")
        await pilot.pause(0.3)
        assert app.navigation_history.can_go_back

        app.screen_stack[0].query_one("#detail-scroll").focus()
        await pilot.press("escape")
        await pilot.pause(0.4)
        assert app.state.selected_id == first_id


@pytest.mark.asyncio
async def test_q_quits_and_escape_or_ctrl_q_in_search_do_not_quit(tmp_path: Path) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("escape")
        pilot.app.screen_stack[0].query_one("#search-input", Input).focus()
        await pilot.press("ctrl+q")
        assert pilot.app.is_running
        await pilot.press("escape")
        await pilot.press("q")
        await pilot.pause(0.05)
        assert not pilot.app.is_running


@pytest.mark.asyncio
async def test_universal_search_entry_back_restores_query_and_selection(tmp_path: Path) -> None:
    database = populated_database(tmp_path)
    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.35)
        origin = pilot.app._current_detail.identity
        await pilot.press("ctrl+k")
        await pilot.press(*list("spark"))
        await pilot.pause(0.45)
        screen = pilot.app.screen
        assert isinstance(screen, UniversalSearchScreen)
        assert screen.query_one("#universal-list", ListView).children
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert pilot.app._current_detail.identity == "example-5e:spell/spark"
        assert pilot.app.personal_data.recent_searches()[0] == "spark"
        await pilot.press("alt+left")
        await pilot.pause(0.5)
        assert isinstance(pilot.app.screen, UniversalSearchScreen)
        assert pilot.app.screen.query_one("#universal-input", Input).value == "spark"
        assert pilot.app.screen.query_one("#universal-list", ListView).index == 0
        await pilot.press("alt+left")
        await pilot.pause(0.4)
        assert pilot.app.screen is pilot.app.screen_stack[0]
        assert pilot.app._current_detail.identity == origin
        await pilot.press("alt+right")
        await pilot.pause(0.4)
        assert isinstance(pilot.app.screen, UniversalSearchScreen)
        assert pilot.app.screen.query_one("#universal-input", Input).value == "spark"
        await pilot.press("alt+right")
        await pilot.pause(0.5)
        assert pilot.app._current_detail.identity == "example-5e:spell/spark"


@pytest.mark.asyncio
async def test_universal_search_refresh_survives_empty_and_populated_transitions(
    tmp_path: Path,
) -> None:
    database = populated_database(tmp_path)
    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.35)
        await pilot.press("ctrl+k")
        await pilot.press(*list("spark"))
        await pilot.pause(0.35)

        screen = pilot.app.screen
        assert isinstance(screen, UniversalSearchScreen)
        rows = screen.query_one("#universal-list", ListView)
        search_input = screen.query_one("#universal-input", Input)
        assert rows.children

        # Replacing a highlighted, populated list with an unknown-prefix literal
        # query used to raise NoMatches while the ListView was clearing rows.
        search_input.value = "banana:"
        await pilot.press("down", "up")
        await pilot.pause(0.35)
        assert screen.is_mounted
        assert not rows.children
        assert screen.query_one("#universal-status", Static).renderable == "0 results"

        # Exercise the reverse transition and keyboard navigation after refresh.
        search_input.value = "spark"
        await pilot.pause(0.35)
        assert screen.is_mounted
        assert rows.children
        await pilot.press("down", "up")
        assert screen.is_mounted


@pytest.mark.asyncio
async def test_universal_search_prefix_queries_and_rapid_edits_do_not_crash(
    tmp_path: Path,
) -> None:
    database = populated_database(tmp_path)
    queries = (
        "banana:",
        "banana:fireball",
        "unknown:value",
        ":",
        "spell:",
        "edition:",
        "source:",
        "monster:dragon",
        "fireball",
    )
    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.35)
        for iteration in range(2):
            await pilot.press("ctrl+k")
            screen = pilot.app.screen
            assert isinstance(screen, UniversalSearchScreen)
            search_input = screen.query_one("#universal-input", Input)
            for query in queries:
                search_input.focus()
                search_input.value = query
                await pilot.pause(0.02)
                # Rapidly replace in-flight queries and exercise held-style deletion.
                await pilot.press("backspace", "backspace")
                search_input.value = query
                await pilot.pause(0.28)
                assert screen.is_mounted
                await pilot.press("up", "down")
                assert screen.is_mounted
            assert search_input.value == queries[-1]
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert pilot.app.screen is pilot.app.screen_stack[0]


@pytest.mark.asyncio
async def test_command_palette_fuzzy_command_and_direct_record_navigation(tmp_path: Path) -> None:
    database = populated_database(tmp_path)
    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.35)
        await pilot.press("ctrl+p")
        await pilot.press(*list("mons"))
        await pilot.pause(0.2)
        assert isinstance(pilot.app.screen, CommandPaletteScreen)
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert pilot.app.category is SearchCategory.MONSTERS
        await pilot.press("ctrl+p")
        await pilot.press(*list("spark"))
        await pilot.pause(0.45)
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert pilot.app.screen is pilot.app.screen_stack[0]
        assert pilot.app._current_detail.identity == "example-5e:spell/spark"
        await pilot.press("alt+left")
        await pilot.pause(0.4)
        assert pilot.app.category is SearchCategory.MONSTERS


def populated_database(tmp_path: Path, dataset: Path = FIXTURE) -> Database:
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    import_dataset(database, load_dataset(dataset))
    return database


def image_fixture_database(tmp_path: Path) -> Database:
    image = pytest.importorskip("PIL.Image")
    dataset = tmp_path / "image-pack"
    shutil.copytree(FIXTURE, dataset)
    (dataset / "images").mkdir()
    image.new("RGB", (200, 100), (20, 40, 60)).save(dataset / "images" / "item.png")
    items_path = dataset / "items.json"
    items = json.loads(items_path.read_text(encoding="utf-8"))
    items["items"][0]["image"] = "images/item.png"
    items_path.write_text(json.dumps(items), encoding="utf-8")
    return populated_database(tmp_path, dataset)


def fake_image_adapter() -> ImageAdapter:
    module = ModuleType("textual_image.widget")

    class FakeImage(Static):
        def __init__(self, _image: object) -> None:
            super().__init__("artwork")

    module.TGPImage = FakeImage  # type: ignore[attr-defined]
    return ImageAdapter(
        "kitty", environ={}, module_loader=lambda: module,
        probe=lambda: SimpleNamespace(
            tgp=True, sixel=False, cell_size=SimpleNamespace(width=10, height=20)
        ),
    )


def dual_fixture_database(tmp_path: Path) -> Database:
    first = tmp_path / "srd-pack"
    second = tmp_path / "official-pack"
    shutil.copytree(FIXTURE, first)
    shutil.copytree(FIXTURE, second)
    first_manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    first_manifest.update(dataset_id="srd-5-2-1", title="SRD Test Pack")
    second_manifest.update(dataset_id="official-5etools-2024", title="Official Test Pack")
    first_manifest["sources"][0].update(
        title="System Reference Document 5.2.1", edition="2024"
    )
    second_manifest["sources"][0].update(title="Player's Handbook (2024)", edition="2024")
    (first / "manifest.json").write_text(json.dumps(first_manifest), encoding="utf-8")
    (second / "manifest.json").write_text(json.dumps(second_manifest), encoding="utf-8")
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    import_dataset(database, load_dataset(first))
    import_dataset(database, load_dataset(second))
    return database


def differing_variant_database(tmp_path: Path) -> Database:
    database = dual_fixture_database(tmp_path)
    with database.connection() as connection:
        connection.execute(
            "UPDATE entries SET description = 'Different SRD mechanics' "
            "WHERE dataset_id = 'srd-5-2-1' AND local_key = 'spell/spark'"
        )
        connection.commit()
    return database


def edition_fixture_database(tmp_path: Path) -> Database:
    first = tmp_path / "edition-2014"
    second = tmp_path / "edition-2024"
    shutil.copytree(FIXTURE, first)
    shutil.copytree(FIXTURE, second)
    for path, dataset_id, edition in (
        (first, "pack-2014", "2014"),
        (second, "pack-2024", "2024"),
    ):
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        manifest.update(dataset_id=dataset_id)
        manifest["sources"][0].update(title=f"Test Book {edition}", edition=edition)
        (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
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
async def test_filter_shortcuts_are_isolated_from_search_input(tmp_path: Path) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("ctrl+f")
        await pilot.press("e", "s")
        assert pilot.app.state.query.endswith("es")
        assert not isinstance(pilot.app.screen, FilterScreen)
        await pilot.press("escape")
        await pilot.press("e")
        assert isinstance(pilot.app.screen, FilterScreen)
        await pilot.press("escape")
        await pilot.press("s")
        assert isinstance(pilot.app.screen, FilterScreen)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_edition_filter_is_discovered_applied_and_cancellable(tmp_path: Path) -> None:
    async with BrowserApp(edition_fixture_database(tmp_path)).run_test(
        size=(80, 24)
    ) as pilot:
        await pilot.pause(0.35)
        await pilot.press("e")
        screen = pilot.app.screen
        assert isinstance(screen, FilterScreen)
        assert [option.value for option in screen.options] == ["2014", "2024"]
        await pilot.press("down", "space", "escape")
        await pilot.pause(0.15)
        assert pilot.app.state.editions == ("2024",)

        await pilot.press("e", "enter")
        await pilot.pause(0.3)
        assert pilot.app.state.editions == ("2024",)
        assert pilot.app.state.query == ""
        assert pilot.app.query_one("#edition-status").renderable == "Edition: 2024 / 5.5e"
        assert pilot.app.state.total_count == 2
        assert {row.source_edition for row in pilot.app.state.results} == {"2024"}

        await pilot.press("e", "home", "space", "enter")
        await pilot.pause(0.2)
        assert pilot.app.state.editions == ()
        assert pilot.app.state.total_count == 2


@pytest.mark.asyncio
async def test_filter_selectors_support_multiple_and_all_clears(tmp_path: Path) -> None:
    async with BrowserApp(edition_fixture_database(tmp_path)).run_test(
        size=(80, 24)
    ) as pilot:
        await pilot.pause(0.3)
        await pilot.press("e", "home", "down", "space", "enter")
        await pilot.pause(0.2)
        assert set(pilot.app.state.editions) == {"2014", "2024"}
        assert pilot.app.state.total_count == 2
        await pilot.press("e", "home", "space", "enter")
        await pilot.pause(0.2)
        assert pilot.app.state.editions == ()

        await pilot.press("s", "down", "space", "down", "space", "enter")
        await pilot.pause(0.2)
        assert set(pilot.app.state.sources) == {
            SourceIdentity("pack-2014", "example-core"),
            SourceIdentity("pack-2024", "example-core"),
        }
        await pilot.press("s", "up", "up", "space", "enter")
        await pilot.pause(0.2)
        assert pilot.app.state.sources == ()
        await pilot.press("p", "enter")
        await pilot.pause(0.2)
        assert pilot.app.state.editions == ("2024",)
        assert pilot.app.state.sources == ()


@pytest.mark.asyncio
async def test_source_filter_uses_stable_ids_and_edition_reconciles_sources(
    tmp_path: Path,
) -> None:
    async with BrowserApp(edition_fixture_database(tmp_path)).run_test(
        size=(80, 24)
    ) as pilot:
        await pilot.pause(0.35)
        await pilot.press("e", "home", "space", "enter")
        await pilot.pause(0.15)
        await pilot.press("s")
        screen = pilot.app.screen
        assert isinstance(screen, FilterScreen)
        assert {option.identity for option in screen.options} == {
            SourceIdentity("pack-2014", "example-core"),
            SourceIdentity("pack-2024", "example-core"),
        }
        # Select the 2014 source by its discovered identity.
        index = next(
            index for index, option in enumerate(screen.options, start=1)
            if option.identity == SourceIdentity("pack-2014", "example-core")
        )
        await pilot.press(*(["down"] * index), "space", "enter")
        await pilot.pause(0.2)
        assert pilot.app.state.sources == (SourceIdentity("pack-2014", "example-core"),)
        assert pilot.app.state.total_count == 2

        await pilot.press("s", "down", "space", "escape")
        assert pilot.app.state.sources == (SourceIdentity("pack-2014", "example-core"),)

        # Changing edition drops the now unavailable source.
        await pilot.press("e", "down", "down", "space", "enter")
        await pilot.pause(0.2)
        assert pilot.app.state.editions == ("2024",)
        assert pilot.app.state.sources == ()
        assert pilot.app.state.total_count == 2
        await pilot.press("s")
        assert {option.identity for option in pilot.app.screen.options} == {
            SourceIdentity("pack-2024", "example-core")
        }
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_filter_state_is_per_category_and_visible_in_narrow_layout(tmp_path: Path) -> None:
    async with BrowserApp(edition_fixture_database(tmp_path)).run_test(
        size=(60, 20)
    ) as pilot:
        await pilot.pause(0.3)
        await pilot.press("e", "enter")
        await pilot.pause(0.2)
        assert pilot.app.query_one("#edition-status").renderable == "Ed: 2024 / 5.5e"
        await pilot.press("s", "down", "space", "enter")
        await pilot.pause(0.15)
        assert pilot.app.state.sources == (SourceIdentity("pack-2024", "example-core"),)
        await pilot.press("1")
        await pilot.pause(0.15)
        assert pilot.app.state.editions == ("2024",)
        await pilot.press("e", "home", "space", "enter")
        await pilot.pause(0.1)
        await pilot.press("s", "down", "space", "enter")
        await pilot.pause(0.15)
        assert pilot.app.state.sources == (SourceIdentity("pack-2014", "example-core"),)
        await pilot.press("2")
        await pilot.pause(0.15)
        assert pilot.app.state.editions == ("2024",)
        assert pilot.app.state.sources == (SourceIdentity("pack-2024", "example-core"),)
        await pilot.press("s")
        assert isinstance(pilot.app.screen, FilterScreen)
        assert pilot.app.screen.query_one("#filter-card").size.width <= 60
        assert pilot.app.screen.query_one("#filter-options") is not None
        await pilot.press("f2")
        assert pilot.app.mode.value == "names"
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_configured_editions_reconcile_per_category_and_session_changes_stick(
    tmp_path: Path,
) -> None:
    app = BrowserApp(
        edition_fixture_database(tmp_path),
        config=Config(content=ContentConfig(default_editions=("2014", "2024"))),
    )
    editions_by_category = {
        SearchCategory.SPELLS: (EditionOption("2024", "2024 / 5.5e"),),
        SearchCategory.ITEMS: (EditionOption("2014", "2014 / 5e"),),
        SearchCategory.FEATS: (),
        SearchCategory.CLASSES: (
            EditionOption("2014", "2014 / 5e"),
            EditionOption("2024", "2024 / 5.5e"),
        ),
    }
    app.search_service.list_available_editions = lambda category=None: editions_by_category[
        category
    ]  # type: ignore[method-assign]

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.25)
        assert pilot.app.state.editions == ("2024",)
        await pilot.press("1")
        await pilot.pause(0.1)
        assert pilot.app.state.editions == ("2014",)
        await pilot.press("3")
        await pilot.pause(0.1)
        assert pilot.app.state.editions == ()
        await pilot.press("4")
        await pilot.pause(0.1)
        assert pilot.app.state.editions == ("2014", "2024")


@pytest.mark.asyncio
async def test_unavailable_configured_editions_fall_back_to_all(tmp_path: Path) -> None:
    app = BrowserApp(
        edition_fixture_database(tmp_path),
        config=Config(content=ContentConfig(default_editions=("banana",))),
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.25)
        assert pilot.app.state.editions == ()
        assert pilot.app.state.sources == ()
        assert pilot.app.query_one("#edition-status").renderable == "Edition: All Editions"


@pytest.mark.asyncio
async def test_configured_default_is_initial_only_and_source_is_not_persisted(
    tmp_path: Path,
) -> None:
    app = BrowserApp(
        edition_fixture_database(tmp_path),
        config=Config(content=ContentConfig(default_editions=("2024",))),
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.25)
        assert pilot.app.state.editions == ("2024",)
        assert pilot.app.state.sources == ()
        await pilot.press("e", "home", "space", "enter")
        await pilot.pause(0.1)
        assert pilot.app.state.editions == ()
        await pilot.press("1", "2")
        await pilot.pause(0.1)
        assert pilot.app.state.editions == ()
        assert pilot.app.state.sources == ()
        await pilot.pause(0.25)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24), (60, 20)])
async def test_duplicate_result_rows_show_compact_source_provenance(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async with BrowserApp(dual_fixture_database(tmp_path)).run_test(size=size) as pilot:
        await pilot.pause(0.35)
        rows = pilot.app.query_one("#result-list", ListView).children
        matching = [row for row in rows if getattr(row, "summary", None)]
        labels = {
            str(row.query_one(".result-source", Label).renderable) for row in matching
        }
        assert any("Player's Handbook 2024" in label for label in labels)
        assert all("+1 source" in label for label in labels)
        assert all("Test Pack" not in label and "official-5etools" not in label for label in labels)


def test_duplicate_sources_remain_filterable_without_collapsing(tmp_path: Path) -> None:
    app = BrowserApp(dual_fixture_database(tmp_path))
    both = app.search_service.search(
        SearchQuery(SearchCategory.SPELLS, "Spark", editions=("2024",))
    )
    assert len(both.results) == 2
    assert {row.source_identity for row in both.results} == {
        SourceIdentity("srd-5-2-1", "example-core"),
        SourceIdentity("official-5etools-2024", "example-core"),
    }

    for source in both.results:
        one = app.search_service.search(
            SearchQuery(
                SearchCategory.SPELLS,
                "Spark",
                editions=("2024",),
                sources=(source.source_identity,),
            )
        )
        assert len(one.results) == 1
        assert one.results[0].source_identity == source.source_identity


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["e", "s"])
async def test_filter_marker_tracks_highlight_separately_from_check(
    tmp_path: Path, key: str
) -> None:
    async with BrowserApp(edition_fixture_database(tmp_path)).run_test(size=(60, 20)) as pilot:
        await pilot.pause(0.3)
        await pilot.press(key)
        screen = pilot.app.screen
        assert isinstance(screen, FilterScreen)
        await pilot.press("home")
        rows = screen.query_one("#filter-options", ListView).children
        assert str(rows[0].query_one(Label).renderable).startswith("> [")
        await pilot.press("down")
        assert str(rows[1].query_one(Label).renderable).startswith("> [ ]")
        assert not str(rows[0].query_one(Label).renderable).startswith(">")
        await pilot.press("space")
        assert str(rows[1].query_one(Label).renderable).startswith("> [x]")
        await pilot.press("k")
        assert str(rows[0].query_one(Label).renderable).startswith("> [ ]")
        await pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (60, 20)])
async def test_group_variant_switch_and_raw_toggle_keep_filters(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    config = Config(
        content=ContentConfig(
            preferred_sources=(SourceIdentity("official-5etools-2024", "example-core"),)
        )
    )
    async with BrowserApp(differing_variant_database(tmp_path), config=config).run_test(
        size=size
    ) as pilot:
        await pilot.pause(0.35)
        await pilot.press("ctrl+f", "s", "p", "a", "r", "k", "escape")
        await pilot.pause(0.25)
        group = pilot.app.state.results[0]
        assert isinstance(group, GroupedEntrySummary)
        assert group.primary.source_identity.dataset_id == "official-5etools-2024"
        assert len(group.alternates) == 1
        assert pilot.app._current_detail.description != "Different SRD mechanics"
        saved = (pilot.app.state.query, pilot.app.state.editions, pilot.app.state.sources)
        await pilot.press("v")
        screen = pilot.app.screen
        assert isinstance(screen, ChoiceScreen)
        rows = screen.query_one("#choice-list", ListView).children
        assert str(rows[0].query_one(Label).renderable).startswith(">")
        await pilot.press("down", "escape")
        assert pilot.app._current_detail.description != "Different SRD mechanics"
        await pilot.press("v")
        await pilot.press("down")
        choice_rows = pilot.app.screen.query_one("#choice-list", ListView).children
        assert str(choice_rows[1].query_one(Label).renderable).startswith(">")
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert pilot.app._current_detail.description == "Different SRD mechanics"
        assert (pilot.app.state.query, pilot.app.state.editions, pilot.app.state.sources) == saved
        await pilot.press("g")
        await pilot.pause(0.25)
        assert pilot.app.state.total_count == 2
        assert all(isinstance(row, EntrySummary) for row in pilot.app.state.results)
        assert pilot.app.state.selected_id == "srd-5-2-1:spell/spark"
        await pilot.press("g")
        await pilot.pause(0.25)
        assert pilot.app.state.total_count == 1


@pytest.mark.asyncio
async def test_presets_reconcile_sources_and_remain_editable(tmp_path: Path) -> None:
    phb = SourceIdentity("pack-2024", "example-core")
    missing = SourceIdentity("missing", "source")
    config = Config(
        content=ContentConfig(
            default_editions=(),
            filter_presets=(FilterPreset("2024 Core", ("2024",), (phb, missing)),),
        )
    )
    async with BrowserApp(edition_fixture_database(tmp_path), config=config).run_test(
        size=(60, 20)
    ) as pilot:
        await pilot.pause(0.3)
        await pilot.press("p")
        screen = pilot.app.screen
        assert isinstance(screen, ChoiceScreen)
        assert "2024 Core" in screen.labels
        await pilot.press("end")
        screen.query_one("#choice-list", ListView).index = len(screen.labels) - 1
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert pilot.app.state.editions == ("2024",)
        assert pilot.app.state.sources == (phb,)
        assert pilot.app.state.total_count == 2
        await pilot.press("s", "home", "space", "enter")
        await pilot.pause(0.2)
        assert pilot.app.state.sources == ()


@pytest.mark.asyncio
async def test_preset_preview_explains_selected_and_all_filters(tmp_path: Path) -> None:
    phb = SourceIdentity("pack-2024", "example-core")
    config = Config(content=ContentConfig(
        default_editions=(),
        filter_presets=(FilterPreset("2024 Core", ("2024",), (phb,)),),
    ))
    async with BrowserApp(edition_fixture_database(tmp_path), config=config).run_test(
        size=(80, 24)
    ) as pilot:
        await pilot.pause(0.3)
        await pilot.press("p")
        screen = pilot.app.screen
        assert isinstance(screen, ChoiceScreen)
        preview = screen.query_one("#choice-preview", Static)
        assert "[x] All Sources" in str(preview.renderable)
        await pilot.press("end")
        await pilot.pause()
        rendered = str(preview.renderable)
        assert "[x] 2024 / 5.5e" in rendered
        assert "[ ] 2014 / 5e" in rendered
        assert "[x]" in rendered and "[ ]" in rendered
        assert "Sources" in rendered
        screen.query_one("#choice-list", ListView).index = len(screen.labels) - 1
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert pilot.app.state.editions == ("2024",)
        assert pilot.app.state.sources == (phb,)


@pytest.mark.asyncio
async def test_artwork_toggle_resize_and_search_input_isolation(tmp_path: Path) -> None:
    app = BrowserApp(
        image_fixture_database(tmp_path), config=Config(ui=UIConfig(images="off"))
    )
    app.image_adapter = fake_image_adapter()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("1")
        await pilot.pause(0.6)
        panel = app.query_one("#image-panel")
        assert panel.display is True
        await pilot.press("i")
        assert panel.display is False
        await pilot.press("i")
        await pilot.pause(0.4)
        assert panel.display is True
        await pilot.resize_terminal(100, 30)
        await pilot.pause()
        assert panel.display is False
        await pilot.resize_terminal(120, 30)
        await pilot.pause(0.4)
        assert panel.display is True
        await pilot.press("p")
        assert panel.display is False
        await pilot.press("escape")
        await pilot.pause(0.4)
        assert panel.display is True
        await pilot.press("b")
        assert panel.display is False
        await pilot.press("escape")
        await pilot.pause(0.4)
        assert panel.display is True
        await pilot.press("?")
        assert panel.display is False
        await pilot.resize_terminal(100, 30)
        await pilot.resize_terminal(140, 40)
        assert panel.display is False
        await pilot.press("escape")
        await pilot.pause(0.4)
        assert panel.display is True
        await pilot.press("ctrl+f", "i")
        assert app.state.query == "i"
        assert app._artwork_visible is True
        await pilot.press("z", "z", "z", "z", "z", "z")
        await pilot.pause(0.3)
        assert panel.display is False


@pytest.mark.asyncio
async def test_stale_image_error_cannot_clear_current_artwork(tmp_path: Path) -> None:
    app = BrowserApp(
        image_fixture_database(tmp_path), config=Config(ui=UIConfig(images="off"))
    )
    app.image_adapter = fake_image_adapter()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("1")
        await pilot.pause(0.6)
        panel = app.query_one("#image-panel")
        assert panel.display is True
        request = app._image_request_id
        assert app._current_image_request(f"image:{request}:{app._selected_variant_id}")
        old_name = f"image:{request}:{app._selected_variant_id}"
        await pilot.press("down")
        await pilot.pause(0.2)
        assert panel.display is False
        assert not app._current_image_request(old_name)
        app._handle_image_result(DecodedImage(object(), 1, 1, "stale"), old_name)
        assert panel.display is False


@pytest.mark.asyncio
async def test_exit_cleans_current_artwork_widget(tmp_path: Path) -> None:
    app = BrowserApp(
        image_fixture_database(tmp_path), config=Config(ui=UIConfig(images="off"))
    )
    app.image_adapter = fake_image_adapter()
    cleaned: list[object] = []
    app.image_adapter.cleanup_widget = cleaned.append  # type: ignore[method-assign]
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("1")
        await pilot.pause(0.6)
        current = app._image_widget
        assert current is not None
    assert current in cleaned


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (60, 20)])
async def test_small_layouts_keep_artwork_collapsed_with_available_backend(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    app = BrowserApp(
        image_fixture_database(tmp_path), config=Config(ui=UIConfig(images="off"))
    )
    app.image_adapter = fake_image_adapter()
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.2)
        await pilot.press("1")
        await pilot.pause(0.4)
        assert app.state.results
        assert app.query_one("#image-panel").display is False
        assert app.query_one("#list-pane").display is True


@pytest.mark.asyncio
async def test_source_browser_counts_and_opens_normal_filtered_browser(tmp_path: Path) -> None:
    async with BrowserApp(edition_fixture_database(tmp_path)).run_test(size=(60, 20)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("b")
        screen = pilot.app.screen
        assert isinstance(screen, SourceBrowserScreen)
        assert len(screen.sources) == 2
        assert [source.source.edition for source in screen.sources] == ["2014", "2024"]
        assert screen.sources[0].counts[SearchCategory.SPELLS] == 2
        assert "2014 / 5e" in str(screen.query_one("#source-detail", Static).renderable)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert screen.stage == "categories"
        categories = tuple(screen.sources[0].counts)
        screen.query_one("#source-browser-list", ListView).index = categories.index(
            SearchCategory.SPELLS
        )
        await pilot.press("enter")
        await pilot.pause(0.25)
        assert pilot.app.category is SearchCategory.SPELLS
        assert pilot.app.state.sources == (SourceIdentity("pack-2014", "example-core"),)
        assert pilot.app.state.total_count == 2


@pytest.mark.asyncio
async def test_new_printable_shortcuts_are_inert_in_search(tmp_path: Path) -> None:
    async with BrowserApp(populated_database(tmp_path)).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.25)
        await pilot.press("ctrl+f", "p", "g", "v", "b")
        assert pilot.app.state.query == "pgvb"
        assert pilot.app.group_alternate_sources is True
        assert not isinstance(pilot.app.screen, (ChoiceScreen, SourceBrowserScreen))


@pytest.mark.asyncio
async def test_filter_change_uses_existing_stale_request_protection(tmp_path: Path) -> None:
    database = edition_fixture_database(tmp_path)
    app = BrowserApp(
        database,
        config=Config(content=ContentConfig(default_editions=(), group_alternate_sources=False)),
    )

    def delayed_search(query: SearchQuery) -> SearchPage:
        if not query.editions:
            time.sleep(0.6)
        result = EntrySummary(
            stable_id="test:filtered" if query.editions else "test:unfiltered",
            category=query.category,
            name="Filtered" if query.editions else "Unfiltered",
            subtitle="test",
            source_label="test",
            dataset_id="test",
            local_key="filtered" if query.editions else "unfiltered",
            dataset_title="test",
            source_identity=SourceIdentity("test", "test"),
            source_edition=query.editions[0] if query.editions else None,
        )
        return SearchPage((result,), 1, 0, 50, query.request_id)

    app.search_service.search = delayed_search  # type: ignore[method-assign]
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.13)
        await pilot.press("e")
        assert isinstance(app.screen, FilterScreen)
        await pilot.pause(0.2)
        await pilot.press("down", "down", "space", "enter")
        await pilot.pause(0.5)
        assert app.state.editions == ("2024",)
        assert [result.name for result in app.state.results] == ["Filtered"]


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
        assert HelpScreen.KEYBOARD_HELP.startswith("## Getting Started")
        assert "`e / s / p` —" in HelpScreen.KEYBOARD_HELP
        assert "## Advanced" in HelpScreen.KEYBOARD_HELP
        await pilot.press("escape")
        assert not isinstance(pilot.app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_favorite_detail_metadata_and_favorites_browser(tmp_path: Path) -> None:
    database = populated_database(tmp_path)
    async with BrowserApp(database, config=Config(ui=UIConfig(images="off"))).run_test(
        size=(80, 24)
    ) as pilot:
        await pilot.pause(0.5)
        detail = pilot.app._current_detail
        assert detail is not None
        pilot.app.personal_data.set_favorite(detail, True)
        pilot.app._refresh_personal_detail(detail)
        await pilot.pause(0.2)
        assert "★ Favorite" in pilot.app.query_one("#personal-status", Static).renderable
        pilot.app.action_favorites()
        await pilot.pause(0.2)
        assert isinstance(pilot.app.screen, PersonalEntriesScreen)
        assert len(pilot.app.screen.entries) == 1
        assert pilot.app.screen.entries[0].identity == detail.identity


@pytest.mark.asyncio
async def test_favorites_entry_back_restores_scoped_query(tmp_path: Path) -> None:
    database = populated_database(tmp_path)
    async with BrowserApp(database, config=Config(ui=UIConfig(images="off"))).run_test(
        size=(80, 24)
    ) as pilot:
        await pilot.pause(0.4)
        detail = pilot.app._current_detail
        assert detail is not None
        pilot.app.personal_data.set_favorite(detail, True)
        pilot.app.action_favorites()
        await pilot.pause(0.2)
        screen = pilot.app.screen
        assert isinstance(screen, PersonalEntriesScreen)
        search = screen.query_one("#personal-search")
        search.value = detail.name[:3]
        await pilot.pause(0.2)
        screen.query_one("#personal-list", ListView).focus()
        await pilot.press("enter")
        await pilot.pause(0.4)
        assert pilot.app._current_detail is not None
        assert pilot.app._current_detail.identity == detail.identity
        await pilot.press("alt+left")
        await pilot.pause(0.4)
        assert isinstance(pilot.app.screen, PersonalEntriesScreen)
        assert pilot.app.screen.query_one("#personal-search").value == detail.name[:3]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24), (60, 20)])
async def test_personal_overlays_mount_at_supported_sizes(
    size: tuple[int, int], tmp_path: Path
) -> None:
    database = populated_database(tmp_path)
    async with BrowserApp(database, config=Config(ui=UIConfig(images="off"))).run_test(
        size=size
    ) as pilot:
        await pilot.pause(0.4)
        app = pilot.app
        detail = app._current_detail
        assert detail is not None
        app.personal_data.set_favorite(detail, True)
        collection_id = app.personal_data.create_collection("Campaign Collection")
        app.personal_data.set_collection_membership(collection_id, detail, True)
        app.personal_data.add_tag(detail.identity, "campaign")
        app.personal_data.save_note(detail, "private\nnote")

        app.action_favorites()
        await pilot.pause(0.1)
        assert isinstance(app.screen, PersonalEntriesScreen)
        await pilot.press("escape")

        app.action_collections()
        await pilot.pause(0.1)
        assert isinstance(app.screen, CollectionListScreen)
        await pilot.press("escape")

        app.action_add_to_collection()
        await pilot.pause(0.1)
        assert isinstance(app.screen, CollectionChooserScreen)
        await pilot.press("escape")

        app.action_edit_tags()
        await pilot.pause(0.1)
        assert isinstance(app.screen, TextEntryScreen)
        await pilot.press("escape")

        app.action_edit_note()
        await pilot.pause(0.1)
        assert isinstance(app.screen, TextEntryScreen)
        await pilot.press("escape")


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
async def test_empty_discovery_filters_remain_usable(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    database.initialize()
    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("e")
        screen = pilot.app.screen
        assert isinstance(screen, FilterScreen)
        assert screen.options == ()
        assert any(
            "No editions available" in str(widget.renderable)
            for widget in screen.query(Static)
        )
        await pilot.press("escape", "s")
        assert isinstance(pilot.app.screen, FilterScreen)
        assert pilot.app.screen.options == ()
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_stale_search_completion_cannot_replace_newer_results(tmp_path: Path) -> None:
    database = populated_database(tmp_path)
    app = BrowserApp(database, config=Config(content=ContentConfig(group_alternate_sources=False)))

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
            source_identity=SourceIdentity("test", "test"),
            source_edition=None,
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
