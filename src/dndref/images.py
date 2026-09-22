"""Optional terminal image capability detection and bounded local image loading.

This module deliberately keeps Pillow and textual-image out of module scope.  The
base installation must be able to import and run the browser without either
optional dependency.
"""

from __future__ import annotations

import importlib
import os
import threading
import time
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping


class ImageBackend(StrEnum):
    KITTY = "kitty"
    SIXEL = "sixel"
    NONE = "none"


IMAGE_MODES = frozenset({"auto", "off", "kitty", "sixel"})
MAX_IMAGE_FILE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_CACHE_ENTRIES = 24
MAX_CACHE_PIXELS = 4_000_000
IMAGE_PROBE_TIMEOUT = 1.0


@dataclass(frozen=True)
class ImageCapabilities:
    requested_mode: str
    backend: ImageBackend
    available: bool
    reason: str
    dependency: str = "textual-image"


@dataclass(frozen=True)
class DecodedImage:
    """A decoded, aspect-preserving thumbnail returned by a worker."""

    image: Any
    width: int
    height: int
    cache_key: str


def _textual_image_module() -> ModuleType:
    return importlib.import_module("textual_image.widget")


def _multiplexed_or_ssh(environ: Mapping[str, str]) -> bool:
    return any(
        environ.get(name)
        for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "TMUX", "STY")
    )


def _terminal_probe() -> Any:
    terminal = importlib.import_module("textual_image._terminal")
    return terminal.probe_terminal()


