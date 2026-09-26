"""UI-independent access to normalized character-building rules."""

from __future__ import annotations

from typing import Iterable

from .models.character_builder import (
    CharacterBuilderCatalog,
    CharacterRuleContext,
    Choice,
    ProgressionEvent,
    Requirement,
    RequirementEvaluation,
    RequirementOperator,
    RequirementStatus,
    SubclassBuilderRules,
)

_FEAT_CATEGORY_ALIASES = {
    "o": "origin",
    "g": "general",
    "eb": "epic boon",
    "dg": "dark gift",
    "fs": "fighting style",
    "fs:p": "fighting style replacement (paladin)",
    "fs:r": "fighting style replacement (ranger)",
}


def normalize_feat_category(value: str) -> str:
    """Map 5etools feat category codes to the normalized catalog labels."""

    normalized = " ".join(value.casefold().replace("_", " ").split())
    return _FEAT_CATEGORY_ALIASES.get(normalized, normalized)


class CharacterBuilderRules:
    """Small query surface over a validated, edition-scoped rules catalog."""

    def __init__(self, catalog: CharacterBuilderCatalog) -> None:
        self.catalog = catalog
        self._classes = {str(row.class_key): row for row in catalog.classes}
        self._subclasses = {str(row.subclass_key): row for row in catalog.subclasses}
        self._species = {str(row.species_key): row for row in catalog.species}
        self._backgrounds = {str(row.background_key): row for row in catalog.backgrounds}
        self._feats = {str(row.feat_key): row for row in catalog.feats}
        self._class_primary_abilities = {
            key: row.primary_ability_options for key, row in self._classes.items()
        }
        self._choices = {
            (owner_type, owner_key, str(choice.choice_key)): choice
            for owner_type, owner_key, choices in _catalog_owner_choices(catalog)
            for choice in choices
        }

    @classmethod
    def for_choice_definitions(
        cls, owner_type: str, owner_key: str, choices: Iterable[Choice]
    ) -> CharacterBuilderRules:
        """Create a small rules view for one queried owner instead of loading the whole catalog."""

        instance = cls(CharacterBuilderCatalog())
        for choice in choices:
            identity = (owner_type, owner_key, str(choice.choice_key))
            if identity in instance._choices:
                raise ValueError("choice keys must be unique within their owner")
            instance._choices[identity] = choice
        return instance

    def get_class_progression(self, class_key: str):
        """Return the published 1–20 class table by stable class key."""

        return self._classes[class_key].progression

    def get_level_events(self, class_key: str, class_level: int) -> tuple[ProgressionEvent, ...]:
        """Return only events owned by this class at the requested class level."""

        if not 1 <= class_level <= 20:
            return ()
        row = self._classes.get(class_key)
        if row is None:
            return ()
        return tuple(event for event in row.progression_events if event.level == class_level)

    def get_starting_class_grants(self, class_key: str):
        row = self._classes.get(class_key)
        return tuple(row.starting_grants) if row is not None else ()

    def get_starting_class_choices(self, class_key: str) -> tuple[Choice, ...]:
        row = self._classes.get(class_key)
        return tuple(row.starting_choices) if row is not None else ()

    def get_multiclass_grants(self, class_key: str):
        row = self._classes.get(class_key)
        return tuple(row.multiclass_grants) if row is not None else ()

    def get_multiclass_choices(self, class_key: str) -> tuple[Choice, ...]:
        row = self._classes.get(class_key)
        return tuple(row.multiclass_choices) if row is not None else ()

    def get_multiclass_requirements(self, class_key: str) -> Requirement | None:
        row = self._classes.get(class_key)
        return row.multiclass_requirement if row is not None else None

    def get_available_subclasses(
        self, class_key: str, class_level: int
    ) -> tuple[SubclassBuilderRules, ...]:
        if not 1 <= class_level <= 20:
            return ()
        return tuple(
            row
            for row in self.catalog.subclasses
            if str(row.class_key) == class_key and class_level >= row.selection_level
        )

    def get_choice_definition(
        self, owner_type: str, owner_key: str, choice_key: str
    ) -> Choice | None:
        """Look up a choice by its owner identity and owner-local key."""

        return self._choices.get((owner_type, owner_key, choice_key))

    def get_species(self, species_key: str):
        return self._species.get(species_key)

    def get_background(self, background_key: str):
        return self._backgrounds.get(background_key)

    def get_feat_rules(self, feat_key: str):
        return self._feats.get(feat_key)

    def can_enter_class(
        self, class_key: str, context: CharacterRuleContext
    ) -> RequirementEvaluation:
        """Evaluate 2024 multiclass entry; existing classes do not recheck entry rules."""

        row = self._classes.get(class_key)
        if row is None:
            return RequirementEvaluation(status="unresolved", reason="unknown class reference")
        if context.edition != self.catalog.edition:
            return RequirementEvaluation(
                status="unsatisfied", reason="character edition does not match this rules catalog"
            )
        current_level = context.class_levels.get(class_key, 0)
        if current_level > 0:
            return RequirementEvaluation(
                status="satisfied", reason="class is already part of the character"
            )
        primary_abilities = dict(self._class_primary_abilities)
        primary_abilities.update(context.class_primary_abilities)
        if context.total_level in (None, 0):
            return RequirementEvaluation(
                status="unsatisfied", reason="a starting class is selected at character level 1"
            )
        enriched = context.model_copy(
            update={
                "entry_mode": "multiclass",
                "target_class_key": class_key,
                "class_primary_abilities": primary_abilities,
            }
        )
        return evaluate_requirement(row.multiclass_requirement, enriched)


