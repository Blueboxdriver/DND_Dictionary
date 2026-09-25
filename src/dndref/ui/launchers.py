"""Keyboard-first Universal Search and Command Palette overlays."""

from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Input, Label, ListItem, ListView, Static
from textual.worker import Worker, WorkerState

from ..commands import Command
from ..navigation import ViewedRecord
from ..search import EntrySummary, SearchPage, SearchService


@dataclass(frozen=True)
class LaunchOption:
    kind: str
    payload: object
    label: str
    rank: int = 0


class LaunchRow(ListItem):
    def __init__(self, option: LaunchOption) -> None:
        self.option = option
        self._display_label = Label(option.label)
        super().__init__(self._display_label)

    def set_display_text(self, text: str) -> None:
        """Update the row's owned label without querying its changing child tree."""
        self._display_label.update(text)


class UniversalSearchScreen(ModalScreen[tuple[object, ...] | None]):
    """Search all reference categories and retain query/selection for history."""

    DEFAULT_CSS = """
    UniversalSearchScreen { align: center middle; background: $background 82%; }
    #universal-card { width: 88; max-width: 94%; height: 90%; max-height: 94%;
        padding: 1 2; border: round $accent; background: $surface; }
    #universal-input { height: 3; }
    #universal-list { height: 1fr; border: none; margin-top: 1; }
    #universal-list > ListItem { height: 2; }
    #universal-list > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    #universal-status { height: 1; color: $text-muted; }
    """

    BINDINGS = [
        ("escape", "cancel", "Close"),
        ("alt+left", "go_back", "Back"),
        ("alt+right", "go_forward", "Forward"),
        ("ctrl+l", "clear_history", "Clear Recent Searches"),
    ]

    def __init__(
        self,
        service: SearchService,
        recent_searches: tuple[str, ...],
        recently_viewed: tuple[ViewedRecord, ...],
        *,
        query: str = "",
        selected_index: int = 0,
        scroll: int = 0,
        clear_history: bool = False,
    ) -> None:
        self.service = service
        self.recent_searches = recent_searches
        self.recently_viewed = recently_viewed
        self.initial_query = query
        self.initial_index = selected_index
        self.initial_scroll = scroll
        self.clear_history = clear_history
        self._timer: Timer | None = None
        self._request = 0
        self._options: list[LaunchOption] = []
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="universal-card"):
            yield Label("Search All  ·  Ctrl+K")
            yield Input(
                self.initial_query,
                placeholder="Search references · spell:fireball · source:XMM dragon",
                id="universal-input",
            )
            yield Static("", id="universal-status")
            yield ListView(id="universal-list")
            yield Static(
                "↑↓ Results   Enter Open   Esc Close\nCtrl+L Clear recent searches",
                id="universal-help",
            )

    def on_mount(self) -> None:
        self.query_one("#universal-input", Input).focus()
        if self.initial_query.strip():
            self._queue_search(self.initial_query)
        else:
            self._show_empty_query()

    def on_resize(self, _event: events.Resize) -> None:
        self._render_labels()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "universal-input":
            if event.value.strip():
                self._queue_search(event.value)
            else:
                self._request += 1
                self._show_empty_query()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "universal-input":
            self._open_selected()

    def _queue_search(self, query: str) -> None:
        if self._timer:
            self._timer.stop()
        self._request += 1
        request = self._request
        self._timer = self.set_timer(0.18, lambda: self._start_search(request, query))

    def _start_search(self, request: int, query: str) -> None:
        self.query_one("#universal-status", Static).update("Searching…")
        self.run_worker(
            lambda: self.service.search_all(query, limit=50),
            name=f"universal:{request}",
            group="universal",
            thread=True,
            exit_on_error=False,
        )

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        worker = event.worker
        if event.state != WorkerState.SUCCESS or not worker.name.startswith("universal:"):
            return
        request = int(worker.name.split(":", 1)[1])
        if request != self._request or not isinstance(worker.result, SearchPage):
            return
        self._options = [
            LaunchOption("entry", row, self._entry_label(row))
            for row in worker.result.results
            if isinstance(row, EntrySummary)
        ]
        total = worker.result.total_count
        visible = len(self._options)
        self.query_one("#universal-status", Static).update(
            f"{visible} of {total} results" if total > visible else f"{total} results"
        )
        await self._replace_rows()

    def _show_empty_query(self) -> None:
        self._options = []
        for query in self.recent_searches:
            self._options.append(LaunchOption("search", query, f"Recent Search  ·  {query}"))
        for record in self.recently_viewed:
            record_label = (
                f"Recently Viewed  ·  {record.name}  ·  {record.category.value.title()}  ·  "
                f"{record.edition or 'Unknown edition'}"
            )
            self._options.append(LaunchOption("recent", record.identity, record_label))
        self.query_one("#universal-status", Static).update(
            "Recent Searches and Recently Viewed" if self._options else "Type to search references"
        )
        self.run_worker(self._replace_rows(), exclusive=True)

    async def _replace_rows(self) -> None:
        options = self.query_one("#universal-list", ListView)
        old_index = options.index or 0
        await options.clear()
        for option in self._options:
            await options.mount(LaunchRow(option))
        if self._options:
            options.index = min(
                self.initial_index if self.initial_query else old_index, len(self._options) - 1
            )
            if self.initial_query:
                options.scroll_y = self.initial_scroll
        else:
            options.index = None
        self._render_labels()

    def _render_labels(self) -> None:
        if not self.is_mounted:
            return
        width = max(24, self.size.width - 12)
        for index, row in enumerate(self.query_one("#universal-list", ListView).children):
            if isinstance(row, LaunchRow):
                label = row.option.label
                highlighted = (
                    row.parent.index == index if isinstance(row.parent, ListView) else False
                )
                row.set_display_text(
                    ("> " if highlighted else "  ")
                    + (label[: width - 1] + "…" if len(label) > width else label)
                )

    @staticmethod
    def _entry_label(entry: EntrySummary) -> str:
        return (
            f"{entry.name}  ·  {entry.category.value.title()}  ·  "
            f"{entry.source_edition or 'Unknown edition'}  ·  {entry.source_label}"
        )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "universal-list":
            self._render_labels()

    def on_key(self, event: events.Key) -> None:
        if event.key in {"alt+left", "alt+right"}:
            event.stop()
            self.dismiss(("history", event.key))
            return
        if event.key == "ctrl+l":
            event.stop()
            options = self.query_one("#universal-list", ListView)
            self.dismiss(
                (
                    "clear",
                    self.query_one("#universal-input", Input).value,
                    options.index or 0,
                    int(options.scroll_y),
                )
            )
            return
        if event.key == "enter":
            event.stop()
            self._open_selected()
            return
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

    def _open_selected(self) -> None:
        options = self.query_one("#universal-list", ListView)
        index = options.index
        query = self.query_one("#universal-input", Input).value
        if index is not None and index < len(self._options):
            option = self._options[index]
            if option.kind in {"entry", "recent"}:
                identity = (
                    option.payload.stable_id
                    if isinstance(option.payload, EntrySummary)
                    else option.payload
                )
                self.dismiss(("entry", identity, query, index, int(options.scroll_y)))
            else:
                self.query_one("#universal-input", Input).value = str(option.payload)
                self.query_one("#universal-input", Input).focus()
        elif query.strip():
            self.dismiss(("search", query))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_go_back(self) -> None:
        self.dismiss(("history", "alt+left"))

    def action_go_forward(self) -> None:
        self.dismiss(("history", "alt+right"))

    def action_clear_history(self) -> None:
        options = self.query_one("#universal-list", ListView)
        self.dismiss(
            (
                "clear",
                self.query_one("#universal-input", Input).value,
                options.index or 0,
                int(options.scroll_y),
            )
        )


