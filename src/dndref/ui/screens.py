"""Transient screens for the browser."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Markdown, Static

from .. import __version__
from ..images import ImageCapabilities
from ..models import display_edition
from ..navigation import ViewedRecord
from ..search import (
    DatasetMetadata,
    EditionOption,
    SearchCategory,
    SourceBrowseInfo,
    SourceIdentity,
    SourceOption,
)


class FilterScreen(ModalScreen[tuple[object, ...] | None]):
    """Keyboard-only multi-select screen for edition or source filters."""

    DEFAULT_CSS = """
    FilterScreen {
        align: center middle;
        background: $background 80%;
    }

    #filter-card {
        width: 72;
        max-width: 94%;
        height: auto;
        max-height: 88%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #filter-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    #filter-options {
        height: auto;
        max-height: 1fr;
        border: none;
        background: transparent;
    }

    #filter-options > ListItem {
        height: 2;
        padding: 0 1;
        background: transparent;
    }

    #filter-options > ListItem.--highlight {
        background: #4a3a20;
        color: #eee7d5;
    }

    #filter-help {
        height: 1;
        color: #aaa18e;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        kind: str,
        options: tuple[EditionOption, ...] | tuple[SourceOption, ...],
        selected: tuple[object, ...],
    ) -> None:
        self.kind = kind
        self.options = options
        self.working = list(selected)
        super().__init__()

    @property
    def _all_label(self) -> str:
        return "All Editions" if self.kind == "edition" else "All Sources"

    def compose(self) -> ComposeResult:
        with Vertical(id="filter-card"):
            yield Label(f"{self.kind.title()} filter", id="filter-title")
            with ListView(id="filter-options"):
                yield ListItem(Label(self._all_label), id="filter-option-0")
                for index, option in enumerate(self.options, start=1):
                    yield ListItem(
                        Label(self._option_label(option)), id=f"filter-option-{index}"
                    )
            if not self.options:
                empty_label = (
                    "No editions available for this category."
                    if self.kind == "edition"
                    else "No sources available for this edition selection."
                )
                yield Static(empty_label, classes="muted")
            yield Static("Space Toggle   Enter Apply   Esc Cancel", id="filter-help")

    def on_mount(self) -> None:
        options = self.query_one("#filter-options", ListView)
        selected_index = 0
        if self.working:
            selected_index = next(
                (
                    index + 1
                    for index, option in enumerate(self.options)
                    if self._option_value(option) in self.working
                ),
                0,
            )
        options.index = selected_index if options.children else None
        options.focus()
        self._render_options()

    def on_key(self, event: events.Key) -> None:
        options = self.query_one("#filter-options", ListView)
        if event.character in {"j", "k"}:
            event.stop()
            if event.character == "j":
                options.action_cursor_down()
            else:
                options.action_cursor_up()
            self.call_after_refresh(self._render_options)
        elif event.key == "space":
            event.stop()
            index = options.index
            if index == 0:
                self.working.clear()
            elif index is not None and index - 1 < len(self.options):
                value = self._option_value(self.options[index - 1])
                if value in self.working:
                    self.working.remove(value)
                else:
                    self.working.append(value)
            self._render_options()
        elif event.key in {"home", "end"}:
            event.stop()
            options.index = 0 if event.key == "home" else len(options.children) - 1
            self._render_options()
        elif event.key == "enter":
            event.stop()
            self.dismiss(tuple(self.working))
        elif event.key == "escape":
            event.stop()
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "filter-options":
            self._render_options()

    def _option_value(self, option: EditionOption | SourceOption) -> object:
        return option.value if isinstance(option, EditionOption) else option.identity

    def _option_label(self, option: EditionOption | SourceOption) -> str:
        if isinstance(option, EditionOption):
            return option.label
        edition = display_edition(option.edition)
        return f"{option.title} · {edition}" if edition else option.title

    def _render_options(self) -> None:
        options = self.query_one("#filter-options", ListView)
        for index, item in enumerate(options.children):
            if not isinstance(item, ListItem):
                continue
            if index == 0:
                checked = not self.working
                label = self._all_label
            else:
                option = self.options[index - 1]
                checked = self._option_value(option) in self.working
                label = self._option_label(option)
            marker = ">" if options.index == index else " "
            item.query_one(Label).update(f"{marker} {'[x]' if checked else '[ ]'} {label}")


