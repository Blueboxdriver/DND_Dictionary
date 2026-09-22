"""Structured class, feature, subclass, and progression contracts."""

from __future__ import annotations

from enum import IntEnum

from pydantic import Field, model_validator

from .common import ContractModel, EntryBase, LocalKey, SourceKey


class HitDie(IntEnum):
    D6 = 6
    D8 = 8
    D10 = 10
    D12 = 12


class ClassFeature(ContractModel):
    feature_key: LocalKey
    level: int = Field(ge=1, le=20)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    display_order: int = Field(ge=0)
    source: SourceKey | None = None


class SubclassFeature(ContractModel):
    feature_key: LocalKey
    level: int = Field(ge=1, le=20)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    display_order: int = Field(ge=0)
    source: SourceKey | None = None


class Subclass(ContractModel):
    subclass_key: LocalKey
    name: str = Field(min_length=1)
    introduction: str = Field(min_length=1)
    source: SourceKey
    features: list[SubclassFeature] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_feature_keys(self) -> Subclass:
        keys = [feature.feature_key for feature in self.features]
        if len(keys) != len(set(keys)):
            raise ValueError("subclass features must have unique feature keys")
        return self


class ProgressionColumn(ContractModel):
    key: LocalKey
    label: str = Field(min_length=1)
    display_order: int = Field(ge=0)


class ProgressionLevel(ContractModel):
    level: int = Field(ge=1, le=20)
    values: dict[LocalKey, str] = Field(default_factory=dict)


class ClassProgression(ContractModel):
    columns: list[ProgressionColumn] = Field(default_factory=list)
    levels: list[ProgressionLevel] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_progression(self) -> ClassProgression:
        column_keys = [column.key for column in self.columns]
        if len(column_keys) != len(set(column_keys)):
            raise ValueError("progression columns must have unique keys")

        level_numbers = [row.level for row in self.levels]
        if len(level_numbers) != len(set(level_numbers)):
            raise ValueError("progression levels must not contain duplicate rows")

        declared = set(column_keys)
        for row in self.levels:
            undeclared = sorted(set(row.values) - declared)
            if undeclared:
                raise ValueError(
                    f"progression level {row.level} references undeclared columns: "
                    + ", ".join(undeclared)
                )
        return self


class CharacterClass(EntryBase):
    hit_die: HitDie
    primary_ability: str = Field(min_length=1)
    saving_throw_proficiencies: str = Field(min_length=1)
    skill_choices: str = Field(min_length=1)
    weapon_proficiencies: str = Field(min_length=1)
    armor_proficiencies: str = Field(min_length=1)
    tool_proficiencies: str = Field(min_length=1)
    starting_equipment: str = Field(min_length=1)
    multiclassing: str = Field(min_length=1)
    spellcasting_ability: str | None = None
    features: list[ClassFeature] = Field(default_factory=list)
    progression: ClassProgression = Field(default_factory=ClassProgression)
    subclasses: list[Subclass] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_class_children(self) -> CharacterClass:
        feature_keys = [feature.feature_key for feature in self.features]
        if len(feature_keys) != len(set(feature_keys)):
            raise ValueError("class features must have unique feature keys")
        subclass_keys = [subclass.subclass_key for subclass in self.subclasses]
        if len(subclass_keys) != len(set(subclass_keys)):
            raise ValueError("subclasses must have unique subclass keys")
        return self

