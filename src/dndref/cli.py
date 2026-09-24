"""Command-line entrypoint and application startup sequence."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from . import __version__
from .app import FoundationApp
from .bundled import install_bundled_datasets
from .config import ApplicationPaths, Config, ConfigurationError, UIConfig, load_config
from .images import format_image_diagnostics, run_image_test
from .importer import (
    DatasetError,
    DatasetImportError,
    format_report,
    format_validation,
    import_dataset,
    load_dataset,
    plan_import,
)
from .performance import PerformanceProfiler
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
    parser.add_argument(
        "--profile",
        action="store_true",
        help="report development timings for application startup and navigation",
    )
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("image-diagnostics", help="report image support and fallback reason")
    commands.add_parser("image-test", help="display and clear a generated terminal image")

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
    profiler: PerformanceProfiler | None = None,
) -> Config:
    """Resolve paths, load configuration, create directories, and initialize SQLite."""
    application_paths = paths or ApplicationPaths.default()
    if profiler is None:
        config = load_config(application_paths)
        application_paths.ensure_directories()
    else:
        with profiler.measure("startup.configuration"):
            config = load_config(application_paths)
            application_paths.ensure_directories()
    database = Database(application_paths.database_path, profiler=profiler)
    if profiler is None:
        database.initialize()
        install_bundled_datasets(database)
    else:
        with profiler.operation("startup.database_initialize"):
            database.initialize()
        with profiler.operation("startup.bundled_datasets"):
            install_bundled_datasets(database)
    if image_mode is not None:
        if image_mode not in {"auto", "off", "kitty", "sixel"}:
            raise ConfigurationError("image mode must be one of: auto, off, kitty, sixel")
        config = replace(config, ui=UIConfig(images=image_mode))
    return config


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    profiling = args.profile or os.environ.get("DNDREF_PROFILE") == "1"
    profiler = PerformanceProfiler() if profiling else None

    if args.command in {"image-diagnostics", "image-test"}:
        try:
            config = load_config(ApplicationPaths.default())
        except (ConfigurationError, OSError) as exc:
            print(f"dndref: error: {exc}", file=sys.stderr)
            return 2
        mode = args.images or config.ui.images
        if args.command == "image-diagnostics":
            print(format_image_diagnostics(mode))
            return 0
        try:
            return run_image_test(mode)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(f"dndref: image test failed: {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130

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
        startup_started = time.perf_counter()
        config = initialize_application(paths, image_mode=args.images, profiler=profiler)
        if profiler is not None:
            profiler.record("startup.total", (time.perf_counter() - startup_started) * 1000)
    except (ConfigurationError, DatasetError, DatasetImportError, DatabaseError, OSError) as exc:
        print(f"dndref: error: {exc}", file=sys.stderr)
        return 2

    if sys.stdin.isatty() and sys.stdout.isatty():
        FoundationApp(paths=paths, config=config, profiler=profiler).run()
    else:
        print("D&D Reference foundation initialized.")
    if profiler is not None:
        print(profiler.report(), file=sys.stderr)
    return 0