def _catalog_owner_choices(catalog: CharacterBuilderCatalog):
    for owner in catalog.classes:
        choices = list(owner.starting_choices) + list(owner.multiclass_choices)
        choices.extend(owner.starting_equipment.choices)
        if owner.spellcasting is not None:
            choices.extend(owner.spellcasting.spell_choices)
        for event in owner.progression_events:
            choices.extend(event.choices)
        yield "class", str(owner.class_key), choices
    for owner in catalog.subclasses:
        choices = []
        if owner.spellcasting is not None:
            choices.extend(owner.spellcasting.spell_choices)
        for event in owner.progression_events:
            choices.extend(event.choices)
        yield "subclass", str(owner.subclass_key), choices
    for owner in catalog.species:
        yield "species", str(owner.species_key), list(owner.choices)
    for owner in catalog.backgrounds:
        choices = list(owner.choices) + list(owner.starting_equipment.choices)
        yield "background", str(owner.background_key), choices
    for owner in catalog.feats:
        choices = list(owner.ability_choices) + list(owner.spell_choices)
        yield "feat", str(owner.feat_key), choices


def _result(status: RequirementStatus, reason: str) -> RequirementEvaluation:
    return RequirementEvaluation(status=status, reason=reason)


def _group_result(
    requirement: Requirement,
    results: Iterable[RequirementEvaluation],
) -> RequirementEvaluation:
    values = tuple(results)
    statuses = {value.status for value in values}
    if requirement.operator is RequirementOperator.ALL:
        if RequirementStatus.UNSATISFIED in statuses:
            return _result(RequirementStatus.UNSATISFIED, "one or more required rules are unmet")
        if statuses == {RequirementStatus.SATISFIED}:
            return _result(RequirementStatus.SATISFIED, "all required rules are met")
        return _result(RequirementStatus.UNRESOLVED, "one or more required facts are unknown")
    if RequirementStatus.SATISFIED in statuses:
        return _result(RequirementStatus.SATISFIED, "at least one alternative rule is met")
    if statuses == {RequirementStatus.UNSATISFIED}:
        return _result(RequirementStatus.UNSATISFIED, "no alternative rule is met")
    return _result(RequirementStatus.UNRESOLVED, "no alternative is met and some facts are unknown")


