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
    """The independently browsable content categories."""

    ITEMS = "items"
    SPELLS = "spells"
    FEATS = "feats"
    CLASSES = "classes"
    SUBCLASSES = "subclasses"
    MONSTERS = "monsters"

    @property
    def storage_kind(self) -> str:
        if self is SearchCategory.CLASSES:
            return "class"
        if self is SearchCategory.SUBCLASSES:
            return "subclass"
        return self.value[:-1]


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
        "subclass": SearchCategory.SUBCLASSES,
        "monster": SearchCategory.MONSTERS,
    }
    if isinstance(value, SearchCategory):
        return value
    try:
        normalized = value.casefold()
        return aliases[normalized] if normalized in aliases else SearchCategory(normalized)
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
        normalized = value.casefold()
        return aliases[normalized] if normalized in aliases else SearchMode(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported search mode: {value!r}") from exc


def _edition_values(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError("edition filters must be strings")
        if not value.strip():
            raise ValueError("edition filters must not be empty")
        if value not in result:
            result.append(value)
    return tuple(result)


@dataclass(frozen=True, order=True)
class SourceIdentity:
    """Stable sourcebook identity; source keys are scoped to a dataset."""

    dataset_id: str
    source_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_id, str) or not self.dataset_id.strip():
            raise ValueError("source identity dataset_id must not be empty")
        if not isinstance(self.source_key, str) or not self.source_key.strip():
            raise ValueError("source identity source_key must not be empty")

    @classmethod
    def parse(cls, value: str) -> SourceIdentity:
        dataset_id, separator, source_key = value.partition(":")
        if not separator:
            raise ValueError("source identity must be dataset_id:source_key")
        return cls(dataset_id, source_key)

    def __str__(self) -> str:
        return f"{self.dataset_id}:{self.source_key}"


@dataclass(frozen=True)
class EditionOption:
    value: str
    label: str


@dataclass(frozen=True)
class SourceOption:
    identity: SourceIdentity
    title: str
    edition: str | None

    @property
    def dataset_id(self) -> str:
        return self.identity.dataset_id


@dataclass(frozen=True)
class SourceBrowseInfo:
    source: SourceOption
    counts: Mapping[SearchCategory, int]


@dataclass(frozen=True)
class SearchQuery:
    """A validated, caller-owned search request."""

    category: SearchCategory | str
    text: str = ""
    mode: SearchMode | str = SearchMode.NAMES
    offset: int = 0
    limit: int = 50
    request_id: str | int | None = None
    editions: tuple[str, ...] = ()
    sources: tuple[SourceIdentity, ...] = ()
    parent_class: str | None = None
    challenge_ratings: tuple[str, ...] = ()
    creature_types: tuple[str, ...] = ()
    sizes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", _coerce_category(self.category))
        object.__setattr__(self, "mode", _coerce_mode(self.mode))
        object.__setattr__(self, "editions", _edition_values(self.editions))
        if any((self.challenge_ratings, self.creature_types, self.sizes)):
            if self.category is not SearchCategory.MONSTERS:
                raise ValueError("monster filters require the Monsters category")
            from .models.monster import cr_value
            for cr in self.challenge_ratings:
                cr_value(cr)
        source_values: list[SourceIdentity] = []
        for source in self.sources:
            if not isinstance(source, SourceIdentity):
                raise TypeError("source filters must be SourceIdentity values")
            if source not in source_values:
                source_values.append(source)
        object.__setattr__(self, "sources", tuple(source_values))
        if not isinstance(self.text, str):
            raise TypeError("search text must be a string")
        if self.offset < 0:
            raise ValueError("search offset must be non-negative")
        if self.limit < 1 or self.limit > 200:
            raise ValueError("search limit must be between 1 and 200")
        if self.parent_class is not None:
            if self.category is not SearchCategory.SUBCLASSES:
                raise ValueError("parent class filtering requires the Subclasses category")
            if not self.parent_class.strip():
                raise ValueError("parent class filter must not be empty")

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
    source_identity: SourceIdentity
    source_edition: str | None
    group_key: str | None = None

    @property
    def identity(self) -> str:
        return self.stable_id


@dataclass(frozen=True)
class GroupedEntrySummary:
    category: SearchCategory
    normalized_name: str
    primary: EntrySummary
    alternates: tuple[EntrySummary, ...]
    group_key: str | None = None

    @property
    def identity(self) -> str:
        return self.group_key or f"group:{self.category.value}:{self.normalized_name}"

    @property
    def variants(self) -> tuple[EntrySummary, ...]:
        return (self.primary, *self.alternates)

    @property
    def name(self) -> str:
        return self.primary.name


@dataclass(frozen=True)
class SearchPage:
    """One deterministic page of search summaries."""

    results: tuple[EntrySummary | GroupedEntrySummary, ...]
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

    def list_monster_facets(self) -> dict[str, tuple[str, ...]]:
        from .storage.repository import list_monster_facets

        with self.database.connection() as connection:
            return list_monster_facets(connection)

    def search(self, query: SearchQuery) -> SearchPage:
        from .storage.repository import search_entries, search_subclasses

        try:
            with self.database.connection() as connection:
                if query.category is SearchCategory.SUBCLASSES:
                    return search_subclasses(connection, query)
                return search_entries(connection, query)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"search failed: {exc}") from exc

    def search_grouped(
        self, query: SearchQuery, preferred_sources: tuple[SourceIdentity, ...] = ()
    ) -> SearchPage:
        from .storage.repository import search_grouped_entries, search_grouped_subclasses

        try:
            with self.database.connection() as connection:
                if query.category is SearchCategory.SUBCLASSES:
                    return search_grouped_subclasses(connection, query, preferred_sources)
                return search_grouped_entries(connection, query, preferred_sources)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"grouped search failed: {exc}") from exc

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

    def list_available_editions(
        self, category: SearchCategory | str | None = None
    ) -> tuple[EditionOption, ...]:
        from .storage.repository import list_available_editions

        selected = _coerce_category(category) if category is not None else None
        try:
            with self.database.connection() as connection:
                return list_available_editions(connection, selected)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"edition discovery failed: {exc}") from exc

    def list_available_sources(
        self,
        category: SearchCategory | str | None = None,
        editions: tuple[str, ...] = (),
    ) -> tuple[SourceOption, ...]:
        from .storage.repository import list_available_sources

        selected = _coerce_category(category) if category is not None else None
        values = _edition_values(editions)
        try:
            with self.database.connection() as connection:
                return list_available_sources(connection, selected, values)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"source discovery failed: {exc}") from exc

    def list_source_contents(self) -> tuple[SourceBrowseInfo, ...]:
        from .storage.repository import list_source_contents

        try:
            with self.database.connection() as connection:
                return list_source_contents(connection)
        except Exception as exc:
            raise SearchError(f"source browser lookup failed: {exc}") from exc

    def list_compatible_subclasses(self, class_identity: str) -> tuple[EntrySummary, ...]:
        """List every same-class, same-edition subclass across installed sources."""

        from .storage.repository import list_compatible_subclasses

        try:
            with self.database.connection() as connection:
                return list_compatible_subclasses(connection, class_identity)
        except Exception as exc:
            raise SearchError(f"compatible subclass lookup failed: {exc}") from exc

    def list_subclass_parents(self, editions: tuple[str, ...] = ()) -> tuple[str, ...]:
        from .storage.repository import list_subclass_parents

        try:
            with self.database.connection() as connection:
                return list_subclass_parents(connection, _edition_values(editions))
        except Exception as exc:
            raise SearchError(f"subclass parent lookup failed: {exc}") from exc


SearchRepository = SearchService


def search(database: Database, query: SearchQuery) -> SearchPage:
    """Convenience wrapper for the production search API."""

    return SearchService(database).search(query)


def list_available_editions(
    database: Database, category: SearchCategory | str | None = None
) -> tuple[EditionOption, ...]:
    """List editions represented by searchable entries in installed datasets."""

    return SearchService(database).list_available_editions(category)


def list_available_sources(
    database: Database,
    category: SearchCategory | str | None = None,
    editions: tuple[str, ...] = (),
) -> tuple[SourceOption, ...]:
    """List sourcebooks represented by searchable entries."""

    return SearchService(database).list_available_sources(category, editions)


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
