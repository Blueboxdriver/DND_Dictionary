"""SQLite connection lifecycle and application-owned SQL migrations."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..performance import PerformanceProfiler


class DatabaseError(RuntimeError):
    """Base error for database initialization failures."""


class MigrationError(DatabaseError):
    """Raised when a database migration cannot be applied safely."""


class FTS5UnavailableError(DatabaseError):
    """Raised when this Python SQLite build cannot create an FTS5 table."""


class _NestedConnection:
    """Connection facade whose context manager cannot commit an outer transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


_MIGRATION_NAME = re.compile(r"^(?P<version>\d+)_(?P<name>[a-z0-9][a-z0-9_]*)\.sql$")
_TRACKING_TABLE = "schema_migrations"
MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> tuple[Migration, ...]:
    """Find and validate ordered SQL migration files in ``directory``."""
    if not directory.is_dir():
        raise MigrationError(f"migration directory does not exist: {directory}")

    migrations: list[Migration] = []
    versions: set[int] = set()
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"invalid migration filename {path.name}; expected NNN_name.sql")
        version = int(match.group("version"))
        if version in versions:
            raise MigrationError(f"duplicate migration version {version:03d}")
        versions.add(version)
        migrations.append(Migration(version, match.group("name"), path))

    migrations.sort(key=lambda migration: migration.version)
    return tuple(migrations)


def _tracking_table_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_TRACKING_TABLE,),
    ).fetchone()
    return row is not None


def _applied_migrations(connection: sqlite3.Connection) -> dict[int, str]:
    if not _tracking_table_exists(connection):
        return {}
    try:
        rows = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise MigrationError(f"cannot read schema migration tracking: {exc}") from exc
    return {int(row[0]): str(row[1]) for row in rows}


def _migration_script(migration: Migration) -> str:
    sql = migration.sql.strip()
    quoted_name = migration.name.replace("'", "''")
    return (
        "BEGIN;\n"
        f"{sql}\n"
        "INSERT INTO schema_migrations (version, name) "
        f"VALUES ({migration.version}, '{quoted_name}');\n"
        "COMMIT;"
    )


def _require_fts5(connection: sqlite3.Connection) -> None:
    """Fail before migrations if the runtime cannot provide the planned search index."""

    try:
        connection.execute("CREATE VIRTUAL TABLE temp.dndref_fts5_check USING fts5(value)")
        connection.execute("DROP TABLE temp.dndref_fts5_check")
    except sqlite3.Error as exc:
        raise FTS5UnavailableError(
            "SQLite FTS5 is required for dndref search but is unavailable in this Python "
            "build; install Python with SQLite FTS5 support or use a supported runtime"
        ) from exc


def apply_migrations(
    connection: sqlite3.Connection, directory: Path = MIGRATIONS_DIR
) -> tuple[int, ...]:
    """Apply unapplied migrations, recording each only after its SQL succeeds."""
    migrations = discover_migrations(directory)
    applied = _applied_migrations(connection)
    known = {migration.version: migration.name for migration in migrations}

    for version, name in applied.items():
        if known.get(version) != name:
            raise MigrationError(
                f"database records unknown or renamed migration {version:03d}_{name}"
            )

    applied_now: list[int] = []
    for migration in migrations:
        if migration.version in applied:
            continue
        rebuild_entries = "requires foreign_keys=off" in migration.sql
        try:
            if rebuild_entries:
                connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript(_migration_script(migration))
            if rebuild_entries and connection.execute("PRAGMA foreign_key_check").fetchone():
                raise MigrationError("entry-table migration left invalid foreign keys")
        except (OSError, sqlite3.Error) as exc:
            connection.rollback()
            raise MigrationError(
                f"migration {migration.version:03d}_{migration.name} failed: {exc}"
            ) from exc
        finally:
            if rebuild_entries:
                connection.execute("PRAGMA foreign_keys = ON")
        applied[migration.version] = migration.name
        applied_now.append(migration.version)

    return tuple(applied_now)


class Database:
    """Own database connections per operation; no mutable global connection exists."""

    def __init__(
        self,
        path: Path,
        migrations_dir: Path = MIGRATIONS_DIR,
        *,
        profiler: PerformanceProfiler | None = None,
    ) -> None:
        self.path = Path(path)
        self.migrations_dir = Path(migrations_dir)
        self.profiler = profiler
        self._active_transaction: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"dndref_transaction_{id(self)}", default=None
        )

    def connect(self) -> sqlite3.Connection:
        """Open one caller-owned connection with foreign keys enabled."""
        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            if self.profiler is not None:
                connection.set_trace_callback(lambda _statement: self.profiler.count_statement())
            return connection
        except sqlite3.Error as exc:
            raise DatabaseError(f"cannot open database {self.path}: {exc}") from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection and always close it when the operation finishes."""
        active = self._active_transaction.get()
        if active is not None:
            yield _NestedConnection(active)  # type: ignore[misc]
            return
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run coordinated service writes on one connection with all-or-nothing commit."""
        active = self._active_transaction.get()
        if active is not None:
            savepoint = f"dndref_nested_{id(active)}"
            active.execute(f"SAVEPOINT {savepoint}")
            try:
                yield active
                active.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                active.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                active.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            return
        connection = self.connect()
        token = self._active_transaction.set(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._active_transaction.reset(token)
            connection.close()

    def initialize(self) -> tuple[int, ...]:
        """Create the database parent directory and apply pending migrations."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DatabaseError(
                f"cannot create database directory {self.path.parent}: {exc}"
            ) from exc

        with self.connection() as connection:
            _require_fts5(connection)
            applied = apply_migrations(connection, self.migrations_dir)
            if 3 in applied or _tracking_table_exists(connection):
                from .repository import normalize_stored_names

                normalize_stored_names(connection)
                connection.commit()
            return applied
