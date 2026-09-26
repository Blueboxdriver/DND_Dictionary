"""Structured models for prospective character-level advancement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .character import PublishedReference
from .character_builder import ChoiceKind
from .derived_character import DerivedCharacter


@dataclass(frozen=True)
class ProgressionIssue:
    severity: Literal["error", "warning"]
    category: str
    message: str


@dataclass(frozen=True)
class AdvancementOption:
    class_reference: PublishedReference
    current_class_level: int
    next_class_level: int
    is_new_multiclass: bool
    status: Literal["satisfied", "unsatisfied", "unresolved"]
    reason: str


@dataclass(frozen=True)
class ProgressionEventSummary:
    owner_type: str
    owner_identity: str
    event_key: str
    class_level: int
    title: str
    source_rule: str
    grant_count: int


@dataclass(frozen=True)
class ProgressionChoice:
    key: str
    owner_name: str
    kind: ChoiceKind
    count: int
    source_rule: str
    event_key: str | None
    selected: tuple[str, ...] = ()
    selected_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProgressionChoiceOption:
    selection_key: str
    label: str
    reference: PublishedReference | None
    value: str | None
    summary: str
    status: Literal["satisfied", "unsatisfied", "unresolved"]
    reason: str


@dataclass(frozen=True)
class SpellProgressionChange:
    owner_name: str
    class_level: int
    previous_cantrips: int | None
    cantrips: int | None
    previous_known: int | None
    known: int | None
    previous_prepared: int | None
    prepared: int | None
    standard_slots: tuple[tuple[int, int], ...]
    pact_slots: tuple[tuple[int, int], ...]
    required_choices: int = 0
    acquisition: str | None = None
    accessible_spell_levels_before: tuple[int, ...] = ()
    accessible_spell_levels_after: tuple[int, ...] = ()


@dataclass(frozen=True)
class ProgressionSpellChoice:
    key: str
    label: str
    acquisition: str
    count: int
    selected: tuple[PublishedReference, ...]
    source_rule: str
    max_spell_level: int
    spell_levels: tuple[int, ...]


@dataclass(frozen=True)
class LevelUpPreview:
    draft_id: str
    current_total_level: int
    next_total_level: int
    selected_class: PublishedReference
    previous_class_level: int
    next_class_level: int
    is_new_multiclass: bool
    automatic_grants: tuple[ProgressionEventSummary, ...]
    pending_choices: tuple[ProgressionChoice, ...]
    spell_choices: tuple[ProgressionSpellChoice, ...]
    hp_die: int | None
    fixed_hp_gain: int | None
    hp_choice_kind: str | None
    hp_amount: int | None
    subclass_choice: PublishedReference | None
    derived_before: DerivedCharacter
    derived_after: DerivedCharacter
    issues: tuple[ProgressionIssue, ...]
    spell_progression: tuple[SpellProgressionChange, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def is_complete(self) -> bool:
        return self.is_valid and self.hp_choice_kind is not None
