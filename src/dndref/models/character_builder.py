"""Typed, edition-scoped rules metadata for future character building."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .character_class import ClassProgression
from .common import ContractModel, LocalKey, SourceKey


class Ability(StrEnum):
    STR = "str"
    DEX = "dex"
    CON = "con"
    INT = "int"
    WIS = "wis"
    CHA = "cha"


class ProficiencyKind(StrEnum):
    ARMOR = "armor"
    WEAPON = "weapon"
    SAVING_THROW = "saving_throw"
    SKILL = "skill"
    TOOL = "tool"
    LANGUAGE = "language"


class GrantKind(StrEnum):
    PROFICIENCY = "proficiency"
    FEATURE = "feature"
    SPELL = "spell"
    CANTRIP = "cantrip"
    WEAPON_MASTERY = "weapon_mastery"
    FEAT = "feat"
    EQUIPMENT = "equipment"
    CURRENCY = "currency"
    ABILITY_SCORE = "ability_score"
    SPEED = "speed"
    SIZE = "size"
    CREATURE_TYPE = "creature_type"
    DARKVISION = "darkvision"
    RESISTANCE = "resistance"
    OTHER = "other"


class ReferenceKind(StrEnum):
    ITEM = "item"
    SPELL = "spell"
    FEAT = "feat"
    CLASS = "class"
    SUBCLASS = "subclass"
    RULE = "rule"
    CLASS_FEATURE = "class_feature"
    SUBCLASS_FEATURE = "subclass_feature"
    OPTIONAL_FEATURE = "optional_feature"


class RuleReference(ContractModel):
    """A published target, or an explicit unresolved upstream identity."""

    kind: ReferenceKind
    identity: str = Field(min_length=1)
    resolved: bool = True


class RequirementOperator(StrEnum):
    ALL = "all"
    ANY = "any"
    ABILITY_SCORE = "ability_score"
    CLASS_LEVEL = "class_level"
    TOTAL_LEVEL = "total_level"
    CLASS_MEMBERSHIP = "class_membership"
    SUBCLASS_MEMBERSHIP = "subclass_membership"
    PROFICIENCY = "proficiency"
    SPELLCASTING = "spellcasting"
    FEAT = "feat"
    EDITION = "edition"
    CLASS_ENTRY = "class_entry"
    CURRENT_CLASS_PRIMARY_ABILITIES = "current_class_primary_abilities"
    UNRESOLVED = "unresolved"


class Requirement(ContractModel):
    """Small declarative requirement tree; groups are AND/OR, leaves are typed."""

    operator: RequirementOperator
    children: list[Requirement] = Field(default_factory=list)
    ability: Ability | None = None
    minimum: int | None = Field(default=None, ge=0)
    class_key: LocalKey | None = None
    subclass_key: LocalKey | None = None
    proficiency_kind: ProficiencyKind | None = None
    proficiency_key: str | None = None
    feat_reference: RuleReference | None = None
    edition: str | None = None
    spellcasting_kind: Literal["spellcasting", "pact_magic"] | None = None
    entry_mode: Literal["starting", "multiclass"] | None = None
    source_text: str | None = None

    @model_validator(mode="after")
    def validate_operator_fields(self) -> Requirement:
        if self.operator in {RequirementOperator.ALL, RequirementOperator.ANY}:
            if not self.children:
                raise ValueError("requirement groups must contain at least one child")
            if any(
                value is not None
                for value in (
                    self.ability,
                    self.minimum,
                    self.class_key,
                    self.subclass_key,
                    self.proficiency_kind,
                    self.proficiency_key,
                    self.feat_reference,
                    self.edition,
                    self.spellcasting_kind,
                    self.entry_mode,
                )
            ):
                raise ValueError("requirement groups cannot also declare leaf fields")
            return self
        if self.children:
            raise ValueError("requirement leaves cannot contain children")
        if self.operator is RequirementOperator.ABILITY_SCORE:
            if self.ability is None or self.minimum is None:
                raise ValueError("ability score requirements need ability and minimum")
        elif self.operator is RequirementOperator.CLASS_LEVEL:
            if self.class_key is None or self.minimum is None:
                raise ValueError("class level requirements need class_key and minimum")
        elif self.operator is RequirementOperator.TOTAL_LEVEL:
            if self.minimum is None:
                raise ValueError("total level requirements need minimum")
        elif self.operator is RequirementOperator.CLASS_MEMBERSHIP:
            if self.class_key is None:
                raise ValueError("class membership requirements need class_key")
        elif self.operator is RequirementOperator.SUBCLASS_MEMBERSHIP:
            if self.class_key is None or self.subclass_key is None:
                raise ValueError("subclass membership needs class_key and subclass_key")
        elif self.operator is RequirementOperator.PROFICIENCY:
            if self.proficiency_kind is None or not self.proficiency_key:
                raise ValueError("proficiency requirements need kind and key")
        elif self.operator is RequirementOperator.FEAT:
            if self.feat_reference is None:
                raise ValueError("feat requirements need feat_reference")
        elif self.operator is RequirementOperator.EDITION:
            if not self.edition:
                raise ValueError("edition requirements need edition")
        elif self.operator is RequirementOperator.SPELLCASTING:
            if self.spellcasting_kind is None:
                self.spellcasting_kind = "spellcasting"
        elif self.operator is RequirementOperator.CLASS_ENTRY:
            if self.entry_mode is None:
                raise ValueError("class entry requirements need entry_mode")
        elif self.operator is RequirementOperator.CURRENT_CLASS_PRIMARY_ABILITIES:
            if self.minimum is None:
                raise ValueError("current class primary ability requirement needs minimum")
        elif self.operator is RequirementOperator.UNRESOLVED:
            if not self.source_text:
                raise ValueError("unresolved requirements must preserve source_text")
        return self


class OptionCriteriaKind(StrEnum):
    SKILL = "skill"
    TOOL_GROUP = "tool_group"
    WEAPON_CATEGORY = "weapon_category"
    WEAPON_MASTERY = "weapon_mastery"
    FEAT_CATEGORY = "feat_category"
    SPELL_LIST = "spell_list"
    SPELL_LEVEL = "spell_level"
    ITEM_TYPE = "item_type"
    OPTIONAL_FEATURE_TYPE = "optional_feature_type"
    LANGUAGE = "language"
    SOURCE_FILTER = "source_filter"


class OptionCriteria(ContractModel):
    kind: OptionCriteriaKind
    values: list[str] = Field(min_length=1)
    filters: dict[str, str | int | bool | list[str]] = Field(default_factory=dict)


class AbilityIncrease(ContractModel):
    ability: Ability
    amount: int = Field(ge=1, le=20)
    maximum: int | None = Field(default=None, ge=1, le=30)


class Grant(ContractModel):
    """One automatic rules effect, with stable target identity when available."""

    grant_key: LocalKey
    kind: GrantKind
    value: str | None = None
    reference: RuleReference | None = None
    proficiency_kind: ProficiencyKind | None = None
    quantity: int | None = Field(default=None, ge=1)
    unit: str | None = None
    condition: Requirement | None = None
    source_rule: str = Field(min_length=1)
    unresolved: bool = False

    @model_validator(mode="after")
    def validate_grant_shape(self) -> Grant:
        if self.kind is GrantKind.PROFICIENCY and self.proficiency_kind is None:
            raise ValueError("proficiency grants require proficiency_kind")
        if self.reference is not None and not self.reference.resolved and not self.unresolved:
            raise ValueError("unresolved references must be marked on the grant")
        if (
            self.kind
            in {
                GrantKind.PROFICIENCY,
                GrantKind.ABILITY_SCORE,
                GrantKind.SPEED,
                GrantKind.SIZE,
                GrantKind.CREATURE_TYPE,
                GrantKind.DARKVISION,
                GrantKind.RESISTANCE,
                GrantKind.CURRENCY,
                GrantKind.OTHER,
            }
            and self.value is None
        ):
            raise ValueError(f"{self.kind.value} grants require value")
        if self.value is None and self.reference is None:
            raise ValueError("grants require a value or reference")
        return self


class ChoiceOption(ContractModel):
    option_key: LocalKey
    label: str = Field(min_length=1)
    value: str | None = None
    reference: RuleReference | None = None
    ability_increases: list[AbilityIncrease] = Field(default_factory=list)
    grants: list[Grant] = Field(default_factory=list)
    group: str | None = None
    resolved: bool = True


class ChoiceKind(StrEnum):
    PROFICIENCY = "proficiency"
    SUBCLASS = "subclass"
    FEAT = "feat"
    SPELL = "spell"
    CANTRIP = "cantrip"
    WEAPON_MASTERY = "weapon_mastery"
    OPTIONAL_FEATURE = "optional_feature"
    EQUIPMENT_PACKAGE = "equipment_package"
    EQUIPMENT = "equipment"
    ABILITY_SCORE = "ability_score"
    SIZE = "size"
    RESISTANCE = "resistance"
    LANGUAGE = "language"
    OTHER = "other"


class Choice(ContractModel):
    """A player decision, identified by choice_key within its owning record."""

    choice_key: LocalKey
    kind: ChoiceKind
    count: int = Field(ge=1)
    options: list[ChoiceOption] = Field(default_factory=list)
    criteria: OptionCriteria | None = None
    requirement: Requirement | None = None
    exclusions: list[RuleReference] = Field(default_factory=list)
    source_rule: str = Field(min_length=1)
    depends_on_choice: LocalKey | None = None
    depends_on_option: LocalKey | None = None

    @model_validator(mode="after")
    def validate_choice_shape(self) -> Choice:
        if not self.options and self.criteria is None:
            raise ValueError("choices need explicit options or option criteria")
        if self.options and self.criteria is not None:
            raise ValueError("choices cannot combine explicit options and criteria")
        if (self.depends_on_choice is None) != (self.depends_on_option is None):
            raise ValueError("conditional choices need both parent choice and option")
        keys = [option.option_key for option in self.options]
        if len(keys) != len(set(keys)):
            raise ValueError("choice option keys must be unique")
        if self.options and self.count > len(self.options):
            raise ValueError("choice count exceeds its explicit option set")
        return self


class ProgressionEvent(ContractModel):
    """Build-relevant changes at one class or subclass level."""

    event_key: LocalKey
    level: int = Field(ge=1, le=20)
    title: str = Field(min_length=1)
    source_rule: str = Field(min_length=1)
    grants: list[Grant] = Field(default_factory=list)
    choices: list[Choice] = Field(default_factory=list)
    requirement: Requirement | None = None

    @model_validator(mode="after")
    def validate_nonempty(self) -> ProgressionEvent:
        if not self.grants and not self.choices:
            raise ValueError("progression events need at least one grant or choice")
        return self


class TraitReference(ContractModel):
    trait_key: LocalKey
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_rule: str = Field(min_length=1)


class MovementSpeed(ContractModel):
    kind: Literal["walk", "burrow", "climb", "fly", "swim"]
    feet: int = Field(ge=0)


class SpellSlotProgression(ContractModel):
    class_level: int = Field(ge=1, le=20)
    slots_by_spell_level: list[int] = Field(min_length=1, max_length=9)


class LevelCount(ContractModel):
    class_level: int = Field(ge=1, le=20)
    count: int = Field(ge=0)


class SpellAccessKind(StrEnum):
    KNOWN = "known"
    PREPARED = "prepared"
    INNATE = "innate"
    EXPANDED = "expanded"
    SPELLBOOK = "spellbook"


class SpellAccessRule(ContractModel):
    access: SpellAccessKind
    class_level: int = Field(ge=1, le=20)
    level_scope: Literal["class", "total"] = "class"
    spells: list[RuleReference] = Field(default_factory=list)
    criteria: OptionCriteria | None = None
    count: int | None = Field(default=None, ge=1)
    uses: str | None = None
    ability: Ability | None = None
    ability_options: list[Ability] = Field(default_factory=list)
    ability_selection: Literal["fixed", "choice", "inherited", "class"] | None = None
    choice_group: LocalKey | None = None
    choice_option: LocalKey | None = None
    requirement: Requirement | None = None
    source_rule: str = Field(min_length=1)
    unresolved_details: str | None = None


class SpellcastingModel(StrEnum):
    SPELLCASTING = "spellcasting"
    PACT_MAGIC = "pact_magic"


class MulticlassContribution(StrEnum):
    NONE = "none"
    FULL = "full"
    HALF_ROUND_UP = "half_round_up"
    HALF_ROUND_DOWN = "half_round_down"
    THIRD_ROUND_DOWN = "third_round_down"
    PACT_MAGIC_SEPARATE = "pact_magic_separate"


class SpellAcquisition(StrEnum):
    PREPARED = "prepared"
    KNOWN = "known"
    SPELLBOOK = "spellbook"
    SPECIAL = "special"


class SpellcastingRules(ContractModel):
    ability: Ability
    model: SpellcastingModel = SpellcastingModel.SPELLCASTING
    multiclass_contribution: MulticlassContribution
    acquisition: SpellAcquisition
    spell_list_reference: RuleReference | None = None
    prepared_spells_change: str | None = None
    cantrips: list[LevelCount] = Field(default_factory=list)
    prepared_spells: list[LevelCount] = Field(default_factory=list)
    known_spells: list[LevelCount] = Field(default_factory=list)
    standalone_slots: list[SpellSlotProgression] = Field(default_factory=list)
    pact_slots: list[SpellSlotProgression] = Field(default_factory=list)
    additional_spells: list[SpellAccessRule] = Field(default_factory=list)
    spell_choices: list[Choice] = Field(default_factory=list)
    source_rule: str = Field(min_length=1)


class EquipmentMetadata(ContractModel):
    item_key: LocalKey
    source: SourceKey
    category: Literal["weapon", "armor", "other"]
    weapon_category: Literal["simple", "martial"] | None = None
    attack_type: Literal["melee", "ranged"] | None = None
    damage: str | None = None
    damage_type: str | None = None
    range_feet: list[int] = Field(default_factory=list)
    properties: list[str] = Field(default_factory=list)
    mastery_references: list[RuleReference] = Field(default_factory=list)
    ammunition_reference: RuleReference | None = None
    versatile_damage: str | None = None
    armor_category: Literal["light", "medium", "heavy", "shield"] | None = None
    base_ac: int | None = Field(default=None, ge=0)
    dexterity_rule: Literal["full", "cap", "none", "shield_bonus"] | None = None
    dexterity_cap: int | None = Field(default=None, ge=0)
    strength_requirement: int | None = Field(default=None, ge=0)
    stealth_disadvantage: bool | None = None
    unresolved_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_equipment_shape(self) -> EquipmentMetadata:
        if self.category == "armor" and (self.armor_category is None or self.base_ac is None):
            raise ValueError("armor metadata requires armor_category and base_ac")
        return self


class EquipmentChoice(ContractModel):
    choices: list[Choice] = Field(default_factory=list)
    includes_background_equipment: bool = False


class ClassBuilderRules(ContractModel):
    class_key: LocalKey
    name: str = Field(min_length=1)
    source: SourceKey
    progression: ClassProgression
    primary_ability_options: list[list[Ability]] = Field(min_length=1)
    starting_grants: list[Grant] = Field(default_factory=list)
    starting_choices: list[Choice] = Field(default_factory=list)
    multiclass_grants: list[Grant] = Field(default_factory=list)
    multiclass_choices: list[Choice] = Field(default_factory=list)
    multiclass_requirement: Requirement
    subclass_selection_level: int | None = Field(default=None, ge=1, le=20)
    starting_equipment: EquipmentChoice = Field(default_factory=EquipmentChoice)
    progression_events: list[ProgressionEvent] = Field(default_factory=list)
    spellcasting: SpellcastingRules | None = None

    @model_validator(mode="after")
    def validate_event_keys(self) -> ClassBuilderRules:
        keys = [event.event_key for event in self.progression_events]
        if len(keys) != len(set(keys)):
            raise ValueError("class progression event keys must be unique")
        return self


class SubclassBuilderRules(ContractModel):
    subclass_key: LocalKey
    class_key: LocalKey
    name: str = Field(min_length=1)
    source: SourceKey
    selection_level: int = Field(ge=1, le=20)
    progression_events: list[ProgressionEvent] = Field(default_factory=list)
    spellcasting: SpellcastingRules | None = None


class SpeciesBuilderRecord(ContractModel):
    species_key: LocalKey
    name: str = Field(min_length=1)
    source: SourceKey
    creature_types: list[str] = Field(min_length=1)
    sizes: list[Literal["tiny", "small", "medium", "large", "huge"]] = Field(min_length=1)
    movement: list[MovementSpeed] = Field(min_length=1)
    darkvision_feet: int | None = Field(default=None, ge=0)
    grants: list[Grant] = Field(default_factory=list)
    choices: list[Choice] = Field(default_factory=list)
    traits: list[TraitReference] = Field(default_factory=list)
    spell_access: list[SpellAccessRule] = Field(default_factory=list)
    description: str = ""


class BackgroundBuilderRecord(ContractModel):
    background_key: LocalKey
    name: str = Field(min_length=1)
    source: SourceKey
    grants: list[Grant] = Field(default_factory=list)
    choices: list[Choice] = Field(default_factory=list)
    origin_feat: RuleReference | None = None
    origin_feat_variant: str | None = None
    starting_equipment: EquipmentChoice = Field(default_factory=EquipmentChoice)
    description: str = ""


class FeatBuilderRules(ContractModel):
    feat_key: LocalKey
    source: SourceKey
    category: str = Field(min_length=1)
    prerequisite: Requirement | None = None
    repeatable: bool = False
    ability_choices: list[Choice] = Field(default_factory=list)
    spell_choices: list[Choice] = Field(default_factory=list)
    grants: list[Grant] = Field(default_factory=list)
    spell_access: list[SpellAccessRule] = Field(default_factory=list)


class BuilderOptionDefinition(ContractModel):
    option_key: LocalKey
    name: str = Field(min_length=1)
    source: SourceKey
    option_type: str = Field(min_length=1)
    feature_types: list[str] = Field(default_factory=list)
    prerequisite: Requirement | None = None
    repeatable: bool = False
    description: str = ""


class SkillDefinition(ContractModel):
    key: LocalKey
    name: str = Field(min_length=1)
    ability: Ability


class CharacterBuilderCatalog(ContractModel):
    """Normalized 2024 rules build input, kept separate from saved character state."""

    edition: Literal["2024"] = "2024"
    skills: list[SkillDefinition] = Field(default_factory=list)
    classes: list[ClassBuilderRules] = Field(default_factory=list)
    subclasses: list[SubclassBuilderRules] = Field(default_factory=list)
    species: list[SpeciesBuilderRecord] = Field(default_factory=list)
    backgrounds: list[BackgroundBuilderRecord] = Field(default_factory=list)
    feats: list[FeatBuilderRules] = Field(default_factory=list)
    optional_features: list[BuilderOptionDefinition] = Field(default_factory=list)
    equipment: list[EquipmentMetadata] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_owner_identity(self) -> CharacterBuilderCatalog:
        classes = {str(item.class_key) for item in self.classes}
        subclass_keys = [str(item.subclass_key) for item in self.subclasses]
        if len(subclass_keys) != len(set(subclass_keys)):
            raise ValueError("builder subclass keys must be unique")
        for subclass in self.subclasses:
            if str(subclass.class_key) not in classes:
                raise ValueError("builder subclass refers to an unknown parent class")
        for label, rows, attr in (
            ("species", self.species, "species_key"),
            ("background", self.backgrounds, "background_key"),
            ("feat", self.feats, "feat_key"),
            ("optional feature", self.optional_features, "option_key"),
            ("equipment", self.equipment, "item_key"),
        ):
            keys = [str(getattr(row, attr)) for row in rows]
            if len(keys) != len(set(keys)):
                raise ValueError(f"builder {label} keys must be unique")
        choices_by_owner: list[tuple[str, str, list[Choice]]] = []
        for owner in self.classes:
            choices = list(owner.starting_choices)
            choices.extend(owner.multiclass_choices)
            choices.extend(owner.starting_equipment.choices)
            if owner.spellcasting is not None:
                choices.extend(owner.spellcasting.spell_choices)
            for event in owner.progression_events:
                choices.extend(event.choices)
            choices_by_owner.append(("class", str(owner.class_key), choices))
        for owner in self.subclasses:
            choices = []
            if owner.spellcasting is not None:
                choices.extend(owner.spellcasting.spell_choices)
            for event in owner.progression_events:
                choices.extend(event.choices)
            choices_by_owner.append(("subclass", str(owner.subclass_key), choices))
        for owner in self.species:
            choices_by_owner.append(("species", str(owner.species_key), list(owner.choices)))
        for owner in self.backgrounds:
            choices = list(owner.choices)
            choices.extend(owner.starting_equipment.choices)
            choices_by_owner.append(("background", str(owner.background_key), choices))
        for owner in self.feats:
            choices = list(owner.ability_choices)
            choices.extend(owner.spell_choices)
            choices_by_owner.append(("feat", str(owner.feat_key), choices))

        for owner_type, owner_key, choices in choices_by_owner:
            choice_map = {str(choice.choice_key): choice for choice in choices}
            if len(choice_map) != len(choices):
                raise ValueError(
                    f"builder choice keys must be unique within {owner_type} '{owner_key}'"
                )
            for choice in choices:
                if choice.depends_on_choice is None:
                    continue
                parent = choice_map.get(str(choice.depends_on_choice))
                if parent is None:
                    raise ValueError(
                        "conditional choice refers to a missing parent choice "
                        f"within {owner_type} '{owner_key}'"
                    )
                option_keys = {str(option.option_key) for option in parent.options}
                if str(choice.depends_on_option) not in option_keys:
                    raise ValueError("conditional choice refers to a missing parent option")
        return self


class RequirementStatus(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNRESOLVED = "unresolved"


class CharacterRuleContext(ContractModel):
    """Read-only facts used by the UI-independent requirement evaluator."""

    edition: str
    total_level: int | None = Field(default=None, ge=0, le=20)
    class_levels: dict[str, int] = Field(default_factory=dict)
    class_primary_abilities: dict[str, list[list[Ability]]] = Field(default_factory=dict)
    ability_scores: dict[Ability, int] = Field(default_factory=dict)
    proficiencies: set[str] = Field(default_factory=set)
    feats: set[str] = Field(default_factory=set)
    subclasses: dict[str, str] = Field(default_factory=dict)
    spellcasting_classes: set[str] = Field(default_factory=set)
    pact_magic_classes: set[str] = Field(default_factory=set)
    target_class_key: str | None = None
    entry_mode: Literal["starting", "multiclass"] | None = None


class RequirementEvaluation(ContractModel):
    status: RequirementStatus
    reason: str = Field(min_length=1)
