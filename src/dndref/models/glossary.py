"""First-class condition and concise rules glossary records."""

from __future__ import annotations

from pydantic import Field

from .common import EntryBase


class GlossaryEntry(EntryBase):
    """A sourced condition or named rules concept."""

    section: str | None = Field(default=None, min_length=1)
