"""Transactional, draft-scoped advancement for saved 2024 characters."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace

from .character_builder import evaluate_requirement
from .character_creation import (
    BuilderChoice,
    BuilderChoiceOption,
    CharacterCreationService,
    SpellChoiceGroup,
)
from .characters import CharacterService
from .derived_character import DerivedCharacterService
from .models.character import (
    AbilityModification,
    Character,
    CharacterLevel,
    ChoiceResolution,
    DecisionProvenance,
    FeatSelection,
    HitPointChoice,
    PublishedReference,
    SpellSelection,
    SubclassSelection,
)
from .models.character_builder import Ability, ChoiceKind, Requirement
from .models.derived_character import DerivedCharacter
from .models.progression import (
    AdvancementOption,
    LevelUpPreview,
    ProgressionChoice,
    ProgressionChoiceOption,
    ProgressionEventSummary,
    ProgressionIssue,
    ProgressionSpellChoice,
    SpellProgressionChange,
)
from .storage.database import Database


@dataclass
class _SpellDraft:
    group: SpellChoiceGroup
    selected: list[PublishedReference] = field(default_factory=list)


@dataclass
class _LevelDraft:
    draft_id: str
    character_id: str
    before: Character
    class_reference: PublishedReference
    previous_class_level: int
    next_class_level: int
    new_multiclass: bool
    hp_die: int | None
    fixed_hp: int | None
    before_derived: DerivedCharacter
    event_summaries: tuple[ProgressionEventSummary, ...]
    spell_change: SpellProgressionChange | None
    issues: list[ProgressionIssue]
    choices: dict[str, BuilderChoice] = field(default_factory=dict)
    selections: dict[str, list[BuilderChoiceOption]] = field(default_factory=dict)
    spells: dict[str, _SpellDraft] = field(default_factory=dict)
    hp_choice_kind: str | None = None
    hp_amount: int | None = None
    subclass: PublishedReference | None = None
    preview: LevelUpPreview | None = None
    subclass_spell_change: SpellProgressionChange | None = None


class CharacterProgressionService:
    """Orchestrate progression choices without mutating the saved character draft."""

    def __init__(
        self,
        database: Database,
        characters: CharacterService | None = None,
        creation: CharacterCreationService | None = None,
        derived: DerivedCharacterService | None = None,
    ) -> None:
        self.database = database
        self.characters = characters or CharacterService(database)
        self.creation = creation or CharacterCreationService(database, self.characters)
        self.derived = derived or DerivedCharacterService(database, self.characters)
        self._drafts: dict[str, _LevelDraft] = {}
        self._advancement_options_cache: dict[
            str, tuple[str, tuple[AdvancementOption, ...]]
        ] = {}

    def get_advancement_options(self, character_id: str) -> tuple[AdvancementOption, ...]:
        """List current class tracks and all exact 2024 multiclass owners with reasons."""
        character = self.characters.get_character(character_id)
        cached = self._advancement_options_cache.get(character_id)
        if cached is not None and cached[0] == character.updated_at:
            return cached[1]
        current = {row.class_reference.identity: row.class_reference for row in character.levels}
        classes = self.creation.list_options("class", limit=50)
        references: dict[str, PublishedReference] = {
            identity: reference for identity, reference in current.items()
        }
        references.update(
            {
                row.identity: PublishedReference("class", row.identity, row.name, row.edition)
                for row in classes
                if row.edition == "2024"
            }
        )
        result: list[AdvancementOption] = []
        derived = self.derived.derive(character) if character.total_level < 20 else None
        for identity, reference in sorted(
            references.items(), key=lambda item: item[1].name.casefold()
        ):
            level = character.class_levels.get(identity, 0)
            is_new = level == 0
            if character.total_level >= 20:
                status, reason = "unsatisfied", "Character level 20 is the maximum."
            elif is_new:
                evaluation = self._multiclass_requirement(character, reference, derived)
                status, reason = evaluation[0], evaluation[1]
            else:
                status, reason = "satisfied", "Advance this existing class track."
            result.append(AdvancementOption(reference, level, level + 1, is_new, status, reason))
        options = tuple(result)
        self._advancement_options_cache[character_id] = (character.updated_at, options)
        return options

    def get_multiclass_options(self, character_id: str) -> tuple[AdvancementOption, ...]:
        return tuple(
            option
            for option in self.get_advancement_options(character_id)
            if option.is_new_multiclass
        )

    def start_level_preview(self, character_id: str, class_identity: str) -> LevelUpPreview:
        character = self.characters.get_character(character_id)
        if character.edition != "2024":
            raise ValueError("Level Up supports 2024 characters only.")
        if character.total_level >= 20:
            raise ValueError(
                "Character level 20 is the maximum; further advancement is unavailable."
            )
        self._validate_starting_character(character)
        option = next(
            (
                row
                for row in self.get_advancement_options(character_id)
                if row.class_reference.identity == class_identity
            ),
            None,
        )
        if option is None:
            raise ValueError("class is not an exact 2024 builder class")
        if option.status != "satisfied":
            raise ValueError(option.reason)
        before = self.derived.derive(character)
        owner = self.creation.get_owner("class", class_identity)
        if owner is None:
            raise ValueError("class progression rules are unavailable for this exact reference")
        level = option.next_class_level
        events = self._events(owner.dataset_id, "class", owner.owner_key, level)
        summaries = list(events)
        issues: list[ProgressionIssue] = []
        for event in events:
            unresolved = self._unresolved_event_grants(
                owner.dataset_id, "class", owner.owner_key, event.event_key
            )
            for source_rule in unresolved:
                issues.append(
                    ProgressionIssue(
                        "warning",
                        "unresolved_grant",
                        f"{event.title}: a structured grant remains unresolved ({source_rule}).",
                    )
                )

        die = self._hit_die(class_identity)
        if die is None:
            issues.append(
                ProgressionIssue(
                    "error", "hit_die_unresolved", "Class Hit Die metadata is unavailable."
                )
            )
        spell_change, spell_groups = self._spell_progression(
            character, option.class_reference, level, before
        )
        if spell_change is not None:
            summaries = summaries
        draft = _LevelDraft(
            str(uuid.uuid4()),
            character_id,
            character,
            option.class_reference,
            option.current_class_level,
            level,
            option.is_new_multiclass,
            die,
            (die // 2 + 1) if die is not None else None,
            before,
            tuple(summaries),
            spell_change,
            issues,
        )
        for choice in self._progression_choices(owner, character, level, option.is_new_multiclass):
            draft.choices[self._choice_key(choice)] = choice
        selected_subclass = next(
            (
                row.subclass_reference
                for row in character.subclasses
                if row.class_reference.identity == class_identity
            ),
            None,
        )
        sub_owner = None
        if selected_subclass is not None:
            sub_owner = self.creation.get_owner("subclass", selected_subclass.identity)
            if sub_owner is None:
                draft.issues.append(
                    ProgressionIssue(
                        "error",
                        "unresolved_subclass",
                        f"Selected subclass {selected_subclass.name} is stale; its exact rules "
                        "owner is missing.",
                    )
                )
            else:
                draft.event_summaries += self._events(
                    sub_owner.dataset_id, "subclass", sub_owner.owner_key, level
                )
                for choice in self._progression_choices(
                    sub_owner, character, level, False, class_identity
                ):
                    draft.choices[self._choice_key(choice)] = choice
                subclass_change, subclass_groups = self._spell_progression(
                    character,
                    option.class_reference,
                    level,
                    before,
                    owner_type="subclass",
                    owner_key=sub_owner.owner_key,
                )
                draft.subclass_spell_change = subclass_change
                draft.spells.update(subclass_groups)
        elif self._subclass_required(owner, level):
            choices = [
                item
                for item in draft.choices.values()
                if item.definition.kind is ChoiceKind.SUBCLASS
            ]
            if not choices:
                draft.issues.append(
                    ProgressionIssue(
                        "error",
                        "subclass_choice_unresolved",
                        "This class level requires a subclass choice, but no structured "
                        "options are available.",
                    )
                )
        draft.spells.update(spell_groups)
        self._drafts[draft.draft_id] = draft
        return self._refresh_preview(draft)

    def get_preview(self, draft_id: str) -> LevelUpPreview:
        return self._require_draft(draft_id).preview  # type: ignore[return-value]

    def get_choice_options(
        self,
        draft_id: str,
        choice_key: str,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[ProgressionChoiceOption, ...]:
        draft = self._require_draft(draft_id)
        choice = draft.choices[choice_key]
        if not self._choice_is_active(draft, choice):
            return ()
        raw_options = self._choice_options(draft, choice, query, offset, limit)
        prospective = self._prospective_character(draft)
        context = self.creation._requirement_context(prospective)
        scores = {
            row.ability: row.final
            for row in draft.preview.derived_after.ability_scores
            if row.final is not None
        }
        choice_status, choice_reason = self._choice_requirement_status(choice, context)
        result = []
        for raw in raw_options:
            status, reason = self._option_requirement_status(raw, choice, context, scores)
            if choice_status != "satisfied":
                status, reason = choice_status, choice_reason
            result.append(
                ProgressionChoiceOption(
                    self._option_key(raw),
                    raw.label,
                    raw.reference,
                    raw.value,
                    raw.summary,
                    status,
                    reason,
                )
            )
        return tuple(result)

    def apply_pending_choice(
        self, draft_id: str, choice_key: str, option_key: str
    ) -> LevelUpPreview:
        draft = self._require_draft(draft_id)
        choice = draft.choices[choice_key]
        if not self._choice_is_active(draft, choice):
            raise ValueError("This choice is not enabled by its published parent choice.")
        option = self._find_choice_option(draft, choice, option_key)
        if option is None:
            raise ValueError("choice option is not in the exact published choice list")
        context = self.creation._requirement_context(self._prospective_character(draft))
        choice_status, choice_reason = self._choice_requirement_status(choice, context)
        if choice_status != "satisfied":
            raise ValueError(choice_reason)
        scores = {
            row.ability: row.final
            for row in draft.preview.derived_after.ability_scores
            if row.final is not None
        }
        status, reason = self._option_requirement_status(option, choice, context, scores)
        if status != "satisfied":
            raise ValueError(reason)
        selected = draft.selections.setdefault(choice_key, [])
        replaced_selections = tuple(selected)
        same = next((row for row in selected if self._option_key(row) == option_key), None)
        if same is not None:
            selected.remove(same)
        else:
            if len(selected) >= choice.definition.count:
                if choice.definition.count != 1:
                    raise ValueError("choice selection count has reached its published limit")
                selected.clear()
            selected.append(option)
        if option.reference is not None and option.reference.kind == "subclass":
            previous_subclasses = {
                row.reference.identity
                for row in replaced_selections
                if row.reference is not None and row.reference.kind == "subclass"
            }
            draft.subclass = option.reference if selected else None
            draft.subclass_spell_change = None
            for spell_key in [
                key for key, value in draft.spells.items() if value.group.owner_type == "subclass"
            ]:
                draft.spells.pop(spell_key, None)
            for old_identity in previous_subclasses - (
                {option.reference.identity} if selected else set()
            ):
                old_owner = self.creation.get_owner("subclass", old_identity)
                if old_owner is not None:
                    old_keys = [
                        key
                        for key, item in draft.choices.items()
                        if item.owner.owner_type == "subclass"
                        and item.owner.owner_key == old_owner.owner_key
                    ]
                    for old_key in old_keys:
                        draft.choices.pop(old_key, None)
                        draft.selections.pop(old_key, None)
            class_events = tuple(row for row in draft.event_summaries if row.owner_type == "class")
            subclass_events: tuple[ProgressionEventSummary, ...] = ()
            if draft.subclass is not None:
                owner = self.creation.get_owner("subclass", draft.subclass.identity)
                if owner is not None:
                    subclass_events = self._events(
                        owner.dataset_id, "subclass", owner.owner_key, draft.next_class_level
                    )
                    for event in subclass_events:
                        for source_rule in self._unresolved_event_grants(
                            owner.dataset_id, "subclass", owner.owner_key, event.event_key
                        ):
                            draft.issues.append(
                                ProgressionIssue(
                                    "warning",
                                    "unresolved_grant",
                                    f"{event.title}: a structured grant remains unresolved "
                                    f"({source_rule}).",
                                )
                            )
                    for child in self._progression_choices(
                        owner,
                        draft.before,
                        draft.next_class_level,
                        False,
                        draft.class_reference.identity,
                    ):
                        draft.choices[self._choice_key(child)] = child
                    subclass_change, subclass_groups = self._spell_progression(
                        draft.before,
                        draft.class_reference,
                        draft.next_class_level,
                        draft.before_derived,
                        owner_type="subclass",
                        owner_key=owner.owner_key,
                        subclass_reference=draft.subclass,
                    )
                    draft.subclass_spell_change = subclass_change
                    draft.spells.update(subclass_groups)
            draft.event_summaries = class_events + subclass_events
        if option.reference is not None and option.reference.kind == "feat" and selected:
            previous_feats = {
                row.reference.identity
                for row in replaced_selections
                if row.reference is not None and row.reference.kind == "feat"
            }
            for old_identity in previous_feats - {option.reference.identity}:
                old_owner = self.creation.get_owner("feat", old_identity)
                if old_owner is not None:
                    old_keys = [
                        key
                        for key, item in draft.choices.items()
                        if item.owner.owner_type == "feat"
                        and item.owner.owner_key == old_owner.owner_key
                    ]
                    for old_key in old_keys:
                        draft.choices.pop(old_key, None)
                        draft.selections.pop(old_key, None)
            self._add_feat_choices(draft, option.reference)
        elif option.reference is not None and option.reference.kind == "feat":
            old_owner = self.creation.get_owner("feat", option.reference.identity)
            if old_owner is not None:
                old_keys = [
                    key
                    for key, item in draft.choices.items()
                    if item.owner.owner_type == "feat"
                    and item.owner.owner_key == old_owner.owner_key
                ]
                for old_key in old_keys:
                    draft.choices.pop(old_key, None)
                    draft.selections.pop(old_key, None)
        return self._refresh_preview(draft)

    def get_spell_options(
        self,
        draft_id: str,
        spell_choice_key: str,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[PublishedReference, ...]:
        draft = self._require_draft(draft_id)
        spell = draft.spells[spell_choice_key]
        options = self.creation.spell_options(
            draft.character_id, spell.group, query=query, offset=offset, limit=limit
        )
        selected = {row.identity for row in spell.selected}
        existing = {
            row.spell_reference.identity for row in self._prospective_character(draft).spells
        }
        return tuple(
            row.reference
            for row in options
            if row.reference is not None
            and row.reference.identity not in selected
            and row.reference.identity not in existing
        )

    def select_spell(
        self, draft_id: str, spell_choice_key: str, spell: PublishedReference
    ) -> LevelUpPreview:
        draft = self._require_draft(draft_id)
        pending = draft.spells[spell_choice_key]
        if spell.kind != "spell" or spell.edition != "2024":
            raise ValueError("Choose an exact 2024 spell.")
        matches = self.creation.spell_options(
            draft.character_id, pending.group, query=spell.name, limit=50
        )
        if not any(row.reference and row.reference.identity == spell.identity for row in matches):
            raise ValueError(
                "Spell is not on the exact spell list or is above this class's access level."
            )
        if any(row.identity == spell.identity for row in pending.selected):
            raise ValueError("Spell is already selected for this level.")
        if len(pending.selected) >= pending.group.count:
            raise ValueError("The published spell choice count has been reached.")
        pending.selected.append(spell)
        return self._refresh_preview(draft)

    def set_hp_choice(
        self, draft_id: str, choice_kind: str, amount: int | None = None
    ) -> LevelUpPreview:
        draft = self._require_draft(draft_id)
        if draft.hp_die is None:
            raise ValueError("Class Hit Die is unresolved.")
        if choice_kind == "fixed_average":
            amount = draft.fixed_hp
        elif choice_kind == "rolled":
            if not isinstance(amount, int) or not 1 <= amount <= draft.hp_die:
                raise ValueError(f"Manual roll must be between 1 and {draft.hp_die}.")
        else:
            raise ValueError("HP decision must be fixed_average or rolled")
        draft.hp_choice_kind = choice_kind
        draft.hp_amount = amount
        return self._refresh_preview(draft)

    def validate_preview(self, draft_id: str) -> tuple[ProgressionIssue, ...]:
        draft = self._require_draft(draft_id)
        return self._build_issues(draft)

    def derive_preview(self, draft_id: str) -> DerivedCharacter:
        return self._require_draft(draft_id).preview.derived_after  # type: ignore[union-attr]

    def commit_level(self, draft_id: str) -> Character:
        draft = self._require_draft(draft_id)
        issues = self._build_issues(draft)
        blocking = [issue for issue in issues if issue.severity == "error"]
        if blocking:
            raise ValueError(blocking[0].message)
        if draft.new_multiclass:
            status, reason = self._multiclass_requirement(draft.before, draft.class_reference)
            if status != "satisfied":
                raise ValueError(reason)
        try:
            with self.database.transaction():
                current = self.characters.get_character(draft.character_id)
                if current != draft.before:
                    raise ValueError("Saved character changed during level-up; cancel and restart.")
                self.characters.add_class_level(draft.character_id, draft.class_reference)
                assert draft.hp_choice_kind is not None and draft.hp_amount is not None
                self.characters.set_hp_choice(
                    draft.character_id,
                    draft.before.total_level + 1,
                    draft.hp_choice_kind,
                    draft.hp_amount,
                )
                if draft.subclass is not None:
                    self.characters.set_subclass(
                        draft.character_id,
                        draft.class_reference.identity,
                        draft.subclass,
                        draft.next_class_level,
                    )
                for key, choice in draft.choices.items():
                    for option in draft.selections.get(key, ()):
                        self.creation.select_choice_option(draft.character_id, choice, option)
                for pending in draft.spells.values():
                    for spell in pending.selected:
                        provenance = DecisionProvenance(
                            owner_dataset_id=pending.group.owner_dataset_id,
                            owner_type=pending.group.owner_type,
                            owner_key=pending.group.owner_key,
                            choice_key=pending.group.choice_key,
                            option_key=pending.group.option_key,
                            character_level=draft.before.total_level + 1,
                            class_identity=draft.class_reference.identity,
                            class_level=draft.next_class_level,
                            source_rule=pending.group.source_rule,
                        )
                        self.characters.add_spell(
                            draft.character_id,
                            spell,
                            acquisition=pending.group.acquisition,
                            source_class=draft.class_reference,
                            character_level=draft.before.total_level + 1,
                            class_level=draft.next_class_level,
                            provenance=provenance,
                        )
                character = self.characters.get_character(draft.character_id)
                report = self.characters.validate_character(draft.character_id)
                errors = [issue for issue in report.issues if issue.severity == "error"]
                if errors:
                    raise ValueError(errors[0].message)
        except Exception:
            raise
        self._drafts.pop(draft_id, None)
        return character

    def cancel(self, draft_id: str) -> None:
        self._drafts.pop(draft_id, None)

    def undo_last_level(self, character_id: str) -> CharacterLevel:
        character = self.characters.get_character(character_id)
        if not character.levels:
            raise ValueError("character has no levels to undo")
        if character.total_level == 1:
            raise ValueError("The starting level cannot be undone; edit the character in creation.")
        last = character.levels[-1]
        with self.database.transaction():
            removed = self.characters.remove_last_class_level(character_id)
            if removed != last:
                raise ValueError("character history changed during undo")
        return removed

    def _validate_starting_character(self, character: Character) -> None:
        if character.edition != "2024":
            raise ValueError("Level Up supports 2024 characters only.")
        if character.state != "complete":
            raise ValueError("Draft character cannot level up. Resume Character Creation first.")
        report = self.creation.validate_creation(character.character_id)
        errors = [
            issue
            for issue in report.issues
            if issue.severity == "error" and issue.code != "level_one_required"
        ]
        if errors:
            raise ValueError(f"Character creation is incomplete: {errors[0].message}")
        validation = self.characters.validate_character(character.character_id)
        errors = [issue for issue in validation.issues if issue.severity == "error"]
        if errors:
            raise ValueError(errors[0].message)
        derived = self.derived.derive(character)
        errors = [issue for issue in derived.issues if issue.severity == "error"]
        if errors:
            raise ValueError(errors[0].message)

    def _multiclass_requirement(
        self,
        character: Character,
        reference: PublishedReference,
        derived: DerivedCharacter | None = None,
    ) -> tuple[str, str]:
        owner = self.creation.get_owner("class", reference.identity)
        if owner is None:
            return "unresolved", "Class prerequisite rules are unavailable."
        try:
            with self.database.connection() as db:
                row = db.execute(
                    "SELECT payload_json FROM character_rule_requirements WHERE dataset_id=? "
                    "AND owner_type='class' AND owner_key=? AND scope='multiclass_entry'",
                    (owner.dataset_id, owner.owner_key),
                ).fetchone()
            if row is None:
                return "unresolved", "Multiclass prerequisite metadata is missing."
            requirement = Requirement.model_validate(json.loads(str(row[0])))
            context = self.creation._requirement_context(character)
            final_scores = {
                Ability(row.ability): row.final
                for row in (derived or self.derived.derive(character)).ability_scores
                if row.final is not None
            }
            class_levels = {
                identity.split(":", 1)[1]: level
                for identity, level in character.class_levels.items()
            }
            primary = self._primary_abilities(character, owner.dataset_id)
            primary[owner.owner_key] = self._primary_options(owner.metadata)
            context = context.model_copy(
                update={
                    "edition": character.edition,
                    "total_level": character.total_level,
                    "class_levels": class_levels,
                    "ability_scores": final_scores,
                    "entry_mode": "multiclass",
                    "target_class_key": owner.owner_key,
                    "class_primary_abilities": primary,
                }
            )
            evaluated = evaluate_requirement(requirement, context)
            return evaluated.status, self._requirement_reason(
                requirement, context, evaluated.reason
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return "unresolved", f"Multiclass prerequisite could not be evaluated: {exc}"

    def _primary_abilities(
        self, character: Character, dataset_id: str
    ) -> dict[str, list[list[Ability]]]:
        result: dict[str, list[list[Ability]]] = {}
        for identity in character.class_levels:
            if ":" not in identity:
                continue
            data, key = identity.split(":", 1)
            if data != dataset_id:
                continue
            owner = self.creation.get_owner("class", identity)
            if owner is not None:
                result[key] = self._primary_options(owner.metadata)
        return result

    @staticmethod
    def _primary_options(metadata: dict[str, object]) -> list[list[Ability]]:
        raw = metadata.get("primary_ability_options", [])
        result = []
        if isinstance(raw, list):
            for group in raw:
                if isinstance(group, list):
                    try:
                        result.append([Ability(str(item)) for item in group])
                    except ValueError:
                        continue
        return result

    @staticmethod
    def _requirement_reason(requirement: Requirement, context, fallback: str) -> str:
        if requirement.operator.value in {"all", "any"}:
            children = [
                CharacterProgressionService._requirement_reason(
                    child, context, evaluate_requirement(child, context).reason
                )
                for child in requirement.children
            ]
            return "; ".join(children) if children else fallback
        return evaluate_requirement(requirement, context).reason

    def _progression_choices(
        self,
        owner,
        character: Character,
        level: int,
        new_multiclass: bool,
        class_identity: str | None = None,
    ) -> tuple[BuilderChoice, ...]:
        scopes = {"progression_event"}
        if new_multiclass:
            scopes.add("multiclass_entry")
        rows = self.creation._owner_choices(owner, scopes, character, progression_level=level)
        progression_class_identity = class_identity
        if progression_class_identity is None and owner.owner_type == "subclass":
            _, _, tail = owner.identity.partition(":subclass:")
            parent_key, _, _subclass_key = tail.partition(":")
            if parent_key:
                progression_class_identity = f"{owner.dataset_id}:{parent_key}"
        if progression_class_identity is None and owner.owner_type == "class":
            progression_class_identity = owner.identity
        return tuple(
            replace(
                choice,
                character_level=character.total_level + 1,
                class_identity=progression_class_identity,
                class_level=level,
            )
            for choice in rows
        )

    def _events(
        self, dataset_id: str, owner_type: str, owner_key: str, level: int
    ) -> tuple[ProgressionEventSummary, ...]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT e.event_key,e.class_level,e.title,e.source_rule,COUNT(g.grant_key) "
                "FROM class_progression_events e LEFT JOIN character_rule_grants g ON "
                "g.dataset_id=e.dataset_id AND g.owner_type=e.owner_type "
                "AND g.owner_key=e.owner_key AND g.event_key=e.event_key "
                "WHERE e.dataset_id=? AND e.owner_type=? AND e.owner_key=? AND e.class_level=? "
                "GROUP BY e.event_key,e.class_level,e.title,e.source_rule ORDER BY e.event_key",
                (dataset_id, owner_type, owner_key, level),
            ).fetchall()
        owner = self.creation.get_owner(owner_type, f"{dataset_id}:{owner_key}")
        identity = owner.identity if owner is not None else f"{dataset_id}:{owner_key}"
        if owner_type == "subclass":
            with self.database.connection() as db:
                parent = db.execute(
                    "SELECT parent_class_key FROM character_builder_owners "
                    "WHERE dataset_id=? AND owner_type='subclass' AND owner_key=?",
                    (dataset_id, owner_key),
                ).fetchone()
            if parent is not None and parent[0] is not None:
                identity = f"{dataset_id}:subclass:{parent[0]}:{owner_key}"
        return tuple(
            ProgressionEventSummary(
                owner_type,
                identity,
                str(row[0]),
                int(row[1]),
                str(row[2]),
                str(row[3]),
                int(row[4]),
            )
            for row in rows
        )

    def _unresolved_event_grants(
        self, dataset_id: str, owner_type: str, owner_key: str, event_key: str
    ) -> tuple[str, ...]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT source_rule FROM character_rule_grants WHERE dataset_id=? "
                "AND owner_type=? AND owner_key=? AND scope='progression_event' "
                "AND event_key=? AND unresolved=1 ORDER BY grant_key",
                (dataset_id, owner_type, owner_key, event_key),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _subclass_required(self, owner, level: int) -> bool:
        if owner is None:
            return False
        try:
            return int(owner.metadata.get("subclass_selection_level", 99)) == level
        except (TypeError, ValueError):
            return False

    def _hit_die(self, class_identity: str) -> int | None:
        dataset, key = class_identity.split(":", 1)
        with self.database.connection() as db:
            row = db.execute(
                "SELECT c.hit_die FROM entries e JOIN classes c ON c.entry_id=e.id "
                "WHERE e.dataset_id=? AND e.local_key=? AND e.kind='class'",
                (dataset, key),
            ).fetchone()
        return int(row[0]) if row else None

    def _spell_progression(
        self,
        character: Character,
        class_reference: PublishedReference,
        level: int,
        derived_before: DerivedCharacter,
        *,
        owner_type: str = "class",
        owner_key: str | None = None,
        subclass_reference: PublishedReference | None = None,
    ) -> tuple[SpellProgressionChange | None, dict[str, _SpellDraft]]:
        dataset, class_key = class_reference.identity.split(":", 1)
        owner_key = owner_key or class_key
        with self.database.connection() as db:
            casting = db.execute(
                "SELECT spellcasting_model,acquisition,source_rule,spell_list_reference_kind,"
                "spell_list_reference_identity,prepared_spells_change "
                "FROM class_spellcasting WHERE dataset_id=? AND owner_type=? AND owner_key=?",
                (dataset, owner_type, owner_key),
            ).fetchone()
            if casting is None:
                return None, {}
            rows = db.execute(
                "SELECT class_level,cantrips_known,prepared_spells,known_spells "
                "FROM class_spellcasting_levels WHERE dataset_id=? AND owner_type=? "
                "AND owner_key=? AND class_level<=? ORDER BY class_level DESC LIMIT 2",
                (dataset, owner_type, owner_key, level),
            ).fetchall()
            current = rows[0] if rows else None
            previous = rows[1] if len(rows) > 1 else None
        if current is None:
            return None, {}
        cantrips, prepared, known = (self._int_or_none(current[index]) for index in (1, 2, 3))
        old_cantrips, old_prepared, old_known = (
            (self._int_or_none(previous[index]) for index in (1, 2, 3))
            if previous is not None
            else (0, 0, 0)
        )
        # The prospective class level determines access. Use the same derived engine after
        # appending a structural level; the UI never calculates slot or spell-level arithmetic.
        projected = replace(
            character,
            levels=character.levels
            + (CharacterLevel(character.total_level + 1, class_reference, level),),
            subclasses=(
                character.subclasses
                + (SubclassSelection(class_reference, subclass_reference, level),)
                if subclass_reference is not None
                else character.subclasses
            ),
        )
        after = self.derived.derive(projected)
        if owner_type == "subclass":
            before_profile = next(
                (
                    profile
                    for profile in derived_before.spellcasting_profiles
                    if profile.owner_type == "subclass"
                    and profile.owner.identity.endswith(f":{owner_key}")
                ),
                None,
            )
            after_profile = next(
                (
                    profile
                    for profile in after.spellcasting_profiles
                    if profile.owner_type == "subclass"
                    and profile.owner.identity.endswith(f":{owner_key}")
                ),
                None,
            )
        else:
            before_profile = next(
                (
                    profile
                    for profile in derived_before.spellcasting_profiles
                    if profile.owner_type == "class"
                    and profile.class_reference is not None
                    and profile.class_reference.identity == class_reference.identity
                ),
                None,
            )
            after_profile = next(
                (
                    profile
                    for profile in after.spellcasting_profiles
                    if profile.owner_type == "class"
                    and profile.class_reference is not None
                    and profile.class_reference.identity == class_reference.identity
                ),
                None,
            )
        max_access = max(after_profile.accessible_spell_levels, default=0)
        groups: dict[str, _SpellDraft] = {}
        acquisition = str(casting[1])
        model = str(casting[0])
        prepared_change = str(casting[5]) if casting[5] is not None else ""
        spell_class_identities = self._spell_list_class_identities(
            dataset,
            owner_type,
            owner_key,
            level,
            str(casting[3]) if casting[3] is not None else None,
            str(casting[4]) if casting[4] is not None else None,
            class_reference,
        )
        spell_list = (
            self.creation._reference_for_identity("class", spell_class_identities[0])
            if spell_class_identities
            else class_reference
        )
        spell_count_before = old_known
        spell_count_after = known
        if spell_count_after is None and (model == "pact_magic" or prepared_change == "level"):
            spell_count_before = old_prepared
            spell_count_after = prepared
        spell_acquisition = (
            "pact_magic"
            if model == "pact_magic"
            else "spellbook"
            if acquisition == "spellbook"
            else acquisition
        )
        for name, old, new, acq, spell_levels in (
            ("cantrip", old_cantrips, cantrips, "cantrip", (0,)),
            (
                "spells",
                spell_count_before,
                spell_count_after,
                spell_acquisition,
                tuple(range(1, max_access + 1)),
            ),
        ):
            count = max(0, (new or 0) - (old or 0))
            if count <= 0:
                continue
            group = SpellChoiceGroup(
                key=f"levelup:{owner_type}:{owner_key}:{level}:{name}",
                label=f"Choose {count} new {name} selection(s)",
                acquisition=acq,
                count=count,
                class_reference=spell_list,
                max_spell_level=max_access,
                source_rule=str(casting[2]),
                owner_dataset_id=dataset,
                owner_type=owner_type,
                owner_key=owner_key,
                choice_key=f"{acq}-{level}",
                spell_class_identities=spell_class_identities,
                spell_levels=spell_levels,
                character_level=character.total_level + 1,
                character_class_identity=class_reference.identity,
                class_level=level,
            )
            groups[group.key] = _SpellDraft(group)
        if model == "pact_magic":
            spellcasting_source = (
                subclass_reference.identity
                if subclass_reference is not None
                else class_reference.identity
            )
            pact_profile = next(
                (row for row in after.pact_magic if row.source == spellcasting_source), None
            )
            pact_slots = (
                ((pact_profile.slot_level, pact_profile.slot_count),)
                if pact_profile is not None
                else ()
            )
            standard_slots = ()
        else:
            standard_slots = tuple((slot.spell_level, slot.count) for slot in after.spell_slots)
            pact_slots = ()
        change = SpellProgressionChange(
            class_reference.name,
            level,
            old_cantrips,
            cantrips,
            old_known,
            known,
            old_prepared,
            prepared,
            standard_slots,
            pact_slots,
            sum(group.group.count for group in groups.values()),
            acquisition,
            before_profile.accessible_spell_levels if before_profile else (),
            after_profile.accessible_spell_levels if after_profile else (),
        )
        return change, groups

    def _spell_list_class_identities(
        self,
        dataset_id: str,
        owner_type: str,
        owner_key: str,
        class_level: int,
        reference_kind: str | None,
        reference_identity: str | None,
        class_reference: PublishedReference,
    ) -> tuple[str, ...]:
        identities: set[str] = set()
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM character_builder_spell_access WHERE dataset_id=? "
                "AND owner_type=? AND owner_key=? AND class_level<=? "
                "ORDER BY class_level,access_key",
                (dataset_id, owner_type, owner_key, class_level),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row[0]))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            criteria = payload.get("criteria")
            if not isinstance(criteria, dict) or criteria.get("kind") != "spell_list":
                continue
            values = criteria.get("values")
            if not isinstance(values, list):
                continue
            identities.update(
                value if ":" in value else f"{dataset_id}:{value}"
                for value in values
                if isinstance(value, str) and value
            )
        if not identities and reference_identity and reference_kind == "class":
            identities.add(
                reference_identity
                if ":" in reference_identity
                else f"{dataset_id}:{reference_identity}"
            )
        if not identities:
            identities.add(class_reference.identity)
        valid = tuple(
            sorted(
                identity
                for identity in identities
                if (reference := self.creation._reference_for_identity("class", identity))
                is not None
                and not reference.missing
            )
        )
        return valid or (class_reference.identity,)

    @staticmethod
    def _int_or_none(value) -> int | None:
        return int(value) if value is not None else None

    @staticmethod
    def _slot_rows(db, dataset: str, owner_key: str, model: str, level: int):
        kind = "pact" if model == "pact_magic" else "standalone"
        rows = db.execute(
            "SELECT spell_level,slot_count FROM class_spell_slots WHERE dataset_id=? "
            "AND owner_type='class' AND owner_key=? AND progression_kind=? AND class_level=? "
            "AND slot_count>0 ORDER BY spell_level",
            (dataset, owner_key, kind, level),
        ).fetchall()
        return tuple((int(row[0]), int(row[1])) for row in rows)

    def _choice_options(
        self, draft: _LevelDraft, choice: BuilderChoice, query: str, offset: int, limit: int
    ):
        if choice.definition.options:
            needle = query.casefold().strip()
            rows = []
            for option in choice.definition.options:
                if needle and needle not in option.label.casefold():
                    continue
                reference = self._rule_reference(choice.owner.dataset_id, option.reference)
                rows.append(
                    BuilderChoiceOption(
                        str(option.option_key),
                        option.label,
                        str(option.value) if option.value is not None else None,
                        reference,
                        option.resolved,
                        ", ".join(
                            f"+{item.amount} {str(item.ability).upper()}"
                            for item in option.ability_increases
                        ),
                    )
                )
            return tuple(rows[offset : offset + limit])
        return self.creation.choice_options(
            draft.character_id, choice, query=query, offset=offset, limit=min(limit, 50)
        )

    def _find_choice_option(
        self, draft: _LevelDraft, choice: BuilderChoice, option_key: str
    ) -> BuilderChoiceOption | None:
        if choice.definition.options:
            return next(
                (
                    option
                    for option in self._choice_options(draft, choice, "", 0, 1000)
                    if self._option_key(option) == option_key
                ),
                None,
            )
        if option_key.startswith("reference:"):
            offset = 0
            while offset < 5000:
                candidates = self.creation.choice_options(
                    draft.character_id, choice, offset=offset, limit=50
                )
                selected = next(
                    (row for row in candidates if self._option_key(row) == option_key),
                    None,
                )
                if selected is not None:
                    return selected
                if len(candidates) < 50:
                    return None
                offset += 50
            return None
        elif option_key.startswith("value:"):
            value = option_key.removeprefix("value:")
            candidates = self.creation.choice_options(
                draft.character_id, choice, query=value, limit=50
            )
        else:
            return None
        return next((row for row in candidates if self._option_key(row) == option_key), None)

    def _rule_reference(self, dataset_id: str, reference) -> PublishedReference | None:
        if reference is None:
            return None
        identity = str(reference.identity)
        if ":" not in identity:
            identity = f"{dataset_id}:{identity}"
        if str(reference.kind) == "subclass":
            with self.database.connection() as db:
                row = db.execute(
                    "SELECT parent.local_key,s.subclass_key,s.name,source.edition "
                    "FROM subclasses s JOIN entries parent ON parent.id=s.class_id "
                    "JOIN sources source ON source.id=s.source_id "
                    "WHERE s.dataset_id=? AND s.subclass_key=? AND source.edition='2024'",
                    (dataset_id, identity.split(":", 1)[1]),
                ).fetchone()
            if row is None:
                return None
            return PublishedReference(
                "subclass",
                f"{dataset_id}:subclass:{row[0]}:{row[1]}",
                str(row[2]),
                str(row[3]),
            )
        return self.creation._reference_for_identity(str(reference.kind), identity)

    def _option_requirement_status(
        self,
        option: BuilderChoiceOption,
        choice: BuilderChoice,
        context,
        ability_scores: dict[str, int] | None = None,
    ):
        if not option.resolved:
            return "unresolved", "Published option is marked unresolved."
        if choice.definition.requirement is not None:
            status, reason = self._choice_requirement_status(choice, context)
            if status != "satisfied":
                return status, reason
        if option.reference is not None and any(
            self._same_rule_reference(
                option.reference.identity, exclusion.identity, choice.owner.dataset_id
            )
            for exclusion in choice.definition.exclusions
        ):
            return "unsatisfied", "This option is excluded by the published choice rules."
        source_option = next(
            (
                row
                for row in choice.definition.options
                if option.option_key is not None and str(row.option_key) == option.option_key
            ),
            None,
        )
        if source_option is not None and ability_scores is not None:
            increases: dict[str, int] = {}
            maxima: dict[str, int] = {}
            for increase in source_option.ability_increases:
                ability = str(increase.ability)
                increases[ability] = increases.get(ability, 0) + increase.amount
                if increase.maximum is not None:
                    maxima[ability] = min(maxima.get(ability, increase.maximum), increase.maximum)
            for ability, amount in increases.items():
                maximum = maxima.get(ability)
                if maximum is not None and ability_scores.get(ability, 0) + amount > maximum:
                    return (
                        "unsatisfied",
                        f"{ability.upper()} would exceed its maximum of {maximum}.",
                    )
        if option.reference is None or option.reference.kind != "feat":
            return "satisfied", "Published option is available."
        owner = self.creation.get_owner("feat", option.reference.identity)
        if owner is None:
            return "unresolved", "Feat prerequisite metadata is unavailable."
        with self.database.connection() as db:
            row = db.execute(
                "SELECT payload_json FROM character_rule_requirements WHERE dataset_id=? "
                "AND owner_type='feat' AND owner_key=? AND requirement_key='prerequisite' "
                "AND scope='feat_prerequisite'",
                (owner.dataset_id, owner.owner_key),
            ).fetchone()
        if row is None:
            return "satisfied", "No structured feat prerequisite."
        try:
            requirement = Requirement.model_validate(json.loads(str(row[0])))
            result = evaluate_requirement(requirement, context)
            return result.status, self._requirement_reason(requirement, context, result.reason)
        except (ValueError, TypeError) as exc:
            return "unresolved", f"Feat prerequisite is unresolved: {exc}"

    @staticmethod
    def _same_rule_reference(option_identity: str, excluded_identity: str, dataset_id: str) -> bool:
        if ":" not in excluded_identity:
            excluded_identity = f"{dataset_id}:{excluded_identity}"
        return option_identity == excluded_identity

    @staticmethod
    def _choice_requirement_status(choice: BuilderChoice, context):
        if choice.definition.requirement is None:
            return "satisfied", "Published choice requirements are met."
        try:
            result = evaluate_requirement(choice.definition.requirement, context)
            return result.status, result.reason
        except (KeyError, TypeError, ValueError) as exc:
            return "unresolved", f"Choice prerequisite is unresolved: {exc}"

    def _choice_is_active(self, draft: _LevelDraft, choice: BuilderChoice) -> bool:
        parent_key = choice.definition.depends_on_choice
        if parent_key is None:
            return True
        parent = next(
            (
                (key, candidate)
                for key, candidate in draft.choices.items()
                if candidate.owner.dataset_id == choice.owner.dataset_id
                and candidate.owner.owner_type == choice.owner.owner_type
                and candidate.owner.owner_key == choice.owner.owner_key
                and str(candidate.definition.choice_key) == str(parent_key)
            ),
            None,
        )
        if parent is None:
            return False
        parent_id, _ = parent
        return any(
            option.option_key == str(choice.definition.depends_on_option)
            for option in draft.selections.get(parent_id, ())
        )

    @staticmethod
    def _choice_key(choice: BuilderChoice) -> str:
        return "/".join((*choice.key, str(choice.event_key or "")))

    @staticmethod
    def _option_key(option: BuilderChoiceOption) -> str:
        if option.option_key is not None:
            return f"option:{option.option_key}"
        if option.reference is not None:
            return f"reference:{option.reference.kind}:{option.reference.identity}"
        return f"value:{option.value or option.label}"

    def _add_feat_choices(self, draft: _LevelDraft, reference: PublishedReference) -> None:
        owner = self.creation.get_owner("feat", reference.identity)
        if owner is None:
            return
        projected = self._prospective_character(draft)
        rows = self.creation._owner_choices(owner, {"ability_score", "spellcasting"}, projected)
        for choice in rows:
            contextual = replace(
                choice,
                character_level=draft.before.total_level + 1,
                class_identity=draft.class_reference.identity,
                class_level=draft.next_class_level,
            )
            draft.choices[self._choice_key(contextual)] = contextual

    def _prospective_character(self, draft: _LevelDraft) -> Character:
        total = draft.before.total_level + 1
        level = CharacterLevel(total, draft.class_reference, draft.next_class_level)
        choices = list(draft.before.choices)
        feats = list(draft.before.feats)
        spells = list(draft.before.spells)
        modifications = list(draft.before.ability_modifications)
        for index, (key, choice) in enumerate(sorted(draft.choices.items())):
            for offset, option in enumerate(draft.selections.get(key, ())):
                selected_key = option.option_key
                choices.append(
                    ChoiceResolution(
                        -(index * 100 + offset + 1),
                        choice.owner.dataset_id,
                        choice.owner.owner_type,
                        choice.owner.owner_key,
                        str(choice.definition.choice_key),
                        selected_key,
                        option.reference,
                        option.value,
                        "resolved",
                        total,
                        choice.class_identity,
                        choice.class_level,
                        choice.definition.source_rule,
                    )
                )
                provenance = {
                    "owner_dataset_id": choice.owner.dataset_id,
                    "owner_type": choice.owner.owner_type,
                    "owner_key": choice.owner.owner_key,
                    "choice_key": str(choice.definition.choice_key),
                    "option_key": selected_key,
                    "character_level": total,
                    "class_identity": choice.class_identity,
                    "class_level": choice.class_level,
                    "source_rule": choice.definition.source_rule,
                }
                source_option = next(
                    (
                        row
                        for row in choice.definition.options
                        if selected_key is not None and str(row.option_key) == selected_key
                    ),
                    None,
                )
                if option.reference is not None and option.reference.kind == "feat":
                    feats.append(
                        FeatSelection(
                            -(index * 100 + offset + 1),
                            option.reference,
                            "class_level",
                            choice.owner.name,
                            **provenance,
                        )
                    )
                elif option.reference is not None and option.reference.kind == "spell":
                    spells.append(
                        SpellSelection(
                            -(index * 100 + offset + 1),
                            option.reference,
                            "cantrip" if choice.definition.kind is ChoiceKind.CANTRIP else "known",
                            draft.class_reference,
                            total,
                            draft.next_class_level,
                            choice.owner.dataset_id,
                            choice.owner.owner_type,
                            choice.owner.owner_key,
                            str(choice.definition.choice_key),
                            selected_key,
                            choice.definition.source_rule,
                        )
                    )
                if source_option is not None:
                    for increase in source_option.ability_increases:
                        modifications.append(
                            AbilityModification(
                                -(index * 100 + offset + 1),
                                str(increase.ability),
                                increase.amount,
                                "asi" if choice.owner.owner_type == "feat" else "feat",
                                choice.owner.name,
                                choice.owner.dataset_id,
                                choice.owner.owner_type,
                                choice.owner.owner_key,
                                str(choice.definition.choice_key),
                                selected_key,
                                total,
                                choice.class_identity,
                                choice.class_level,
                                choice.definition.source_rule,
                            )
                        )
        for spell_draft in draft.spells.values():
            for index, spell in enumerate(spell_draft.selected):
                group = spell_draft.group
                spells.append(
                    SpellSelection(
                        -10000 - index,
                        spell,
                        group.acquisition,
                        draft.class_reference,
                        total,
                        draft.next_class_level,
                        group.owner_dataset_id,
                        group.owner_type,
                        group.owner_key,
                        group.choice_key,
                        group.option_key,
                        group.source_rule,
                    )
                )
        subclasses = list(draft.before.subclasses)
        if draft.subclass is not None:
            subclasses = [
                row
                for row in subclasses
                if row.class_reference.identity != draft.class_reference.identity
            ]
            subclasses.append(
                SubclassSelection(draft.class_reference, draft.subclass, draft.next_class_level)
            )
        hp_choices = list(draft.before.hp_choices)
        if draft.hp_choice_kind is not None and draft.hp_amount is not None:
            hp_choices.append(
                HitPointChoice(
                    total,
                    draft.class_reference,
                    draft.next_class_level,
                    draft.hp_choice_kind,
                    draft.hp_amount,
                )
            )
        return replace(
            draft.before,
            levels=draft.before.levels + (level,),
            subclasses=tuple(subclasses),
            choices=tuple(choices),
            feats=tuple(feats),
            spells=tuple(spells),
            ability_modifications=tuple(modifications),
            hp_choices=tuple(hp_choices),
        )

    def _build_issues(
        self, draft: _LevelDraft, after: DerivedCharacter | None = None
    ) -> list[ProgressionIssue]:
        issues = list(draft.issues)
        if draft.hp_choice_kind is None:
            issues.append(
                ProgressionIssue(
                    "error", "hp_choice_required", "Choose fixed HP or enter a manual roll."
                )
            )
        for key, choice in draft.choices.items():
            if not self._choice_is_active(draft, choice):
                continue
            selected = draft.selections.get(key, ())
            context = self.creation._requirement_context(self._prospective_character(draft))
            choice_status, choice_reason = self._choice_requirement_status(choice, context)
            if choice_status != "satisfied":
                issues.append(
                    ProgressionIssue(
                        "error",
                        "choice_requirement",
                        f"{choice.owner.name}: {choice_reason}",
                    )
                )
            remaining = choice.definition.count - len(selected)
            if remaining > 0:
                options = self._choice_options(draft, choice, "", 0, 1)
                category = "choice_unresolved" if not options else "choice_required"
                issues.append(
                    ProgressionIssue(
                        "error",
                        category,
                        f"{choice.owner.name}: {choice.definition.source_rule} needs "
                        f"{remaining} more selection(s).",
                    )
                )
            scores = {
                row.ability: row.final
                for row in (after or draft.preview.derived_after).ability_scores
                if row.final is not None
            }
            for option in selected:
                status, reason = self._option_requirement_status(option, choice, context, scores)
                if status != "satisfied":
                    issues.append(
                        ProgressionIssue(
                            "error",
                            "choice_no_longer_legal",
                            f"{option.label}: {reason}",
                        )
                    )
        for key, pending in draft.spells.items():
            missing = pending.group.count - len(pending.selected)
            if missing > 0:
                issues.append(
                    ProgressionIssue(
                        "error",
                        "spell_choice_required",
                        f"{pending.group.label}: choose {missing} more spell(s).",
                    )
                )
        if (
            self._subclass_required(
                self.creation.get_owner("class", draft.class_reference.identity),
                draft.next_class_level,
            )
            and draft.subclass is None
        ):
            issues.append(
                ProgressionIssue(
                    "error", "subclass_required", "Choose a subclass for this class track."
                )
            )
        after = after or self.derived.derive(self._prospective_character(draft))
        if after.total_level != draft.before.total_level + 1:
            issues.append(
                ProgressionIssue(
                    "error", "level_history", "Preview did not append exactly one character level."
                )
            )
        if after.total_level > 20:
            issues.append(
                ProgressionIssue("error", "level_cap", "Character level cannot exceed 20.")
            )
        return issues

    def _refresh_preview(self, draft: _LevelDraft) -> LevelUpPreview:
        after_character = self._prospective_character(draft)
        after = self.derived.derive(after_character)
        issues = self._build_issues(draft, after)
        pending = tuple(
            ProgressionChoice(
                key,
                choice.owner.name,
                choice.definition.kind,
                choice.definition.count,
                choice.definition.source_rule,
                choice.event_key,
                tuple(self._option_key(option) for option in draft.selections.get(key, ())),
                tuple(option.label for option in draft.selections.get(key, ())),
            )
            for key, choice in sorted(draft.choices.items())
            if self._choice_is_active(draft, choice)
        )
        spells = tuple(
            ProgressionSpellChoice(
                key,
                pending.group.label,
                pending.group.acquisition,
                pending.group.count,
                tuple(pending.selected),
                pending.group.source_rule,
                pending.group.max_spell_level,
                pending.group.spell_levels,
            )
            for key, pending in sorted(draft.spells.items())
        )
        preview = LevelUpPreview(
            draft.draft_id,
            draft.before.total_level,
            draft.before.total_level + 1,
            draft.class_reference,
            draft.previous_class_level,
            draft.next_class_level,
            draft.new_multiclass,
            draft.event_summaries,
            pending,
            spells,
            draft.hp_die,
            draft.fixed_hp,
            draft.hp_choice_kind,
            draft.hp_amount,
            draft.subclass,
            draft.before_derived,
            after,
            tuple(issues),
            tuple(
                change
                for change in (draft.spell_change, draft.subclass_spell_change)
                if change is not None
            ),
        )
        draft.preview = preview
        return preview

    def _require_draft(self, draft_id: str) -> _LevelDraft:
        try:
            return self._drafts[draft_id]
        except KeyError as exc:
            raise ValueError("Level-up draft was canceled or is no longer available.") from exc
