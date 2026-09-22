"""Spell data contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import ContractModel, EntryBase, StableReference


class MagicSchool(StrEnum):
    ABJURATION = "abjuration"
    CONJURATION = "conjuration"
    DIVINATION = "divination"
    ENCHANTMENT = "enchantment"
    EVOCATION = "evocation"
    ILLUSION = "illusion"
    NECROMANCY = "necromancy"
    TRANSMUTATION = "transmutation"


class SpellComponents(ContractModel):
    verbal: bool = False
    somatic: bool = False
    material: bool = False
    material_description: str | None = None

    @model_validator(mode="after")
    def validate_material_description(self) -> SpellComponents:
        if self.material and not self.material_description:
            raise ValueError("material_description is required when material=true")
        if not self.material and self.material_description:
            raise ValueError("material_description requires material=true")
        if not (self.verbal or self.somatic or self.material):
            raise ValueError("a spell must declare at least one component")
        return self


class Spell(EntryBase):
    level: int = Field(ge=0, le=9)
    school: MagicSchool
    casting_time: str = Field(min_length=1)
    range: str = Field(min_length=1)
    components: SpellComponents
    duration: str = Field(min_length=1)
    concentration: bool = False
    ritual: bool = False
    class_references: list[StableReference] = Field(default_factory=list)
    higher_level_effects: str | None = None