class CommandPaletteScreen(ModalScreen[tuple[str, object] | None]):
    """Searchable command registry with indexed direct-entry matches."""

    DEFAULT_CSS = """
    CommandPaletteScreen { align: center middle; background: $background 82%; }
    #palette-card { width: 88; max-width: 94%; height: 88%; max-height: 94%;
        padding: 1 2; border: round $accent; background: $surface; }
    #palette-input { height: 3; }
    #palette-list { height: 1fr; border: none; margin-top: 1; }
    #palette-list > ListItem { height: 2; }
    #palette-list > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    #palette-status { height: 1; color: $text-muted; }
    """

    BINDINGS = [("escape", "cancel", "Close")]

    def __init__(
        self,
        service: SearchService,
        commands: tuple[Command, ...],
        *,
        default_command_ids: tuple[str, ...] | None = None,
    ) -> None:
        self.service = service
        self.commands = commands
        self.default_command_ids = default_command_ids
        self._options: list[LaunchOption] = []
        self._timer: Timer | None = None
        self._request = 0
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-card"):
            yield Label("Commands  ·  Ctrl+P")
            yield Input(placeholder="Search commands or reference names", id="palette-input")
            yield Static("", id="palette-status")
            yield ListView(id="palette-list")
            yield Static("Enter Select   Esc Close", id="palette-help")

    def on_mount(self) -> None:
        self._filter("")
        self.query_one("#palette-input", Input).focus()

    def on_resize(self, _event: events.Resize) -> None:
        self._render_labels()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-input":
            query = event.value
            self._filter(query)
            if self._timer:
                self._timer.stop()
            if query.strip():
                self._request += 1
                request = self._request
                self._timer = self.set_timer(0.18, lambda: self._start_entries(request, query))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-input":
            self._open_selected()

    def _filter(self, query: str) -> None:
        needle = query.casefold().strip()
        options: list[LaunchOption] = []
        default_order = {
            command_id: index for index, command_id in enumerate(self.default_command_ids or ())
        }
        for index, command in enumerate(self.commands):
            words = (command.name, *command.aliases)
            if not needle:
                if self.default_command_ids is not None and command.command_id not in default_order:
                    continue
                rank = default_order.get(command.command_id, index)
            elif any(word.casefold() == needle for word in words):
                rank = 0
            elif any(
                needle in word.casefold() or all(c in word.casefold() for c in needle)
                for word in words
            ):
                rank = 2
            else:
                continue
            options.append(LaunchOption("command", command.command_id, command.name, rank))
        self._options = sorted(
            options,
            key=lambda option: (
                option.rank,
                option.label.casefold() if needle else "",
            ),
        )
        self.query_one("#palette-status", Static).update(
            "Common actions" if not needle else "Commands and matching references"
        )
        self.run_worker(self._replace_rows(), exclusive=True)

    def _start_entries(self, request: int, query: str) -> None:
        self.run_worker(
            lambda: self.service.search_all(query, limit=50),
            name=f"palette:{request}",
            group="palette-search",
            thread=True,
            exit_on_error=False,
        )

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        worker = event.worker
        if event.state != WorkerState.SUCCESS or not worker.name.startswith("palette:"):
            return
        request = int(worker.name.split(":", 1)[1])
        if request != self._request or not isinstance(worker.result, SearchPage):
            return
        query = self.query_one("#palette-input", Input).value.strip()
        entries = [
            LaunchOption(
                "entry",
                row.stable_id,
                (
                    f"Entry  ·  {row.name}  ·  {row.category.value.title()}  ·  "
                    f"{row.source_edition or 'Unknown'}  ·  {row.source_label}"
                ),
                1 if row.name.casefold() == query.casefold() else 3,
            )
            for row in worker.result.results
            if isinstance(row, EntrySummary)
        ]
        self._options.extend(entries)
        self._options.sort(key=lambda option: (option.rank, option.label.casefold()))
        self.query_one("#palette-status", Static).update(
            f"{len(self._options)} commands or entries"
        )
        await self._replace_rows()

    async def _replace_rows(self) -> None:
        options = self.query_one("#palette-list", ListView)
        await options.clear()
        for option in self._options:
            await options.mount(LaunchRow(option))
        options.index = 0 if self._options else None
        self._render_labels()

    def _render_labels(self) -> None:
        if not self.is_mounted:
            return
        width = max(20, self.size.width - 12)
        options = self.query_one("#palette-list", ListView)
        for index, row in enumerate(options.children):
            if isinstance(row, LaunchRow):
                label = row.option.label
                highlighted = options.index == index
                row.set_display_text(
                    ("> " if highlighted else "  ")
                    + (label[: width - 1] + "…" if len(label) > width else label)
                )

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            self._open_selected()
        elif event.key == "escape":
            event.stop()
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _open_selected(self) -> None:
        options = self.query_one("#palette-list", ListView)
        index = options.index
        if index is None or index >= len(self._options):
            return
        option = self._options[index]
        self.dismiss((option.kind, option.payload))
