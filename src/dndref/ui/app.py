"""Keyboard-driven production browser for imported reference data."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from textwrap import shorten
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Input, Label, ListItem, ListView, Markdown, Static
from textual.worker import Worker, WorkerState

from ..commands import Command, CommandRegistry
from ..config import ApplicationPaths, Config, FilterPreset
from ..crossrefs import CrossReferenceResolver, ReferenceTarget
from ..images import DecodedImage, ImageAdapter, ImageLoader
from ..models import display_edition
from ..navigation import NavigationHistory, NavigationState, RecentlyViewed, ViewedRecord
from ..personal import PersonalDataService
from ..search import (
    DetailSection,
    EntryDetail,
    EntrySummary,
    GroupedEntrySummary,
    SearchCategory,
    SearchMode,
    SearchPage,
    SearchQuery,
    SearchService,
    SourceIdentity,
    SourceOption,
    normalize_name,
)
from ..storage.database import Database
from .class_detail import ClassDetailView, render_class_detail, render_subclass_detail
from .launchers import CommandPaletteScreen, UniversalSearchScreen
from .monster_detail import render_monster_detail
from .screens import (
    AboutScreen,
    ChoiceScreen,
    CollectionChooserScreen,
    CollectionListScreen,
    FilterScreen,
    HelpScreen,
    PersonalEntriesScreen,
    RecentlyViewedScreen,
    SourceBrowserScreen,
    TextEntryScreen,
)
from .widgets import ImagePanel, ResultRow


@dataclass
class CategoryState:
    query: str = ""
    editions: tuple[str, ...] = ()
    sources: tuple[SourceIdentity, ...] = ()
    parent_class: str | None = None
    challenge_rating: str | None = None
    creature_type: str | None = None
    size: str | None = None
    filters_initialized: bool = False
    selected_id: str | None = None
    list_index: int = 0
    list_scroll: int = 0
    detail_scroll: int = 0
    results: list[EntrySummary | GroupedEntrySummary] = field(default_factory=list)
    total_count: int = 0


class BrowserApp(App[None]):
    """The production Textual browser.

    The app owns no SQLite connection. Every worker calls the Milestone 5
    service, which opens and closes its own connection.
    """

    TITLE = "D&D Reference"
    SUB_TITLE = "Offline reference browser"

    CSS = """
    Screen {
        background: #171719;
        color: #eee7d5;
    }

    #topbar {
        height: 3;
        background: #2a261f;
        padding: 1 1 0 2;
    }

    #brand {
        width: 20;
        color: #d4aa58;
        text-style: bold;
        content-align: left middle;
    }

    Screen.-stacked #brand {
        display: none;
    }

    #tabs {
        width: 1fr;
        height: 2;
    }

    .category-tab {
        width: 1fr;
        min-width: 9;
        height: 2;
        border: none;
        background: transparent;
        color: #aaa18e;
    }

    .category-tab:hover {
        background: #3a3020;
        color: #eee7d5;
    }

    .category-tab.active {
        background: #4a3a20;
        color: #d4aa58;
        text-style: bold;
    }

    #searchbar {
        height: 3;
        padding: 0 1;
        background: #211f1d;
        layout: horizontal;
        align: left middle;
    }

    #search-label {
        width: 9;
        color: #aaa18e;
        content-align: left middle;
    }

    #search-input {
        width: 1fr;
        height: 3;
        border: none;
        background: transparent;
        color: #eee7d5;
    }

    #mode-label, #result-count {
        width: auto;
        min-width: 13;
        padding: 0 1;
        color: #aaa18e;
        content-align: center middle;
    }

    #browser {
        height: 1fr;
        padding: 0 1;
    }

    #filterbar {
        height: 1;
        padding: 0 2;
        background: #211f1d;
    }

    .filter-status {
        width: 1fr;
        color: #aaa18e;
        content-align: left middle;
    }

    #list-pane {
        width: 32%;
        min-width: 24;
        max-width: 36;
        border: round #6c5530;
        background: #211f1d;
        padding: 0 1;
    }

    #list-heading {
        height: 2;
        color: #aaa18e;
        content-align: left middle;
    }

    #result-list {
        height: 1fr;
        border: none;
        background: transparent;
    }

    ResultRow {
        height: 4;
        padding: 0 1;
        background: transparent;
    }

    ResultRow.selected {
        background: #4a3a20;
        border-left: thick #d4aa58;
    }

    ResultRow:focus {
        outline: none;
    }

    .result-name {
        color: #eee7d5;
        text-style: bold;
    }

    .result-meta, .result-source, .detail-meta, .muted {
        color: #aaa18e;
    }

    .result-source {
        text-style: dim;
    }

    Screen.-stacked ResultRow {
        height: 3;
    }

    Screen.-stacked .result-source {
        display: none;
    }

    #result-message {
        height: auto;
        padding: 1;
        color: #aaa18e;
    }

    #detail-pane {
        width: 1fr;
        margin-left: 1;
        border: round #6c5530;
        background: #211f1d;
        padding: 0 2;
    }

    #detail-body {
        width: 1fr;
        height: 1fr;
    }

    #detail-scroll {
        width: 1fr;
        height: 1fr;
        scrollbar-size: 1 1;
    }

    #detail-copy {
        width: 1fr;
        padding: 1 0;
    }

    #related-list {
        display: none;
        height: auto;
        max-height: 10;
        border: round #6c5530;
        margin: 1 0;
    }

    #related-heading { display: none; height: 1; color: #d4aa58; text-style: bold; }

    #related-list > ListItem { height: 2; }
    #related-list > ListItem.--highlight { background: #4a3a20; color: #d4aa58; }

    #detail-copy MarkdownH1, #detail-copy MarkdownH2, #detail-copy MarkdownH3 {
        color: #d4aa58;
    }

    #image-panel {
        display: none;
        width: 28;
        height: 12;
        min-width: 28;
        max-width: 28;
        min-height: 12;
        max-height: 12;
        margin: 1 0 0 2;
        align: center middle;
        background: #171719;
        border: round #6c5530;
        overflow: hidden;
    }

    #image-panel > * {
        width: 100%;
        height: 100%;
    }

    #class-detail {
        display: none;
    }

    #too-small {
        display: none;
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        text-align: center;
        color: #d4aa58;
        padding: 2;
    }

    #footer {
        height: 1;
        padding: 0 2;
        color: #aaa18e;
        background: #2a261f;
    }

    Screen.-stacked #browser {
        layout: vertical;
    }

    Screen.-stacked #list-pane {
        width: 1fr;
        max-width: 1fr;
        height: 10;
    }

    Screen.-stacked #detail-pane {
        width: 1fr;
        height: 1fr;
        margin-left: 0;
        margin-top: 1;
    }

    Screen.-compact #browser {
        display: none;
    }

    Screen.-compact #too-small {
        display: block;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+f", "focus_search", "Search"),
        ("ctrl+k", "universal_search", "Universal Search"),
        ("ctrl+p", "command_palette", "Command Palette"),
        ("f2", "toggle_mode", "Mode"),
        ("f1", "show_help", "Help"),
        ("f3", "show_about", "About/Data"),
    ]

    CATEGORIES = (
        SearchCategory.ITEMS,
        SearchCategory.SPELLS,
        SearchCategory.FEATS,
        SearchCategory.CLASSES,
        SearchCategory.SUBCLASSES,
        SearchCategory.MONSTERS,
    )
    CATEGORY_LABELS = {
        SearchCategory.ITEMS: "Items",
        SearchCategory.SPELLS: "Spells",
        SearchCategory.FEATS: "Feats",
        SearchCategory.CLASSES: "Classes",
        SearchCategory.SUBCLASSES: "Subclasses",
        SearchCategory.MONSTERS: "Monsters",
    }

    def __init__(
        self,
        database: Database | None = None,
        *,
        paths: ApplicationPaths | None = None,
        config: Config | None = None,
    ) -> None:
        self.paths = paths or ApplicationPaths.default()
        self.config = config or Config()
        self.database = database or Database(self.paths.database_path)
        self.search_service = SearchService(self.database)
        self.personal_data = PersonalDataService(self.database)
        self.commands = CommandRegistry()
        self.personal_view: str | None = None
        self.personal_collection_id: int | None = None
        self.personal_query = ""
        self.personal_category_filter: str | None = None
        self.personal_edition_filter: str | None = None
        self.personal_tag_filter: str | None = None
        self.category = SearchCategory.SPELLS
        self.mode = SearchMode.NAMES
        self.group_alternate_sources = self.config.content.group_alternate_sources
        self.category_states = {category: CategoryState() for category in self.CATEGORIES}
        self.layout_mode = "unknown"
        self.narrow_detail_open = False
        self._search_timer: Timer | None = None
        self._request_id = 0
        self._detail_request_id = 0
        self._detail_requested_for: str | None = None
        self._detail_loaded_for: str | None = None
        self._selected_variant_id: str | None = None
        self._current_detail: EntryDetail | None = None
        self.cross_references = CrossReferenceResolver(self.database)
        self.navigation_history = NavigationHistory(100)
        self.recently_viewed = RecentlyViewed(30)
        self._related_targets: tuple[ReferenceTarget, ...] = ()
        self._history_restore_detail_id: str | None = None
        self._restoring_history = False
        self._category_transition = False
        self._updating_input = False
        self._page_loading = False
        self.image_adapter = ImageAdapter(self.config.ui.images)
        self.image_loader = ImageLoader()
        self._image_timer: Timer | None = None
        self._image_request_id = 0
        self._image_widget: Any | None = None
        self._image_worker: Worker[Any] | None = None
        self._artwork_visible = True
        self._image_status = self.image_adapter.capabilities.reason
        self._edition_options = ()
        self._source_options = ()
        self._presets: tuple[FilterPreset, ...] = ()
        self._register_commands()
        super().__init__()

    def _register_commands(self) -> None:
        for category in self.CATEGORIES:
            aliases = {
                SearchCategory.MONSTERS: ("monsters", "creatures", "mons"),
                SearchCategory.SUBCLASSES: ("subclasses",),
            }.get(category, ())
            self.commands.register(
                Command(
                    f"open-{category.value}", f"Open {self.CATEGORY_LABELS[category]}", aliases
                ),
                lambda category=category: self._switch_category(category),
            )
        actions = (
            ("favorites", "Open Favorites", ("fav", "favourites"), self.action_favorites),
            ("collections", "Open Collections", ("collection",), self.action_collections),
            ("recent", "Open Recently Viewed", ("recent",), self.action_recently_viewed),
            ("sources", "Open Sources", ("source browser",), self.action_browse_sources),
            ("universal-search", "Universal Search", ("search",), self.action_universal_search),
            ("back", "Back", ("previous",), self.action_back),
            ("forward", "Forward", ("next",), self.action_forward),
            ("images", "Toggle Images", ("artwork",), self.action_toggle_artwork),
            ("help", "Help", ("?",), self.action_show_help),
            (
                "image-diagnostics",
                "Image Diagnostics / Instructions",
                ("diagnostics",),
                self.action_show_about,
            ),
            ("quit", "Quit", ("exit",), self.exit),
        )
        for command_id, name, aliases, handler in actions:
            self.commands.register(Command(command_id, name, aliases), handler)

    @property
    def state(self) -> CategoryState:
        return self.category_states[self.category]

    def _query_widget(self, selector: str, expect_type: type[Any] | None = None) -> Any:
        """Query the browser screen even while a modal screen is active."""
        root = self.screen_stack[0]
        return root.query_one(selector, expect_type)

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Label("D&D Reference", id="brand")
            with Horizontal(id="tabs"):
                for category in self.CATEGORIES:
                    yield Button(
                        self.CATEGORY_LABELS[category],
                        id=f"tab-{category.value}",
                        classes="category-tab",
                    )
        with Horizontal(id="searchbar"):
            yield Label("Search:", id="search-label")
            yield Input(placeholder="type to search", id="search-input")
            yield Label("Names", id="mode-label")
            yield Label("0 results", id="result-count")
        with Horizontal(id="filterbar"):
            yield Label("Edition: All Editions", id="edition-status", classes="filter-status")
            yield Label("Source: All Sources", id="source-status", classes="filter-status")
        with Horizontal(id="browser"):
            with Vertical(id="list-pane"):
                yield Label("Results", id="list-heading")
                yield Label("", id="result-message")
                yield ListView(id="result-list")
            with Vertical(id="detail-pane"):
                with Horizontal(id="detail-body"):
                    with VerticalScroll(id="detail-scroll"):
                        yield Static("", id="detail-variant-hint")
                        yield Markdown("", id="detail-copy")
                        yield ClassDetailView(id="class-detail")
                        yield Static("", id="personal-status")
                        yield Static("", id="related-heading")
                        yield ListView(id="related-list")
                    yield ImagePanel(id="image-panel")
        yield Static(
            "Terminal too small. Resize to at least 50×16 · ? Help · q Quit",
            id="too-small",
        )
        yield Static(
            "/ Search   Alt+← Back   Alt+→ Forward   r Recent   ? Help   q Quit",
            id="footer",
        )

    def on_mount(self) -> None:
        self._update_tab_styles()
        self._update_group_status()
        self._update_layout(self.size.width, self.size.height)
        self._query_widget("#result-list", ListView).focus()
        self._update_input_from_state()
        self._refresh_filter_options()
        self._show_loading("Loading…")
        self._queue_search()

    def on_resize(self, event: events.Resize) -> None:
        # A virtual Kitty placement and a Sixel bitmap can outlive the cells
        # that owned them. Invalidate in-flight loads before moving the pane.
        self._clear_image()
        self._update_layout(event.size.width, event.size.height)

    def on_unmount(self) -> None:
        self._clear_image()
        self.image_loader.close()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("tab-"):
            self._switch_category(SearchCategory(button_id.removeprefix("tab-")))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input" or self._updating_input:
            return
        self.state.query = event.value
        self.state.selected_id = None
        self.state.list_index = 0
        self._invalidate_search()
        self._queue_search()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self._query_widget("#result-list", ListView).focus()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if self._restoring_history or event.list_view.id != "result-list" or event.item is None:
            return
        if not isinstance(event.item, ResultRow):
            return
        result_list = self._query_widget("#result-list", ListView)
        self.state.list_index = max(0, result_list.index)
        self._select_summary(event.item.summary)
        if self.state.list_index >= len(self.state.results) - 5:
            self._load_next_page_if_needed()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "subclass-list":
            self._open_highlighted_subclass()
            return
        if event.list_view.id == "related-list":
            self._open_highlighted_reference()
            return
        if event.list_view.id != "result-list" or not isinstance(event.item, ResultRow):
            return
        self._select_summary(event.item.summary)
        if self.layout_mode == "stacked":
            self.narrow_detail_open = True
            self._update_layout(self.size.width, self.size.height)
            self._query_widget("#detail-scroll", VerticalScroll).focus()

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        worker = event.worker
        if event.state == WorkerState.SUCCESS:
            if worker.name.startswith("search:"):
                await self._handle_search_result(worker.result)
            elif worker.name.startswith("page:"):
                await self._handle_page_result(worker.result)
            elif worker.name.startswith("detail:"):
                await self._handle_detail_result(worker.result, worker.name)
            elif worker.name.startswith("image:"):
                self._handle_image_result(worker.result, worker.name)
            elif worker.name == "about" and isinstance(self.screen, AboutScreen):
                self.screen.set_datasets(worker.result)
        elif event.state == WorkerState.ERROR:
            if worker.name.startswith("search:"):
                request_id = int(worker.name.split(":", 1)[1])
                if request_id == self._request_id:
                    self._show_error("Search unavailable. Initialize or import a dataset first.")
            elif worker.name.startswith("detail:"):
                parts = worker.name.split(":", 2)
                if (
                    len(parts) == 3
                    and parts[1].isdigit()
                    and int(parts[1]) == self._detail_request_id
                    and parts[2] == self._selected_variant_id
                ):
                    self._show_detail_error("The selected entry could not be loaded.")
            elif worker.name.startswith("image:"):
                if self._current_image_request(worker.name):
                    self._image_status = "Image could not be loaded."
                    self._clear_image()

    def on_key(self, event: events.Key) -> None:
        focus = self.screen.focused
        if not isinstance(self.screen, ModalScreen) and event.key in {"ctrl+k", "ctrl+p"}:
            event.stop()
            if event.key == "ctrl+k":
                self.action_universal_search()
            else:
                self.action_command_palette()
            return
        if isinstance(focus, Input):
            if event.key == "escape":
                event.stop()
                self._query_widget("#result-list", ListView).focus()
            return

        # Modal screens own all keys while they are active.
        if isinstance(self.screen, ModalScreen):
            return

        if event.key == "alt+left":
            event.stop()
            self.action_back()
            return
        if event.key == "alt+right":
            event.stop()
            self.action_forward()
            return
        if event.key == "ctrl+k":
            event.stop()
            self.action_universal_search()
            return
        if event.key == "ctrl+p":
            event.stop()
            self.action_command_palette()
            return

        if event.character == "q":
            event.stop()
            self.exit()
            return
        if event.character == "?":
            event.stop()
            self.action_show_help()
            return
        if event.key == "f3":
            event.stop()
            self.action_show_about()
            return
        if event.character in {"1", "2", "3", "4", "5", "6"}:
            event.stop()
            self._switch_category(self.CATEGORIES[int(event.character) - 1])
            return
        if event.character == "/":
            event.stop()
            self._query_widget("#search-input", Input).focus()
            return
        if event.character == "e":
            event.stop()
            self.action_editions()
            return
        if event.character == "s":
            event.stop()
            self.action_sources()
            return
        if event.character == "f" and self.category is SearchCategory.SUBCLASSES:
            event.stop()
            self.action_parent_class()
            return
        if event.character in {"c", "t", "z"} and self.category is SearchCategory.MONSTERS:
            event.stop()
            self.action_monster_filter({"c": "cr", "t": "type", "z": "size"}[event.character])
            return
        if event.character == "g":
            event.stop()
            self.action_toggle_grouping()
            return
        if event.character == "v":
            event.stop()
            self.action_variants()
            return
        if event.character == "p":
            event.stop()
            self.action_presets()
            return
        if event.character == "b":
            event.stop()
            self.action_browse_sources()
            return
        if event.character == "r":
            event.stop()
            self.action_recently_viewed()
            return
        if event.character == "F":
            event.stop()
            self.action_favorites()
            return
        if event.character == "C":
            event.stop()
            self.action_collections()
            return
        if event.character == "*" and self._current_detail is not None:
            event.stop()
            self.action_toggle_favorite()
            return
        if event.character == "n" and self._current_detail is not None:
            event.stop()
            self.action_edit_note()
            return
        if event.character == "T" and self._current_detail is not None:
            event.stop()
            self.action_edit_tags()
            return
        if event.character == "m" and self._current_detail is not None:
            event.stop()
            self.action_add_to_collection()
            return
        if event.character == "i":
            event.stop()
            self.action_toggle_artwork()
            return
        if event.character == "c" and self._current_detail is not None:
            if self._current_detail.category is SearchCategory.SUBCLASSES:
                parent_id = self._current_detail.fields.get("parent_class_identity")
                if isinstance(parent_id, str):
                    event.stop()
                    self._navigate_to_identity(parent_id)
                    return
        if event.key == "enter" and isinstance(focus, ListView):
            event.stop()
            if focus.id == "subclass-list":
                self._open_highlighted_subclass()
                return
            if focus.id == "related-list":
                self._open_highlighted_reference()
                return
            row = focus.highlighted_child
            if isinstance(row, ResultRow):
                self._select_summary(row.summary)
                self._commit_current_location()
                if self.layout_mode == "stacked":
                    self.narrow_detail_open = True
                    self._update_layout(self.size.width, self.size.height)
                    self._query_widget("#detail-scroll", VerticalScroll).focus()
            return
        if event.key == "escape":
            event.stop()
            if self.layout_mode == "stacked" and self.narrow_detail_open:
                self.narrow_detail_open = False
                self._update_layout(self.size.width, self.size.height)
                self._query_widget("#result-list", ListView).focus()
            elif isinstance(focus, VerticalScroll):
                self._query_widget("#result-list", ListView).focus()
            return

        if isinstance(focus, ListView):
            if event.character == "j":
                event.stop()
                focus.action_cursor_down()
            elif event.character == "k":
                event.stop()
                focus.action_cursor_up()
            elif event.key == "home":
                event.stop()
                focus.index = 0 if focus.children else None
                focus.scroll_home()
            elif event.key == "end":
                event.stop()
                focus.index = len(focus.children) - 1 if focus.children else None
                focus.scroll_end()
            elif event.key in {"pageup", "pagedown"}:
                event.stop()
                if event.key == "pageup":
                    focus.action_page_up()
                else:
                    focus.action_page_down()
        elif isinstance(focus, VerticalScroll):
            if event.key == "j":
                event.stop()
                focus.scroll_down(3)
            elif event.key == "k":
                event.stop()
                focus.scroll_up(3)
            elif event.key == "home":
                event.stop()
                focus.scroll_home()
            elif event.key == "end":
                event.stop()
                focus.scroll_end()
            elif event.key in {"pageup", "pagedown"}:
                event.stop()
                if event.key == "pageup":
                    focus.scroll_page_up()
                else:
                    focus.scroll_page_down()
        elif isinstance(focus, DataTable):
            if event.character == "h" or event.key == "left":
                event.stop()
                if focus.allow_horizontal_scroll:
                    focus.action_scroll_left()
            elif event.character == "l" or event.key == "right":
                event.stop()
                if focus.allow_horizontal_scroll:
                    focus.action_scroll_right()
            elif event.character == "j" or event.key == "down":
                event.stop()
                focus.action_cursor_down()
            elif event.character == "k" or event.key == "up":
                event.stop()
                focus.action_cursor_up()
            elif event.key in {"pageup", "pagedown"}:
                event.stop()
                if event.key == "pageup":
                    focus.action_page_up()
                else:
                    focus.action_page_down()
            elif event.key == "home":
                event.stop()
                focus.action_scroll_home()
            elif event.key == "end":
                event.stop()
                focus.action_scroll_end()

    def action_toggle_mode(self) -> None:
        self.mode = SearchMode.ALL_TEXT if self.mode is SearchMode.NAMES else SearchMode.NAMES
        self._query_widget("#mode-label", Label).update(self._mode_label())
        self._invalidate_search()
        self._queue_search()

    def action_focus_search(self) -> None:
        self._query_widget("#search-input", Input).focus()

    def action_universal_search(self) -> None:
        origin = self._navigation_state()
        self._clear_image()
        screen = UniversalSearchScreen(
            self.search_service,
            self.personal_data.recent_searches(),
            self.recently_viewed.records,
        )
        self.push_screen(screen, lambda result: self._universal_search_result(result, origin))

    def _universal_search_result(
        self, result: tuple[object, ...] | None, origin: NavigationState
    ) -> None:
        if result is None:
            if origin.universal_search:
                self.action_back()
            else:
                self._restore_current_image()
            return
        if result[0] == "history":
            self.action_back() if result[1] == "alt+left" else self.action_forward()
            return
        if result[0] == "clear":
            self.personal_data.clear_recent_searches()
            query = str(result[1])
            screen = UniversalSearchScreen(
                self.search_service,
                (),
                self.recently_viewed.records,
                query=query,
                selected_index=int(result[2]),
                scroll=int(result[3]),
            )
            self.push_screen(screen, lambda value: self._universal_search_result(value, origin))
            return
        if result[0] == "search":
            query = str(result[1])
            self.personal_data.record_search(query)
            screen = UniversalSearchScreen(
                self.search_service,
                self.personal_data.recent_searches(),
                self.recently_viewed.records,
                query=query,
            )
            self.push_screen(screen, lambda value: self._universal_search_result(value, origin))
            return
        if result[0] != "entry":
            self._restore_current_image()
            return
        _, identity, query, selected_index, scroll = result
        query_text = str(query)
        self.personal_data.record_search(query_text)
        universal = replace(
            origin,
            universal_search=True,
            universal_query=query_text,
            universal_selected_index=int(selected_index),
            universal_scroll=int(scroll),
        )
        self.navigation_history.navigate_to(universal)
        self._navigate_to_identity(str(identity))

    def action_command_palette(self) -> None:
        self._clear_image()
        self.push_screen(
            CommandPaletteScreen(self.search_service, self.commands.commands),
            self._command_palette_result,
        )

    def _command_palette_result(self, result: tuple[str, object] | None) -> None:
        if result is None:
            self._restore_current_image()
            return
        kind, value = result
        if kind == "command":
            self.commands.execute(str(value))
        elif kind == "entry":
            self._navigate_to_identity(str(value))

    def action_editions(self) -> None:
        if self.layout_mode == "compact":
            return
        if not self._refresh_filter_options():
            return
        self._clear_image()
        screen = FilterScreen("edition", self._edition_options, self.state.editions)
        self.push_screen(screen, self._edition_filter_selected)

    def action_sources(self) -> None:
        if self.layout_mode == "compact":
            return
        if not self._refresh_filter_options():
            return
        self._clear_image()
        screen = FilterScreen("source", self._source_options, self.state.sources)
        self.push_screen(screen, self._source_filter_selected)

    def action_parent_class(self) -> None:
        if self.layout_mode == "compact" or self.category is not SearchCategory.SUBCLASSES:
            return
        try:
            parents = self.search_service.list_subclass_parents(self.state.editions)
        except Exception:
            self._show_error("Class filter is unavailable.")
            return
        self._parent_options = ("All Classes", *parents)
        selected = next(
            (
                index
                for index, name in enumerate(self._parent_options)
                if name == self.state.parent_class
            ),
            0,
        )
        self._clear_image()
        self.push_screen(
            ChoiceScreen("Parent class", self._parent_options, selected),
            self._parent_class_selected,
        )

    def _parent_class_selected(self, index: int | None) -> None:
        if index is None:
            self._restore_current_image()
            return
        self.state.parent_class = self._parent_options[index] if index else None
        self._update_filter_status()
        self._apply_filter_change()

    def action_monster_filter(self, kind: str) -> None:
        if self.layout_mode == "compact":
            return
        try:
            values = self.search_service.list_monster_facets()[kind]
        except Exception:
            self._show_error("Monster filter options are unavailable.")
            return
        self._monster_filter_kind = kind
        self._monster_filter_values = ("All", *values)
        current = {
            "cr": self.state.challenge_rating,
            "type": self.state.creature_type,
            "size": self.state.size,
        }[kind]
        selected = self._monster_filter_values.index(current) if current in values else 0
        self._clear_image()
        self.push_screen(
            ChoiceScreen(
                {"cr": "Challenge rating", "type": "Creature type", "size": "Size"}[kind],
                self._monster_filter_values,
                selected,
            ),
            self._monster_filter_selected,
        )

    def _monster_filter_selected(self, index: int | None) -> None:
        if index is None:
            self._restore_current_image()
            return
        value = self._monster_filter_values[index] if index else None
        setattr(
            self.state,
            {"cr": "challenge_rating", "type": "creature_type", "size": "size"}[
                self._monster_filter_kind
            ],
            value,
        )
        self._update_filter_status()
        self._apply_filter_change()

    def action_toggle_grouping(self) -> None:
        if self.layout_mode == "compact":
            return
        previous = next(
            (result for result in self.state.results if result.identity == self.state.selected_id),
            None,
        )
        self.group_alternate_sources = not self.group_alternate_sources
        if isinstance(previous, GroupedEntrySummary):
            self.state.selected_id = (
                self._selected_variant_id
                if any(
                    variant.identity == self._selected_variant_id for variant in previous.variants
                )
                else previous.primary.identity
            )
        elif isinstance(previous, EntrySummary) and self.group_alternate_sources:
            self.state.selected_id = (
                previous.group_key
                or f"group:{previous.category.value}:{normalize_name(previous.name)}"
            )
        self.state.list_index = 0
        self._update_group_status()
        self._invalidate_search()
        self._queue_search()

    def action_variants(self) -> None:
        selected = next(
            (result for result in self.state.results if result.identity == self.state.selected_id),
            None,
        )
        if not isinstance(selected, GroupedEntrySummary) or not selected.alternates:
            self.notify("No alternate sources for this entry.", timeout=2)
            return
        variants = selected.variants
        labels = tuple(f"{variant.source_label} · {variant.dataset_title}" for variant in variants)
        current = next(
            (
                index
                for index, variant in enumerate(variants)
                if variant.identity == self._selected_variant_id
            ),
            0,
        )
        self._clear_image()
        self.push_screen(
            ChoiceScreen("Source variants", labels, current, mark_selected=True),
            lambda index: self._variant_selected(selected, index),
        )

    def _variant_selected(self, group: GroupedEntrySummary, index: int | None) -> None:
        if index is None:
            self._restore_current_image()
            return
        if self.state.selected_id != group.identity:
            return
        self._load_variant(group.variants[index])

    def action_presets(self) -> None:
        if self.layout_mode == "compact":
            return
        presets = {
            preset.name: preset
            for preset in (
                FilterPreset("Everything 2024", ("2024",)),
                FilterPreset("Everything 2014", ("2014",)),
                FilterPreset("All Content"),
            )
        }
        presets.update((preset.name, preset) for preset in self.config.content.filter_presets)
        self._presets = tuple(presets.values())
        try:
            all_sources = self.search_service.list_available_sources(self.category, ())
        except Exception:
            all_sources = self._source_options
        previews = tuple(self._preset_preview(preset, all_sources) for preset in self._presets)
        self._clear_image()
        self.push_screen(
            ChoiceScreen(
                "Filter presets",
                tuple(preset.name for preset in self._presets),
                previews=previews,
            ),
            self._preset_selected,
        )

    def _preset_preview(self, preset: FilterPreset, all_sources: tuple[SourceOption, ...]) -> str:
        lines = ["Editions"]
        if not preset.editions:
            lines.append("  [x] All Editions")
        else:
            lines.extend(
                f"  {'[x]' if option.value in preset.editions else '[ ]'} {option.label}"
                for option in self._edition_options
            )
        lines.append("Sources")
        if not preset.sources:
            lines.append("  [x] All Sources")
        else:
            lines.extend(
                f"  {'[x]' if option.identity in preset.sources else '[ ]'} "
                f"{option.title} · {display_edition(option.edition) or 'Unknown edition'}"
                for option in all_sources
            )
            available = {option.identity for option in all_sources}
            lines.extend(
                f"  [x] {source} (unavailable)"
                for source in preset.sources
                if source not in available
            )
        return "\n".join(lines)

    def _preset_selected(self, index: int | None) -> None:
        if index is None:
            self._restore_current_image()
            return
        preset = self._presets[index]
        self.state.editions = preset.editions
        self.state.sources = preset.sources
        self.state.parent_class = None
        self.state.filters_initialized = True
        self._refresh_filter_options()
        self._apply_filter_change()

    def action_browse_sources(self) -> None:
        if self.layout_mode == "compact":
            return
        try:
            sources = self.search_service.list_source_contents()
        except Exception:
            self._show_error("Source browser is unavailable.")
            return
        self._clear_image()
        self.push_screen(SourceBrowserScreen(sources), self._source_category_selected)

    def _source_category_selected(
        self, selection: tuple[SourceIdentity, SearchCategory] | None
    ) -> None:
        if selection is None:
            self._restore_current_image()
            return
        source, category = selection
        self._switch_category(category)
        self.state.query = ""
        self.state.editions = ()
        self.state.sources = (source,)
        self.state.parent_class = None
        self.state.filters_initialized = True
        self._update_input_from_state()
        self._refresh_filter_options()
        self._apply_filter_change()

    def _edition_filter_selected(self, values: tuple[object, ...] | None) -> None:
        if values is None:
            self._restore_current_image()
            return
        self.state.editions = tuple(str(value) for value in values)
        self._refresh_filter_options()
        self._update_filter_status()
        self._apply_filter_change()

    def _source_filter_selected(self, values: tuple[object, ...] | None) -> None:
        if values is None:
            self._restore_current_image()
            return
        self.state.sources = tuple(value for value in values if isinstance(value, SourceIdentity))
        self._update_filter_status()
        self._apply_filter_change()

    def _refresh_filter_options(self) -> bool:
        try:
            editions = self.search_service.list_available_editions(self.category)
            available_editions = {option.value for option in editions}
            if not self.state.filters_initialized:
                configured = self.config.content.default_editions
                self.state.editions = tuple(
                    edition for edition in configured if edition in available_editions
                )
                self.state.filters_initialized = True
            else:
                self.state.editions = tuple(
                    edition for edition in self.state.editions if edition in available_editions
                )
            sources = self.search_service.list_available_sources(self.category, self.state.editions)
            if self.category is SearchCategory.SUBCLASSES and self.state.parent_class:
                parents = self.search_service.list_subclass_parents(self.state.editions)
                if self.state.parent_class not in parents:
                    self.state.parent_class = None
        except Exception:
            self._show_error("Filter options are unavailable.")
            return False
        self._edition_options = editions
        self._source_options = sources
        available_sources = {option.identity for option in sources}
        self.state.sources = tuple(
            source for source in self.state.sources if source in available_sources
        )
        self._update_filter_status()
        return True

    def _update_filter_status(self) -> None:
        if not self.is_mounted:
            return
        heading = "Results"
        if self.category is SearchCategory.SUBCLASSES:
            heading = "Subclasses · f Class: " + (self.state.parent_class or "All")
        elif self.category is SearchCategory.MONSTERS:
            active = [
                f"CR {self.state.challenge_rating}" if self.state.challenge_rating else "",
                self.state.creature_type or "",
                self.state.size or "",
            ]
            heading = "Monsters" + (" · " + " / ".join(filter(None, active)) if any(active) else "")
        self._query_widget("#list-heading", Label).update(heading)
        if not self.state.editions:
            edition_label = "All Editions"
        elif len(self.state.editions) > 1:
            edition_label = f"{len(self.state.editions)} selected"
        else:
            edition_label = display_edition(self.state.editions[0]) or self.state.editions[0]
        if not self.state.sources:
            source_label = "All Sources"
        elif len(self.state.sources) > 1:
            source_label = f"{len(self.state.sources)} selected"
        else:
            selected = self.state.sources[0]
            source = next(
                (option for option in self._source_options if option.identity == selected), None
            )
            source_label = source.title if source else "1 selected"
        narrow = self.size.width < 80
        edition_prefix = "Ed:" if narrow else "Edition:"
        source_prefix = "Src:" if narrow else "Source:"
        source_width = 27 if narrow else 43
        source_label = shorten(source_label, width=source_width, placeholder="…")
        self._query_widget("#edition-status", Label).update(f"{edition_prefix} {edition_label}")
        self._query_widget("#source-status", Label).update(f"{source_prefix} {source_label}")

    def _apply_filter_change(self) -> None:
        self.state.selected_id = None
        self.state.list_index = 0
        request_id = self._invalidate_search()
        if self._search_timer is not None:
            self._search_timer.stop()
        self._start_search(request_id)

    def _update_group_status(self) -> None:
        self._query_widget("#footer", Static).update(
            "/ Search   F2 Mode   1–6 Category   "
            f"g Groups: {'On' if self.group_alternate_sources else 'Off'}   ? Help   q Quit"
        )

    def action_show_help(self) -> None:
        self._clear_image()
        self.push_screen(HelpScreen(), lambda _value: self._restore_current_image())

    def action_show_about(self) -> None:
        self._clear_image()
        screen = AboutScreen(self.image_adapter.capabilities, self._image_status)
        self.push_screen(screen, lambda _value: self._restore_current_image())
        self.run_worker(
            self.search_service.list_installed_datasets,
            name="about",
            group="about",
            thread=True,
            exit_on_error=False,
        )

    def on_screen_resume(self) -> None:
        self._restore_current_image()

    def _mode_label(self) -> str:
        return "All text" if self.mode is SearchMode.ALL_TEXT else "Names"

    def _invalidate_search(self) -> int:
        self._request_id += 1
        self._detail_request_id += 1
        self._detail_requested_for = None
        self._detail_loaded_for = None
        self._selected_variant_id = None
        self._current_detail = None
        self._related_targets = ()
        self._clear_image()
        self._page_loading = False
        return self._request_id

    def _queue_search(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        request_id = self._request_id
        self._search_timer = self.set_timer(
            0.1,
            lambda: self._start_search(request_id),
            name="search-debounce",
        )

    def _start_search(self, request_id: int) -> None:
        if request_id != self._request_id:
            return
        self.state.results.clear()
        self.state.total_count = 0
        self.state.list_index = 0
        self._detail_requested_for = None
        self._page_loading = False
        self._show_loading("Searching…")
        query = SearchQuery(
            self.category,
            self.state.query,
            self.mode,
            offset=0,
            limit=50,
            request_id=request_id,
            editions=self.state.editions,
            sources=self.state.sources,
            parent_class=self.state.parent_class,
            challenge_ratings=(self.state.challenge_rating,) if self.state.challenge_rating else (),
            creature_types=(self.state.creature_type,) if self.state.creature_type else (),
            sizes=(self.state.size,) if self.state.size else (),
        )
        self.run_worker(
            lambda: self._search_page(query),
            name=f"search:{request_id}",
            group="search",
            thread=True,
            exit_on_error=False,
        )

    def _load_next_page_if_needed(self) -> None:
        if self._page_loading or self.state.total_count <= len(self.state.results):
            return
        self._page_loading = True
        request_id = self._request_id
        query = SearchQuery(
            self.category,
            self.state.query,
            self.mode,
            offset=len(self.state.results),
            limit=50,
            request_id=request_id,
            editions=self.state.editions,
            sources=self.state.sources,
            parent_class=self.state.parent_class,
            challenge_ratings=(self.state.challenge_rating,) if self.state.challenge_rating else (),
            creature_types=(self.state.creature_type,) if self.state.creature_type else (),
            sizes=(self.state.size,) if self.state.size else (),
        )
        self.run_worker(
            lambda: self._search_page(query),
            name=f"page:{request_id}",
            group="page",
            thread=True,
            exit_on_error=False,
        )

    def _search_page(self, query: SearchQuery) -> SearchPage:
        if self.group_alternate_sources:
            return self.search_service.search_grouped(query, self.config.content.preferred_sources)
        return self.search_service.search(query)

    async def _handle_search_result(self, page: Any) -> None:
        if not isinstance(page, SearchPage) or page.request_id != self._request_id:
            return
        self.state.results = list(page.results)
        if (
            self.state.selected_id is not None
            and self.state.selected_id.startswith("group:")
            and not any(result.identity == self.state.selected_id for result in self.state.results)
            and any(result.identity == self._selected_variant_id for result in self.state.results)
        ):
            self.state.selected_id = self._selected_variant_id
        self.state.total_count = page.total_count
        self._page_loading = False
        await self._populate_results()
        self._restore_selection()
        if self._restoring_history:
            self._restoring_history = False
            self._history_restore_detail_id = None
        elif self._category_transition:
            self.navigation_history.restore_history_state(self._navigation_state())
            self._category_transition = False

    async def _handle_page_result(self, page: Any) -> None:
        if not isinstance(page, SearchPage) or page.request_id != self._request_id:
            return
        existing = {summary.identity for summary in self.state.results}
        self.state.results.extend(
            summary for summary in page.results if summary.identity not in existing
        )
        self.state.total_count = page.total_count
        self._page_loading = False
        await self._populate_results(preserve_index=True)

    async def _populate_results(self, *, preserve_index: bool = False) -> None:
        result_list = self._query_widget("#result-list", ListView)
        index = self.state.list_index if preserve_index else 0
        if not preserve_index and self.state.selected_id is not None:
            index = next(
                (
                    position
                    for position, result in enumerate(self.state.results)
                    if result.identity == self.state.selected_id
                ),
                index,
            )
        await result_list.clear()
        await result_list.extend(ResultRow(summary) for summary in self.state.results)
        self._query_widget("#result-count", Label).update(
            f"{self.state.total_count} result" + ("s" if self.state.total_count != 1 else "")
        )
        message = self._query_widget("#result-message", Label)
        if not self.state.results:
            if self.database.path.exists():
                message.update(
                    f'No entries found for "{self.state.query}".'
                    if self.state.query
                    else "No entries in this category."
                )
            else:
                message.update("No datasets are installed. Use `dndref import PATH` first.")
            message.display = True
        else:
            message.update("")
            message.display = False
        if self.state.results:
            result_list.index = min(index, len(self.state.results) - 1)
            self._mark_selected_row()

    def _restore_selection(self) -> None:
        if self._restoring_history:
            result_list = self._query_widget("#result-list", ListView)
            if self.state.results:
                restored_index = self._history_result_index(self._history_restore_detail_id)
                if restored_index is None:
                    restored_index = next(
                        (
                            index
                            for index, summary in enumerate(self.state.results)
                            if summary.identity == self.state.selected_id
                        ),
                        min(self.state.list_index, len(self.state.results) - 1),
                    )
                self.state.list_index = restored_index
                summary = self.state.results[restored_index]
                self.state.selected_id = summary.identity
                if (
                    self._history_restore_detail_id
                    and isinstance(summary, GroupedEntrySummary)
                    and any(
                        variant.identity == self._history_restore_detail_id
                        for variant in summary.variants
                    )
                ):
                    self._selected_variant_id = self._history_restore_detail_id
                result_list.index = restored_index
            result_list.scroll_y = self.state.list_scroll
            self.call_after_refresh(self._restore_list_scroll, self.state.list_scroll)
            self.set_timer(
                0.05,
                lambda scroll=self.state.list_scroll: self._restore_list_scroll(scroll),
                name="history-list-scroll-restore",
            )
            return
        if not self.state.results:
            self._detail_loaded_for = None
            self._detail_requested_for = None
            self._current_detail = None
            self._selected_variant_id = None
            self._query_widget("#detail-variant-hint", Static).display = False
            self._clear_image()
            self._query_widget("#detail-copy", Markdown).update(
                "# No entry selected\n\nSelect a result to view its details."
            )
            self._query_widget("#detail-copy", Markdown).display = True
            self._query_widget("#class-detail", ClassDetailView).display = False
            self._query_widget("#related-list", ListView).display = False
            self._query_widget("#related-heading", Static).display = False
            return
        selected_index = next(
            (
                index
                for index, summary in enumerate(self.state.results)
                if summary.identity == self.state.selected_id
            ),
            min(self.state.list_index, len(self.state.results) - 1),
        )
        self.state.list_index = selected_index
        self._query_widget("#result-list", ListView).index = selected_index
        selected = self.state.results[selected_index]
        self._select_summary(selected)

    def _restore_list_scroll(self, scroll: int) -> None:
        self._query_widget("#result-list", ListView).scroll_y = scroll

    def _history_result_index(self, identity: str | None) -> int | None:
        if identity is None:
            return None
        for index, summary in enumerate(self.state.results):
            if summary.identity == identity:
                return index
            if isinstance(summary, GroupedEntrySummary) and any(
                variant.identity == identity for variant in summary.variants
            ):
                return index
        return None

    def _select_summary(self, summary: EntrySummary | GroupedEntrySummary) -> None:
        if (
            self.state.selected_id == summary.identity
            and self._detail_requested_for == summary.identity
        ):
            self._mark_selected_row()
            return
        self.state.detail_scroll = 0
        self._related_targets = ()
        self.state.selected_id = summary.identity
        self._detail_requested_for = summary.identity
        self._detail_loaded_for = None
        self._mark_selected_row()
        self._query_widget("#detail-copy", Markdown).update(f"# {summary.name}\n\nLoading details…")
        self._query_widget("#detail-copy", Markdown).display = True
        self._query_widget("#class-detail", ClassDetailView).display = False
        self._query_widget("#related-list", ListView).display = False
        self._query_widget("#related-heading", Static).display = False
        self._query_widget("#detail-scroll", VerticalScroll).scroll_home()
        if isinstance(summary, GroupedEntrySummary):
            selected_variant = next(
                (
                    variant
                    for variant in summary.variants
                    if variant.identity == self._selected_variant_id
                ),
                summary.primary,
            )
            self._load_variant(selected_variant)
        else:
            self._load_variant(summary)

    def _load_variant(self, variant: EntrySummary) -> None:
        self._load_variant_identity(variant.identity)

    def _load_variant_identity(self, identity: str) -> None:
        self._selected_variant_id = identity
        self._clear_image()
        self._detail_request_id += 1
        detail_request_id = self._detail_request_id
        self.run_worker(
            lambda: self._load_detail_bundle(identity),
            name=f"detail:{detail_request_id}:{identity}",
            group="detail",
            thread=True,
            exit_on_error=False,
        )

    async def _handle_detail_result(self, detail: Any, worker_name: str) -> None:
        parts = worker_name.split(":", 2)
        if len(parts) != 3 or int(parts[1]) != self._detail_request_id:
            return
        identity = parts[2]
        if identity != self._selected_variant_id:
            return
        if not isinstance(detail, tuple) or len(detail) != 2:
            self._show_detail_error("The selected entry is no longer available.")
            return
        detail, references = detail
        if not isinstance(detail, EntryDetail):
            self._show_detail_error("The selected entry is no longer available.")
            return
        self._detail_loaded_for = detail.identity
        self._current_detail = detail
        self._related_targets = tuple(references)
        self.recently_viewed.add(
            ViewedRecord(
                detail.identity,
                detail.category,
                detail.name,
                str(detail.fields.get("edition")) if detail.fields.get("edition") else None,
            )
        )
        if self.navigation_history.current is None:
            self.navigation_history.restore_history_state(self._navigation_state(detail.identity))
        await self._render_detail(detail)

    def _load_detail_bundle(
        self, identity: str
    ) -> tuple[EntryDetail | None, tuple[ReferenceTarget, ...]]:
        detail = self.search_service.get_entry_detail(identity)
        if detail is None:
            return detail, ()
        edition = str(detail.fields.get("edition")) if detail.fields.get("edition") else None
        if detail.category is SearchCategory.FEATS:
            return detail, self.cross_references.explicit_feat_prerequisite_references(
                str(detail.fields.get("prerequisite"))
                if detail.fields.get("prerequisite")
                else None,
                edition,
            )
        if detail.category is not SearchCategory.MONSTERS:
            return detail, ()
        abilities = detail.fields.get("abilities_and_actions") or ()
        descriptions = tuple(
            str(ability.get("description", ""))
            for ability in abilities
            if ability.get("section") in {"spellcasting", "innate_spellcasting"}
        )
        return detail, self.cross_references.monster_spell_references(descriptions, edition)

    async def _render_detail(self, detail: EntryDetail) -> None:
        history_state = self.navigation_history.current
        if history_state is not None and history_state.detail_id == detail.identity:
            self.state.detail_scroll = history_state.detail_scroll
        detail_copy = self._query_widget("#detail-copy", Markdown)
        class_detail = self._query_widget("#class-detail", ClassDetailView)
        selected = next(
            (result for result in self.state.results if result.identity == self.state.selected_id),
            None,
        )
        hint = self._query_widget("#detail-variant-hint", Static)
        if isinstance(selected, GroupedEntrySummary) and selected.alternates:
            hint.update(
                f"Source: {detail.source_label} · "
                f"{len(selected.alternates)} alternates · v Variants"
            )
            hint.display = True
        else:
            hint.display = False
        if detail.category is SearchCategory.CLASSES:
            detail_copy.display = False
            class_detail.display = True
            await class_detail.update_detail(detail)
        else:
            class_detail.display = False
            detail_copy.display = True
            detail_copy.update(render_detail(detail))
        favorite = self.personal_data.is_favorite(detail.identity)
        collections = self.personal_data.collections_for(detail.identity)
        tags = self.personal_data.tags_for(detail.identity)
        note = self.personal_data.note_for(detail.identity)
        personal = "Personal\n" + ("★ Favorite" if favorite else "☆ Not favorite")
        personal += "\nCollections: " + (", ".join(collections) if collections else "None")
        personal += "\nTags: " + (", ".join(tags) if tags else "None")
        personal += "\nNote: " + ("present" if note else "none")
        self._query_widget("#personal-status", Static).update(personal)
        await self._render_related_targets(detail)
        detail_scroll = self.state.detail_scroll
        self._query_widget("#detail-scroll", VerticalScroll).scroll_y = detail_scroll
        self.call_after_refresh(self._restore_detail_scroll, detail_scroll)
        self.set_timer(
            0.05,
            lambda scroll=detail_scroll: self._restore_detail_scroll(scroll),
            name="history-detail-scroll-restore",
        )
        self._schedule_image(detail)

    def _restore_detail_scroll(self, scroll: int) -> None:
        if not self.screen_stack:
            return
        self._query_widget("#detail-scroll", VerticalScroll).scroll_y = scroll

    async def _render_related_targets(self, detail: EntryDetail) -> None:
        related = self._query_widget("#related-list", ListView)
        await related.clear()
        heading = self._query_widget("#related-heading", Static)
        targets = list(self._related_targets)
        if detail.category is SearchCategory.SUBCLASSES:
            parent_id = detail.fields.get("parent_class_identity")
            target = (
                self.cross_references.get_by_id(parent_id) if isinstance(parent_id, str) else None
            )
            targets = [target] if target is not None else []
        self._related_targets = tuple(targets)
        related.display = bool(targets)
        heading.display = bool(targets)
        if not targets:
            return
        # The class detail's existing, focused subclass list is already its
        # Related Content section; do not render a duplicate list here.
        if detail.category is SearchCategory.CLASSES:
            related.display = False
            heading.display = False
            return
        title = {
            SearchCategory.SUBCLASSES: "Parent Class",
            SearchCategory.FEATS: "Feats",
        }.get(detail.category, "Spells")
        heading.update(f"Related Content · {title}")
        for target in targets:
            edition = display_edition(target.edition) or "Unknown edition"
            await related.mount(
                ListItem(
                    Label(
                        f"↗ {target.name} · {target.category.value.title()} · {edition} · "
                        f"{target.source_label}"
                    )
                )
            )

    def _open_highlighted_subclass(self) -> None:
        subclass_list = self._query_widget("#subclass-list", ListView)
        class_detail = self._query_widget("#class-detail", ClassDetailView)
        index = subclass_list.index
        if index is not None and 0 <= index < len(getattr(class_detail, "subclass_ids", ())):
            self._navigate_to_identity(class_detail.subclass_ids[index])

    def _open_highlighted_reference(self) -> None:
        related = self._query_widget("#related-list", ListView)
        index = related.index
        if index is not None and 0 <= index < len(self._related_targets):
            target = self._related_targets[index]
            options = tuple(item for item in self._related_targets if item.name == target.name)
            if len(options) == 1:
                self._navigate_to_identity(target.identity)
            else:
                self.push_screen(
                    ChoiceScreen(
                        f"Choose {target.name} variant",
                        tuple(f"{item.source_label} · {item.dataset_id}" for item in options),
                    ),
                    lambda choice: self._reference_variant_selected(options, choice),
                )

    def _reference_variant_selected(
        self, targets: tuple[ReferenceTarget, ...], index: int | None
    ) -> None:
        if index is not None and 0 <= index < len(targets):
            self._navigate_to_identity(targets[index].identity)

    def _navigate_to_identity(self, identity: str) -> None:
        target = self.cross_references.get_by_id(identity)
        if target is None:
            self.notify("That reference is no longer available.", timeout=3)
            return
        if self._current_detail is not None and target.identity == self._current_detail.identity:
            return
        self._commit_current_location()
        state = self.category_states[target.category]
        state.query = target.name
        state.editions = (target.edition,) if target.edition else ()
        state.sources = ()
        state.parent_class = None
        state.challenge_rating = None
        state.creature_type = None
        state.size = None
        state.filters_initialized = True
        state.selected_id = target.identity
        state.list_index = 0
        state.list_scroll = 0
        state.detail_scroll = 0
        self.category = target.category
        self.narrow_detail_open = self.layout_mode == "stacked"
        self._selected_variant_id = target.identity
        self._current_detail = None
        self._related_targets = ()
        self._history_restore_detail_id = target.identity
        self._restoring_history = True
        self.navigation_history.navigate_to(self._navigation_state(target.identity))
        self._update_input_from_state()
        self._refresh_filter_options()
        self._update_filter_status()
        self._update_tab_styles()
        self._query_widget("#related-list", ListView).display = False
        self._query_widget("#related-heading", Static).display = False
        self._query_widget("#detail-copy", Markdown).display = True
        self._query_widget("#class-detail", ClassDetailView).display = False
        self._query_widget("#detail-copy", Markdown).update("Loading details…")
        self._query_widget("#detail-scroll", VerticalScroll).scroll_home()
        self._invalidate_search()
        self._queue_search()
        self._load_variant_identity(target.identity)

    def _navigation_state(
        self, detail_id: str | None = None, *, include_current_detail: bool = True
    ) -> NavigationState:
        state = self.state
        scroll = int(self._query_widget("#detail-scroll", VerticalScroll).scroll_y)
        identity = detail_id
        if identity is None and include_current_detail:
            identity = (
                self._current_detail.identity
                if self._current_detail is not None
                else self._selected_variant_id
            )
        return NavigationState(
            category=self.category,
            query=state.query,
            mode=self.mode.value,
            editions=state.editions,
            sources=state.sources,
            parent_class=state.parent_class,
            challenge_rating=state.challenge_rating,
            creature_type=state.creature_type,
            size=state.size,
            selected_id=state.selected_id,
            variant_id=self._selected_variant_id,
            list_index=state.list_index,
            list_scroll=int(self._query_widget("#result-list", ListView).scroll_y),
            detail_scroll=scroll,
            detail_id=identity,
            narrow_detail_open=self.narrow_detail_open,
            personal_view=self.personal_view,
            personal_collection_id=self.personal_collection_id,
            personal_query=self.personal_query,
            personal_category_filter=self.personal_category_filter,
            personal_edition_filter=self.personal_edition_filter,
            personal_tag_filter=self.personal_tag_filter,
        )

    def _commit_current_location(self) -> None:
        current = self.navigation_history.current
        if current is not None and current.universal_search:
            return
        if current is not None and current.personal_view and current.detail_id is None:
            return
        self.navigation_history.navigate_to(self._navigation_state())

    def _restore_navigation_state(self, state: NavigationState) -> None:
        self.category = state.category
        self.mode = SearchMode(state.mode)
        category_state = self.state
        category_state.query = state.query
        category_state.editions = state.editions
        category_state.sources = state.sources
        category_state.parent_class = state.parent_class
        category_state.challenge_rating = state.challenge_rating
        category_state.creature_type = state.creature_type
        category_state.size = state.size
        category_state.selected_id = state.selected_id
        category_state.list_index = state.list_index
        category_state.list_scroll = state.list_scroll
        category_state.detail_scroll = state.detail_scroll
        self._selected_variant_id = state.variant_id
        self._current_detail = None
        self._history_restore_detail_id = state.detail_id
        self._restoring_history = True
        self.narrow_detail_open = state.narrow_detail_open
        self.personal_view = state.personal_view
        self.personal_collection_id = state.personal_collection_id
        self.personal_query = state.personal_query
        self.personal_category_filter = state.personal_category_filter
        self.personal_edition_filter = state.personal_edition_filter
        self.personal_tag_filter = state.personal_tag_filter
        self.navigation_history.restore_history_state(state)
        self._update_input_from_state()
        self._update_tab_styles()
        self._refresh_filter_options()
        self._update_filter_status()
        self._update_layout(self.size.width, self.size.height)
        self._invalidate_search()
        self._queue_search()
        self._query_widget("#detail-scroll", VerticalScroll).scroll_y = state.detail_scroll
        self._query_widget("#result-list", ListView).scroll_y = state.list_scroll
        if state.detail_id:
            if self.cross_references.get_by_id(state.detail_id) is None:
                self._show_detail_error("This history entry is no longer available.")
            else:
                self._load_variant_identity(state.detail_id)
        elif state.personal_view:
            self.call_after_refresh(self._show_personal_view)
        if state.universal_search:
            self.call_after_refresh(lambda: self._open_universal_search_from_state(state))

    def _open_universal_search_from_state(self, state: NavigationState) -> None:
        self._clear_image()
        screen = UniversalSearchScreen(
            self.search_service,
            self.personal_data.recent_searches(),
            self.recently_viewed.records,
            query=state.universal_query,
            selected_index=state.universal_selected_index,
            scroll=state.universal_scroll,
        )
        self.push_screen(screen, lambda result: self._universal_search_result(result, state))

    def action_back(self) -> None:
        while self.navigation_history.can_go_back:
            state = self.navigation_history.go_back()
            if state is None:
                break
            if state.detail_id and self.cross_references.get_by_id(state.detail_id) is None:
                continue
            self._restore_navigation_state(state)
            return

    def action_forward(self) -> None:
        while self.navigation_history.can_go_forward:
            state = self.navigation_history.go_forward()
            if state is None:
                break
            if state.detail_id and self.cross_references.get_by_id(state.detail_id) is None:
                continue
            self._restore_navigation_state(state)
            return

    def action_recently_viewed(self) -> None:
        if not self.recently_viewed.records:
            self.notify("No recently viewed entries.", timeout=2)
            return
        self.push_screen(
            RecentlyViewedScreen(self.recently_viewed.records),
            self._recent_entry_selected,
        )

    def action_toggle_favorite(self) -> None:
        detail = self._current_detail
        if detail is None:
            return
        present = self.personal_data.is_favorite(detail.identity)
        self.personal_data.set_favorite(detail, not present)
        self._refresh_personal_detail(detail)

    def _refresh_personal_detail(self, detail: EntryDetail) -> None:
        self.run_worker(
            self._render_detail(detail),
            name="personal-detail-refresh",
            group="personal-detail-refresh",
            exclusive=True,
            thread=False,
            exit_on_error=False,
        )

    def action_edit_note(self) -> None:
        detail = self._current_detail
        if detail is None:
            return
        self.push_screen(
            TextEntryScreen(
                "Edit private note · Ctrl+S to save",
                self.personal_data.note_for(detail.identity) or "",
                multiline=True,
            ),
            lambda result: self._note_saved(detail, result),
        )

    def _note_saved(self, detail: EntryDetail, result: tuple[str, str] | None) -> None:
        if result is not None:
            self.personal_data.save_note(detail, result[1])
            self._refresh_personal_detail(detail)

    def action_edit_tags(self) -> None:
        detail = self._current_detail
        if detail is None:
            return
        tags = self.personal_data.tags_for(detail.identity)
        self.push_screen(
            TextEntryScreen(
                "Edit tags · comma or newline separated · Ctrl+S to save",
                "\n".join(tags),
                multiline=True,
            ),
            lambda result: self._tags_saved(detail, result),
        )

    def _tags_saved(self, detail: EntryDetail, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        desired = {tag.strip() for tag in result[1].replace(",", "\n").splitlines() if tag.strip()}
        current = set(self.personal_data.tags_for(detail.identity))
        for tag in current - desired:
            self.personal_data.remove_tag(detail.identity, tag)
        for tag in desired - current:
            self.personal_data.add_tag(detail.identity, tag)
        self._refresh_personal_detail(detail)

    def action_add_to_collection(self) -> None:
        detail = self._current_detail
        if detail is None:
            return
        collections = self.personal_data.list_collections()
        current = self.personal_data.collection_ids_for(detail.identity)
        self.push_screen(
            CollectionChooserScreen(collections, current),
            lambda selected: self._collection_membership_saved(detail, selected),
        )

    def _collection_membership_saved(
        self, detail: EntryDetail, selected: tuple[int, ...] | None
    ) -> None:
        if selected is None:
            return
        existing = set(self.personal_data.collection_ids_for(detail.identity))
        chosen = set(selected)
        for collection in self.personal_data.list_collections():
            if (collection.collection_id in chosen) != (collection.collection_id in existing):
                self.personal_data.set_collection_membership(
                    collection.collection_id, detail, collection.collection_id in chosen
                )
        self._refresh_personal_detail(detail)

    def action_favorites(self) -> None:
        self._commit_current_location()
        self.personal_view = "favorites"
        self.personal_collection_id = None
        self.personal_query = ""
        self.personal_category_filter = None
        self.personal_edition_filter = None
        self.personal_tag_filter = None
        self.navigation_history.navigate_to(self._navigation_state(include_current_detail=False))
        self._show_personal_view()

    def action_collections(self) -> None:
        self._commit_current_location()
        self.personal_view = "collections"
        self.personal_collection_id = None
        self.personal_query = ""
        self.personal_category_filter = None
        self.personal_edition_filter = None
        self.personal_tag_filter = None
        self.navigation_history.navigate_to(self._navigation_state(include_current_detail=False))
        self._show_personal_view()

    def _show_personal_view(self) -> None:
        if self.personal_view == "favorites":
            self.push_screen(
                PersonalEntriesScreen(
                    "Favorites",
                    self.personal_data.list_favorites(self.personal_query),
                    removable=True,
                    query=self.personal_query,
                    kind_filter=self.personal_category_filter,
                    edition_filter=self.personal_edition_filter,
                    tag_filter=self.personal_tag_filter,
                ),
                self._personal_entries_result,
            )
        elif self.personal_view == "collection" and self.personal_collection_id is not None:
            collection = next(
                (
                    c
                    for c in self.personal_data.list_collections()
                    if c.collection_id == self.personal_collection_id
                ),
                None,
            )
            title = collection.name if collection else "Missing collection"
            self.push_screen(
                PersonalEntriesScreen(
                    title,
                    self.personal_data.list_collection_entries(
                        self.personal_collection_id, query=self.personal_query
                    ),
                    removable=True,
                    query=self.personal_query,
                    kind_filter=self.personal_category_filter,
                    edition_filter=self.personal_edition_filter,
                    tag_filter=self.personal_tag_filter,
                ),
                self._personal_entries_result,
            )
        elif self.personal_view == "collections":
            self.push_screen(
                CollectionListScreen(self.personal_data.list_collections()),
                self._collections_result,
            )

    def _personal_entries_result(
        self,
        result: tuple[str, str, str, str | None, str | None, str | None] | None,
    ) -> None:
        if result is None:
            return
        action, identity, query, category_filter, edition_filter, tag_filter = result
        self.personal_query = query
        self.personal_category_filter = category_filter
        self.personal_edition_filter = edition_filter
        self.personal_tag_filter = tag_filter
        current = self.navigation_history.current
        if current is not None and current.personal_view:
            self.navigation_history.restore_history_state(
                replace(
                    current,
                    personal_query=query,
                    personal_category_filter=category_filter,
                    personal_edition_filter=edition_filter,
                    personal_tag_filter=tag_filter,
                )
            )
        if action == "remove":
            if self.personal_view == "favorites":
                self.personal_data.remove_favorite(identity)
            elif self.personal_collection_id is not None:
                self.personal_data.remove_collection_identity(self.personal_collection_id, identity)
            self._show_personal_view()
            return
        self._current_detail = None
        self._navigate_to_identity(identity)

    def _collections_result(self, result: tuple[str, int] | None) -> None:
        if result is None:
            return
        action, collection_id = result
        if action == "open":
            self.personal_view = "collection"
            self.personal_collection_id = collection_id
            self.personal_query = ""
            self.personal_category_filter = None
            self.personal_edition_filter = None
            self.personal_tag_filter = None
            self.navigation_history.navigate_to(
                self._navigation_state(include_current_detail=False)
            )
            self._show_personal_view()
        elif action == "create":
            self.push_screen(
                TextEntryScreen("New collection name · Ctrl+S to save"),
                self._collection_name_result,
            )
        elif action == "rename":
            collection = next(
                (
                    c
                    for c in self.personal_data.list_collections()
                    if c.collection_id == collection_id
                ),
                None,
            )
            if collection:
                self.push_screen(
                    TextEntryScreen("Rename collection · Ctrl+S to save", collection.name),
                    lambda value: self._collection_renamed(collection_id, value),
                )
        elif action == "delete":
            self.push_screen(
                TextEntryScreen("Type DELETE to confirm collection deletion", multiline=False),
                lambda value: self._collection_deleted(collection_id, value),
            )

    def _collection_name_result(self, result: tuple[str, str] | None) -> None:
        if result and result[1].strip():
            self.personal_data.create_collection(result[1])
        self._show_personal_view()

    def _collection_renamed(self, collection_id: int, result: tuple[str, str] | None) -> None:
        if result and result[1].strip():
            self.personal_data.rename_collection(collection_id, result[1])
        self._show_personal_view()

    def _collection_deleted(self, collection_id: int, result: tuple[str, str] | None) -> None:
        if result and result[1] == "DELETE":
            self.personal_data.delete_collection(collection_id)
        self._show_personal_view()

    def _recent_entry_selected(self, identity: str | None) -> None:
        if identity:
            self._navigate_to_identity(identity)

    def _show_detail_error(self, message: str) -> None:
        self._current_detail = None
        self._related_targets = ()
        self._query_widget("#detail-variant-hint", Static).display = False
        self._query_widget("#related-list", ListView).display = False
        self._query_widget("#related-heading", Static).display = False
        self._clear_image()
        self._query_widget("#class-detail", ClassDetailView).display = False
        self._query_widget("#detail-copy", Markdown).display = True
        self._query_widget("#detail-copy", Markdown).update(f"# Unable to load entry\n\n{message}")

    def _update_input_from_state(self) -> None:
        input_widget = self._query_widget("#search-input", Input)
        self._updating_input = True
        try:
            input_widget.value = self.state.query
        finally:
            self._updating_input = False
        self._query_widget("#mode-label", Label).update(self._mode_label())

    def _switch_category(self, category: SearchCategory) -> None:
        if category is self.category:
            return
        self._commit_current_location()
        self.state.detail_scroll = self._query_widget("#detail-scroll", VerticalScroll).scroll_y
        self._current_detail = None
        self.personal_view = None
        self.personal_collection_id = None
        self._clear_image()
        self.category = category
        self._category_transition = True
        self.narrow_detail_open = False
        self._update_input_from_state()
        self._refresh_filter_options()
        self._update_tab_styles()
        self._invalidate_search()
        self._queue_search()

    def _update_tab_styles(self) -> None:
        for category in self.CATEGORIES:
            self._query_widget(f"#tab-{category.value}", Button).set_class(
                category is self.category, "active"
            )

    def _mark_selected_row(self) -> None:
        result_list = self._query_widget("#result-list", ListView)
        for child in result_list.children:
            if isinstance(child, ResultRow):
                child.set_selected(child.summary.identity == self.state.selected_id)

    def _show_loading(self, message: str) -> None:
        label = self._query_widget("#result-message", Label)
        label.update(message)
        label.display = True

    def _show_error(self, message: str) -> None:
        self._show_loading(message)

    def _image_layout_available(self) -> bool:
        return (
            self._artwork_visible
            and self.layout_mode == "split"
            and self.size.width >= 120
            and self.size.height >= 30
            and self.screen is self.screen_stack[0]
        )

    def action_toggle_artwork(self) -> None:
        if not self.image_adapter.capabilities.available:
            self.notify("No image backend available.", timeout=2)
            return
        self._artwork_visible = not self._artwork_visible
        if self._artwork_visible:
            self._restore_current_image()
        else:
            self._clear_image()
        self.notify(f"Artwork {'shown' if self._artwork_visible else 'hidden'}.", timeout=2)

    def _current_image_request(self, worker_name: str) -> bool:
        parts = worker_name.split(":", 2)
        return (
            len(parts) == 3
            and parts[1].isdigit()
            and int(parts[1]) == self._image_request_id
            and parts[2] == self._selected_variant_id
        )

    def _clear_image(self) -> None:
        self._image_request_id += 1
        if self._image_timer is not None:
            self._image_timer.stop()
            self._image_timer = None
        if self._image_worker is not None:
            if not self._image_worker.is_finished:
                self._image_worker.cancel()
            self._image_worker = None
        if self._image_widget is not None:
            self.image_adapter.cleanup_widget(self._image_widget)
            self._image_widget = None
        try:
            self._query_widget("#image-panel", ImagePanel).clear_image()
        except Exception:
            pass

    def _schedule_image(self, detail: EntryDetail) -> None:
        self._clear_image()
        if (
            not self.image_adapter.capabilities.available
            or not self._image_layout_available()
            or detail.image is None
        ):
            return
        request_id = self._image_request_id
        self._image_timer = self.set_timer(
            0.15,
            lambda: self._start_image_load(detail, request_id),
            name="image-debounce",
        )

    def _restore_current_image(self) -> None:
        if self._current_detail is not None and self._image_layout_available():
            self._schedule_image(self._current_detail)

    def _start_image_load(self, detail: EntryDetail, request_id: int) -> None:
        self._image_timer = None
        if (
            request_id != self._image_request_id
            or detail.identity != self._selected_variant_id
            or detail.image is None
            or not self._image_layout_available()
        ):
            return
        self._image_worker = self.run_worker(
            lambda: self.image_loader.load(detail.image.path, media_type=detail.image.media_type),
            name=f"image:{request_id}:{detail.identity}",
            group="image",
            thread=True,
            exit_on_error=False,
        )

    def _handle_image_result(self, decoded: Any, worker_name: str) -> None:
        parts = worker_name.split(":", 2)
        if len(parts) != 3 or not isinstance(decoded, DecodedImage):
            return
        if not self._current_image_request(worker_name):
            return
        self._image_worker = None
        widget = self.image_adapter.create_widget(decoded.image)
        if widget is None:
            self._image_status = "Image could not be displayed."
            self._clear_image()
            return
        try:
            panel = self._query_widget("#image-panel", ImagePanel)
            panel.show_image(widget)
            self._image_widget = widget
            self._image_status = f"{self.image_adapter.capabilities.backend.value} image ready"
        except Exception:
            self.image_adapter.cleanup_widget(widget)
            self._image_status = "Image could not be displayed."
            self._clear_image()

    def _update_layout(self, width: int, height: int) -> None:
        if width < 50 or height < 16:
            self.layout_mode = "compact"
        elif width < 80:
            self.layout_mode = "stacked"
        else:
            self.layout_mode = "split"
        self.remove_class("-compact", "-stacked", "-split")
        self.add_class(f"-{self.layout_mode}")
        for category in self.CATEGORIES:
            label = self.CATEGORY_LABELS[category]
            if width < 80 and category is SearchCategory.SUBCLASSES:
                label = "Subcls"
            self._query_widget(f"#tab-{category.value}", Button).label = label
        self._update_filter_status()
        if not self._image_layout_available():
            self._clear_image()
        elif (
            self._current_detail is not None
            and self._image_widget is None
            and self._image_timer is None
        ):
            self._schedule_image(self._current_detail)
        browser = self._query_widget("#browser", Horizontal)
        list_pane = self._query_widget("#list-pane", Vertical)
        detail_pane = self._query_widget("#detail-pane", Vertical)
        too_small = self._query_widget("#too-small", Static)
        if self.layout_mode == "compact":
            browser.display = False
            too_small.display = True
            return
        browser.display = True
        too_small.display = False
        if self.layout_mode == "stacked" and self.narrow_detail_open:
            list_pane.display = False
            detail_pane.display = True
        else:
            list_pane.display = True
            detail_pane.display = self.layout_mode == "split" or self.narrow_detail_open


def _value(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int) and value in (0, 1):
        return "Yes" if value else "No"
    return str(value)


def _field(label: str, value: object) -> str:
    rendered = _value(value)
    return f"**{label}:** {rendered}\n" if rendered else ""


def _section_markdown(section: DetailSection) -> str:
    return f"### {section.heading}\n\n{section.body}\n"


def render_detail(detail: EntryDetail) -> str:
    """Render API-shaped details as readable, limited Markdown."""
    fields = detail.fields
    if detail.category is SearchCategory.CLASSES:
        return render_class_detail(detail)
    if detail.category is SearchCategory.SUBCLASSES:
        return render_subclass_detail(detail)
    if detail.category is SearchCategory.MONSTERS:
        return render_monster_detail(detail)
    output = [
        f"# {detail.name}",
        f"*{detail.category.value.title()} · {detail.dataset_title} · {detail.source_label}*\n",
    ]
    if detail.category is SearchCategory.SPELLS:
        components = [
            label
            for key, label in (("verbal", "V"), ("somatic", "S"), ("material", "M"))
            if fields.get(key)
        ]
        components_text = ", ".join(components)
        if fields.get("material_description"):
            components_text += f" ({fields['material_description']})"
        level = "Cantrip" if fields.get("level") == 0 else f"Level {fields.get('level')}"
        output.extend(
            [
                _field("Level / School", f"{level} · {fields.get('school')}"),
                _field("Casting time", fields.get("casting_time")),
                _field("Range", fields.get("range")),
                _field("Components", components_text),
                _field("Duration", fields.get("duration")),
                _field("Concentration", fields.get("concentration")),
                _field("Ritual", fields.get("ritual")),
                _field(
                    "Classes",
                    ", ".join(
                        map(str, fields.get("class_names") or fields.get("class_entry_ids") or ())
                    ),
                ),
                f"\n{detail.description}\n",
            ]
        )
        if fields.get("higher_level_effects"):
            output.append(f"### At higher levels\n\n{fields['higher_level_effects']}\n")
    elif detail.category is SearchCategory.ITEMS:
        output.extend(
            [
                _field(
                    "Type / subtype",
                    " · ".join(
                        str(value)
                        for value in (fields.get("item_type"), fields.get("subtype"))
                        if value
                    ),
                ),
                _field("Rarity", fields.get("rarity")),
                _field(
                    "Attunement",
                    fields.get("attunement_prerequisite") or fields.get("requires_attunement"),
                ),
                _field("Weight", fields.get("weight_display")),
                _field("Cost", fields.get("cost_display")),
            ]
        )
        weapon = fields.get("weapon")
        if weapon:
            output.append("### Weapon details\n\n")
            output.extend(
                _field(label, weapon.get(key))
                for key, label in (
                    ("damage_expression", "Damage"),
                    ("damage_type", "Damage type"),
                    ("range", "Range"),
                    ("versatile_damage", "Versatile"),
                    ("mastery", "Mastery"),
                )
            )
        armor = fields.get("armor")
        if armor:
            output.append("### Armor details\n\n")
            output.extend(
                _field(label, armor.get(key))
                for key, label in (
                    ("armor_category", "Category"),
                    ("ac_expression", "Armor class"),
                    ("strength_requirement", "Strength requirement"),
                    ("stealth_disadvantage", "Stealth disadvantage"),
                )
            )
        properties = fields.get("properties") or ()
        if properties:
            output.append("### Properties\n\n")
            output.extend(f"- **{prop['name']}** — {prop['description']}\n" for prop in properties)
        output.append(f"\n{detail.description}\n")
    elif detail.category is SearchCategory.FEATS:
        output.extend(
            [
                _field("Category", fields.get("category")),
                _field("Prerequisites", fields.get("prerequisite")),
                _field("Minimum level", fields.get("minimum_level")),
                _field("Repeatable", fields.get("repeatable")),
                _field("Ability increase", fields.get("ability_increase")),
                f"\n{detail.description}\n",
            ]
        )
    for section in detail.sections:
        output.append(_section_markdown(section))
    output.append(f"\n**Source:** {detail.source_label} · {detail.dataset_title}\n")
    return "\n".join(output)


__all__ = ["BrowserApp", "CategoryState", "render_detail"]
