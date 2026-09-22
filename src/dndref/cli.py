"""Command-line entrypoint and application startup sequence."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from . import __version__
from .app import FoundationApp
from .bundled import install_bundled_datasets
from .config import ApplicationPaths, Config, ConfigurationError, UIConfig, load_config
from .importer import (
    DatasetError,
    DatasetImportError,
    format_report,
    format_validation,
    import_dataset,
    load_dataset,
    plan_import,
)
from .storage import Database, DatabaseError
from .storage.repository import RepositoryError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dndref",
        description="Offline D&D reference application.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--images",
        choices=("auto", "off", "kitty", "sixel"),
        default=None,
        help="image backend policy for the browser (default: config or auto)",
    )
    commands = parser.add_subparsers(dest="command")

    validate = commands.add_parser("validate", help="validate a local dataset pack")
    validate.add_argument("path", type=Path)

    import_command = commands.add_parser("import", help="import a local dataset pack")
    import_command.add_argument("path", type=Path)
    import_command.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show the import plan without changing persistent state",
    )
    return parser


def initialize_application(
    paths: ApplicationPaths | None = None,
    *,
    image_mode: str | None = None,
) -> Config:
    """Resolve paths, load configuration, create directories, and initialize SQLite."""
    application_paths = paths or ApplicationPaths.default()
    config = load_config(application_paths)
    application_paths.ensure_directories()
    database = Database(application_paths.database_path)
    database.initialize()
    install_bundled_datasets(database)
    if image_mode is not None:
        if image_mode not in {"auto", "off", "kitty", "sixel"}:
            raise ConfigurationError("image mode must be one of: auto, off, kitty, sixel")
        config = replace(config, ui=UIConfig(images=image_mode))
    return config


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        try:
            loaded = load_dataset(args.path)
        except (DatasetError, OSError) as exc:
            print(f"dndref: error: {exc}", file=sys.stderr)
            return 2
        print(format_validation(loaded))
        return 0

    if args.command == "import":
        try:
            loaded = load_dataset(args.path)
            paths = ApplicationPaths.default()
            database = Database(paths.database_path)
            if args.dry_run:
                report = plan_import(database, loaded)
            else:
                report = import_dataset(database, loaded)
        except (DatasetError, DatasetImportError, DatabaseError, RepositoryError, OSError) as exc:
            print(f"dndref: error: {exc}", file=sys.stderr)
            return 2
        print(format_report(report, dry_run=args.dry_run))
        return 0

    try:
        paths = ApplicationPaths.default()
        config = initialize_application(paths, image_mode=args.images)
    except (ConfigurationError, DatasetError, DatasetImportError, DatabaseError, OSError) as exc:
        print(f"dndref: error: {exc}", file=sys.stderr)
        return 2

    if sys.stdin.isatty() and sys.stdout.isatty():
        FoundationApp(paths=paths, config=config).run()
    else:
        print("D&D Reference foundation initialized.")
    return 0
