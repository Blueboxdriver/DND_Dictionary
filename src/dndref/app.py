"""Compatibility entrypoint for the production Textual browser."""

from .ui.app import BrowserApp

FoundationApp = BrowserApp

__all__ = ["BrowserApp", "FoundationApp"]
