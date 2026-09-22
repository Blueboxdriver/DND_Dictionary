"""Typed search contracts and the connection-scoped production search API."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from .storage.database import Database


class SearchMode(StrEnum):
    """The supported search semantics."""

    NAMES = "names"
    ALL_TEXT = "all_text"


class SearchCategory(StrEnum):
    """The four independently browsable entry categories."""

    ITEMS = "items"
    SPELLS = "spells"
    FEATS = "feats"
    CLASSES = "classes"

    @property
    def storage_kind(self) -> str:
        return self.value[:-1] if self is not SearchCategory.CLASSES else "class"


Category = SearchCategory

_APOSTROPHES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201b": "'",
        "\u02bc": "'",
        "\u2032": "'",
        "\uff07": "'",
    }
)
_TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
_FTS_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalize_name(value: str) -> str:
    """Normalize names and query text without rewriting ordinary punctuation."""

    translated = value.translate(_APOSTROPHES)
    decomposed = unicodedata.normalize("NFKD", translated)
    without_marks = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(without_marks.casefold().split())


def search_tokens(value: str) -> tuple[str, ...]:
    """Return literal word tokens used by both name and FTS searches."""

    return tuple(_TOKEN_RE.findall(normalize_name(value)))


def fts_tokens(value: str) -> tuple[str, ...]:
    """Return FTS-safe word tokens, splitting apostrophe compounds literally."""

    return tuple(_FTS_TOKEN_RE.findall(normalize_name(value)))


def _coerce_category(value: SearchCategory | str) -> SearchCategory:
    aliases = {
        "item": SearchCategory.ITEMS,
        "spell": SearchCategory.SPELLS,
        "feat": SearchCategory.FEATS,
        "class": SearchCategory.CLASSES,
    }
    if isinstance(value, SearchCategory):
        return value
    try:
        return aliases.get(value.casefold(), SearchCategory(value.casefold()))
    except ValueError as exc:
        raise ValueError(f"unsupported search category: {value!r}") from exc


def _coerce_mode(value: SearchMode | str) -> SearchMode:
    aliases = {
        "name": SearchMode.NAMES,
        "all text": SearchMode.ALL_TEXT,
        "all-text": SearchMode.ALL_TEXT,
        "full text": SearchMode.ALL_TEXT,
        "full_text": SearchMode.ALL_TEXT,
    }
    if isinstance(value, SearchMode):
        return value
    try:
        return aliases.get(value.casefold(), SearchMode(value.casefold()))
    except ValueError as exc:
        raise ValueError(f"unsupported search mode: {value!r}") from exc


@dataclass(frozen=True)
class SearchQuery:
    """A validated, caller-owned search request."""

    category: SearchCategory | str
    text: str = ""
    mode: SearchMode | str = SearchMode.NAMES
    offset: int = 0
    limit: int = 50
    request_id: str | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", _coerce_category(self.category))
        object.__setattr__(self, "mode", _coerce_mode(self.mode))
        if not isinstance(self.text, str):
            raise TypeError("search text must be a string")
        if self.offset < 0:
            raise ValueError("search offset must be non-negative")
        if self.limit < 1 or self.limit > 200:
            raise ValueError("search limit must be between 1 and 200")

    @property
    def normalized_text(self) -> str:
        return normalize_name(self.text)

    @property
    def tokens(self) -> tuple[str, ...]:
        return search_tokens(self.text)

    @property
    def fts_tokens(self) -> tuple[str, ...]:
        return fts_tokens(self.text)


@dataclass(frozen=True)
class EntrySummary:
    """The small row shape needed by a future result list."""

    stable_id: str
    category: SearchCategory
    name: str
    subtitle: str
    source_label: str
    dataset_id: str
    local_key: str
    dataset_title: str

    @property
    def identity(self) -> str:
        return self.stable_id


@dataclass(frozen=True)
class SearchPage:
    """One deterministic page of search summaries."""

    results: tuple[EntrySummary, ...]
    total_count: int
    offset: int
    limit: int
    request_id: str | int | None = None


@dataclass(frozen=True)
class DetailSection:
    key: str
    heading: str
    body: str
    display_order: int


@dataclass(frozen=True)
class ImageAsset:
    """Application-owned image asset resolved from an installed dataset."""

    path: Path
    media_type: str
    content_hash: str


@dataclass(frozen=True)
class SourceInfo:
    key: str
    title: str
    edition: str | None
    citation: str | None


@dataclass(frozen=True)
class DatasetMetadata:
    dataset_id: str
    title: str
    version: str
    ruleset: str
    language: str
    license_identifier: str
    attribution: str
    origin_url: str | None
    sources: tuple[SourceInfo, ...]


@dataclass(frozen=True)
class EntryDetail:
    """Storage-shaped detail data; presentation remains a later milestone."""

    stable_id: str
    category: SearchCategory
    name: str
    description: str
    source_label: str
    dataset_id: str
    local_key: str
    dataset_title: str
    fields: Mapping[str, object]
    sections: tuple[DetailSection, ...]
    image: ImageAsset | None = None

    @property
    def identity(self) -> str:
        return self.stable_id


class SearchError(RuntimeError):
    """Raised when the search database is missing or cannot be queried."""


class SearchService:
    """Open one SQLite connection per call so callers can use workers safely."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def search(self, query: SearchQuery) -> SearchPage:
        from .storage.repository import search_entries

        try:
            with self.database.connection() as connection:
                return search_entries(connection, query)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"search failed: {exc}") from exc

    def get_entry_detail(self, identity: str) -> EntryDetail | None:
        from .storage.repository import get_entry_detail

        try:
            with self.database.connection() as connection:
                return get_entry_detail(connection, identity, self.database.path.parent / "assets")
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"detail lookup failed: {exc}") from exc

    def list_installed_datasets(self) -> tuple[DatasetMetadata, ...]:
        from .storage.repository import list_dataset_metadata

        try:
            with self.database.connection() as connection:
                return list_dataset_metadata(connection)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"dataset metadata lookup failed: {exc}") from exc


SearchRepository = SearchService


def search(database: Database, query: SearchQuery) -> SearchPage:
    """Convenience wrapper for the production search API."""

    return SearchService(database).search(query)


def get_entry_detail(database: Database, identity: str) -> EntryDetail | None:
    """Convenience wrapper for selecting one persisted entry."""

    return SearchService(database).get_entry_detail(identity)


__all__ = [
    "Category",
    "DetailSection",
    "DatasetMetadata",
    "EntryDetail",
    "EntrySummary",
    "ImageAsset",
    "SearchCategory",
    "SearchError",
    "SearchMode",
    "SearchPage",
    "SearchQuery",
    "SearchRepository",
    "SearchService",
    "SourceInfo",
    "get_entry_detail",
    "normalize_name",
    "fts_tokens",
    "search",
    "search_tokens",
]
