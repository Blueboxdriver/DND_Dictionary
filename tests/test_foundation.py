from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dndref.app import FoundationApp
from dndref.cli import initialize_application
from dndref.config import ApplicationPaths, ConfigurationError, load_config
from dndref.search import SourceIdentity
from dndref.storage.database import Database, MigrationError


def test_paths_are_resolvable_and_explicitly_created(tmp_path: Path) -> None:
    paths = ApplicationPaths.for_root(tmp_path / "app")

    assert paths.database_path == tmp_path / "app" / "data" / "dndref.sqlite3"
    assert not paths.config_dir.exists()

    paths.ensure_directories()

    assert paths.config_dir.is_dir()
    assert paths.data_dir.is_dir()
    assert paths.cache_dir.is_dir()
    assert paths.log_dir.is_dir()


def test_path_override_keeps_real_application_locations_untouched(tmp_path: Path) -> None:
    paths = ApplicationPaths.for_root(tmp_path / "isolated")
    initialize_application(paths)

    assert paths.database_path.is_file()
    assert paths.database_path.parent == tmp_path / "isolated" / "data"
    assert not (tmp_path / "isolated" / "config" / "config.toml").exists()


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    config = load_config(ApplicationPaths.for_root(tmp_path))

    assert config.ui.images == "auto"
    assert config.content.default_editions == ("2024",)
    assert config.content.group_alternate_sources is True


def test_grouping_preference_and_custom_preset_config(tmp_path: Path) -> None:
    paths = ApplicationPaths.for_root(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        '[content]\n'
        'group_alternate_sources = false\n'
        'preferred_sources = ["phb:book", "missing:source"]\n'
        '[[content.filter_presets]]\n'
        'name = "2024 Core"\n'
        'editions = ["2024"]\n'
        'sources = ["phb:book"]\n'
        '[[content.filter_presets]]\n'
        'name = "Everything 2024"\n'
        'editions = ["2024"]\n'
        'sources = []\n',
        encoding="utf-8",
    )
    config = load_config(paths).content
    assert config.group_alternate_sources is False
    assert config.preferred_sources == (
        SourceIdentity("phb", "book"), SourceIdentity("missing", "source")
    )
    assert config.filter_presets[0].sources == (SourceIdentity("phb", "book"),)
    assert config.filter_presets[1].sources == ()


@pytest.mark.parametrize(
    "setting",
    [
        'preferred_sources = ["title only"]',
        'group_alternate_sources = "yes"',
        '[[content.filter_presets]]\nname = "Broken"\nsources = ["bad"]',
    ],
)
def test_invalid_grouping_config_follows_config_errors(tmp_path: Path, setting: str) -> None:
    paths = ApplicationPaths.for_root(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(f"[content]\n{setting}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(paths)


def test_valid_config_overrides_defaults(tmp_path: Path) -> None:
    paths = ApplicationPaths.for_root(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        '[ui]\nimages = "off"\n[content]\ndefault_editions = ["2014", "2024"]\n',
        encoding="utf-8",
    )

    config = load_config(paths)

    assert config.ui.images == "off"
    assert config.content.default_editions == ("2014", "2024")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('["2014"]', ("2014",)),
        ('[]', ()),
        ('["2030-homebrew"]', ("2030-homebrew",)),
    ],
)
def test_configured_default_editions_are_open_ended_and_may_be_empty(
    tmp_path: Path, value: str, expected: tuple[str, ...]
) -> None:
    paths = ApplicationPaths.for_root(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        f"[content]\ndefault_editions = {value}\n", encoding="utf-8"
    )

    assert load_config(paths).content.default_editions == expected


@pytest.mark.parametrize("value", ['"2024"', '["", "2014"]', '["2014", 2024]'])
def test_invalid_default_editions_fail_config_validation(
    tmp_path: Path, value: str
) -> None:
    paths = ApplicationPaths.for_root(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        f"[content]\ndefault_editions = {value}\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="content.default_editions"):
        load_config(paths)


def test_malformed_config_fails_clearly(tmp_path: Path) -> None:
    paths = ApplicationPaths.for_root(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text("[ui\nimages = 'off'", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid TOML"):
        load_config(paths)


def test_unknown_config_setting_is_rejected(tmp_path: Path) -> None:
    paths = ApplicationPaths.for_root(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text("[future]\nvalue = true\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="unknown configuration setting"):
        load_config(paths)


def test_fresh_database_initializes_and_enables_foreign_keys(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "dndref.sqlite3")

    applied = database.initialize()

    assert applied == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
    with database.connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sources)").fetchall()
        }
        assert "edition" in source_columns
        migrations = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [(row[0], row[1]) for row in migrations] == [
            (1, "initial"),
            (2, "content"),
            (3, "search_fts"),
            (4, "canonical_source_editions"),
            (5, "monsters"),
            (6, "personal_organization"),
            (7, "recent_searches"),
            (8, "conditions_rules"),
            (9, "dataset_source_hash"),
            (10, "character_builder"),
            (11, "character_persistence"),
            (12, "character_creation"),
        ]


def test_migrations_are_not_applied_twice(tmp_path: Path) -> None:
    database = Database(tmp_path / "dndref.sqlite3")
    database.initialize()

    assert database.initialize() == ()
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 12


def test_failed_migration_is_not_recorded(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "001_initial.sql").write_text(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);",
        encoding="utf-8",
    )
    (migration_dir / "002_broken.sql").write_text(
        "CREATE TABLE should_rollback (value TEXT);\nCREATE TABLE should_rollback (value TEXT);",
        encoding="utf-8",
    )
    database = Database(tmp_path / "dndref.sqlite3", migration_dir)

    with pytest.raises(MigrationError, match="002_broken"):
        database.initialize()

    with database.connection() as connection:
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        assert [row[0] for row in versions] == [1]
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'should_rollback'"
        ).fetchone() is None

    with pytest.raises(MigrationError, match="002_broken"):
        database.initialize()


def test_cli_help_and_version_use_package_entrypoint() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")

    help_result = subprocess.run(
        [sys.executable, "-m", "dndref", "--help"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    version_result = subprocess.run(
        [sys.executable, "-m", "dndref", "--version"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert help_result.returncode == 0
    assert "usage: dndref" in help_result.stdout
    assert version_result.returncode == 0
    assert version_result.stdout.strip() == "dndref 0.1.0"


def test_cli_startup_and_shutdown_with_xdg_overrides(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
            "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "dndref"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0
    assert "foundation initialized" in result.stdout
    database_path = tmp_path / "xdg-data" / "dndref" / "dndref.sqlite3"
    assert database_path.is_file()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 12


@pytest.mark.asyncio
async def test_minimal_production_shell_starts_and_quits() -> None:
    async with FoundationApp().run_test() as pilot:
        await pilot.press("q")
