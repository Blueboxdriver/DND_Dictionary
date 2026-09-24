"""Access and first-run installation of read-only packaged dataset resources."""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

from .importer import (
    DatasetError,
    ImportReport,
    dataset_source_hash,
    import_dataset,
    legacy_glossaryless_hash,
    legacy_monsterless_hash,
    load_dataset,
)
from .storage.database import Database

BUNDLED_DATASET_DIRECTORIES = (
    "srd-5.2.1",
    "official-5etools-2024",
)


@contextmanager
def bundled_dataset_paths() -> Iterator[tuple[Path, ...]]:
    """Materialize bundled dataset directories for the duration of a read/import.

    ``importlib.resources`` may expose resources from a directory or an archive.
    ``as_file`` handles both without making the runtime depend on the source tree
    or attempting to write into site-packages.
    """

    dataset_root = resources.files("dndref").joinpath("datasets")
    with ExitStack() as stack:
        paths: list[Path] = []
        for directory in BUNDLED_DATASET_DIRECTORIES:
            resource = dataset_root.joinpath(directory)
            try:
                path = stack.enter_context(resources.as_file(resource))
            except (FileNotFoundError, OSError) as exc:
                raise DatasetError(f"Bundled dataset resource is unavailable: {directory}") from exc
            if not path.is_dir():
                raise DatasetError(f"Bundled dataset resource is not a directory: {directory}")
            paths.append(path)
        yield tuple(paths)


def install_bundled_datasets(database: Database) -> tuple[ImportReport, ...]:
    """Install any missing bundled packs into the application-owned database.

    Existing dataset snapshots are left untouched. This makes repeated startup
    idempotent and preserves explicit user imports while ensuring a clean
    installation has both production packs available for browsing.
    """

    with database.connection() as connection:
        installed = {
            str(row[0]): (str(row[1]), str(row[2]) if row[2] is not None else None)
            for row in connection.execute(
                "SELECT dataset_id, content_hash, source_hash FROM datasets"
            ).fetchall()
        }
        asset_paths: dict[str, list[str]] = {}
        for row in connection.execute(
            "SELECT dataset_id, relative_path FROM images ORDER BY dataset_id, relative_path"
        ).fetchall():
            asset_paths.setdefault(str(row[0]), []).append(str(row[1]))

    reports: list[ImportReport] = []
    with bundled_dataset_paths() as paths:
        for path in paths:
            try:
                manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
                dataset_id = str(manifest.get("dataset_id", ""))
            except (OSError, AttributeError, TypeError, ValueError):
                dataset_id = ""
            previous = installed.get(dataset_id)
            if previous is not None and previous[1] is not None:
                current_source_hash = dataset_source_hash(
                    path, tuple(asset_paths.get(dataset_id, ()))
                )
                if current_source_hash == previous[1]:
                    continue
            loaded = load_dataset(path)
            previous_hash, previous_source_hash = installed.get(
                loaded.dataset_id, (None, None)
            )
            if previous_hash is not None:
                known_previous_hashes = {legacy_glossaryless_hash(loaded)}
                if loaded.pack.monsters:
                    known_previous_hashes.add(legacy_monsterless_hash(loaded))
                known_previous_hashes.add(loaded.content_hash)
                if previous_hash not in known_previous_hashes:
                    continue
                if (
                    previous_hash == loaded.content_hash
                    and previous_source_hash == loaded.source_hash
                ):
                    continue
            reports.append(import_dataset(database, loaded))
            installed[loaded.dataset_id] = (loaded.content_hash, loaded.source_hash)
            asset_paths[loaded.dataset_id] = [asset.relative_path for asset in loaded.assets]
    return tuple(reports)