class ChoiceScreen(ModalScreen[int | None]):
    """Single-choice keyboard selector with a visible text cursor."""

    DEFAULT_CSS = """
    ChoiceScreen { align: center middle; background: $background 80%; }
    #choice-card { width: 66; max-width: 94%; height: auto; max-height: 88%;
        padding: 1 2; border: round $accent; background: $surface; }
    #choice-list { height: auto; max-height: 1fr; border: none; }
    #choice-list > ListItem { height: 2; }
    #choice-list > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    #choice-preview { height: auto; max-height: 10; overflow-y: auto;
        border-top: solid #6c5530; padding: 1 1 0 1; color: #aaa18e; }
    #choice-help { height: 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self, title: str, labels: tuple[str, ...], selected: int = 0,
        *, previews: tuple[str, ...] = (), mark_selected: bool = False,
    ) -> None:
        self.choice_title = title
        self.labels = labels
        self.selected = selected
        self.previews = previews
        self.mark_selected = mark_selected
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="choice-card"):
            yield Label(self.choice_title)
            with ListView(id="choice-list"):
                for label in self.labels:
                    yield ListItem(Label(label))
            if self.previews:
                yield Static("", id="choice-preview")
            yield Static("Enter Select   Esc Cancel", id="choice-help")

    def on_mount(self) -> None:
        options = self.query_one("#choice-list", ListView)
        options.index = min(self.selected, len(self.labels) - 1) if self.labels else None
        options.focus()
        self._render_rows()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "choice-list":
            self._render_rows()

    def on_key(self, event: events.Key) -> None:
        options = self.query_one("#choice-list", ListView)
        if event.character in {"j", "k"}:
            event.stop()
            (options.action_cursor_down if event.character == "j" else options.action_cursor_up)()
            self.call_after_refresh(self._render_rows)
        elif event.key == "enter":
            event.stop()
            self.dismiss(options.index)
        elif event.key == "escape":
            event.stop()
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _render_rows(self) -> None:
        options = self.query_one("#choice-list", ListView)
        for index, item in enumerate(options.children):
            check = f"{'[x]' if index == self.selected else '[ ]'} " if self.mark_selected else ""
            item.query_one(Label).update(
                f"{'> ' if index == options.index else '  '}{check}{self.labels[index]}"
            )
        if self.previews and options.index is not None:
            self.query_one("#choice-preview", Static).update(self.previews[options.index])


class SourceBrowserScreen(ModalScreen[tuple[SourceIdentity, SearchCategory] | None]):
    """Source list followed by categories; selected category reuses normal search."""

    DEFAULT_CSS = """
    SourceBrowserScreen { align: center middle; background: $background 80%; }
    #source-card { width: 72; max-width: 94%; height: auto; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface; }
    #source-browser-list { height: auto; max-height: 1fr; border: none; }
    #source-browser-list > ListItem { height: 2; }
    #source-browser-list > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    #source-detail { height: 5; color: #aaa18e; }
    """
    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, sources: tuple[SourceBrowseInfo, ...]) -> None:
        self.sources = sources
        self.stage = "sources"
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="source-card"):
            yield Label("Browse sources", id="source-browser-title")
            with ListView(id="source-browser-list"):
                for source in self.sources:
                    yield ListItem(Label(source.source.title))
            yield Static("", id="source-detail")
            yield Static("Enter Open   Esc Back   j/k Navigate", id="source-browser-help")

    def on_mount(self) -> None:
        options = self.query_one("#source-browser-list", ListView)
        options.index = 0 if self.sources else None
        options.focus()
        self._render_rows()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "source-browser-list":
            self._render_rows()

    def on_key(self, event: events.Key) -> None:
        options = self.query_one("#source-browser-list", ListView)
        if event.character in {"j", "k"}:
            event.stop()
            (options.action_cursor_down if event.character == "j" else options.action_cursor_up)()
            self.call_after_refresh(self._render_rows)
        elif event.key == "enter":
            event.stop()
            index = options.index
            if index is None:
                return
            if self.stage == "sources":
                self._open_categories(index)
            else:
                source = self.sources[self._source_index]
                categories = tuple(source.counts)
                if index < len(categories):
                    self.dismiss((source.source.identity, categories[index]))
        elif event.key == "escape":
            event.stop()
            self.action_back()

    def action_back(self) -> None:
        if self.stage == "categories":
            self.stage = "sources"
            self.query_one("#source-browser-title", Label).update("Browse sources")
            self._replace_rows(
                tuple(source.source.title for source in self.sources), self._source_index
            )
        else:
            self.dismiss(None)

    def _open_categories(self, index: int) -> None:
        self._source_index = index
        source = self.sources[index]
        self.stage = "categories"
        self.query_one("#source-browser-title", Label).update(source.source.title)
        self._replace_rows(
            tuple(
                f"{category.value.title()}  {count}"
                for category, count in source.counts.items()
            ),
            0,
        )

    def _replace_rows(self, labels: tuple[str, ...], index: int) -> None:
        async def replace() -> None:
            options = self.query_one("#source-browser-list", ListView)
            await options.clear()
            for label in labels:
                await options.mount(ListItem(Label(label)))
            options.index = index if labels else None
            options.focus()
            self._render_rows()

        self.run_worker(replace(), exclusive=True)

    def _render_rows(self) -> None:
        options = self.query_one("#source-browser-list", ListView)
        index = options.index
        if self.stage == "sources":
            labels = tuple(source.source.title for source in self.sources)
            if index is not None and index < len(self.sources):
                source = self.sources[index]
                edition = display_edition(source.source.edition) or "Unknown edition"
                counts = " · ".join(
                    f"{category.value.title()} {count}" for category, count in source.counts.items()
                )
                detail = f"{source.source.title}\n{edition}\n{counts or 'No categories available.'}"
            else:
                detail = "No sources installed."
        else:
            source = self.sources[self._source_index]
            labels = tuple(
                f"{category.value.title()}  {count}" for category, count in source.counts.items()
            )
            detail = (
                f"{source.source.title}\n"
                f"{display_edition(source.source.edition) or 'Unknown edition'}\n"
                "Choose a category to browse."
            )
        self.query_one("#source-detail", Static).update(detail)
        for row_index, item in enumerate(options.children):
            if row_index < len(labels):
                try:
                    item.query_one(Label).update(
                        f"{'> ' if row_index == index else '  '}{labels[row_index]}"
                    )
                except NoMatches:
                    pass


class RecentlyViewedScreen(ModalScreen[str | None]):
    """Select an in-memory recently viewed record by its stable identity."""

    DEFAULT_CSS = """
    RecentlyViewedScreen { align: center middle; background: $background 80%; }
    #recent-card { width: 72; max-width: 94%; height: auto; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface; }
    #recent-list { height: auto; max-height: 1fr; border: none; }
    #recent-list > ListItem { height: 2; }
    #recent-list > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, records: tuple[ViewedRecord, ...]) -> None:
        self.records = records
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="recent-card"):
            yield Label("Recently Viewed")
            with ListView(id="recent-list"):
                for record in self.records:
                    edition = record.edition or "Unknown edition"
                    yield ListItem(Label(
                        f"{record.name}  ·  {record.category.value.title()}  ·  {edition}"
                    ))
            yield Static("Enter Open   Esc Close   j/k Navigate")

    def on_mount(self) -> None:
        options = self.query_one("#recent-list", ListView)
        options.index = 0 if self.records else None
        self._render_rows()
        options.focus()

    def on_resize(self, _event: events.Resize) -> None:
        self._render_rows()

    def _render_rows(self) -> None:
        options = self.query_one("#recent-list", ListView)
        name_width = max(8, self.size.width - 34)
        for item, record in zip(options.children, self.records):
            name = record.name
            if len(name) > name_width:
                name = name[: max(1, name_width - 1)] + "…"
            category = record.category.value.title()
            edition = record.edition or "Unknown"
            item.query_one(Label).update(f"{name}  {category}  {edition}")

    def on_key(self, event: events.Key) -> None:
        options = self.query_one("#recent-list", ListView)
        if event.character in {"j", "k"}:
            event.stop()
            (options.action_cursor_down if event.character == "j" else options.action_cursor_up)()
        elif event.key == "enter":
            event.stop()
            index = options.index
            self.dismiss(self.records[index].identity if index is not None else None)
        elif event.key == "escape":
            event.stop()
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Compact keyboard reference."""

    KEYBOARD_HELP = """# D&D Reference — Help

