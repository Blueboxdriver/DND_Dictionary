"""Keyboard-driven production browser for imported reference data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, DataTable, Input, Label, ListView, Markdown, Static
from textual.worker import Worker, WorkerState

from ..config import ApplicationPaths, Config
from ..images import DecodedImage, ImageAdapter, ImageLoader
from ..search import (
    DetailSection,
    EntryDetail,
    EntrySummary,
    SearchCategory,
    SearchMode,
    SearchPage,
    SearchQuery,
    SearchService,
)
from ..storage.database import Database
from .class_detail import ClassDetailView, render_class_detail
from .screens import AboutScreen, HelpScreen
from .widgets import ImagePanel, ResultRow


@dataclass
class CategoryState:
    query: str = ""
    selected_id: str | None = None
    list_index: int = 0
    detail_scroll: int = 0
    results: list[EntrySummary] = field(default_factory=list)
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
        ("f2", "toggle_mode", "Mode"),
        ("f1", "show_help", "Help"),
        ("f3", "show_about", "About/Data"),
    ]

    CATEGORIES = (
        SearchCategory.ITEMS,
        SearchCategory.SPELLS,
        SearchCategory.FEATS,
        SearchCategory.CLASSES,
    )
    CATEGORY_LABELS = {
        SearchCategory.ITEMS: "Items",
        SearchCategory.SPELLS: "Spells",
        SearchCategory.FEATS: "Feats",
        SearchCategory.CLASSES: "Classes",
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
        self.category = SearchCategory.SPELLS
        self.mode = SearchMode.NAMES
        self.category_states = {category: CategoryState() for category in self.CATEGORIES}
        self.layout_mode = "unknown"
        self.narrow_detail_open = False
        self._search_timer: Timer | None = None
        self._request_id = 0
        self._detail_request_id = 0
        self._detail_requested_for: str | None = None
        self._detail_loaded_for: str | None = None
        self._current_detail: EntryDetail | None = None
        self._updating_input = False
        self._page_loading = False
        self.image_adapter = ImageAdapter(self.config.ui.images)
        self.image_loader = ImageLoader()
        self._image_timer: Timer | None = None
        self._image_request_id = 0
        self._image_widget: Any | None = None
        self._image_worker: Worker[Any] | None = None
        super().__init__()

    @property
    def state(self) -> CategoryState:
        return self.category_states[self.category]

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
        with Horizontal(id="browser"):
            with Vertical(id="list-pane"):
                yield Label("Results", id="list-heading")
                yield Label("", id="result-message")
                yield ListView(id="result-list")
            with Vertical(id="detail-pane"):
                with Horizontal(id="detail-body"):
                    with VerticalScroll(id="detail-scroll"):
                        yield Markdown("", id="detail-copy")
                        yield ClassDetailView(id="class-detail")
                    yield ImagePanel(id="image-panel")
        yield Static(
            "Terminal too small. Resize to at least 50×16 · ? Help · q Quit",
            id="too-small",
        )
        yield Static(
            "/ Search   F2 Mode   1–4 Category   Tab Focus   ? Help   q Quit",
            id="footer",
        )

    def on_mount(self) -> None:
        self._update_tab_styles()
        self._update_layout(self.size.width, self.size.height)
        self.query_one("#result-list", ListView).focus()
        self._update_input_from_state()
        self._show_loading("Loading…")
        self._queue_search()

    def on_resize(self, event: events.Resize) -> None:
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
            self.query_one("#result-list", ListView).focus()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "result-list" or event.item is None:
            return
        if not isinstance(event.item, ResultRow):
            return
        result_list = self.query_one("#result-list", ListView)
        self.state.list_index = max(0, result_list.index)
        self._select_summary(event.item.summary)
        if self.state.list_index >= len(self.state.results) - 5:
            self._load_next_page_if_needed()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "result-list" or not isinstance(event.item, ResultRow):
            return
        self._select_summary(event.item.summary)
        if self.layout_mode == "stacked":
            self.narrow_detail_open = True
            self._update_layout(self.size.width, self.size.height)
            self.query_one("#detail-scroll", VerticalScroll).focus()

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        worker = event.worker
        if event.state == WorkerState.SUCCESS:
            if worker.name.startswith("search:"):
                await self._handle_search_result(worker.result)
            elif worker.name.startswith("page:"):
                await self._handle_page_result(worker.result)
            elif worker.name.startswith("detail:"):
                self._handle_detail_result(worker.result, worker.name)
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
                self._show_detail_error("The selected entry could not be loaded.")
            elif worker.name.startswith("image:"):
                self._clear_image()

    def on_key(self, event: events.Key) -> None:
        focus = self.screen.focused
        if isinstance(focus, Input):
            if event.key == "escape":
                event.stop()
                self.query_one("#result-list", ListView).focus()
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
        if event.character in {"1", "2", "3", "4"}:
            event.stop()
            self._switch_category(self.CATEGORIES[int(event.character) - 1])
            return
        if event.character == "/":
            event.stop()
            self.query_one("#search-input", Input).focus()
            return
        if event.key == "enter" and isinstance(focus, ListView):
            event.stop()
            row = focus.highlighted_child
            if isinstance(row, ResultRow):
                self._select_summary(row.summary)
                if self.layout_mode == "stacked":
                    self.narrow_detail_open = True
                    self._update_layout(self.size.width, self.size.height)
                    self.query_one("#detail-scroll", VerticalScroll).focus()
            return
        if event.key == "escape":
            event.stop()
            if self.layout_mode == "stacked" and self.narrow_detail_open:
                self.narrow_detail_open = False
                self._update_layout(self.size.width, self.size.height)
                self.query_one("#result-list", ListView).focus()
            elif isinstance(focus, VerticalScroll):
                self.query_one("#result-list", ListView).focus()
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
        self.query_one("#mode-label", Label).update(self._mode_label())
        self._invalidate_search()
        self._queue_search()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_show_help(self) -> None:
        self._clear_image()
        self.push_screen(HelpScreen())

    def action_show_about(self) -> None:
        self._clear_image()
        screen = AboutScreen(self.image_adapter.capabilities)
        self.push_screen(screen)
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
        self._page_loading = False
        self._show_loading("Searching…")
        query = SearchQuery(
            self.category,
            self.state.query,
            self.mode,
            offset=0,
            limit=50,
            request_id=request_id,
        )
        self.run_worker(
            lambda: self.search_service.search(query),
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
        )
        self.run_worker(
            lambda: self.search_service.search(query),
            name=f"page:{request_id}",
            group="page",
            thread=True,
            exit_on_error=False,
        )

    async def _handle_search_result(self, page: Any) -> None:
        if not isinstance(page, SearchPage) or page.request_id != self._request_id:
            return
        self.state.results = list(page.results)
        self.state.total_count = page.total_count
        self._page_loading = False
        await self._populate_results()
        self._restore_selection()

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
        result_list = self.query_one("#result-list", ListView)
        index = self.state.list_index if preserve_index else 0
        await result_list.clear()
        for summary in self.state.results:
            await result_list.mount(ResultRow(summary))
        self.query_one("#result-count", Label).update(
            f"{self.state.total_count} result" + ("s" if self.state.total_count != 1 else "")
        )
        message = self.query_one("#result-message", Label)
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
        if not self.state.results:
            self._detail_loaded_for = None
            self._detail_requested_for = None
            self._current_detail = None
            self._clear_image()
            self.query_one("#detail-copy", Markdown).update(
                "# No entry selected\n\nSelect a result to view its details."
            )
            self.query_one("#detail-copy", Markdown).display = True
            self.query_one("#class-detail", ClassDetailView).display = False
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
        self.query_one("#result-list", ListView).index = selected_index
        selected = self.state.results[selected_index]
        self._select_summary(selected)

    def _select_summary(self, summary: EntrySummary) -> None:
        if (
            self.state.selected_id == summary.identity
            and self._detail_requested_for == summary.identity
        ):
            self._mark_selected_row()
            return
        self.state.detail_scroll = 0
        self.state.selected_id = summary.identity
        self._clear_image()
        self._detail_requested_for = summary.identity
        self._detail_loaded_for = None
        self._mark_selected_row()
        self.query_one("#detail-copy", Markdown).update(f"# {summary.name}\n\nLoading details…")
        self.query_one("#detail-copy", Markdown).display = True
        self.query_one("#class-detail", ClassDetailView).display = False
        self.query_one("#detail-scroll", VerticalScroll).scroll_home()
        self._detail_request_id += 1
        detail_request_id = self._detail_request_id
        self.run_worker(
            lambda: self.search_service.get_entry_detail(summary.identity),
            name=f"detail:{detail_request_id}:{summary.identity}",
            group="detail",
            thread=True,
            exit_on_error=False,
        )

    def _handle_detail_result(self, detail: Any, worker_name: str) -> None:
        parts = worker_name.split(":", 2)
        if len(parts) != 3 or int(parts[1]) != self._detail_request_id:
            return
        identity = parts[2]
        if identity != self.state.selected_id:
            return
        if not isinstance(detail, EntryDetail):
            self._show_detail_error("The selected entry is no longer available.")
            return
        self._detail_loaded_for = detail.identity
        self._current_detail = detail
        self._render_detail(detail)

    def _render_detail(self, detail: EntryDetail) -> None:
        detail_copy = self.query_one("#detail-copy", Markdown)
        class_detail = self.query_one("#class-detail", ClassDetailView)
        if detail.category is SearchCategory.CLASSES:
            detail_copy.display = False
            class_detail.display = True
            class_detail.update_detail(detail)
        else:
            class_detail.display = False
            detail_copy.display = True
            detail_copy.update(render_detail(detail))
        self.query_one("#detail-scroll", VerticalScroll).scroll_y = self.state.detail_scroll
        self._schedule_image(detail)

    def _show_detail_error(self, message: str) -> None:
        self._current_detail = None
        self._clear_image()
        self.query_one("#class-detail", ClassDetailView).display = False
        self.query_one("#detail-copy", Markdown).display = True
        self.query_one("#detail-copy", Markdown).update(f"# Unable to load entry\n\n{message}")

    def _update_input_from_state(self) -> None:
        input_widget = self.query_one("#search-input", Input)
        self._updating_input = True
        try:
            input_widget.value = self.state.query
        finally:
            self._updating_input = False
        self.query_one("#mode-label", Label).update(self._mode_label())

    def _switch_category(self, category: SearchCategory) -> None:
        if category is self.category:
            return
        self.state.detail_scroll = self.query_one("#detail-scroll", VerticalScroll).scroll_y
        self._current_detail = None
        self._clear_image()
        self.category = category
        self.narrow_detail_open = False
        self._update_input_from_state()
        self._update_tab_styles()
        self._invalidate_search()
        self._queue_search()

    def _update_tab_styles(self) -> None:
        for category in self.CATEGORIES:
            self.query_one(f"#tab-{category.value}", Button).set_class(
                category is self.category, "active"
            )

    def _mark_selected_row(self) -> None:
        result_list = self.query_one("#result-list", ListView)
        for child in result_list.children:
            if isinstance(child, ResultRow):
                child.set_selected(child.summary.identity == self.state.selected_id)

    def _show_loading(self, message: str) -> None:
        label = self.query_one("#result-message", Label)
        label.update(message)
        label.display = True

    def _show_error(self, message: str) -> None:
        self._show_loading(message)

    def _image_layout_available(self) -> bool:
        return self.layout_mode == "split" and self.size.width >= 120 and self.size.height >= 30

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
            self.query_one("#image-panel", ImagePanel).clear_image()
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
            or detail.identity != self.state.selected_id
            or detail.image is None
            or not self._image_layout_available()
        ):
            return
        self._image_worker = self.run_worker(
            lambda: self.image_loader.load(detail.image.path),
            name=f"image:{request_id}:{detail.identity}",
            group="image",
            thread=True,
            exit_on_error=False,
        )

    def _handle_image_result(self, decoded: Any, worker_name: str) -> None:
        parts = worker_name.split(":", 2)
        if len(parts) != 3 or not isinstance(decoded, DecodedImage):
            return
        if int(parts[1]) != self._image_request_id or parts[2] != self.state.selected_id:
            return
        self._image_worker = None
        widget = self.image_adapter.create_widget(decoded.image)
        if widget is None:
            self._clear_image()
            return
        panel = self.query_one("#image-panel", ImagePanel)
        panel.show_image(widget)
        self._image_widget = widget

    def _update_layout(self, width: int, height: int) -> None:
        if width < 50 or height < 16:
            self.layout_mode = "compact"
        elif width < 80:
            self.layout_mode = "stacked"
        else:
            self.layout_mode = "split"
        self.remove_class("-compact", "-stacked", "-split")
        self.add_class(f"-{self.layout_mode}")
        if not self._image_layout_available():
            self._clear_image()
        elif (
            self._current_detail is not None
            and self._image_widget is None
            and self._image_timer is None
        ):
            self._schedule_image(self._current_detail)
        browser = self.query_one("#browser", Horizontal)
        list_pane = self.query_one("#list-pane", Vertical)
        detail_pane = self.query_one("#detail-pane", Vertical)
        too_small = self.query_one("#too-small", Static)
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
                    fields.get("attunement_prerequisite")
                    or fields.get("requires_attunement"),
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
