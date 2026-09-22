from __future__ import annotations

import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from dndref.cli import build_parser, initialize_application
from dndref.config import ApplicationPaths, load_config
from dndref.images import (
    DecodedImage,
    ImageAdapter,
    ImageBackend,
    ImageLoader,
    ThumbnailCache,
    detect_capabilities,
    fit_aspect_ratio,
)


class FakeWidget:
    def __init__(self, image: object) -> None:
        self.image = image


def fake_module() -> ModuleType:
    module = ModuleType("textual_image.widget")
    module.TGPImage = FakeWidget  # type: ignore[attr-defined]
    module.SixelImage = FakeWidget  # type: ignore[attr-defined]
    return module


def terminal(*, tgp: bool = False, sixel: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        tgp=tgp,
        sixel=sixel,
        cell_size=SimpleNamespace(width=10, height=20),
    )


def test_cli_config_precedence_and_invalid_options(tmp_path: Path) -> None:
    paths = ApplicationPaths.for_root(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        '[ui]\nimages = "kitty"\n[content]\ndefault_editions = ["2014"]\n',
        encoding="utf-8",
    )
    assert load_config(paths).ui.images == "kitty"
    initialized = initialize_application(paths, image_mode="off")
    assert initialized.ui.images == "off"
    assert initialized.content.default_editions == ("2014",)
    assert build_parser().parse_args(["--images", "off"]).images == "off"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--images", "invalid"])


def test_off_mode_does_not_import_or_probe() -> None:
    def unexpected() -> ModuleType:
        raise AssertionError("off mode imported an optional image module")

    capabilities = detect_capabilities(
        "off", module_loader=unexpected, probe=unexpected, environ={}
    )
    assert capabilities.backend is ImageBackend.NONE
    assert capabilities.reason == "images disabled"


def test_auto_requires_proof_and_disables_ssh_or_multiplexer() -> None:
    calls: list[str] = []

    def module_loader() -> ModuleType:
        calls.append("import")
        return fake_module()

    def probe() -> SimpleNamespace:
        calls.append("probe")
        return terminal(tgp=True)

    hinted = detect_capabilities(
        "auto",
        environ={"KITTY_WINDOW_ID": "1"},
        module_loader=module_loader,
        probe=lambda: terminal(),
    )
    assert hinted.backend is ImageBackend.NONE
    assert "did not prove" in hinted.reason

    calls.clear()
    ssh = detect_capabilities(
        "auto",
        environ={"SSH_CONNECTION": "1"},
        module_loader=module_loader,
        probe=probe,
    )
    assert ssh.backend is ImageBackend.NONE
    assert calls == []


def test_supported_backend_selection_and_missing_dependency() -> None:
    capabilities = detect_capabilities(
        "kitty",
        environ={},
        module_loader=fake_module,
        probe=lambda: terminal(tgp=True),
    )
    assert capabilities.backend is ImageBackend.KITTY
    assert capabilities.available

    missing = ImageAdapter(
        "kitty",
        environ={},
        module_loader=lambda: (_ for _ in ()).throw(ImportError("missing")),
        probe=lambda: terminal(tgp=True),
    )
    assert not missing.capabilities.available
    assert missing.create_widget(object()) is None


def test_probe_timeout_is_bounded() -> None:
    started = time.monotonic()
    capabilities = detect_capabilities(
        "kitty",
        environ={},
        module_loader=fake_module,
        probe=lambda: time.sleep(2),
        timeout=0.05,
    )
    assert time.monotonic() - started < 0.5
    assert "timed out" in capabilities.reason


def test_aspect_ratio_and_bounded_cache_cleanup() -> None:
    assert fit_aspect_ratio(200, 100, 28, 12) == (24, 12)
    assert fit_aspect_ratio(100, 200, 28, 12) == (6, 12)

    class Closable:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    first = Closable()
    second = Closable()
    cache = ThumbnailCache(max_entries=1, max_pixels=100)
    cache.put(DecodedImage(first, 5, 5, "first"))
    cache.put(DecodedImage(second, 5, 5, "second"))
    assert len(cache) == 1
    assert first.closed
    cache.clear()
    assert second.closed


def test_image_loader_rejects_missing_and_oversized_files(tmp_path: Path) -> None:
    loader = ImageLoader()
    with pytest.raises(ValueError, match="missing"):
        loader.load(tmp_path / "missing.png")
    oversized = tmp_path / "large.png"
    oversized.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="8 MiB"):
        loader.load(oversized)


def test_valid_corrupt_and_bomb_image_inputs(tmp_path: Path) -> None:
    PIL = pytest.importorskip("PIL.Image", reason="optional image tests require Pillow")
    valid = tmp_path / "valid.png"
    PIL.new("RGBA", (200, 100), (120, 80, 40, 255)).save(valid)
    loaded = ImageLoader().load(valid, max_width=28, max_height=12)
    assert (loaded.width, loaded.height) == (24, 12)

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="decode"):
        ImageLoader().load(corrupt)

    huge = tmp_path / "huge.png"
    PIL.new("RGBA", (4096, 4096), (0, 0, 0, 255)).save(huge)
    with pytest.raises(ValueError, match="pixel|decompression"):
        ImageLoader().load(huge)
