"""Small widgets used by the production browser."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Label, ListItem, Static

from ..search import EntrySummary


class ResultRow(ListItem):
    """A result row that keeps its search summary with the widget."""

    def __init__(self, summary: EntrySummary, *, id: str | None = None) -> None:
        self.summary = summary
        self._selected = False
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield Label(self.summary.name, classes="result-name")
        yield Label(self.summary.subtitle, classes="result-meta")
        yield Label(self.summary.dataset_title, classes="result-source")

    def set_selected(self, selected: bool) -> None:
        """Show selection with both a marker and styling."""
        self._selected = selected
        self.set_class(selected, "selected")
        try:
            self.query_one(".result-name", Label).update(
                ("> " if selected else "  ") + self.summary.name
            )
        except NoMatches:
            pass

    def on_mount(self) -> None:
        self.set_selected(self._selected)


class ImagePanel(Static):
    """Stationary detail artwork panel; it never owns scrolling or selection."""

    def clear_image(self) -> None:
        self.remove_children()
        self.display = False

    def show_image(self, widget: Widget) -> None:
        self.remove_children()
        self.mount(widget)
        self.display = True
