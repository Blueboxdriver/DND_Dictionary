"""Reference-first monster stat blocks."""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, EntryBase


def cr_value(value: str) -> Fraction:
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid challenge rating: {value}") from exc
    if result < 0 or result.denominator not in (1, 2, 4, 8):
        raise ValueError(f"invalid challenge rating: {value}")
    return result


class MonsterAbility(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    display_order: int = Field(ge=0)
    section: Literal[
        "traits", "actions", "bonus_actions", "reactions", "legendary_actions",
        "lair_actions", "regional_effects", "spellcasting", "innate_spellcasting",
    ]
    cost: int | None = Field(default=None, ge=1)


class Monster(EntryBase):
    page: int | None = Field(default=None, ge=1)
    group: str | None = None
    variant: str | None = None
    size: str = Field(min_length=1)
    creature_type: str = Field(min_length=1)
    subtype: str | None = None
    alignment: str | None = None
    armor_class: str = Field(min_length=1)
    hit_points: int | None = Field(default=None, ge=0)
    hit_points_text: str = Field(min_length=1)
    hit_dice: str | None = None
    speed: dict[str, str] = Field(default_factory=dict)
    speed_text: str | None = None
    abilities: dict[str, int]
    saving_throws: dict[str, str] = Field(default_factory=dict)
    skills: dict[str, str] = Field(default_factory=dict)
    proficiency_bonus: str | None = None
    damage_vulnerabilities: str | None = None
    damage_resistances: str | None = None
    damage_immunities: str | None = None
    condition_immunities: str | None = None
    senses: str | None = None
    passive_perception: int | None = Field(default=None, ge=0)
    languages: str | None = None
    telepathy: str | None = None
    challenge_rating: str
    xp: int | None = Field(default=None, ge=0)
    legendary_intro: str | None = None
    abilities_and_actions: list[MonsterAbility] = Field(default_factory=list)

    @field_validator("challenge_rating")
    @classmethod
    def valid_cr(cls, value: str) -> str:
        parsed = cr_value(value)
        return str(parsed)

    @field_validator("speed")
    @classmethod
    def valid_speed(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not text.strip() for key, text in value.items()):
            raise ValueError("speed types and values must be nonempty")
        return value

    @model_validator(mode="after")
    def valid_abilities(self) -> Monster:
        if set(self.abilities) != {"str", "dex", "con", "int", "wis", "cha"}:
            raise ValueError("monster requires six ability scores")
        if any(not 0 <= score <= 30 for score in self.abilities.values()):
            raise ValueError("monster ability scores must be between 0 and 30")
        orders = [
            (ability.section, ability.display_order)
            for ability in self.abilities_and_actions
        ]
        if len(orders) != len(set(orders)):
            raise ValueError("monster ability ordering must be unique within each section")
        return self