def _run_bounded(function: Callable[[], Any], timeout: float) -> tuple[Any | None, bool]:
    """Run a possibly blocking terminal probe without extending the caller wait.

    The worker is a daemon because terminal reads cannot be forcefully cancelled
    portably.  A timed out worker is ignored permanently and never touches the UI.
    """

    result: list[Any] = []
    error: list[BaseException] = []

    def run() -> None:
        try:
            result.append(function())
        except BaseException as exc:  # optional integrations must never escape startup
            error.append(exc)

    thread = threading.Thread(target=run, name="dndref-image-probe", daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(max(0.0, min(timeout, IMAGE_PROBE_TIMEOUT)))
    if thread.is_alive():
        return None, True
    if error:
        return None, False
    if time.monotonic() - started > IMAGE_PROBE_TIMEOUT:
        return None, True
    return (result[0] if result else None), False


def _backend_class(module: ModuleType, backend: ImageBackend) -> type[Any] | None:
    name = "TGPImage" if backend is ImageBackend.KITTY else "SixelImage"
    candidate = getattr(module, name, None)
    return candidate if isinstance(candidate, type) else None


def _capability_from_probe(
    requested: str,
    module: ModuleType,
    terminal: Any,
) -> ImageCapabilities:
    cell_size = getattr(terminal, "cell_size", None)
    cell_width = getattr(cell_size, "width", 0)
    cell_height = getattr(cell_size, "height", 0)
    if (
        not isinstance(cell_width, int)
        or not isinstance(cell_height, int)
        or min(cell_width, cell_height) <= 0
    ):
        return ImageCapabilities(
            requested, ImageBackend.NONE, False, "terminal cell dimensions are unusable"
        )

    if requested == "auto":
        if bool(getattr(terminal, "tgp", False)):
            backend = ImageBackend.KITTY
        elif bool(getattr(terminal, "sixel", False)):
            backend = ImageBackend.SIXEL
        else:
            return ImageCapabilities(
                requested, ImageBackend.NONE, False, "terminal did not prove Kitty or Sixel support"
            )
    else:
        backend = ImageBackend(requested)
        if not bool(getattr(terminal, "tgp" if backend is ImageBackend.KITTY else "sixel", False)):
            return ImageCapabilities(
                requested,
                ImageBackend.NONE,
                False,
                f"terminal did not prove {backend.value} support",
            )

    if _backend_class(module, backend) is None:
        return ImageCapabilities(
            requested,
            ImageBackend.NONE,
            False,
            f"textual-image does not expose its {backend.value} widget",
        )
    return ImageCapabilities(requested, backend, True, f"{backend.value} capability confirmed")


def _detect_capabilities(
    mode: str = "auto",
    *,
    environ: Mapping[str, str] | None = None,
    module_loader: Callable[[], ModuleType] = _textual_image_module,
    probe: Callable[[], Any] = _terminal_probe,
    timeout: float = IMAGE_PROBE_TIMEOUT,
) -> tuple[ImageCapabilities, ModuleType | None]:
    """Detect the requested backend before Textual starts terminal input.

    Environment variables may disable auto mode through SSH or a multiplexer,
    but they cannot establish graphics support.  ``module_loader`` and ``probe``
    are injectable so timeout and capability behavior can be tested without a
    real terminal.
    """

    if mode not in IMAGE_MODES:
        raise ValueError(f"image mode must be one of: {', '.join(sorted(IMAGE_MODES))}")
    environment = os.environ if environ is None else environ
    if mode == "off":
        return ImageCapabilities(mode, ImageBackend.NONE, False, "images disabled"), None
    if mode == "auto" and _multiplexed_or_ssh(environment):
        return (
            ImageCapabilities(
                mode,
                ImageBackend.NONE,
                False,
                "auto images disabled under SSH or a multiplexer",
            ),
            None,
        )
    def load_and_probe() -> tuple[ModuleType, Any]:
        return module_loader(), probe()

    loaded, timed_out = _run_bounded(load_and_probe, timeout)
    if timed_out:
        return (
            ImageCapabilities(mode, ImageBackend.NONE, False, "terminal image probe timed out"),
            None,
        )
    if loaded is None:
        return (
            ImageCapabilities(
                mode, ImageBackend.NONE, False, "optional image dependency or probe unavailable"
            ),
            None,
        )
    module, terminal = loaded
    capabilities = _capability_from_probe(mode, module, terminal)
    return capabilities, module if capabilities.available else None


def detect_capabilities(
    mode: str = "auto",
    *,
    environ: Mapping[str, str] | None = None,
    module_loader: Callable[[], ModuleType] = _textual_image_module,
    probe: Callable[[], Any] = _terminal_probe,
    timeout: float = IMAGE_PROBE_TIMEOUT,
) -> ImageCapabilities:
    """Return capability status without exposing the optional module object."""

    capabilities, _module = _detect_capabilities(
        mode,
        environ=environ,
        module_loader=module_loader,
        probe=probe,
        timeout=timeout,
    )
    return capabilities


class ImageAdapter:
    """Optional textual-image adapter with graceful text-only fallback."""

    def __init__(self, mode: str = "auto", **kwargs: Any) -> None:
        self.capabilities, self._module = _detect_capabilities(mode, **kwargs)

    def create_widget(self, image: Any) -> Any | None:
        if not self.capabilities.available or self._module is None:
            return None
        image_class = _backend_class(self._module, self.capabilities.backend)
        if image_class is None:
            return None
        try:
            return image_class(image)
        except (OSError, ValueError, TypeError, EOFError):
            return None

    @staticmethod
    def cleanup_widget(widget: Any | None) -> None:
        if widget is None:
            return
        renderable = getattr(widget, "_renderable", None)
        cleanup = getattr(renderable, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass


def fit_aspect_ratio(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    """Return a positive, aspect-preserving size inside the requested bounds."""

    if min(width, height, max_width, max_height) <= 0:
        raise ValueError("image dimensions must be positive")
    scale = min(max_width / width, max_height / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


class ThumbnailCache:
    """Thread-safe LRU cache bounded by both entry count and decoded pixels."""

    def __init__(
        self, max_entries: int = MAX_CACHE_ENTRIES, max_pixels: int = MAX_CACHE_PIXELS
    ) -> None:
        self.max_entries = max_entries
        self.max_pixels = max_pixels
        self._items: OrderedDict[str, DecodedImage] = OrderedDict()
        self._pixels = 0
        self._lock = threading.RLock()

    def get(self, key: str) -> DecodedImage | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, value: DecodedImage) -> None:
        with self._lock:
            previous = self._items.pop(value.cache_key, None)
            if previous is not None:
                self._pixels -= previous.width * previous.height
                _close_image(previous.image)
            self._items[value.cache_key] = value
            self._pixels += value.width * value.height
            while self._items and (
                len(self._items) > self.max_entries or self._pixels > self.max_pixels
            ):
                _key, evicted = self._items.popitem(last=False)
                self._pixels -= evicted.width * evicted.height
                _close_image(evicted.image)

    def clear(self) -> None:
        with self._lock:
            values = tuple(self._items.values())
            self._items.clear()
            self._pixels = 0
        for value in values:
            _close_image(value.image)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def _close_image(image: Any) -> None:
    close = getattr(image, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class ImageLoader:
    """Decode local image files.  Call from a Textual worker, never the UI thread."""

    def __init__(self, cache: ThumbnailCache | None = None) -> None:
        self.cache = cache or ThumbnailCache()

    def load(self, path: Path, *, max_width: int = 280, max_height: int = 240) -> DecodedImage:
        try:
            stat = path.stat()
        except OSError as exc:
            raise ValueError("image asset is missing or unreadable") from exc
        cache_key = f"{path}:{stat.st_mtime_ns}:{stat.st_size}:{max_width}x{max_height}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        if stat.st_size > MAX_IMAGE_FILE_BYTES:
            raise ValueError("image file exceeds the 8 MiB limit")
        try:
            from PIL import Image, UnidentifiedImageError
            from PIL.Image import DecompressionBombError, DecompressionBombWarning
        except ImportError as exc:
            raise RuntimeError("Pillow is unavailable") from exc

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", DecompressionBombWarning)
                with Image.open(path) as source:
                    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                        raise ValueError("unsupported image format")
                    if source.format not in {"PNG", "JPEG", "WEBP"}:
                        raise ValueError("unsupported image format")
                    width, height = source.size
                    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                        raise ValueError("decoded image dimensions exceed the pixel limit")
                    source.load()
                    image = source.convert("RGBA")
        except (DecompressionBombError, DecompressionBombWarning) as exc:
            raise ValueError("image dimensions trigger decompression-bomb protection") from exc
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise ValueError(f"unable to decode image: {exc}") from exc

        target_width, target_height = fit_aspect_ratio(width, height, max_width, max_height)
        if (target_width, target_height) != image.size:
            image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        decoded = DecodedImage(image, target_width, target_height, cache_key)
        self.cache.put(decoded)
        return decoded

    def close(self) -> None:
        self.cache.clear()


__all__ = [
    "DecodedImage",
    "IMAGE_MODES",
    "ImageAdapter",
    "ImageBackend",
    "ImageCapabilities",
    "ImageLoader",
    "MAX_CACHE_ENTRIES",
    "MAX_CACHE_PIXELS",
    "MAX_IMAGE_FILE_BYTES",
    "MAX_IMAGE_PIXELS",
    "ThumbnailCache",
    "detect_capabilities",
    "fit_aspect_ratio",
]
