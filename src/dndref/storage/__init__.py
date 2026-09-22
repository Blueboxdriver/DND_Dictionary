"""SQLite storage infrastructure for the application."""

from .database import (
    Database,
    DatabaseError,
    FTS5UnavailableError,
    MigrationError,
    apply_migrations,
)
from .repository import InstalledDataset, RepositoryError

__all__ = [
    "Database",
    "DatabaseError",
    "InstalledDataset",
    "FTS5UnavailableError",
    "MigrationError",
    "RepositoryError",
    "apply_migrations",
]
