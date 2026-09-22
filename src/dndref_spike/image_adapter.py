from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Mapping

from textual.widget import Widget


class ImageBackend(StrEnum):
    KITTY = "kitty"
    SIXEL = "sixel"
    NONE = "none"


@dataclass(frozen=True)
class ImageCapabilities:
    backend: ImageBackend
    available: bool
    reason: str


def _textual_image_module() -> ModuleType | None:
    """Import the optional integration before Textual starts its I/O threads."""
    try:
        return importlib.import_module("textual_image.widget")
    except (ImportError, ModuleNotFoundError):
        return None


def _requested_mode(mode: str | None, environ: Mapping[str, str]) -> str:
    requested = mode or environ.get("DNDREF_IMAGES", "auto")
    if requested not in {"auto", "off", "kitty", "sixel"}:
        return "auto"
    return requested


def _auto_backend(environ: Mapping[str, str]) -> ImageBackend:
    """Use conservative hints; auto never claims support from TERM alone."""
    if environ.get("KITTY_WINDOW_ID"):
        return ImageBackend.KITTY
    if environ.get("DNDREF_SIXEL", "").lower() in {"1", "true", "yes"}:
        return ImageBackend.SIXEL
    return ImageBackend.NONE


def detect_capabilities(
    mode: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> ImageCapabilities:
    """Select an image backend without making it a requirement for the TUI."""
    environment = os.environ if environ is None else environ
    requested = _requested_mode(mode, environment)
    if requested == "off":
        return ImageCapabilities(ImageBackend.NONE, False, "images disabled")

    module = _textual_image_module()
    if module is None:
        return ImageCapabilities(
            ImageBackend.NONE,
            False,
            "optional dependency textual-image is unavailable",
        )

    backend = _auto_backend(environment) if requested == "auto" else ImageBackend(requested)
    if backend is ImageBackend.NONE:
        return ImageCapabilities(ImageBackend.NONE, False, "terminal image capability is unknown")

    class_name = "TGPImage" if backend is ImageBackend.KITTY else "SixelImage"
    if not hasattr(module, class_name):
        return ImageCapabilities(
            ImageBackend.NONE,
            False,
            f"textual-image does not expose its {backend.value} widget",
        )
    return ImageCapabilities(backend, True, f"selected by {requested} mode")


class ImageAdapter:
    """Small optional adapter used only by this spike's detail panel."""

    def __init__(
        self, mode: str | None = None, *, environ: Mapping[str, str] | None = None
    ) -> None:
        self.capabilities = detect_capabilities(mode, environ=environ)
        self._module = _textual_image_module() if self.capabilities.available else None

    def create_widget(self, image_path: Path) -> Widget | None:
        if not self.capabilities.available or self._module is None:
            return None
        class_name = "TGPImage" if self.capabilities.backend is ImageBackend.KITTY else "SixelImage"
        image_class = getattr(self._module, class_name, None)
        if image_class is None:
            return None
        try:
            return image_class(str(image_path))
        except (OSError, ValueError, TypeError):
            return None
