"""Serializable user-owned character state; derived game statistics are absent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PublishedReference:
    """An exact published identity with a display snapshot and live availability."""

    kind: str
    identity: str
    name: str
    edition: str
    missing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CharacterLevel:
    total_level: int
    class_reference: PublishedReference
    class_level: int


@dataclass(frozen=True)
class SubclassSelection:
    class_reference: PublishedReference
    subclass_reference: PublishedReference
    selected_class_level: int


@dataclass(frozen=True)
class AbilityModification:
    modification_id: int
    ability: str
    amount: int
    source_kind: str
    source_label: str
    owner_dataset_id: str | None = None
    owner_type: str | None = None
    owner_key: str | None = None
    choice_key: str | None = None
    option_key: str | None = None
    character_level: int | None = None
    class_identity: str | None = None
    class_level: int | None = None
    source_rule: str | None = None


@dataclass(frozen=True)
class DecisionProvenance:
    owner_dataset_id: str | None = None
    owner_type: str | None = None
    owner_key: str | None = None
    choice_key: str | None = None
    option_key: str | None = None
    character_level: int | None = None
    class_identity: str | None = None
    class_level: int | None = None
    source_rule: str | None = None


@dataclass(frozen=True)
class AbilityChange:
    ability: str
    amount: int
    source_kind: str
    source_label: str
    provenance: DecisionProvenance = field(default_factory=DecisionProvenance)


@dataclass(frozen=True)
class ChoiceResolution:
    resolution_id: int
    owner_dataset_id: str
    owner_type: str
    owner_key: str
    choice_key: str
    selected_option_key: str | None
    selected_reference: PublishedReference | None
    selected_value: str | None
    resolution_state: str
    character_level: int | None
    class_identity: str | None
    class_level: int | None
    source_rule: str


@dataclass(frozen=True)
class FeatSelection:
    selection_id: int
    feat_reference: PublishedReference
    provenance_kind: str
    provenance_label: str
    owner_dataset_id: str | None = None
    owner_type: str | None = None
    owner_key: str | None = None
    choice_key: str | None = None
    option_key: str | None = None
    character_level: int | None = None
    class_identity: str | None = None
    class_level: int | None = None
    source_rule: str | None = None
    resolution_state: str = "resolved"


@dataclass(frozen=True)
class SpellSelection:
    selection_id: int
    spell_reference: PublishedReference
    acquisition: str
    source_class_reference: PublishedReference | None = None
    character_level: int | None = None
    class_level: int | None = None
    owner_dataset_id: str | None = None
    owner_type: str | None = None
    owner_key: str | None = None
    choice_key: str | None = None
    option_key: str | None = None
    source_rule: str | None = None
    resolution_state: str = "resolved"


@dataclass(frozen=True)
class EquipmentRecord:
    equipment_id: int
    item_reference: PublishedReference | None
    unresolved_selection: str | None
    quantity: int
    equipped: bool
    carried_state: str
    provenance_kind: str
    provenance_label: str
    owner_dataset_id: str | None = None
    owner_type: str | None = None
    owner_key: str | None = None
    choice_key: str | None = None
    option_key: str | None = None
    character_level: int | None = None
    class_identity: str | None = None
    class_level: int | None = None
    source_rule: str | None = None
    resolution_state: str = "resolved"


@dataclass(frozen=True)
class CurrencyRecord:
    currency_id: int
    currency: str
    amount: int
    provenance_kind: str
    provenance_label: str
    owner_dataset_id: str | None = None
    owner_type: str | None = None
    owner_key: str | None = None
    choice_key: str | None = None
    option_key: str | None = None
    source_rule: str | None = None


@dataclass(frozen=True)
class HitPointChoice:
    total_level: int
    class_reference: PublishedReference
    class_level: int
    choice_kind: str
    amount: int


@dataclass(frozen=True)
class Character:
    character_id: str
    name: str
    edition: str
    state: str
    schema_version: int
    name_confirmed: bool
    ability_score_method: str | None
    created_at: str
    updated_at: str
    species: PublishedReference | None = None
    background: PublishedReference | None = None
    levels: tuple[CharacterLevel, ...] = ()
    subclasses: tuple[SubclassSelection, ...] = ()
    base_ability_scores: tuple[tuple[str, int], ...] = ()
    ability_modifications: tuple[AbilityModification, ...] = ()
    choices: tuple[ChoiceResolution, ...] = ()
    feats: tuple[FeatSelection, ...] = ()
    spells: tuple[SpellSelection, ...] = ()
    equipment: tuple[EquipmentRecord, ...] = ()
    currency: tuple[CurrencyRecord, ...] = ()
    hp_choices: tuple[HitPointChoice, ...] = ()
    notes: str = ""

    @property
    def total_level(self) -> int:
        return len(self.levels)

    @property
    def class_levels(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for row in self.levels:
            totals[row.class_reference.identity] = row.class_level
        return totals

    @property
    def starting_class(self) -> PublishedReference | None:
        return self.levels[0].class_reference if self.levels else None

    @property
    def class_summary(self) -> str:
        ordered: list[tuple[str, str, int]] = []
        positions: dict[str, int] = {}
        for row in self.levels:
            identity = row.class_reference.identity
            index = positions.get(identity)
            if index is None:
                positions[identity] = len(ordered)
                ordered.append((identity, row.class_reference.name, row.class_level))
            else:
                identity, name, _ = ordered[index]
                ordered[index] = (identity, name, row.class_level)
        return " / ".join(f"{name} {level}" for _identity, name, level in ordered)

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible snapshot including structural derivations."""

        value = asdict(self)
        value["total_level"] = self.total_level
        tracks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in self.levels:
            identity = row.class_reference.identity
            if identity in seen:
                continue
            seen.add(identity)
            tracks.append(
                {
                    "class_identity": identity,
                    "class_name": row.class_reference.name,
                    "levels": self.class_levels[identity],
                }
            )
        value["class_levels"] = tracks
        value["starting_class_identity"] = (
            self.starting_class.identity if self.starting_class is not None else None
        )
        value["class_summary"] = self.class_summary
        return value


@dataclass(frozen=True)
class CharacterSummary:
    character_id: str
    name: str
    edition: str
    state: str
    total_level: int
    class_summary: str
    updated_at: str

    @property
    def display_summary(self) -> str:
        if self.total_level == 0:
            return f"{self.name}\nDraft\nLevel 0"
        details = [self.name]
        if self.state == "draft":
            details.append("Draft")
        details.append(f"Level {self.total_level}")
        if self.class_summary:
            details.append(self.class_summary)
        details.append(self.edition)
        return "\n".join(details)


@dataclass(frozen=True)
class CharacterValidationIssue:
    code: str
    severity: str
    message: str
    path: str | None = None
    reference_identity: str | None = None


@dataclass(frozen=True)
class CharacterValidationReport:
    character_id: str
    issues: tuple[CharacterValidationIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)
