"""Application paths and the small Milestone 2 configuration surface."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from platformdirs import PlatformDirs


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
class Config:
    ui: UIConfig = UIConfig()


_IMAGE_MODES = frozenset({"auto", "off", "kitty", "sixel"})
_TOP_LEVEL_KEYS = frozenset({"ui"})
_UI_KEYS = frozenset({"images"})


def _require_table(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration section [{name}] must be a TOML table")
    return value


def _reject_unknown(values: Mapping[str, Any], allowed: frozenset[str], prefix: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        names = ", ".join(f"{prefix + '.' if prefix else ''}{key}" for key in unknown)
        raise ConfigurationError(f"unknown configuration setting: {names}")


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

    images = ui_values.get("images", Config().ui.images)
    if not isinstance(images, str) or images not in _IMAGE_MODES:
        choices = ", ".join(sorted(_IMAGE_MODES))
        raise ConfigurationError(f"ui.images must be one of: {choices}")

    return Config(ui=UIConfig(images=images))
