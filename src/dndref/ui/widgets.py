"""Small widgets used by the production browser."""

from __future__ import annotations

import re
from textwrap import shorten

from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Label, ListItem, Static

from ..search import EntrySummary, GroupedEntrySummary


def _source_display_label(summary: EntrySummary) -> str:
    """Show the sourcebook title compactly; dataset/provider names stay separate."""
    label = summary.source_label.strip()
    edition = summary.source_edition
    if edition:
        label = re.sub(rf"\s*\({re.escape(edition)}\)", "", label).strip()
        title_has_version = any(character.isdigit() for character in label)
        if edition not in label and not title_has_version:
            label = f"{label} {edition}".strip()
    if len(label) > 28:
        tokens = label.split()
        first_number = next(
            (index for index, token in enumerate(tokens) if any(char.isdigit() for char in token)),
            len(tokens),
        )
        title_words = tokens[:first_number]
        if len(title_words) >= 3 and first_number < len(tokens):
            initials = "".join(word[0].upper() for word in title_words if word)
            label = " ".join((initials, *tokens[first_number:]))
    return shorten(label, width=28, placeholder="…")


class ResultRow(ListItem):
    """A result row that keeps its search summary with the widget."""

    def __init__(
        self, summary: EntrySummary | GroupedEntrySummary, *, id: str | None = None
    ) -> None:
        self.summary = summary
        self._selected = False
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        primary = (
            self.summary.primary
            if isinstance(self.summary, GroupedEntrySummary)
            else self.summary
        )
        yield Label(self.summary.name, classes="result-name")
        yield Label(primary.subtitle, classes="result-meta")
        source = _source_display_label(primary)
        if isinstance(self.summary, GroupedEntrySummary) and self.summary.alternates:
            count = len(self.summary.alternates)
            source += f" · +{count} source" + ("s" if count != 1 else "")
        yield Label(source, classes="result-source")

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
