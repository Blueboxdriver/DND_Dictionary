"""Feat data contracts."""

from __future__ import annotations

from pydantic import Field, model_validator

from .common import AdditionalSection, EntryBase


class Feat(EntryBase):
    category: str = Field(min_length=1)
    prerequisite: str | None = None
    minimum_level: int | None = Field(default=None, ge=1, le=20)
    repeatable: bool = False
    ability_increase: str | None = None
    benefits: list[AdditionalSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_benefit_keys(self) -> Feat:
        keys = [benefit.key for benefit in self.benefits]
        if len(keys) != len(set(keys)):
            raise ValueError("feat benefits must have unique section keys")
        return self