**Search**

`/` or `Ctrl+F`  Focus search  
`F2`  Toggle Names / All text  
`1`–`6`  Select Items / Spells / Feats / Classes / Subclasses / Monsters
`e` Edition filter · `s` Source filter
`f` Parent class filter in Subclasses
`c` CR · `t` creature type · `z` size filter in Monsters
`p` Filter presets · `b` Browse sources
`g` Group alternates on/off · `v` Select source variant
`i` Toggle artwork when available
`r` Recently Viewed

Filter dialogs show `>` for the active row and `[x]` for a selected row.
Space toggles a filter choice; Enter applies; Escape cancels.

**Navigation**

`Tab` / `Shift+Tab`  Change focus  
`Up`/`Down` or `j`/`k`  Navigate or scroll  
`PageUp`/`PageDown`  Page through the focused pane  
`Left`/`Right` or `h`/`l`  Scroll a focused class progression table horizontally  
`Home`/`End`  Start or end  
`Enter`  Open or select  
On a class page, Tab to Subclasses and Enter to open one.
On a subclass page, `c` opens its matching edition parent class.
Monster spell names in structured spellcasting lists open same-edition spells.
Multiple same-edition source variants show a chooser. Class/subclass links use
their stored parent relationship. Arbitrary prose is intentionally not auto-linked.
`Alt+Left` Back · `Alt+Right` Forward through visited records and browser states.
`Escape`  Back, close, or leave search  

