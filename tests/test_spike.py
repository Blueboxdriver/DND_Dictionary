from __future__ import annotations

import importlib

import pytest

from dndref_spike.app import ASSET_PATH, DEMO_ENTRIES, SpikeApp
from dndref_spike.image_adapter import ImageAdapter, ImageBackend, detect_capabilities


@pytest.mark.asyncio
async def test_keyboard_navigation_updates_detail() -> None:
    async with SpikeApp(image_mode="off").run_test(size=(80, 24)) as pilot:
        assert pilot.app.selected_entry == DEMO_ENTRIES[0]
        await pilot.press("down")
        await pilot.pause()
        assert pilot.app.selected_entry == DEMO_ENTRIES[1]
        assert "Moonmoth Draught" in pilot.app.query_one("#title").renderable

        await pilot.press("j")
        await pilot.pause()
        assert pilot.app.selected_entry == DEMO_ENTRIES[2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "expected_mode"),
    [((80, 24), "split"), ((100, 30), "split"), ((140, 40), "wide"), ((60, 20), "stacked")],
)
async def test_layout_reacts_to_planned_terminal_sizes(
    size: tuple[int, int], expected_mode: str
) -> None:
    async with SpikeApp(image_mode="off").run_test(size=size) as pilot:
        assert pilot.app.layout_mode == expected_mode
        assert pilot.app.query_one("#title").renderable == "Emberglass Compass"


@pytest.mark.asyncio
async def test_compact_terminal_keeps_help_and_hides_main_layout() -> None:
    async with SpikeApp(image_mode="off").run_test(size=(49, 15)) as pilot:
        assert pilot.app.layout_mode == "compact"
        assert pilot.app.query_one("#main").display is False
        assert pilot.app.query_one("#too-small").display is True


@pytest.mark.asyncio
async def test_image_disabled_keeps_text_layout_and_hides_artwork() -> None:
    async with SpikeApp(image_mode="off").run_test(size=(140, 40)) as pilot:
        assert pilot.app.image_adapter.capabilities.backend is ImageBackend.NONE
        assert pilot.app.query_one("#image-panel").display is False
        assert pilot.app.query_one("#description").renderable


def test_off_mode_skips_optional_import(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_import() -> None:
        raise AssertionError("image dependency should not be imported in off mode")

    monkeypatch.setattr("dndref_spike.image_adapter._textual_image_module", unexpected_import)
    adapter = ImageAdapter("off")
    assert adapter.capabilities.backend is ImageBackend.NONE
    assert adapter.create_widget(__import__("pathlib").Path("missing.ppm")) is None


def test_capability_selection_handles_kitty_sixel_and_unknown() -> None:
    assert detect_capabilities("off").backend is ImageBackend.NONE
    assert detect_capabilities("kitty", environ={"KITTY_WINDOW_ID": "42"}).backend in {
        ImageBackend.KITTY,
        ImageBackend.NONE,
    }
    assert detect_capabilities("sixel", environ={"DNDREF_SIXEL": "1"}).backend in {
        ImageBackend.SIXEL,
        ImageBackend.NONE,
    }
    assert detect_capabilities("auto", environ={}).backend is ImageBackend.NONE


def test_textual_image_integration_is_optional() -> None:
    module = importlib.util.find_spec("textual_image")
    if module is None:
        pytest.skip("optional textual-image extra is not installed")
    imported = importlib.import_module("textual_image.widget")
    assert hasattr(imported, "Image")
    assert hasattr(imported, "TGPImage")
    assert hasattr(imported, "SixelImage")

    for mode in ("kitty", "sixel"):
        widget = ImageAdapter(mode).create_widget(ASSET_PATH)
        assert widget is not None
