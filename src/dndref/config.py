"""Application paths and the small Milestone 2 configuration surface."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from platformdirs import PlatformDirs

from .search import SourceIdentity


class ConfigurationError(ValueError):
    """Raised when the user configuration cannot be loaded or validated."""


@dataclass(frozen=True)
class ApplicationPaths:
    """Centralized application directories.

    Construct this class with explicit directories in tests. Its constructors only
    resolve paths; directory creation is performed by :meth:`ensure_directories`.
    """

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    state_dir: Path

    @classmethod
    def default(cls) -> ApplicationPaths:
        directories = PlatformDirs(appname="dndref", appauthor=False)
        return cls(
            config_dir=Path(directories.user_config_dir),
            data_dir=Path(directories.user_data_dir),
            cache_dir=Path(directories.user_cache_dir),
            state_dir=Path(directories.user_state_dir),
        )

    @classmethod
    def for_root(cls, root: Path) -> ApplicationPaths:
        """Return isolated application paths rooted at ``root`` for tests."""
        root = Path(root)
        return cls(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            state_dir=root / "state",
        )

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "dndref.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    def ensure_directories(self) -> None:
        """Create application directories during explicit startup only."""
        for directory in (self.config_dir, self.data_dir, self.cache_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class UIConfig:
    images: str = "auto"


@dataclass(frozen=True)
class FilterPreset:
    name: str
    editions: tuple[str, ...] = ()
    sources: tuple[SourceIdentity, ...] = ()


@dataclass(frozen=True)
class ContentConfig:
    default_editions: tuple[str, ...] = ("2024",)
    group_alternate_sources: bool = True
    preferred_sources: tuple[SourceIdentity, ...] = ()
    filter_presets: tuple[FilterPreset, ...] = ()


@dataclass(frozen=True)
class Config:
    ui: UIConfig = UIConfig()
    content: ContentConfig = ContentConfig()


_IMAGE_MODES = frozenset({"auto", "off", "kitty", "sixel"})
_TOP_LEVEL_KEYS = frozenset({"ui", "content"})
_UI_KEYS = frozenset({"images"})
_CONTENT_KEYS = frozenset(
    {"default_editions", "group_alternate_sources", "preferred_sources", "filter_presets"}
)
_PRESET_KEYS = frozenset({"name", "editions", "sources"})


def _require_table(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration section [{name}] must be a TOML table")
    return value


def _reject_unknown(values: Mapping[str, Any], allowed: frozenset[str], prefix: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        names = ", ".join(f"{prefix + '.' if prefix else ''}{key}" for key in unknown)
        raise ConfigurationError(f"unknown configuration setting: {names}")


def _string_array(value: Any, setting: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ConfigurationError(f"{setting} must be an array of nonempty strings")
    result = tuple(item.strip() for item in value)
    if len(result) != len(set(result)):
        raise ConfigurationError(f"{setting} must not contain duplicates")
    return result


def _source_array(value: Any, setting: str) -> tuple[SourceIdentity, ...]:
    values = _string_array(value, setting)
    try:
        return tuple(SourceIdentity.parse(item) for item in values)
    except ValueError as exc:
        raise ConfigurationError(f"{setting}: {exc}") from exc


def load_config(paths: ApplicationPaths) -> Config:
    """Load the user TOML file, or return built-in defaults when it is absent.

    Unknown sections and keys are rejected so typos cannot silently change
    application behavior.
    """
    config_path = paths.config_file
    if not config_path.exists():
        return Config()

    try:
        with config_path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration file {config_path}: {exc}") from exc

    _reject_unknown(raw, _TOP_LEVEL_KEYS, "")
    ui_values = _require_table(raw.get("ui", {}), "ui")
    _reject_unknown(ui_values, _UI_KEYS, "ui")
    content_values = _require_table(raw.get("content", {}), "content")
    _reject_unknown(content_values, _CONTENT_KEYS, "content")

    images = ui_values.get("images", Config().ui.images)
    if not isinstance(images, str) or images not in _IMAGE_MODES:
        choices = ", ".join(sorted(_IMAGE_MODES))
        raise ConfigurationError(f"ui.images must be one of: {choices}")

    default_editions = (
        _string_array(content_values["default_editions"], "content.default_editions")
        if "default_editions" in content_values
        else Config().content.default_editions
    )
    group_alternate_sources = content_values.get("group_alternate_sources", True)
    if not isinstance(group_alternate_sources, bool):
        raise ConfigurationError("content.group_alternate_sources must be a boolean")
    preferred_sources = _source_array(
        content_values.get("preferred_sources", []), "content.preferred_sources"
    )
    preset_values = content_values.get("filter_presets", [])
    if not isinstance(preset_values, list):
        raise ConfigurationError("content.filter_presets must be an array of tables")
    presets = []
    for index, raw_preset in enumerate(preset_values):
        section = f"content.filter_presets[{index}]"
        preset = _require_table(raw_preset, section)
        _reject_unknown(preset, _PRESET_KEYS, section)
        name = preset.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(f"{section}.name must be a nonempty string")
        presets.append(
            FilterPreset(
                name.strip(),
                _string_array(preset.get("editions", []), f"{section}.editions"),
                _source_array(preset.get("sources", []), f"{section}.sources"),
            )
        )
    if len({preset.name for preset in presets}) != len(presets):
        raise ConfigurationError("content.filter_presets names must be unique")

    return Config(
        ui=UIConfig(images=images),
        content=ContentConfig(
            default_editions=default_editions,
            group_alternate_sources=group_alternate_sources,
            preferred_sources=preferred_sources,
            filter_presets=tuple(presets),
        ),
    )