`?` / `F1`  Help  
`F3`  About / installed dataset data  
`q`  Quit when not editing  
`Ctrl+Q`  Quit globally

**Images**

Set `[ui].images` in `config.toml` to `auto`, `off`, `kitty`, or `sixel`.
`dndref --images MODE` overrides that setting for the current session. Images
are local imported assets; missing dependencies, unsupported terminals, and
image failures collapse the artwork panel while text browsing continues.
"""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: $background 80%;
    }

    #help-card {
        width: 64;
        max-width: 90%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #help-copy {
        height: auto;
        max-height: 1fr;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("f1", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="help-card"):
            yield Markdown(self.KEYBOARD_HELP, id="help-copy")


class AboutScreen(ModalScreen[None]):
    """Installed dataset and image backend information."""

    DEFAULT_CSS = """
    AboutScreen {
        align: center middle;
        background: $background 80%;
    }

    #about-card {
        width: 78;
        max-width: 94%;
        height: auto;
        max-height: 92%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #about-copy {
        height: auto;
        max-height: 1fr;
        overflow-y: auto;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close"), ("f3", "dismiss", "Close")]

    def __init__(self, capabilities: ImageCapabilities, status: str | None = None) -> None:
        self.capabilities = capabilities
        self.status = status or capabilities.reason
        self.datasets: tuple[DatasetMetadata, ...] = ()
        super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="about-card"):
            yield Markdown(self._render_copy(), id="about-copy")

    def set_datasets(self, datasets: tuple[DatasetMetadata, ...]) -> None:
        self.datasets = datasets
        if self.is_mounted:
            self.query_one("#about-copy", Markdown).update(self._render_copy())

    def _render_copy(self) -> str:
        backend = self.capabilities.backend.value if self.capabilities.available else "none"
        lines = [
            "# D&D Reference — About / Data",
            f"Application version: `{__version__}`",
            f"Image mode: `{self.capabilities.requested_mode}` · backend: `{backend}`",
            f"Image status: {self.status}",
            "",
            "Artwork metadata: no artwork attribution is recorded by the installed packs.",
            "",
            "## Installed datasets",
        ]
        if not self.datasets:
            lines.append("No datasets are installed. Use `dndref import PATH`.")
        for dataset in self.datasets:
            kind = "SRD pack" if dataset.dataset_id == "srd-5-2-1" else "Separate content pack"
            lines.extend(
                (
                    f"### {dataset.title} ({kind})",
                    f"ID: `{dataset.dataset_id}` · version: `{dataset.version}`",
                    f"Ruleset: {dataset.ruleset} · language: {dataset.language}",
                    f"License identifier: `{dataset.license_identifier}`",
                    f"Attribution: {dataset.attribution}",
                )
            )
            if dataset.origin_url:
                lines.append(f"Origin: {dataset.origin_url}")
            if dataset.sources:
                lines.append("Sources / books:")
                lines.extend(
                    f"- {source.title}"
                    + (f" ({source.edition})" if source.edition else "")
                    + (f" — {source.citation}" if source.citation else "")
                    for source in dataset.sources
                )
            lines.append("")
        lines.extend(("Press `Escape` or `F3` to close.",))
        return "\n".join(lines)
