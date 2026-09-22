from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from .image_adapter import ImageAdapter

ASSET_PATH = Path(__file__).resolve().parents[2] / "assets" / "placeholder.ppm"


@dataclass(frozen=True)
class DemoEntry:
    key: str
    name: str
    category: str
    subtitle: str
    description: str
    image: bool = False


DEMO_ENTRIES = (
    DemoEntry(
        "emberglass-compass",
        "Emberglass Compass",
        "Relic",
        "Wondrous item · uncommon",
        "A thumb-sized compass whose needle points toward the nearest open flame. "
        "It warms gently when its bearer is within a day's travel of a place where "
        "a dragon has slept.",
        True,
    ),
    DemoEntry(
        "moonmoth-potion",
        "Moonmoth Draught",
        "Consumable",
        "Potion · curious",
        "For one hour after drinking, the imbiber can see the pale trails left by creatures "
        "that crossed a room under moonlight. The effect ends early if bright sunlight "
        "touches the drinker.",
    ),
    DemoEntry(
        "thunder-knot",
        "Thunder Knot",
        "Trinket",
        "Charm · loud",
        "This blue cord snaps with a tiny clap whenever it is tied. Once per day, its owner may "
        "untie it to make a thunderous report audible across a small courtyard.",
    ),
    DemoEntry(
        "mossback-ward",
        "Mossback Ward",
        "Talisman",
        "Warding token · common",
        "Pressed leaves cover this wooden disk. While carried, the bearer has advantage on the "
        "first saving throw made against being startled by a natural creature.",
    ),
)


class SpikeApp(App[None]):
    """Disposable list/detail layout for validating Textual before production work."""

    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (80, "-split"), (120, "-wide")]

    CSS = """
    Screen {
        background: #171719;
        color: #eee7d5;
    }

    Header {
        background: #28231b;
        color: #f1c96b;
    }

    #main {
        height: 1fr;
        padding: 0 1;
    }

    #list-pane {
        width: 30%;
        min-width: 24;
        max-width: 36;
        border: round #6c5530;
        padding: 0 1;
    }

    #detail-pane {
        width: 1fr;
        border: round #6c5530;
        padding: 1 2;
        margin-left: 1;
    }

    #detail-copy {
        width: 1fr;
        height: 1fr;
    }

    #image-panel {
        display: none;
        width: 28;
        height: 14;
        margin-left: 2;
        border: round #6c5530;
        align: center middle;
    }

    #image-panel Image, #image-panel TGPImage, #image-panel SixelImage {
        width: 24;
        height: 12;
    }

    #title {
        color: #f1c96b;
        text-style: bold;
        margin-bottom: 1;
    }

    .muted {
        color: #aaa18e;
    }

    #description {
        margin-top: 1;
    }

    #status {
        color: #aaa18e;
        height: 1;
        padding: 0 2;
    }

    #too-small {
        display: none;
        height: 1fr;
        content-align: center middle;
        text-align: center;
        padding: 2;
        color: #f1c96b;
    }

    ListView:focus {
        border: none;
    }

    ListItem {
        padding: 1 0;
    }

    Screen.-narrow #main {
        layout: vertical;
    }

    Screen.-narrow #list-pane {
        width: 1fr;
        max-width: 1fr;
        height: 11;
    }

    Screen.-narrow #detail-pane {
        width: 1fr;
        height: 1fr;
        margin-left: 0;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("j", "navigate_down", "Next entry"),
        ("k", "navigate_up", "Previous entry"),
    ]

    def __init__(self, image_mode: str = "auto") -> None:
        self.image_adapter = ImageAdapter(image_mode)
        self.selected_entry = DEMO_ENTRIES[0]
        self.layout_mode = "unknown"
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("D&D Reference spike · original placeholder content", id="status")
        with Horizontal(id="main"):
            with Vertical(id="list-pane"):
                yield Label("Entries", classes="muted")
                with ListView(id="entry-list"):
                    for entry in DEMO_ENTRIES:
                        yield ListItem(
                            Label(f"{entry.name}\n[dim]{entry.subtitle}[/dim]"),
                            id=f"entry-{entry.key}",
                        )
            with Horizontal(id="detail-pane"):
                with VerticalScroll(id="detail-copy"):
                    yield Label(id="title")
                    yield Label(id="subtitle", classes="muted")
                    yield Static(id="description")
                with Container(id="image-panel"):
                    yield Label("Artwork unavailable", id="image-placeholder", classes="muted")
        yield Label(
            "↑/↓ or j/k navigate · resize to test layout · q quit",
            id="too-small",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#entry-list", ListView).focus()
        self._show_entry(self.selected_entry)
        self._update_layout(self.size.width, self.size.height)
        self._mount_image_if_possible()

    def on_resize(self, event: Resize) -> None:
        self._update_layout(event.size.width, event.size.height)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._select_item(event.item)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._select_item(event.item)

    def _select_item(self, item: ListItem | None) -> None:
        if item is None:
            return
        entry_id = item.id
        if entry_id is None:
            return
        self.selected_entry = next(
            entry for entry in DEMO_ENTRIES if entry_id == f"entry-{entry.key}"
        )
        self._show_entry(self.selected_entry)

    def action_navigate_down(self) -> None:
        self.query_one("#entry-list", ListView).action_cursor_down()

    def action_navigate_up(self) -> None:
        self.query_one("#entry-list", ListView).action_cursor_up()

    def _show_entry(self, entry: DemoEntry) -> None:
        self.query_one("#title", Label).update(entry.name)
        self.query_one("#subtitle", Label).update(f"{entry.category} · {entry.subtitle}")
        self.query_one("#description", Static).update(entry.description)

    def _update_layout(self, width: int, height: int) -> None:
        image_panel = self.query_one("#image-panel", Container)
        if width < 50 or height < 16:
            self.layout_mode = "compact"
            self.query_one("#main").display = False
            self.query_one("#too-small").display = True
        elif width < 80:
            self.layout_mode = "stacked"
            self.query_one("#main").display = True
            self.query_one("#too-small").display = False
        elif width >= 120 and height >= 30:
            self.layout_mode = "wide"
            self.query_one("#main").display = True
            self.query_one("#too-small").display = False
        else:
            self.layout_mode = "split"
            self.query_one("#main").display = True
            self.query_one("#too-small").display = False
        image_panel.display = (
            self.layout_mode == "wide"
            and height >= 30
            and self.image_adapter.capabilities.available
        )

    def _mount_image_if_possible(self) -> None:
        if not self.image_adapter.capabilities.available:
            return
        image_panel = self.query_one("#image-panel", Container)
        widget = self.image_adapter.create_widget(ASSET_PATH)
        if widget is None:
            self.image_adapter = ImageAdapter("off")
            image_panel.display = False
            return
        self.query_one("#image-placeholder").display = False
        image_panel.mount(widget)


if __name__ == "__main__":
    SpikeApp().run()
