from __future__ import annotations

import time
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from dndref.cli import build_parser, initialize_application, main
from dndref.config import ApplicationPaths, load_config
from dndref.images import (
    DecodedImage,
    ImageAdapter,
    ImageBackend,
    ImageLoader,
    ThumbnailCache,
    _stable_kitty_widget,
    detect_capabilities,
    fit_aspect_ratio,
    format_image_diagnostics,
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
    auto_kitty = detect_capabilities(
        "auto", environ={}, module_loader=fake_module, probe=lambda: terminal(tgp=True),
    )
    assert auto_kitty.backend is ImageBackend.KITTY
    auto_sixel = detect_capabilities(
        "auto", environ={}, module_loader=fake_module, probe=lambda: terminal(sixel=True),
    )
    assert auto_sixel.backend is ImageBackend.SIXEL
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

    forced = detect_capabilities(
        "sixel", environ={"TMUX": "1"}, module_loader=fake_module,
        probe=lambda: terminal(),
    )
    assert forced.backend is ImageBackend.SIXEL
    assert "unverified" in forced.reason

    unsupported = detect_capabilities(
        "auto", environ={}, module_loader=fake_module, probe=lambda: terminal(),
    )
    assert unsupported.backend is ImageBackend.NONE


def test_no_color_and_corrupt_probe_cannot_claim_kitty() -> None:
    for mode in ("auto", "kitty"):
        result = detect_capabilities(
            mode, environ={"NO_COLOR": "1"}, module_loader=fake_module,
            probe=lambda: terminal(tgp=True),
        )
        assert result.backend is ImageBackend.NONE
        assert "NO_COLOR" in result.reason
    sixel_fallback = detect_capabilities(
        "auto", environ={"NO_COLOR": "1"}, module_loader=fake_module,
        probe=lambda: terminal(tgp=True, sixel=True),
    )
    assert sixel_fallback.backend is ImageBackend.SIXEL
    corrupt = detect_capabilities(
        "auto", environ={}, module_loader=fake_module,
        probe=lambda: SimpleNamespace(
            tgp="false", sixel="true", cell_size=SimpleNamespace(width=10, height=20)
        ),
    )
    assert corrupt.backend is ImageBackend.NONE
    invalid_size = detect_capabilities(
        "auto", environ={}, module_loader=fake_module,
        probe=lambda: SimpleNamespace(
            tgp=True, sixel=False, cell_size=SimpleNamespace(width=True, height=20)
        ),
    )
    assert invalid_size.backend is ImageBackend.NONE


def test_kitty_renderable_is_reused_across_unrelated_redraws() -> None:
    class Base:
        _Renderable = object

        def __init_subclass__(cls, *, Renderable: type[object]) -> None:
            cls._Renderable = Renderable

        def __init__(self) -> None:
            self._renderable: object | None = None
            self.created = 0

        def render(self) -> object:
            self.created += 1
            self._renderable = object()
            return self._renderable

    widget = _stable_kitty_widget(Base)()
    first = widget.render()
    assert widget.render() is first
    assert widget.created == 1
    widget._renderable = None
    assert widget.render() is not first
    assert widget.created == 2


def test_dumb_auto_never_imports_or_probes() -> None:
    def unexpected() -> ModuleType:
        raise AssertionError("TERM=dumb attempted optional image work")

    result = detect_capabilities(
        "auto", environ={"TERM": "dumb"}, module_loader=unexpected, probe=unexpected,
    )
    assert result.backend is ImageBackend.NONE
    assert result.reason == "TERM=dumb"


def test_probe_timeout_is_bounded() -> None:
    started = time.monotonic()
    capabilities = detect_capabilities(
        "auto",
        environ={},
        module_loader=fake_module,
        probe=lambda: time.sleep(2),
        timeout=0.05,
    )
    assert time.monotonic() - started < 0.5
    assert "timed out" in capabilities.reason

    forced = detect_capabilities(
        "kitty", environ={}, module_loader=fake_module,
        probe=lambda: time.sleep(2), timeout=0.05,
    )
    assert forced.backend is ImageBackend.KITTY
    assert "unverified" in forced.reason


def test_diagnostics_reports_selection_and_missing_dependencies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    from dndref import images

    selected = detect_capabilities(
        "kitty", environ={}, module_loader=fake_module, probe=lambda: terminal(tgp=True),
    )
    report = format_image_diagnostics(
        "kitty", environ={"TERM": "xterm-kitty"}, capabilities=selected,
    )
    assert "Selected backend: kitty" in report
    assert "TERM: xterm-kitty" in report
    fallback = format_image_diagnostics("off", environ={"TERM": "dumb"})
    assert "Selected backend: text" in fallback
    assert "Reason: images disabled" in fallback

    monkeypatch.setattr(images, "_installed", lambda _module: False)
    missing = format_image_diagnostics("off", environ={"TERM": "dumb"})
    assert "Pillow: missing" in missing
    assert "textual-image: missing" in missing
    assert "Sample decode: unavailable" in missing
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["--images", "off", "image-diagnostics"]) == 0
    assert "Selected backend: text" in capsys.readouterr().out


def test_image_test_failure_is_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    from dndref import cli

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "run_image_test", lambda _mode: (_ for _ in ()).throw(OSError("oops")))
    assert main(["--images", "kitty", "image-test"]) == 2
    assert "image test failed: oops" in capsys.readouterr().err


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


def test_extensionless_staged_asset_uses_recorded_media_type(tmp_path: Path) -> None:
    PIL = pytest.importorskip("PIL.Image")
    path = tmp_path / "sha256" / "a1" / "hash-without-extension"
    path.parent.mkdir(parents=True)
    PIL.new("RGB", (200, 100), (20, 40, 60)).save(path, format="PNG")
    loaded = ImageLoader().load(path, media_type="image/png", max_width=28, max_height=12)
    assert (loaded.width, loaded.height) == (24, 12)
    with pytest.raises(ValueError, match="media type"):
        ImageLoader().load(path, media_type="image/jpeg")


def test_jpeg_webp_bad_media_type_and_unreadable(tmp_path: Path) -> None:
    PIL = pytest.importorskip("PIL.Image")
    loader = ImageLoader()
    for format_name, media_type, suffix in (
        ("JPEG", "image/jpeg", "jpg"), ("WEBP", "image/webp", "webp"),
    ):
        path = tmp_path / f"valid.{suffix}"
        PIL.new("RGB", (20, 10), "red").save(path, format=format_name)
        assert loader.load(path, media_type=media_type).width == 20
        with pytest.raises(ValueError, match="media type"):
            loader.load(path, media_type="image/gif")
    unreadable = tmp_path / "unreadable.png"
    unreadable.mkdir()
    with pytest.raises(ValueError, match="decode|unreadable"):
        loader.load(unreadable)


def test_sixel_renderer_produces_a_bounded_control_string() -> None:
    PIL = pytest.importorskip("PIL.Image")
    sixel = pytest.importorskip("textual_image.renderable.sixel")
    from rich.console import Console

    output = StringIO()
    renderable = sixel.Image(PIL.new("RGB", (16, 16), "red"))
    Console(file=output, force_terminal=True, color_system="truecolor").print(renderable)
    sequence = output.getvalue()
    assert sequence.count("\x1bP") == 1
    assert sequence.count("\x1b\\") == 1
    assert len(sequence) < 1024
    renderable.cleanup()
