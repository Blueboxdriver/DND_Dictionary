"""Shared data-contract types for versioned D&D datasets."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


class ContractModel(BaseModel):
    """Base model used by external dataset contracts.

    Unknown fields are rejected so misspelled dataset fields do not silently
    disappear during a future import.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


_DATASET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _validate_dataset_id(value: str) -> str:
    if not _DATASET_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "dataset ID must use lowercase letters, numbers, '.', '_' or '-' and "
            "start with a letter or number"
        )
    return value


DatasetId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80),
    AfterValidator(_validate_dataset_id),
]


def _validate_local_key(value: str) -> str:
    if not value or value.startswith("/") or value.endswith("/"):
        raise ValueError("local key must be a non-empty relative key")
    if "\\" in value or ":" in value or any(character.isspace() for character in value):
        raise ValueError("local key must not contain whitespace, backslashes, or ':'")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("local key must not contain empty, '.', or '..' path segments")
    if any(ord(character) < 32 for character in value):
        raise ValueError("local key must not contain control characters")
    return value


LocalKey = Annotated[
    str, StringConstraints(min_length=1, max_length=200), AfterValidator(_validate_local_key)
]
SourceKey = LocalKey


def _validate_image_reference(value: str) -> str:
    if value.startswith(("/", "\\")) or "\\" in value or ":" in value:
        raise ValueError("image reference must be a relative local path")
    segments = value.split("/")
    if not value or any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("image reference must not contain unsafe path segments")
    return value


ImageReference = Annotated[
    str,
    StringConstraints(min_length=1, max_length=300),
    AfterValidator(_validate_image_reference),
]


def _validate_reference(value: str) -> str:
    if ":" in value:
        dataset_id, local_key = value.split(":", 1)
        _validate_dataset_id(dataset_id)
    else:
        local_key = value
    _validate_local_key(local_key)
    return value


StableReference = Annotated[
    str,
    StringConstraints(min_length=1, max_length=300),
    AfterValidator(_validate_reference),
]


def split_reference(reference: str, current_dataset_id: str) -> tuple[str, str]:
    """Return ``(dataset_id, local_key)`` for a same- or cross-dataset reference."""

    if ":" in reference:
        dataset_id, local_key = reference.split(":", 1)
        return dataset_id, local_key
    return current_dataset_id, reference


class SourceMetadata(ContractModel):
    """Human-readable metadata for a source cited by entries in a dataset."""

    key: SourceKey
    title: str = Field(min_length=1)
    edition: str | None = None
    citation: str | None = None


class AdditionalSection(ContractModel):
    """Ordered supplemental Markdown content that is not a dedicated field."""

    key: LocalKey
    heading: str = Field(min_length=1)
    body: str = Field(min_length=1)
    display_order: int = Field(ge=0)


class EntryBase(ContractModel):
    """Metadata shared by every browsable reference entry."""

    local_key: LocalKey
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: SourceKey
    image: ImageReference | None = None
    sections: list[AdditionalSection] = Field(default_factory=list)