def evaluate_requirement(
    requirement: Requirement, context: CharacterRuleContext
) -> RequirementEvaluation:
    """Evaluate one requirement deterministically without UI or database state."""

    op = requirement.operator
    if op in {RequirementOperator.ALL, RequirementOperator.ANY}:
        return _group_result(
            requirement,
            (evaluate_requirement(child, context) for child in requirement.children),
        )
    if op is RequirementOperator.ABILITY_SCORE:
        score = context.ability_scores.get(requirement.ability)
        if score is None:
            return _result(RequirementStatus.UNRESOLVED, "ability score is not available")
        status = (
            RequirementStatus.SATISFIED
            if score >= requirement.minimum
            else RequirementStatus.UNSATISFIED
        )
        return _result(
            status,
            f"{requirement.ability.value.upper()} score {score}; needs {requirement.minimum}",
        )
    if op is RequirementOperator.TOTAL_LEVEL:
        if context.total_level is None:
            return _result(RequirementStatus.UNRESOLVED, "total character level is not available")
        status = (
            RequirementStatus.SATISFIED
            if context.total_level >= requirement.minimum
            else RequirementStatus.UNSATISFIED
        )
        return _result(status, f"total level {context.total_level}; needs {requirement.minimum}")
    if op is RequirementOperator.CLASS_LEVEL:
        level = context.class_levels.get(str(requirement.class_key), 0)
        status = (
            RequirementStatus.SATISFIED
            if level >= requirement.minimum
            else RequirementStatus.UNSATISFIED
        )
        return _result(status, f"class level {level}; needs {requirement.minimum}")
    if op is RequirementOperator.CLASS_MEMBERSHIP:
        member = context.class_levels.get(str(requirement.class_key), 0) > 0
        return _result(
            RequirementStatus.SATISFIED if member else RequirementStatus.UNSATISFIED,
            "class is present" if member else "class is not present",
        )
    if op is RequirementOperator.SUBCLASS_MEMBERSHIP:
        actual = context.subclasses.get(str(requirement.class_key))
        if actual is None:
            return _result(RequirementStatus.UNSATISFIED, "class has no selected subclass")
        status = (
            RequirementStatus.SATISFIED
            if actual == str(requirement.subclass_key)
            else RequirementStatus.UNSATISFIED
        )
        return _result(status, "subclass membership checked")
    if op is RequirementOperator.PROFICIENCY:
        key = f"{requirement.proficiency_kind.value}:{requirement.proficiency_key}"
        member = key in context.proficiencies
        return _result(
            RequirementStatus.SATISFIED if member else RequirementStatus.UNSATISFIED,
            "proficiency is present" if member else "proficiency is not present",
        )
    if op is RequirementOperator.SPELLCASTING:
        known = (
            context.pact_magic_classes
            if requirement.spellcasting_kind == "pact_magic"
            else context.spellcasting_classes
        )
        if not known and not context.class_levels:
            return _result(
                RequirementStatus.UNRESOLVED, "spellcasting capabilities are not available"
            )
        return _result(
            RequirementStatus.SATISFIED if known else RequirementStatus.UNSATISFIED,
            "required spellcasting capability is present"
            if known
            else "required spellcasting capability is absent",
        )
    if op is RequirementOperator.FEAT:
        identity = requirement.feat_reference.identity
        member = identity in context.feats
        return _result(
            RequirementStatus.SATISFIED if member else RequirementStatus.UNSATISFIED,
            "required feat is present" if member else "required feat is absent",
        )
    if op is RequirementOperator.EDITION:
        status = (
            RequirementStatus.SATISFIED
            if context.edition == requirement.edition
            else RequirementStatus.UNSATISFIED
        )
        return _result(status, "edition checked")
    if op is RequirementOperator.CLASS_ENTRY:
        status = (
            RequirementStatus.SATISFIED
            if context.entry_mode == requirement.entry_mode
            else RequirementStatus.UNSATISFIED
        )
        return _result(status, "class entry mode checked")
    if op is RequirementOperator.CURRENT_CLASS_PRIMARY_ABILITIES:
        current_classes = sorted(
            class_key for class_key, level in context.class_levels.items() if level > 0
        )
        if not current_classes:
            return _result(RequirementStatus.SATISFIED, "there are no current classes to check")
        unknown = False
        for class_key in current_classes:
            options = context.class_primary_abilities.get(class_key)
            if not options:
                unknown = True
                continue
            option_statuses = []
            for option in options:
                scores = [context.ability_scores.get(ability) for ability in option]
                if any(score is None for score in scores):
                    option_statuses.append(RequirementStatus.UNRESOLVED)
                elif all(score >= requirement.minimum for score in scores):
                    option_statuses.append(RequirementStatus.SATISFIED)
                else:
                    option_statuses.append(RequirementStatus.UNSATISFIED)
            if RequirementStatus.SATISFIED not in option_statuses:
                if RequirementStatus.UNRESOLVED in option_statuses:
                    unknown = True
                else:
                    return _result(
                        RequirementStatus.UNSATISFIED,
                        f"primary ability requirement for {class_key} is unmet",
                    )
        if unknown:
            return _result(
                RequirementStatus.UNRESOLVED, "a current class primary ability is unavailable"
            )
        return _result(
            RequirementStatus.SATISFIED, "current class primary abilities meet the minimum"
        )
    if op is RequirementOperator.UNRESOLVED:
        return _result(RequirementStatus.UNRESOLVED, requirement.source_text)
    return _result(RequirementStatus.UNRESOLVED, "unsupported requirement operator")
