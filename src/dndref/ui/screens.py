"""Transient screens for the browser."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Markdown

from .. import __version__
from ..images import ImageCapabilities
from ..search import DatasetMetadata


class HelpScreen(ModalScreen[None]):
    """Compact keyboard reference."""

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
            yield Markdown(
                """# D&D Reference — Help

**Search**

`/` or `Ctrl+F`  Focus search  
`F2`  Toggle Names / All text  
`1`–`4`  Select Items / Spells / Feats / Classes  

**Navigation**

`Tab` / `Shift+Tab`  Change focus  
`Up`/`Down` or `j`/`k`  Navigate or scroll  
`PageUp`/`PageDown`  Page through the focused pane  
`Left`/`Right` or `h`/`l`  Scroll a focused class progression table horizontally  
`Home`/`End`  Start or end  
`Enter`  Open or select  
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
""",
                id="help-copy",
            )


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

    def __init__(self, capabilities: ImageCapabilities) -> None:
        self.capabilities = capabilities
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
            f"Image status: {self.capabilities.reason}",
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
