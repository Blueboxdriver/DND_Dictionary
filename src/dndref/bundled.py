"""Access and first-run installation of read-only packaged dataset resources."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

from .importer import (
    DatasetError,
    ImportReport,
    import_dataset,
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
            str(row[0]): str(row[1])
            for row in connection.execute(
                "SELECT dataset_id, content_hash FROM datasets"
            ).fetchall()
        }

    reports: list[ImportReport] = []
    with bundled_dataset_paths() as paths:
        for path in paths:
            loaded = load_dataset(path)
            previous_hash = installed.get(loaded.dataset_id)
            if previous_hash is not None:
                if not loaded.pack.monsters or previous_hash != legacy_monsterless_hash(loaded):
                    continue
            reports.append(import_dataset(database, loaded))
            installed[loaded.dataset_id] = loaded.content_hash
    return tuple(reports)
