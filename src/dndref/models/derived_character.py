"""Immutable results produced from saved character decisions and published rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from .character import PublishedReference

DerivedState = Literal["complete", "partial", "unresolved"]
ProficiencyLevel = Literal["none", "proficient", "expertise"]


@dataclass(frozen=True)
class DerivedIssue:
    severity: Literal["error", "warning"]
    category: str
    message: str
    source_identity: str | None = None


@dataclass(frozen=True)
class CalculationComponent:
    label: str
    value: int
    source_identity: str | None = None


@dataclass(frozen=True)
class DerivedValue:
    value: int | None
    state: DerivedState
    components: tuple[CalculationComponent, ...] = ()
    formula: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AbilityScoreResult:
    ability: str
    base: int | None
    adjustments: tuple[CalculationComponent, ...]
    final: int | None
    modifier: int | None
    state: DerivedState


@dataclass(frozen=True)
class ProficiencySource:
    label: str
    source_identity: str | None
    source_rule: str | None


@dataclass(frozen=True)
class EffectiveProficiency:
    kind: str
    key: str
    level: ProficiencyLevel
    sources: tuple[ProficiencySource, ...]


@dataclass(frozen=True)
class SavingThrowResult:
    ability: str
    ability_modifier: int | None
    proficiency: ProficiencyLevel
    proficiency_bonus: int
    result: DerivedValue


@dataclass(frozen=True)
class SkillResult:
    key: str
    name: str
    ability: str
    ability_modifier: int | None
    proficiency: ProficiencyLevel
    proficiency_bonus: int
    result: DerivedValue


@dataclass(frozen=True)
class MovementSpeedResult:
    kind: str
    feet: int | None
    state: DerivedState
    sources: tuple[ProficiencySource, ...]


@dataclass(frozen=True)
class HitDiePool:
    die_size: int
    count: int
    classes: tuple[PublishedReference, ...]


@dataclass(frozen=True)
class HitPointLevel:
    total_level: int
    class_reference: PublishedReference
    class_level: int
    hit_die: int | None
    die_result: int | None
    constitution_modifier: int | None
    hit_points_gained: int | None
    choice_kind: str
    state: DerivedState


@dataclass(frozen=True)
class HitPointsResult:
    maximum: int | None
    state: DerivedState
    levels: tuple[HitPointLevel, ...]
    constitution_modifier: int | None
    reason: str | None = None


@dataclass(frozen=True)
class ArmorClassResult:
    value: int | None
    state: DerivedState
    formula: str | None
    components: tuple[CalculationComponent, ...]
    reason: str | None = None


@dataclass(frozen=True)
class FeatureReference:
    reference: PublishedReference
    source_identity: str
    source_label: str
    source_rule: str
    class_level: int | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class AttackSummary:
    weapon: PublishedReference
    ability: str | None
    proficient: bool | None
    attack_bonus: int | None
    damage: str | None
    damage_type: str | None
    range_feet: tuple[int, ...]
    properties: tuple[str, ...]
    mastery: tuple[str, ...]
    state: DerivedState
    reason: str | None = None


@dataclass(frozen=True)
class SpellSelectionResult:
    spell: PublishedReference
    acquisition: str
    source_rule: str | None
    valid: bool | None
    issue: str | None = None
    level: int | None = None
    school: str | None = None


@dataclass(frozen=True)
class SpellSlot:
    spell_level: int
    count: int


@dataclass(frozen=True)
class PactMagicResult:
    slot_count: int
    slot_level: int
    class_level: int
    source: str


@dataclass(frozen=True)
class SpellcastingProfile:
    owner_type: str
    owner: PublishedReference
    class_reference: PublishedReference | None
    class_level: int | None
    spellcasting_ability: str | None
    spell_save_dc: DerivedValue
    spell_attack_bonus: DerivedValue
    acquisition: str
    cantrips_known: int | None
    prepared_spells: int | None
    known_spells: int | None
    accessible_spell_levels: tuple[int, ...]
    individual_slots: tuple[SpellSlot, ...]
    spells: tuple[SpellSelectionResult, ...]


@dataclass(frozen=True)
class MulticlassValidation:
    class_reference: PublishedReference
    status: Literal["satisfied", "unsatisfied", "unresolved"]
    reason: str


@dataclass(frozen=True)
class DerivedCharacter:
    character_id: str
    edition: str
    total_level: int
    ability_scores: tuple[AbilityScoreResult, ...]
    proficiency_bonus: DerivedValue
    saving_throws: tuple[SavingThrowResult, ...]
    skills: tuple[SkillResult, ...]
    passive_perception: DerivedValue
    initiative: DerivedValue
    armor_class: ArmorClassResult
    hit_points: HitPointsResult
    hit_dice: tuple[HitDiePool, ...]
    speed: tuple[MovementSpeedResult, ...]
    proficiencies: tuple[EffectiveProficiency, ...]
    features: tuple[FeatureReference, ...]
    attacks: tuple[AttackSummary, ...]
    spellcasting_profiles: tuple[SpellcastingProfile, ...]
    effective_caster_level: int
    spell_slots: tuple[SpellSlot, ...]
    spell_slots_state: DerivedState
    pact_magic: tuple[PactMagicResult, ...]
    multiclass_validation: tuple[MulticlassValidation, ...]
    issues: tuple[DerivedIssue, ...]

    @property
    def warnings(self) -> tuple[DerivedIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def unresolved(self) -> tuple[DerivedIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.category.startswith("unresolved") or issue.category == "stale_reference"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the full stable, JSON-compatible derivation and provenance."""

        return asdict(self)
