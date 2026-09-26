"""Read-only 2024 character statistics derived from persisted choices and rules."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Iterable

from .character_builder import evaluate_requirement
from .characters import CharacterService
from .models.character import Character, EquipmentRecord, PublishedReference
from .models.character_builder import (
    Ability,
    CharacterRuleContext,
    Requirement,
)
from .models.derived_character import (
    AbilityScoreResult,
    ArmorClassResult,
    AttackSummary,
    CalculationComponent,
    DerivedCharacter,
    DerivedIssue,
    DerivedValue,
    EffectiveProficiency,
    FeatureReference,
    HitDiePool,
    HitPointLevel,
    HitPointsResult,
    MovementSpeedResult,
    MulticlassValidation,
    PactMagicResult,
    ProficiencySource,
    SavingThrowResult,
    SkillResult,
    SpellcastingProfile,
    SpellSelectionResult,
    SpellSlot,
)
from .storage.database import Database

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
_ABILITY_NAME = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}

# 2024 multiclass Spell Slots per Spell Level, indexed by effective caster level.
# This is the one shared table used whenever two or more standard progressions combine.
_MULTICLASS_SLOTS: tuple[tuple[int, ...], ...] = (
    (2, 0, 0, 0, 0, 0, 0, 0, 0),
    (3, 0, 0, 0, 0, 0, 0, 0, 0),
    (4, 2, 0, 0, 0, 0, 0, 0, 0),
    (4, 3, 0, 0, 0, 0, 0, 0, 0),
    (4, 3, 2, 0, 0, 0, 0, 0, 0),
    (4, 3, 3, 0, 0, 0, 0, 0, 0),
    (4, 3, 3, 1, 0, 0, 0, 0, 0),
    (4, 3, 3, 2, 0, 0, 0, 0, 0),
    (4, 3, 3, 3, 1, 0, 0, 0, 0),
    (4, 3, 3, 3, 2, 0, 0, 0, 0),
    (4, 3, 3, 3, 2, 1, 0, 0, 0),
    (4, 3, 3, 3, 2, 1, 0, 0, 0),
    (4, 3, 3, 3, 2, 1, 1, 0, 0),
    (4, 3, 3, 3, 2, 1, 1, 0, 0),
    (4, 3, 3, 3, 2, 1, 1, 1, 0),
    (4, 3, 3, 3, 2, 1, 1, 1, 0),
    (4, 3, 3, 3, 2, 1, 1, 1, 1),
    (4, 3, 3, 3, 3, 1, 1, 1, 1),
    (4, 3, 3, 3, 3, 2, 1, 1, 1),
    (4, 3, 3, 3, 3, 2, 2, 1, 1),
)


def ability_modifier(score: int) -> int:
    """Return the standard ability modifier, including negative-score boundaries."""

    return math.floor((score - 10) / 2)


@dataclass(frozen=True)
class _Owner:
    dataset_id: str
    owner_type: str
    owner_key: str
    identity: str
    name: str
    class_identity: str | None = None
    class_level: int | None = None
    starting_class: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return self.dataset_id, self.owner_type, self.owner_key


@dataclass(frozen=True)
class _Grant:
    owner: tuple[str, str, str]
    grant_key: str
    scope: str
    event_key: str | None
    choice_key: str | None
    option_key: str | None
    grant_type: str
    value: str | None
    reference_kind: str | None
    reference_identity: str | None
    proficiency_kind: str | None
    quantity: int | None
    unit: str | None
    requirement_key: str | None
    source_rule: str
    unresolved: bool
    class_level: int | None


@dataclass(frozen=True)
class _SpellcastingRule:
    owner: _Owner
    ability: str
    model: str
    contribution: str
    acquisition: str
    spell_list_identity: str | None
    levels: dict[int, tuple[int | None, int | None, int | None]]
    slots: dict[str, dict[int, dict[int, int]]]


class DerivedCharacterService:
    """Derive character statistics without writing character state or parsing builder JSON."""

    def __init__(self, database: Database, characters: CharacterService | None = None) -> None:
        self.database = database
        self.characters = characters or CharacterService(database)

    def derive_character(self, character_id: str) -> DerivedCharacter:
        """Load and derive one persisted character using its exact published references."""

        profile = (
            self.database.profiler.operation("character.derive")
            if self.database.profiler is not None
            else nullcontext()
        )
        with profile:
            character = self.characters.get_character(character_id)
            return self._derive(character)

    def derive(self, character: Character) -> DerivedCharacter:
        """Derive a supplied persisted-character snapshot; the snapshot is never mutated."""

        profile = (
            self.database.profiler.operation("character.derive")
            if self.database.profiler is not None
            else nullcontext()
        )
        with profile:
            return self._derive(character)

    def _derive(self, character: Character) -> DerivedCharacter:
        if character.edition != "2024":
            return self._unsupported_edition(character)
        issues: list[DerivedIssue] = []
        with self.database.connection() as db:
            loaded = self._load_rules(db, character)
            for owner in loaded["owners"]:
                if owner.key not in loaded["owner_rows"]:
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "stale_reference",
                            "The exact 2024 character-builder rules owner is unavailable.",
                            owner.identity,
                        )
                    )
            for level in character.levels:
                if level.class_reference.missing or level.class_reference.edition != "2024":
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "stale_reference",
                            "The exact 2024 published class reference is unavailable.",
                            level.class_reference.identity,
                        )
                    )
            for reference in (character.species, character.background):
                if reference is not None and (reference.missing or reference.edition != "2024"):
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "stale_reference",
                            "The exact published character option reference is unavailable.",
                            reference.identity,
                        )
                    )
            for selection in (*character.subclasses, *character.feats):
                reference = (
                    selection.subclass_reference
                    if hasattr(selection, "subclass_reference")
                    else selection.feat_reference
                )
                if reference.missing or reference.edition != "2024":
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "stale_reference",
                            "The exact published feature reference is unavailable.",
                            reference.identity,
                        )
                    )
            for selection in character.spells:
                if selection.spell_reference.missing:
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "stale_reference",
                            "The exact published spell reference is unavailable.",
                            selection.spell_reference.identity,
                        )
                    )
                if (
                    selection.source_class_reference is not None
                    and selection.source_class_reference.identity not in character.class_levels
                ):
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "spell_validation",
                            "Saved spell source class is not present in the ordered class history.",
                            selection.spell_reference.identity,
                        )
                    )
            for record in character.equipment:
                if record.item_reference is not None and (
                    record.item_reference.missing
                    or record.item_reference.identity not in loaded["equipment"]
                ):
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "stale_reference",
                            "The exact published equipment reference is unavailable.",
                            record.item_reference.identity,
                        )
                    )
            scores = self._ability_scores(character, issues)
            abilities = {row.ability: row for row in scores}
            ability_mods = {key: abilities[key].modifier for key in ABILITIES if key in abilities}
            total_level = character.total_level
            proficiency_bonus = self._proficiency_bonus(total_level)
            owners = loaded["owners"]
            grants = loaded["grants"]
            prof_sources, mastery_sources, features, granted_spells, speed_grants = (
                self._effective_grants(
                    character,
                    owners,
                    grants,
                    loaded,
                    {row.ability: row.final for row in scores},
                    issues,
                )
            )
            proficiencies = self._proficiencies(prof_sources, mastery_sources)
            proficiency_lookup = {(row.kind, row.key): row for row in proficiencies}
            saves = self._saving_throws(ability_mods, proficiency_bonus, proficiency_lookup)
            skills = self._skills(
                character,
                loaded["skills"],
                ability_mods,
                proficiency_bonus,
                proficiency_lookup,
                issues,
            )
            passive = self._passive_perception(skills, issues)
            initiative = self._initiative(ability_mods, loaded, issues)
            speed = self._speed(character, loaded, speed_grants, issues)
            armor = self._armor_class(
                db, character, loaded, ability_mods, proficiency_lookup, issues
            )
            hit_dice, hit_points = self._hit_points(
                character,
                loaded["class_dice"],
                ability_mods.get("con"),
                loaded,
                issues,
            )
            attacks = self._attacks(
                character,
                loaded,
                ability_mods,
                proficiency_bonus,
                proficiency_lookup,
                mastery_sources,
                issues,
            )
            profiles, standard_slots, slot_state, effective_level, pact_magic = self._spellcasting(
                character,
                loaded,
                ability_mods,
                proficiency_bonus,
                granted_spells,
                issues,
            )
            multiclass = self._multiclass_validation(
                db,
                character,
                loaded,
                abilities,
                proficiency_lookup,
                issues,
            )
            self._prose_effect_issues(loaded, issues)

        return DerivedCharacter(
            character_id=character.character_id,
            edition=character.edition,
            total_level=total_level,
            ability_scores=scores,
            proficiency_bonus=proficiency_bonus,
            saving_throws=saves,
            skills=skills,
            passive_perception=passive,
            initiative=initiative,
            armor_class=armor,
            hit_points=hit_points,
            hit_dice=hit_dice,
            speed=speed,
            proficiencies=proficiencies,
            features=features,
            attacks=attacks,
            spellcasting_profiles=profiles,
            effective_caster_level=effective_level,
            spell_slots=standard_slots,
            spell_slots_state=slot_state,
            pact_magic=pact_magic,
            multiclass_validation=multiclass,
            issues=tuple(_unique_issues(issues)),
        )

    @staticmethod
    def _unsupported_edition(character: Character) -> DerivedCharacter:
        issue = DerivedIssue("error", "unsupported_edition", "Only 2024 characters can be derived.")

        def unresolved(reason: str) -> DerivedValue:
            return DerivedValue(None, "unresolved", reason=reason)

        abilities = tuple(
            AbilityScoreResult(
                ability,
                dict(character.base_ability_scores).get(ability),
                (),
                None,
                None,
                "unresolved",
            )
            for ability in ABILITIES
        )
        saves = tuple(
            SavingThrowResult(ability, None, "none", 0, unresolved("unsupported edition"))
            for ability in ABILITIES
        )
        return DerivedCharacter(
            character.character_id,
            character.edition,
            character.total_level,
            abilities,
            unresolved("unsupported edition"),
            saves,
            (),
            unresolved("unsupported edition"),
            unresolved("unsupported edition"),
            ArmorClassResult(None, "unresolved", None, (), "unsupported edition"),
            HitPointsResult(None, "unresolved", (), None, "unsupported edition"),
            (),
            (),
            (),
            (),
            (),
            (),
            0,
            (),
            "unresolved",
            (),
            (),
            (issue,),
        )

    def _load_rules(self, db: sqlite3.Connection, character: Character) -> dict[str, object]:
        owners = self._active_owners(character)
        owner_keys = sorted({owner.key for owner in owners})
        owner_rows = self._fetch_owner_rows(db, owner_keys)
        requirements = self._fetch_requirements(db, owner_keys)
        grants = self._fetch_grants(db, owner_keys)
        choices = self._fetch_selections(db, character.character_id)
        skill_rows = self._fetch_skills(db, character)
        equipment = self._fetch_equipment(db, character.equipment)
        equipment_labels = self._fetch_equipment_labels(db, equipment)
        spellcasting = self._fetch_spellcasting(db, owners)
        spell_access = self._fetch_spell_access(db, owner_keys)
        spell_rows = self._fetch_spell_rows(db, character.spells, spell_access)
        spell_lists = self._fetch_spell_lists(db, character, spell_rows, spell_access)
        class_dice = self._fetch_hit_dice(db, character)
        active_features = self._fetch_active_features(db, character, owners)
        traits = self._fetch_traits(db, owner_keys)
        return {
            "owners": owners,
            "owner_rows": owner_rows,
            "requirements": requirements,
            "grants": grants,
            "choices": choices,
            "skills": skill_rows,
            "equipment": equipment,
            "equipment_labels": equipment_labels,
            "spellcasting": spellcasting,
            "spell_access": spell_access,
            "spell_rows": spell_rows,
            "spell_lists": spell_lists,
            "class_dice": class_dice,
            "active_features": active_features,
            "active_feature_identities": {row[0] for row in active_features},
            "traits": traits,
        }

    @staticmethod
    def _active_owners(character: Character) -> tuple[_Owner, ...]:
        owners: list[_Owner] = []
        start_identity = character.starting_class.identity if character.starting_class else None
        seen_class: set[str] = set()
        for level in character.levels:
            reference = level.class_reference
            if reference.edition != "2024":
                continue
            parts = _direct_identity(reference.identity)
            if parts is None:
                continue
            dataset_id, owner_key = parts
            if reference.identity in seen_class:
                continue
            seen_class.add(reference.identity)
            owners.append(
                _Owner(
                    dataset_id,
                    "class",
                    owner_key,
                    reference.identity,
                    reference.name,
                    class_identity=reference.identity,
                    class_level=character.class_levels.get(reference.identity, level.class_level),
                    starting_class=reference.identity == start_identity,
                )
            )
        class_levels = character.class_levels
        for selected in character.subclasses:
            if (
                selected.class_reference.edition != "2024"
                or selected.subclass_reference.edition != "2024"
            ):
                continue
            parts = _subclass_identity(selected.subclass_reference.identity)
            if parts is None:
                continue
            dataset_id, _parent_key, owner_key = parts
            owners.append(
                _Owner(
                    dataset_id,
                    "subclass",
                    owner_key,
                    selected.subclass_reference.identity,
                    selected.subclass_reference.name,
                    class_identity=selected.class_reference.identity,
                    class_level=class_levels.get(selected.class_reference.identity),
                )
            )
        for kind, reference in (
            ("species", character.species),
            ("background", character.background),
        ):
            if reference is None:
                continue
            if reference.edition != "2024":
                continue
            parts = _direct_identity(reference.identity)
            if parts is not None:
                owners.append(_Owner(parts[0], kind, parts[1], reference.identity, reference.name))
        for feat in character.feats:
            if feat.feat_reference.edition != "2024":
                continue
            parts = _direct_identity(feat.feat_reference.identity)
            if parts is not None:
                owners.append(
                    _Owner(
                        parts[0],
                        "feat",
                        parts[1],
                        feat.feat_reference.identity,
                        feat.feat_reference.name,
                        class_identity=feat.class_identity,
                        class_level=feat.class_level,
                    )
                )
        return tuple(owners)

    @staticmethod
    def _fetch_owner_rows(
        db: sqlite3.Connection, keys: list[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], sqlite3.Row]:
        if not keys:
            return {}
        where, params = _owner_predicate(keys)
        rows = db.execute(
            "SELECT dataset_id,owner_type,owner_key,name,parent_class_key,metadata_json "
            "FROM character_builder_owners WHERE edition='2024' AND " + where + " "
            "ORDER BY dataset_id,owner_type,owner_key",
            params,
        ).fetchall()
        return {(str(row[0]), str(row[1]), str(row[2])): row for row in rows}

    @staticmethod
    def _fetch_requirements(
        db: sqlite3.Connection, keys: list[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str, str], str]:
        if not keys:
            return {}
        where, params = _owner_predicate(keys)
        rows = db.execute(
            "SELECT dataset_id,owner_type,owner_key,requirement_key,payload_json "
            "FROM character_rule_requirements WHERE " + where + " "
            "ORDER BY dataset_id,owner_type,owner_key,requirement_key",
            params,
        ).fetchall()
        return {(str(row[0]), str(row[1]), str(row[2]), str(row[3])): str(row[4]) for row in rows}

    @staticmethod
    def _fetch_grants(
        db: sqlite3.Connection, keys: list[tuple[str, str, str]]
    ) -> tuple[_Grant, ...]:
        if not keys:
            return ()
        where, params = _owner_predicate(keys)
        rows = db.execute(
            "SELECT g.dataset_id,g.owner_type,g.owner_key,g.grant_key,g.scope,g.event_key,"
            "g.choice_key,g.option_key,g.grant_type,g.value,g.reference_kind,"
            "g.reference_identity,g.proficiency_kind,g.quantity,g.unit,"
            "g.activation_requirement_key,g.source_rule,g.unresolved,e.class_level "
            "FROM character_rule_grants g LEFT JOIN class_progression_events e ON "
            "e.dataset_id=g.dataset_id AND e.owner_type=g.owner_type "
            "AND e.owner_key=g.owner_key AND e.event_key=g.event_key WHERE "
            + where.replace("dataset_id", "g.dataset_id")
            .replace("owner_type", "g.owner_type")
            .replace("owner_key", "g.owner_key")
            + " ORDER BY g.dataset_id,g.owner_type,g.owner_key,g.scope,e.class_level,g.grant_key",
            params,
        ).fetchall()
        return tuple(
            _Grant(
                (str(row[0]), str(row[1]), str(row[2])),
                str(row[3]),
                str(row[4]),
                str(row[5]) if row[5] is not None else None,
                str(row[6]) if row[6] is not None else None,
                str(row[7]) if row[7] is not None else None,
                str(row[8]),
                str(row[9]) if row[9] is not None else None,
                str(row[10]) if row[10] is not None else None,
                str(row[11]) if row[11] is not None else None,
                str(row[12]) if row[12] is not None else None,
                int(row[13]) if row[13] is not None else None,
                str(row[14]) if row[14] is not None else None,
                str(row[15]) if row[15] is not None else None,
                str(row[16]),
                bool(row[17]),
                int(row[18]) if row[18] is not None else None,
            )
            for row in rows
        )

    @staticmethod
    def _fetch_selections(db: sqlite3.Connection, character_id: str) -> tuple[sqlite3.Row, ...]:
        return tuple(
            db.execute(
                "SELECT c.resolution_id,c.owner_dataset_id,c.owner_type,c.owner_key,c.choice_key,"
                "c.selected_option_key,c.selected_reference_kind,c.selected_reference_identity,"
                "c.selected_value,c.resolution_state,c.source_rule,r.choice_type,r.criteria_kind,"
                "o.value,o.reference_kind,o.reference_identity "
                "FROM user_character_choices c LEFT JOIN character_rule_choices r ON "
                "r.dataset_id=c.owner_dataset_id AND r.owner_type=c.owner_type "
                "AND r.owner_key=c.owner_key AND r.choice_key=c.choice_key "
                "LEFT JOIN character_rule_choice_options o ON "
                "o.dataset_id=c.owner_dataset_id AND o.owner_type=c.owner_type "
                "AND o.owner_key=c.owner_key AND o.choice_key=c.choice_key "
                "AND o.option_key=c.selected_option_key "
                "WHERE c.character_id=? ORDER BY c.resolution_id",
                (character_id,),
            ).fetchall()
        )

    @staticmethod
    def _fetch_skills(db: sqlite3.Connection, character: Character) -> dict[str, tuple[str, str]]:
        datasets = sorted(
            {
                parts[0]
                for level in character.levels
                if level.class_reference.edition == "2024"
                if (parts := _direct_identity(level.class_reference.identity)) is not None
            }
        )
        if not datasets:
            return {}
        marks = ",".join("?" for _ in datasets)
        rows = db.execute(
            "SELECT dataset_id,skill_key,name,ability_key FROM character_builder_skills "
            f"WHERE dataset_id IN ({marks}) ORDER BY skill_key,dataset_id",
            datasets,
        ).fetchall()
        skills: dict[str, tuple[str, str]] = {}
        for row in rows:
            key = str(row[1])
            value = (str(row[2]), str(row[3]))
            if key in skills and skills[key][1] != value[1]:
                # Conflicting dataset mappings are surfaced by _skills rather than guessed.
                skills[key] = (skills[key][0], "")
            else:
                skills[key] = value
        return skills

    @staticmethod
    def _fetch_equipment(
        db: sqlite3.Connection, equipment: tuple[EquipmentRecord, ...]
    ) -> dict[str, sqlite3.Row]:
        keys = sorted(
            {
                parts
                for row in equipment
                if row.item_reference is not None
                and (parts := _direct_identity(row.item_reference.identity)) is not None
            }
        )
        if not keys:
            return {}
        pair_clause, params = _pairs_predicate(keys)
        rows = db.execute(
            "SELECT e.dataset_id,e.local_key,e.name,i.kind,i.item_type,m.category,"
            "m.weapon_category,m.attack_type,m.damage,m.damage_type,m.range_json,"
            "m.properties_json,m.mastery_references_json,m.armor_category,m.base_ac,"
            "m.dexterity_rule,m.dexterity_cap,m.unresolved_fields_json "
            "FROM entries e JOIN items i ON i.entry_id=e.id "
            "JOIN sources src ON src.id=e.source_id "
            "LEFT JOIN character_builder_equipment m "
            "ON m.dataset_id=e.dataset_id AND m.item_key=e.local_key "
            "WHERE e.kind='item' AND src.edition='2024' AND "
            + pair_clause.replace("dataset_id", "e.dataset_id").replace("local_key", "e.local_key")
            + " ORDER BY e.dataset_id,e.local_key",
            params,
        ).fetchall()
        return {f"{row[0]}:{row[1]}": row for row in rows}

    @staticmethod
    def _fetch_equipment_labels(
        db: sqlite3.Connection, equipment: dict[str, sqlite3.Row]
    ) -> dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]]:
        """Resolve structured weapon properties and mastery references in two batches."""

        property_refs: dict[str, set[str]] = defaultdict(set)
        mastery_refs: dict[str, set[str]] = defaultdict(set)
        property_keys: set[tuple[str, str]] = set()
        mastery_keys: set[tuple[str, str]] = set()
        for identity, row in equipment.items():
            item_parts = _direct_identity(identity)
            try:
                properties = json.loads(str(row[11]))
                masteries = json.loads(str(row[12]))
            except (TypeError, ValueError, json.JSONDecodeError):
                property_refs[identity].add("<malformed properties>")
                continue
            if not isinstance(properties, list) or not isinstance(masteries, list):
                property_refs[identity].add("<malformed properties>")
                continue
            for value in properties:
                if not isinstance(value, str):
                    continue
                exact_reference = (
                    value if ":" in value or item_parts is None else f"{item_parts[0]}:{value}"
                )
                property_refs[identity].add(exact_reference)
                if parts := _direct_identity(exact_reference):
                    property_keys.add(parts)
            for value in masteries:
                if not isinstance(value, dict) or not isinstance(value.get("identity"), str):
                    continue
                reference = str(value["identity"])
                mastery_refs[identity].add(reference)
                if parts := _direct_identity(reference):
                    mastery_keys.add(parts)

        property_names: dict[str, str] = {}
        if property_keys:
            terms = ["(dataset_id=? AND property_key=?)" for _ in property_keys]
            rows = db.execute(
                "SELECT dataset_id,property_key,name FROM item_properties WHERE "
                + " OR ".join(terms)
                + " ORDER BY dataset_id,property_key",
                [part for key in sorted(property_keys) for part in key],
            ).fetchall()
            property_names = {f"{row[0]}:{row[1]}": str(row[2]) for row in rows}
        mastery_names: dict[str, str] = {}
        if mastery_keys:
            clause, params = _pairs_predicate(sorted(mastery_keys))
            rows = db.execute(
                "SELECT e.dataset_id,e.local_key,e.name FROM entries e "
                "WHERE e.kind='rule' AND "
                + clause.replace("dataset_id", "e.dataset_id").replace("local_key", "e.local_key")
                + " ORDER BY e.dataset_id,e.local_key",
                params,
            ).fetchall()
            mastery_names = {f"{row[0]}:{row[1]}": str(row[2]) for row in rows}

        result = {}
        for identity in equipment:
            references = property_refs.get(identity, set())
            names = tuple(
                sorted(property_names[ref] for ref in references if ref in property_names)
            )
            missing = tuple(sorted(ref for ref in references if ref not in property_names))
            mastery_labels = tuple(
                sorted(
                    (reference, mastery_names.get(reference, reference))
                    for reference in mastery_refs.get(identity, set())
                )
            )
            result[identity] = names, missing, mastery_labels
        return result

    @staticmethod
    def _fetch_spellcasting(
        db: sqlite3.Connection, owners: tuple[_Owner, ...]
    ) -> tuple[_SpellcastingRule, ...]:
        keys = sorted({owner.key for owner in owners if owner.owner_type in {"class", "subclass"}})
        if not keys:
            return ()
        where, params = _owner_predicate(keys)
        rows = db.execute(
            "SELECT dataset_id,owner_type,owner_key,spellcasting_ability,spellcasting_model,"
            "multiclass_contribution,acquisition,spell_list_reference_identity "
            "FROM class_spellcasting WHERE " + where + " "
            "ORDER BY dataset_id,owner_type,owner_key",
            params,
        ).fetchall()
        if not rows:
            return ()
        owner_by_key = {owner.key: owner for owner in owners}
        profile_keys = sorted((str(row[0]), str(row[1]), str(row[2])) for row in rows)
        profile_where, profile_params = _owner_predicate(profile_keys)
        levels_rows = db.execute(
            "SELECT dataset_id,owner_type,owner_key,class_level,cantrips_known,prepared_spells,"
            "known_spells "
            "FROM class_spellcasting_levels WHERE " + profile_where + " "
            "ORDER BY dataset_id,owner_type,owner_key,class_level",
            profile_params,
        ).fetchall()
        slot_rows = db.execute(
            "SELECT dataset_id,owner_type,owner_key,progression_kind,class_level,spell_level,"
            "slot_count "
            "FROM class_spell_slots WHERE " + profile_where + " "
            "ORDER BY dataset_id,owner_type,owner_key,progression_kind,class_level,spell_level",
            profile_params,
        ).fetchall()
        levels_by_owner: dict[
            tuple[str, str, str], dict[int, tuple[int | None, int | None, int | None]]
        ] = defaultdict(dict)
        for item in levels_rows:
            key = (str(item[0]), str(item[1]), str(item[2]))
            levels_by_owner[key][int(item[3])] = tuple(
                int(item[index]) if item[index] is not None else None for index in (4, 5, 6)
            )
        slots_by_owner: dict[tuple[str, str, str], dict[str, dict[int, dict[int, int]]]] = (
            defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
        )
        for item in slot_rows:
            key = (str(item[0]), str(item[1]), str(item[2]))
            slots_by_owner[key][str(item[3])][int(item[4])][int(item[5])] = int(item[6])
        profiles: list[_SpellcastingRule] = []
        for row in rows:
            key = (str(row[0]), str(row[1]), str(row[2]))
            owner = owner_by_key.get(key)
            if owner is None:
                continue
            profiles.append(
                _SpellcastingRule(
                    owner,
                    str(row[3]),
                    str(row[4]),
                    str(row[5]),
                    str(row[6]),
                    str(row[7]) if row[7] is not None else None,
                    levels_by_owner.get(key, {}),
                    slots_by_owner.get(key, {}),
                )
            )
        return tuple(profiles)

    @staticmethod
    def _fetch_spell_access(
        db: sqlite3.Connection, keys: list[tuple[str, str, str]]
    ) -> tuple[sqlite3.Row, ...]:
        if not keys:
            return ()
        where, params = _owner_predicate(keys)
        return tuple(
            db.execute(
                "SELECT dataset_id,owner_type,owner_key,access_key,access_type,class_level,"
                "level_scope,source_rule,payload_json FROM character_builder_spell_access WHERE "
                + where
                + " ORDER BY dataset_id,owner_type,owner_key,class_level,access_key",
                params,
            ).fetchall()
        )

    @staticmethod
    def _fetch_spell_rows(
        db: sqlite3.Connection,
        spells: tuple[object, ...],
        access_rows: tuple[sqlite3.Row, ...],
    ) -> dict[str, sqlite3.Row]:
        identities = {selection.spell_reference.identity for selection in spells}
        for access in access_rows:
            try:
                payload = json.loads(str(access[8]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for reference in payload.get("spells", []):
                if isinstance(reference, dict) and isinstance(reference.get("identity"), str):
                    identities.add(reference["identity"])
        keys = sorted(
            {parts for identity in identities if (parts := _direct_identity(identity)) is not None}
        )
        if not keys:
            return {}
        pair_clause, params = _pairs_predicate(keys)
        rows = db.execute(
            "SELECT e.dataset_id,e.local_key,e.name,src.edition,sp.level,sp.school "
            "FROM entries e JOIN spells sp ON sp.entry_id=e.id "
            "JOIN sources src ON src.id=e.source_id WHERE e.kind='spell' AND "
            + pair_clause.replace("dataset_id", "e.dataset_id").replace("local_key", "e.local_key")
            + " ORDER BY e.dataset_id,e.local_key",
            params,
        ).fetchall()
        return {f"{row[0]}:{row[1]}": row for row in rows}

    @staticmethod
    def _fetch_spell_lists(
        db: sqlite3.Connection,
        character: Character,
        spell_rows: dict[str, sqlite3.Row],
        access_rows: tuple[sqlite3.Row, ...],
    ) -> set[tuple[str, str]]:
        spells = sorted({identity for identity in spell_rows})
        classes = sorted(
            {
                level.class_reference.identity
                for level in character.levels
                if level.class_reference.edition == "2024"
            }
            | {
                selection.class_reference.identity
                for selection in character.subclasses
                if selection.class_reference.edition == "2024"
            }
        )
        access_classes: set[str] = set()
        for access in access_rows:
            try:
                payload = json.loads(str(access[8]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            criteria = payload.get("criteria")
            if not isinstance(criteria, dict) or criteria.get("kind") != "spell_list":
                continue
            for value in criteria.get("values", []):
                if not isinstance(value, str):
                    continue
                identity = value if ":" in value else f"{access[0]}:{value}"
                if _direct_identity(identity):
                    access_classes.add(identity)
        classes.extend(access_classes)
        classes = sorted(set(classes))
        spell_parts = [value for identity in spells if (value := _direct_identity(identity))]
        class_parts = [value for identity in classes if (value := _direct_identity(identity))]
        if not spell_parts or not class_parts:
            return set()
        spell_clause, spell_params = _pairs_predicate(spell_parts)
        class_clause, class_params = _pairs_predicate(class_parts)
        rows = db.execute(
            "SELECT se.dataset_id || ':' || se.local_key,ce.dataset_id || ':' || ce.local_key "
            "FROM spell_classes sc JOIN spells sp ON sp.entry_id=sc.spell_id "
            "JOIN entries se ON se.id=sp.entry_id JOIN classes c ON c.entry_id=sc.class_id "
            "JOIN entries ce ON ce.id=c.entry_id WHERE "
            + spell_clause.replace("dataset_id", "se.dataset_id").replace(
                "local_key", "se.local_key"
            )
            + " AND "
            + class_clause.replace("dataset_id", "ce.dataset_id").replace(
                "local_key", "ce.local_key"
            ),
            spell_params + class_params,
        ).fetchall()
        return {(str(row[0]), str(row[1])) for row in rows}

    @staticmethod
    def _fetch_hit_dice(db: sqlite3.Connection, character: Character) -> dict[str, int]:
        keys = sorted(
            {
                parts
                for level in character.levels
                if level.class_reference.edition == "2024"
                if (parts := _direct_identity(level.class_reference.identity)) is not None
            }
        )
        if not keys:
            return {}
        pair_clause, params = _pairs_predicate(keys)
        rows = db.execute(
            "SELECT e.dataset_id || ':' || e.local_key,c.hit_die FROM classes c "
            "JOIN entries e ON e.id=c.entry_id JOIN sources src ON src.id=e.source_id "
            "WHERE src.edition='2024' AND "
            + pair_clause.replace("dataset_id", "e.dataset_id").replace("local_key", "e.local_key"),
            params,
        ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    @staticmethod
    def _fetch_active_features(
        db: sqlite3.Connection, character: Character, owners: tuple[_Owner, ...]
    ) -> tuple[tuple[str, str, str, str], ...]:
        rows = db.execute(
            "WITH class_tracks AS (SELECT class_identity,MAX(resulting_class_level) class_level "
            "FROM user_character_levels WHERE character_id=? GROUP BY class_identity) "
            "SELECT l.class_identity || '#' || f.feature_key,'class_feature',"
            "f.title || ' ' || f.description,f.title "
            "FROM class_tracks l JOIN entries e ON l.class_identity="
            "e.dataset_id || ':' || e.local_key "
            "JOIN sources src ON src.id=e.source_id AND src.edition='2024' "
            "JOIN classes c ON c.entry_id=e.id JOIN class_features f ON f.class_id=c.entry_id "
            "WHERE f.level<=l.class_level "
            "UNION ALL "
            "SELECT s.subclass_identity || '#' || f.feature_key,'subclass_feature',"
            "f.title || ' ' || f.description,f.title "
            "FROM user_character_subclasses s JOIN subclasses sc ON "
            "s.subclass_identity=sc.dataset_id || ':subclass:' || "
            "(SELECT p.local_key FROM entries p WHERE p.id=sc.class_id) || ':' || sc.subclass_key "
            "JOIN entries pe ON pe.id=sc.class_id JOIN sources ps ON ps.id=pe.source_id "
            "JOIN subclass_features f ON f.subclass_id=sc.id WHERE s.character_id=? AND "
            "ps.edition='2024' AND "
            "f.level<=(SELECT MAX(l.resulting_class_level) FROM user_character_levels l "
            "WHERE l.character_id=s.character_id AND l.class_identity=s.class_identity) "
            "UNION ALL SELECT e.dataset_id || ':' || e.local_key,'feat',e.description,e.name "
            "FROM user_character_feats f JOIN entries e ON f.feat_identity="
            "e.dataset_id || ':' || e.local_key JOIN sources fs ON fs.id=e.source_id "
            "WHERE f.character_id=? AND e.kind='feat' "
            "AND fs.edition='2024' "
            "ORDER BY 1,2,3",
            (character.character_id, character.character_id, character.character_id),
        ).fetchall()
        result = [(str(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows]
        owner_keys = sorted(
            {owner.key for owner in owners if owner.owner_type in {"species", "background"}}
        )
        if owner_keys:
            where, params = _owner_predicate(owner_keys)
            traits = db.execute(
                "SELECT dataset_id,owner_type,owner_key,name,description "
                "FROM character_builder_traits "
                "WHERE " + where + " ORDER BY dataset_id,owner_type,owner_key,trait_key",
                params,
            ).fetchall()
            identities = {owner.key: owner.identity for owner in owners}
            result.extend(
                (
                    identities[(str(row[0]), str(row[1]), str(row[2]))],
                    "trait",
                    f"{row[3]} {row[4]}",
                    str(row[3]),
                )
                for row in traits
                if (str(row[0]), str(row[1]), str(row[2])) in identities
            )
        return tuple(sorted(result))

    @staticmethod
    def _fetch_traits(
        db: sqlite3.Connection, keys: list[tuple[str, str, str]]
    ) -> tuple[sqlite3.Row, ...]:
        if not keys:
            return ()
        where, params = _owner_predicate(keys)
        return tuple(
            db.execute(
                "SELECT dataset_id,owner_type,owner_key,trait_key,name,source_rule "
                "FROM character_builder_traits WHERE "
                + where
                + " ORDER BY dataset_id,owner_type,owner_key,trait_key",
                params,
            ).fetchall()
        )

    @staticmethod
    def _ability_scores(
        character: Character, issues: list[DerivedIssue]
    ) -> tuple[AbilityScoreResult, ...]:
        base = dict(character.base_ability_scores)
        changes: dict[str, list[CalculationComponent]] = defaultdict(list)
        for change in character.ability_modifications:
            if change.ability not in ABILITIES:
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_ability_adjustment",
                        f"Unknown ability adjustment target '{change.ability}'.",
                        change.source_rule,
                    )
                )
                continue
            identity = _provenance_identity(
                change.owner_dataset_id, change.owner_type, change.owner_key
            )
            changes[change.ability].append(
                CalculationComponent(change.source_label, change.amount, identity)
            )
        results: list[AbilityScoreResult] = []
        for ability in ABILITIES:
            base_score = base.get(ability)
            adjustments = tuple(changes.get(ability, ()))
            if base_score is None:
                results.append(
                    AbilityScoreResult(ability, None, adjustments, None, None, "unresolved")
                )
                continue
            final = base_score + sum(component.value for component in adjustments)
            results.append(
                AbilityScoreResult(
                    ability, base_score, adjustments, final, ability_modifier(final), "complete"
                )
            )
        if len(base) < 6:
            issues.append(
                DerivedIssue(
                    "warning",
                    "incomplete_ability_scores",
                    "One or more base ability scores are missing.",
                )
            )
        return tuple(results)

    @staticmethod
    def _proficiency_bonus(total_level: int) -> DerivedValue:
        if total_level <= 0:
            return DerivedValue(None, "unresolved", reason="character has no class levels")
        value = 2 + (total_level - 1) // 4
        return DerivedValue(
            value,
            "complete",
            (CalculationComponent("Total character level", value),),
            "2 + floor((level - 1) / 4)",
        )

    def _effective_grants(
        self,
        character: Character,
        owners: tuple[_Owner, ...],
        grants: tuple[_Grant, ...],
        loaded: dict[str, object],
        ability_scores: dict[str, int | None],
        issues: list[DerivedIssue],
    ) -> tuple[
        dict[tuple[str, str], list[ProficiencySource]],
        dict[str, list[ProficiencySource]],
        tuple[FeatureReference, ...],
        dict[str, list[SpellSelectionResult]],
        list[tuple[str, int, ProficiencySource]],
    ]:
        owners_by_key: dict[tuple[str, str, str], list[_Owner]] = defaultdict(list)
        for owner in owners:
            owners_by_key[owner.key].append(owner)
        owner_rows = loaded["owner_rows"]
        requirements = loaded["requirements"]
        choices = loaded["choices"]
        active_owner_keys = set(owners_by_key)
        selections = {
            (str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5]))
            for row in choices
            if row[5] is not None and str(row[9]) == "resolved"
        }
        profs: dict[tuple[str, str], list[ProficiencySource]] = defaultdict(list)
        masteries: dict[str, list[ProficiencySource]] = defaultdict(list)
        features: list[FeatureReference] = []
        feature_keys: set[tuple[str, str, str, str]] = set()
        granted_spells: dict[str, list[SpellSelectionResult]] = defaultdict(list)
        speed_grants: list[tuple[str, int, ProficiencySource]] = []
        active_feature_identities = loaded["active_feature_identities"]
        active_feature_names = {row[0]: row[3] for row in loaded["active_features"]}
        for grant in grants:
            contexts = owners_by_key.get(grant.owner, ())
            if not contexts or not self._grant_is_active(grant, contexts, selections):
                continue
            for owner in contexts:
                if grant.unresolved:
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "unresolved_rule",
                            f"Structured grant {grant.source_rule} is marked unresolved.",
                            owner.identity,
                        )
                    )
                    continue
                if grant.requirement_key:
                    payload = requirements.get((*grant.owner, grant.requirement_key))
                    status, reason = self._evaluate_saved_requirement(
                        payload,
                        character,
                        ability_scores,
                        profs,
                        owners,
                        owner_rows,
                        loaded["spellcasting"],
                    )
                    if status == "unsatisfied":
                        continue
                    if status == "unresolved":
                        issues.append(
                            DerivedIssue(
                                "warning",
                                "unresolved_rule",
                                f"Conditional grant {grant.source_rule} could not be evaluated: "
                                f"{reason}.",
                                owner.identity,
                            )
                        )
                        continue
                source = ProficiencySource(owner.name, owner.identity, grant.source_rule)
                value = grant.value.casefold().strip() if grant.value is not None else ""
                if grant.grant_type in {"proficiency", "expertise"}:
                    if not grant.proficiency_kind or not value:
                        issues.append(
                            DerivedIssue(
                                "warning",
                                "unresolved_proficiency",
                                "A proficiency grant lacks a kind or target.",
                                owner.identity,
                            )
                        )
                        continue
                    normalized_kind, normalized_value = _normalize_proficiency(
                        grant.proficiency_kind, value
                    )
                    if normalized_kind == "weapon_mastery":
                        masteries[normalized_value].append(source)
                    else:
                        profs[(normalized_kind, normalized_value)].append(source)
                        if grant.grant_type == "expertise":
                            profs[(normalized_kind, normalized_value)].append(
                                ProficiencySource(
                                    f"Expertise: {owner.name}", owner.identity, grant.source_rule
                                )
                            )
                    continue
                if grant.grant_type == "weapon_mastery":
                    mastery_key = grant.reference_identity or value
                    if mastery_key:
                        masteries[mastery_key.casefold()].append(source)
                    else:
                        issues.append(
                            DerivedIssue(
                                "warning",
                                "unresolved_proficiency",
                                "Weapon Mastery grant has no target.",
                                owner.identity,
                            )
                        )
                    continue
                if grant.grant_type == "feature":
                    if grant.reference_identity is None:
                        issues.append(
                            DerivedIssue(
                                "warning",
                                "unresolved_feature",
                                "Feature grant has no exact reference.",
                                owner.identity,
                            )
                        )
                        continue
                    feature_missing = grant.reference_identity not in active_feature_identities
                    if feature_missing:
                        issues.append(
                            DerivedIssue(
                                "warning",
                                "stale_reference",
                                "Exact class or subclass feature reference is unavailable.",
                                grant.reference_identity,
                            )
                        )
                    feature_key = (
                        grant.reference_identity,
                        owner.identity,
                        grant.source_rule,
                        owner.class_identity or "",
                    )
                    if feature_key not in feature_keys:
                        feature_keys.add(feature_key)
                        features.append(
                            FeatureReference(
                                PublishedReference(
                                    grant.reference_kind or "rule",
                                    grant.reference_identity,
                                    grant.reference_identity.rsplit("/", 1)[-1],
                                    "2024",
                                    feature_missing,
                                ),
                                owner.identity,
                                owner.name,
                                grant.source_rule,
                                owner.class_level,
                                active_feature_names.get(grant.reference_identity),
                            )
                        )
                    continue
                if grant.grant_type in {"spell", "cantrip"}:
                    if grant.reference_identity is None:
                        issues.append(
                            DerivedIssue(
                                "warning",
                                "unresolved_spell_rule",
                                "A spell grant has no exact spell identity.",
                                owner.identity,
                            )
                        )
                        continue
                    granted_spells[owner.identity].append(
                        SpellSelectionResult(
                            PublishedReference(
                                "spell",
                                grant.reference_identity,
                                grant.reference_identity.rsplit("/", 1)[-1],
                                "2024",
                                False,
                            ),
                            "cantrip" if grant.grant_type == "cantrip" else "granted",
                            grant.source_rule,
                            True,
                            level=(
                                int(loaded["spell_rows"][grant.reference_identity][4])
                                if grant.reference_identity in loaded["spell_rows"]
                                else None
                            ),
                            school=(
                                str(loaded["spell_rows"][grant.reference_identity][5])
                                if grant.reference_identity in loaded["spell_rows"]
                                else None
                            ),
                        )
                    )
                    continue
                if grant.grant_type == "speed":
                    kind = (grant.unit or "").casefold()
                    feet = grant.quantity
                    if feet is None:
                        try:
                            feet = int(value)
                        except ValueError:
                            feet = None
                    if kind not in {"walk", "burrow", "climb", "fly", "swim"} or feet is None:
                        issues.append(
                            DerivedIssue(
                                "warning",
                                "unresolved_speed",
                                "A speed grant has incomplete movement metadata.",
                                owner.identity,
                            )
                        )
                    else:
                        speed_grants.append((kind, feet, source))

        for feat in character.feats:
            features.append(
                FeatureReference(
                    feat.feat_reference,
                    feat.feat_reference.identity,
                    feat.provenance_label,
                    feat.source_rule or feat.provenance_kind,
                    feat.class_level,
                    feat.feat_reference.name,
                )
            )
        owner_identity_by_key = {owner.key: owner.identity for owner in owners}
        for row in loaded["traits"]:
            key = (str(row[0]), str(row[1]), str(row[2]))
            identity = owner_identity_by_key.get(key)
            if identity is None:
                continue
            owner = next(owner for owner in owners if owner.key == key)
            features.append(
                FeatureReference(
                    PublishedReference(owner.owner_type, owner.identity, owner.name, "2024", False),
                    owner.identity,
                    str(row[4]),
                    str(row[5]),
                    owner.class_level,
                    str(row[4]),
                )
            )

        for row in choices:
            choice_owner = (str(row[1]), str(row[2]), str(row[3]))
            if str(row[9]) != "resolved":
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_choice",
                        "A saved character choice is explicitly unresolved.",
                        _owner_identity(choice_owner[0], choice_owner[1], choice_owner[2]),
                    )
                )
                continue
            if choice_owner not in active_owner_keys:
                continue
            owner_type = str(row[2])
            choice_type = str(row[11]) if row[11] is not None else ""
            criterion = str(row[12]) if row[12] is not None else ""
            value = (
                str(row[8]) if row[8] is not None else (str(row[13]) if row[13] is not None else "")
            )
            owner_identity = next(
                (owner.identity for owner in owners if owner.key == choice_owner),
                _owner_identity(str(row[1]), owner_type, str(row[3])),
            )
            source = ProficiencySource(
                owner_identity.rsplit("/", 1)[-1], owner_identity, str(row[10])
            )
            option_grants_exist = any(
                grant.owner == (str(row[1]), owner_type, str(row[3]))
                and grant.scope == "choice_option"
                and grant.choice_key == str(row[4])
                and grant.option_key == (str(row[5]) if row[5] is not None else None)
                for grant in grants
            )
            if option_grants_exist:
                continue
            kind = _choice_proficiency_kind(choice_type, criterion, value, loaded["skills"])
            if kind is not None and value:
                normalized_kind, normalized_value = _normalize_proficiency(kind, value)
                if normalized_kind == "weapon_mastery":
                    masteries[normalized_value].append(source)
                else:
                    profs[(normalized_kind, normalized_value)].append(source)
            elif choice_type == "weapon_mastery" and row[15] is not None:
                masteries[str(row[15]).casefold()].append(source)
        return profs, masteries, tuple(features), granted_spells, speed_grants

    @staticmethod
    def _grant_is_active(
        grant: _Grant,
        contexts: Iterable[_Owner],
        selections: set[tuple[str, str, str, str, str]],
    ) -> bool:
        if grant.scope == "choice_option":
            return any(
                (
                    grant.owner[0],
                    grant.owner[1],
                    grant.owner[2],
                    grant.choice_key or "",
                    grant.option_key or "",
                )
                in selections
                for _ in contexts
            )
        for owner in contexts:
            if (
                grant.scope == "starting_class"
                and owner.owner_type == "class"
                and owner.starting_class
            ):
                return True
            if (
                grant.scope == "multiclass_entry"
                and owner.owner_type == "class"
                and not owner.starting_class
            ):
                return True
            if (
                grant.scope == "progression_event"
                and owner.class_level is not None
                and grant.class_level is not None
                and owner.class_level >= grant.class_level
            ):
                return True
            if grant.scope == owner.owner_type:
                return True
            if grant.scope == "feat" and owner.owner_type == "feat":
                return True
            if grant.scope == "background" and owner.owner_type == "background":
                return True
            if grant.scope == "species" and owner.owner_type == "species":
                return True
        return False

    @staticmethod
    def _evaluate_saved_requirement(
        payload: str | None,
        character: Character,
        ability_scores: dict[str, int | None],
        profs: dict[tuple[str, str], list[ProficiencySource]],
        owners: tuple[_Owner, ...],
        owner_rows: dict[tuple[str, str, str], sqlite3.Row],
        spell_rules: tuple[_SpellcastingRule, ...],
    ) -> tuple[str, str]:
        if payload is None:
            return "unresolved", "the published requirement is missing"
        try:
            requirement = Requirement.model_validate_json(payload)
        except (ValueError, TypeError):
            return "unresolved", "the published requirement is malformed"
        primary_abilities: dict[str, list[list[Ability]]] = {}
        for owner in owners:
            if owner.owner_type == "class":
                row = owner_rows.get(owner.key)
                if row is None:
                    continue
                try:
                    options = json.loads(str(row[5])).get("primary_ability_options", [])
                    primary_abilities[owner.owner_key] = [
                        [Ability(value) for value in option] for option in options
                    ]
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        class_levels = {
            parts[1]: count
            for identity, count in character.class_levels.items()
            if (parts := _direct_identity(identity)) is not None
        }
        spellcasting_classes = {
            rule.owner.owner_key for rule in spell_rules if rule.model == "spellcasting"
        }
        pact_magic_classes = {
            rule.owner.owner_key for rule in spell_rules if rule.model == "pact_magic"
        }
        context = CharacterRuleContext(
            edition=character.edition,
            total_level=character.total_level,
            class_levels=class_levels,
            ability_scores={
                Ability(key): value for key, value in ability_scores.items() if value is not None
            },
            proficiencies={f"{kind}:{key}" for (kind, key), sources in profs.items() if sources},
            feats={feat.feat_reference.identity for feat in character.feats},
            spellcasting_classes=spellcasting_classes,
            pact_magic_classes=pact_magic_classes,
            class_primary_abilities=primary_abilities,
        )
        result = evaluate_requirement(requirement, context)
        return str(result.status), result.reason

    @staticmethod
    def _proficiencies(
        profs: dict[tuple[str, str], list[ProficiencySource]],
        masteries: dict[str, list[ProficiencySource]],
    ) -> tuple[EffectiveProficiency, ...]:
        results = []
        for (kind, key), sources in profs.items():
            if not sources:
                continue
            unique_sources = _unique_sources(sources)
            level = (
                "expertise"
                if any(source.label.startswith("Expertise:") for source in unique_sources)
                else "proficient"
            )
            results.append(EffectiveProficiency(kind, key, level, unique_sources))
        results.extend(
            EffectiveProficiency("weapon_mastery", key, "proficient", _unique_sources(sources))
            for key, sources in masteries.items()
            if sources
        )
        return tuple(sorted(results, key=lambda row: (row.kind, row.key)))

    @staticmethod
    def _saving_throws(
        ability_mods: dict[str, int | None],
        proficiency_bonus: DerivedValue,
        profs: dict[tuple[str, str], EffectiveProficiency],
    ) -> tuple[SavingThrowResult, ...]:
        result: list[SavingThrowResult] = []
        pb = proficiency_bonus.value or 0
        for ability in ABILITIES:
            mod = ability_mods.get(ability)
            proficiency = profs.get(("saving_throw", ability))
            level = proficiency.level if proficiency else "none"
            prof_bonus = pb if level == "proficient" else 2 * pb if level == "expertise" else 0
            components = [CalculationComponent(_ABILITY_NAME[ability], mod or 0)]
            if prof_bonus:
                components.append(CalculationComponent("Proficiency", prof_bonus))
            state = "unresolved" if mod is None else "complete"
            result.append(
                SavingThrowResult(
                    ability,
                    mod,
                    level,
                    prof_bonus,
                    DerivedValue(
                        None if mod is None else mod + prof_bonus,
                        state,
                        tuple(components),
                        f"{ability.upper()} modifier + proficiency",
                    ),
                )
            )
        return tuple(result)

    def _skills(
        self,
        character: Character,
        skills: dict[str, tuple[str, str]],
        ability_mods: dict[str, int | None],
        proficiency_bonus: DerivedValue,
        profs: dict[tuple[str, str], EffectiveProficiency],
        issues: list[DerivedIssue],
    ) -> tuple[SkillResult, ...]:
        if not skills:
            issues.append(
                DerivedIssue(
                    "warning",
                    "unresolved_skill_rules",
                    "The character's exact rules dataset has no structured skill-to-ability map.",
                )
            )
        results: list[SkillResult] = []
        pb = proficiency_bonus.value or 0
        for key, (name, ability) in sorted(skills.items()):
            if not ability:
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_skill_rules",
                        f"The skill mapping for {name} conflicts across rules datasets.",
                        key,
                    )
                )
                continue
            mod = ability_mods.get(ability)
            proficiency = profs.get(("skill", key))
            level = proficiency.level if proficiency else "none"
            contribution = pb if level == "proficient" else 2 * pb if level == "expertise" else 0
            components = [
                CalculationComponent(_ABILITY_NAME.get(ability, ability.upper()), mod or 0)
            ]
            if contribution:
                components.append(CalculationComponent("Proficiency", contribution))
            results.append(
                SkillResult(
                    key,
                    name,
                    ability,
                    mod,
                    level,
                    contribution,
                    DerivedValue(
                        None if mod is None else mod + contribution,
                        "unresolved" if mod is None else "complete",
                        tuple(components),
                        f"{ability.upper()} modifier + proficiency",
                    ),
                )
            )
        return tuple(results)

    @staticmethod
    def _passive_perception(
        skills: tuple[SkillResult, ...], issues: list[DerivedIssue]
    ) -> DerivedValue:
        perception = next((skill for skill in skills if skill.key == "perception"), None)
        if perception is None or perception.result.value is None:
            issues.append(
                DerivedIssue(
                    "warning",
                    "unresolved_passive_perception",
                    "Perception modifier is unavailable.",
                )
            )
            return DerivedValue(
                None,
                "unresolved",
                reason="Perception skill mapping or ability score is unavailable",
            )
        components = (
            CalculationComponent("Baseline", 10),
            CalculationComponent("Perception modifier", perception.result.value),
        )
        return DerivedValue(
            10 + perception.result.value, "complete", components, "10 + Perception modifier"
        )

    @staticmethod
    def _initiative(
        ability_mods: dict[str, int | None], loaded: dict[str, object], issues: list[DerivedIssue]
    ) -> DerivedValue:
        modifier = ability_mods.get("dex")
        affected = [
            row for row in loaded["active_features"] if re.search(r"\binitiative\b", row[2], re.I)
        ]
        if modifier is None:
            return DerivedValue(None, "unresolved", reason="Dexterity score is unavailable")
        if affected:
            issues.append(
                DerivedIssue(
                    "warning",
                    "unresolved_initiative_rule",
                    "An active prose-only feature mentions Initiative; only its "
                    "Dexterity baseline is calculated.",
                    affected[0][0],
                )
            )
            return DerivedValue(
                modifier,
                "partial",
                (CalculationComponent("Dexterity", modifier),),
                "Dexterity modifier",
                "A prose-only Initiative effect may apply",
            )
        return DerivedValue(
            modifier,
            "complete",
            (CalculationComponent("Dexterity", modifier),),
            "Dexterity modifier",
        )

    @staticmethod
    def _speed(
        character: Character,
        loaded: dict[str, object],
        speed_grants: list[tuple[str, int, ProficiencySource]],
        issues: list[DerivedIssue],
    ) -> tuple[MovementSpeedResult, ...]:
        candidates: dict[str, list[tuple[int, ProficiencySource]]] = defaultdict(list)
        owner_rows = loaded["owner_rows"]
        if character.species is not None:
            parts = _direct_identity(character.species.identity)
            row = owner_rows.get((parts[0], "species", parts[1])) if parts else None
            if row is not None:
                try:
                    metadata = json.loads(str(row[5]))
                    for movement in metadata.get("movement", []):
                        if isinstance(movement, dict):
                            kind, feet = str(movement.get("kind", "")), movement.get("feet")
                            if kind in {"walk", "burrow", "climb", "fly", "swim"} and isinstance(
                                feet, int
                            ):
                                candidates[kind].append(
                                    (
                                        feet,
                                        ProficiencySource(
                                            character.species.name,
                                            character.species.identity,
                                            "species.movement",
                                        ),
                                    )
                                )
                except (TypeError, ValueError, json.JSONDecodeError):
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "unresolved_speed",
                            "Species movement metadata is malformed.",
                            character.species.identity,
                        )
                    )
        for kind, feet, source in speed_grants:
            candidates[kind].append((feet, source))
        prose_speed = [
            row for row in loaded["active_features"] if _feature_may_modify_speed(row[2])
        ]
        if prose_speed:
            issues.append(
                DerivedIssue(
                    "warning",
                    "unresolved_speed",
                    "An active prose-only feature may modify movement speed.",
                    prose_speed[0][0],
                )
            )
        results: list[MovementSpeedResult] = []
        for kind in sorted(candidates):
            values = {feet for feet, _ in candidates[kind]}
            sources = _unique_sources(source for _, source in candidates[kind])
            if len(values) > 1:
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_speed",
                        f"Multiple structured {kind} speeds conflict; no value was selected.",
                    )
                )
                results.append(MovementSpeedResult(kind, None, "unresolved", sources))
            else:
                results.append(
                    MovementSpeedResult(
                        kind,
                        next(iter(values)),
                        "partial" if prose_speed else "complete",
                        sources,
                    )
                )
        if not results and character.species is None:
            issues.append(
                DerivedIssue(
                    "warning", "unresolved_speed", "No structured movement speed is available."
                )
            )
        return tuple(results)

    def _armor_class(
        self,
        db: sqlite3.Connection,
        character: Character,
        loaded: dict[str, object],
        ability_mods: dict[str, int | None],
        profs: dict[tuple[str, str], EffectiveProficiency],
        issues: list[DerivedIssue],
    ) -> ArmorClassResult:
        dex = ability_mods.get("dex")
        equipped = [
            row for row in character.equipment if row.equipped and row.carried_state != "stowed"
        ]
        armor_records: list[tuple[EquipmentRecord, sqlite3.Row]] = []
        shields: list[tuple[EquipmentRecord, sqlite3.Row]] = []
        for record in equipped:
            if record.item_reference is None:
                issues.append(
                    DerivedIssue(
                        "error",
                        "conflicting_equipment",
                        "An equipped unresolved item may affect AC; exact rules are unavailable.",
                        record.unresolved_selection,
                    )
                )
                return ArmorClassResult(
                    None, "unresolved", None, (), "equipped item reference is unresolved"
                )
            metadata = loaded["equipment"].get(record.item_reference.identity)
            if metadata is None:
                issues.append(
                    DerivedIssue(
                        "error",
                        "stale_reference",
                        "Equipped item reference or equipment metadata is unavailable.",
                        record.item_reference.identity,
                    )
                )
                return ArmorClassResult(
                    None, "unresolved", None, (), "equipped item metadata is unavailable"
                )
            category = str(metadata[5]) if metadata[5] is not None else None
            if category == "armor":
                if str(metadata[13]) == "shield":
                    shields.append((record, metadata))
                else:
                    armor_records.append((record, metadata))
            elif category is None and str(metadata[3]) not in {"weapon"}:
                # Known weapons do not affect AC, but unknown equipped objects might.
                if str(metadata[4]).casefold() not in {"weapon", "ammunition"}:
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "unresolved_armor_class",
                            "Equipped item has no structured armor metadata; AC effect is unknown.",
                            record.item_reference.identity,
                        )
                    )
                    return ArmorClassResult(
                        None, "unresolved", None, (), "equipped item may affect AC"
                    )
        if len(armor_records) > 1 or len(shields) > 1:
            issues.append(
                DerivedIssue(
                    "error",
                    "conflicting_equipment",
                    "More than one equipped armor item or shield conflicts with AC calculation.",
                )
            )
            return ArmorClassResult(
                None, "unresolved", None, (), "conflicting equipped armor or shields"
            )
        prose_ac = [
            row
            for row in loaded["active_features"]
            if re.search(r"armor class|unarmored defense", row[2], re.I)
        ]
        if prose_ac:
            issues.append(
                DerivedIssue(
                    "warning",
                    "unresolved_armor_class",
                    "An active prose-only feature may provide an alternative AC formula.",
                    prose_ac[0][0],
                )
            )
            return ArmorClassResult(
                None, "unresolved", None, (), "a prose-only alternative AC rule may apply"
            )
        if dex is None:
            return ArmorClassResult(None, "unresolved", None, (), "Dexterity score is unavailable")
        components: list[CalculationComponent] = []
        formula: str
        if armor_records:
            record, metadata = armor_records[0]
            if not bool(
                (
                    profs.get(("armor", str(metadata[13])))
                    or EffectiveProficiency("", "", "none", ())
                ).level
                == "proficient"
            ):
                issues.append(
                    DerivedIssue(
                        "warning",
                        "armor_proficiency",
                        "Equipped armor is not covered by an effective armor proficiency.",
                        record.item_reference.identity if record.item_reference else None,
                    )
                )
            base_ac = metadata[14]
            rule = str(metadata[15]) if metadata[15] is not None else None
            cap = int(metadata[16]) if metadata[16] is not None else None
            if (
                base_ac is None
                or rule not in {"full", "cap", "none"}
                or (rule == "cap" and cap is None)
            ):
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_armor_class",
                        "Equipped armor has incomplete AC or Dexterity metadata.",
                        record.item_reference.identity if record.item_reference else None,
                    )
                )
                return ArmorClassResult(
                    None, "unresolved", None, (), "armor metadata is incomplete"
                )
            dex_part = dex if rule == "full" else min(dex, cap) if rule == "cap" else 0
            components.extend(
                (
                    CalculationComponent(
                        record.item_reference.name, int(base_ac), record.item_reference.identity
                    ),
                    CalculationComponent("Dexterity", dex_part),
                )
            )
            value = int(base_ac) + dex_part
            formula = "armor base AC + permitted Dexterity modifier"
        else:
            components.extend(
                (
                    CalculationComponent("Unarmored baseline", 10),
                    CalculationComponent("Dexterity", dex),
                )
            )
            value = 10 + dex
            formula = "10 + Dexterity modifier"
        if shields:
            record, metadata = shields[0]
            bonus = metadata[14]
            if bonus is None or str(metadata[15]) != "shield_bonus":
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_armor_class",
                        "Equipped shield has incomplete structured bonus metadata.",
                        record.item_reference.identity if record.item_reference else None,
                    )
                )
                return ArmorClassResult(
                    None, "unresolved", None, (), "shield bonus metadata is incomplete"
                )
            components.append(
                CalculationComponent(
                    record.item_reference.name, int(bonus), record.item_reference.identity
                )
            )
            value += int(bonus)
            formula += " + shield"
        return ArmorClassResult(value, "complete", formula, tuple(components))

    def _hit_points(
        self,
        character: Character,
        die_by_class: dict[str, int],
        con_modifier: int | None,
        loaded: dict[str, object],
        issues: list[DerivedIssue],
    ) -> tuple[tuple[HitDiePool, ...], HitPointsResult]:
        pools: dict[int, list[PublishedReference]] = defaultdict(list)
        for identity in sorted(character.class_levels):
            die = die_by_class.get(identity)
            level = next(
                row for row in character.levels if row.class_reference.identity == identity
            )
            if die is None:
                issues.append(
                    DerivedIssue(
                        "warning",
                        "stale_reference",
                        "Class Hit Die metadata is unavailable.",
                        identity,
                    )
                )
                continue
            pools[die].append(level.class_reference)
        hit_dice = tuple(
            HitDiePool(
                die, sum(character.class_levels.get(ref.identity, 0) for ref in refs), tuple(refs)
            )
            for die, refs in sorted(pools.items())
        )
        if not character.levels:
            return hit_dice, HitPointsResult(
                None, "unresolved", (), con_modifier, "character has no class levels"
            )
        if con_modifier is None:
            issues.append(
                DerivedIssue(
                    "warning", "unresolved_hit_points", "Constitution modifier is unavailable."
                )
            )
        choices = {row.total_level: row for row in character.hp_choices}
        levels: list[HitPointLevel] = []
        total = 0
        complete = con_modifier is not None
        for level in character.levels:
            die = die_by_class.get(level.class_reference.identity)
            choice = choices.get(level.total_level)
            if die is None:
                levels.append(
                    HitPointLevel(
                        level.total_level,
                        level.class_reference,
                        level.class_level,
                        None,
                        None,
                        con_modifier,
                        None,
                        "unresolved",
                        "unresolved",
                    )
                )
                complete = False
                continue
            if level.total_level == 1:
                die_result = die
                choice_kind = "first_level_maximum"
            elif choice is None:
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_hit_points",
                        f"No HP decision is saved for character level {level.total_level}.",
                        level.class_reference.identity,
                    )
                )
                levels.append(
                    HitPointLevel(
                        level.total_level,
                        level.class_reference,
                        level.class_level,
                        die,
                        None,
                        con_modifier,
                        None,
                        "missing",
                        "unresolved",
                    )
                )
                complete = False
                continue
            else:
                die_result = choice.amount
                choice_kind = choice.choice_kind
                if (
                    choice.class_reference.identity != level.class_reference.identity
                    or choice.class_level != level.class_level
                ):
                    issues.append(
                        DerivedIssue(
                            "error",
                            "invalid_hit_point_choice",
                            f"HP choice at level {level.total_level} conflicts with class history.",
                            level.class_reference.identity,
                        )
                    )
                    levels.append(
                        HitPointLevel(
                            level.total_level,
                            level.class_reference,
                            level.class_level,
                            die,
                            die_result,
                            con_modifier,
                            None,
                            choice_kind,
                            "unresolved",
                        )
                    )
                    complete = False
                    continue
                expected_fixed = die // 2 + 1
                invalid_roll = choice.choice_kind == "rolled" and not 1 <= choice.amount <= die
                invalid_fixed = (
                    choice.choice_kind == "fixed_average" and choice.amount != expected_fixed
                )
                invalid_kind = choice.choice_kind not in {"rolled", "fixed_average"}
                if invalid_roll or invalid_fixed or invalid_kind:
                    reason = (
                        f"outside d{die}"
                        if invalid_roll
                        else f"does not match fixed average {expected_fixed}"
                        if invalid_fixed
                        else "uses an unsupported HP choice kind"
                    )
                    issues.append(
                        DerivedIssue(
                            "error",
                            "invalid_hit_point_choice",
                            f"HP choice at level {level.total_level} {reason}.",
                            level.class_reference.identity,
                        )
                    )
                    levels.append(
                        HitPointLevel(
                            level.total_level,
                            level.class_reference,
                            level.class_level,
                            die,
                            die_result,
                            con_modifier,
                            None,
                            choice_kind,
                            "unresolved",
                        )
                    )
                    complete = False
                    continue
            if con_modifier is None:
                gain = None
                complete = False
            else:
                gain = max(1, die_result + con_modifier)
                total += gain
            levels.append(
                HitPointLevel(
                    level.total_level,
                    level.class_reference,
                    level.class_level,
                    die,
                    die_result,
                    con_modifier,
                    gain,
                    choice_kind,
                    "complete" if gain is not None else "unresolved",
                )
            )
        hp_effects = [
            row
            for row in loaded["active_features"]
            if _feature_may_modify_hit_point_maximum(row[2])
        ]
        if hp_effects:
            issues.append(
                DerivedIssue(
                    "warning",
                    "unresolved_hit_points",
                    "An active prose-only feature may change maximum Hit Points.",
                    hp_effects[0][0],
                )
            )
        if not complete or hp_effects:
            return hit_dice, HitPointsResult(
                None,
                "unresolved",
                tuple(levels),
                con_modifier,
                "a hit point input or permanent HP rule is unresolved",
            )
        return hit_dice, HitPointsResult(total, "complete", tuple(levels), con_modifier)

    def _attacks(
        self,
        character: Character,
        loaded: dict[str, object],
        ability_mods: dict[str, int | None],
        proficiency_bonus: DerivedValue,
        profs: dict[tuple[str, str], EffectiveProficiency],
        masteries: dict[str, list[ProficiencySource]],
        issues: list[DerivedIssue],
    ) -> tuple[AttackSummary, ...]:
        equipment = loaded["equipment"]
        equipment_labels = loaded["equipment_labels"]
        attack_effects = [
            row for row in loaded["active_features"] if _feature_may_modify_attack_bonus(row[2])
        ]
        if attack_effects:
            issues.append(
                DerivedIssue(
                    "warning",
                    "unresolved_attack_rule",
                    "An active prose-only feature may modify attack bonuses.",
                    attack_effects[0][0],
                )
            )
        results: list[AttackSummary] = []
        for record in character.equipment:
            if record.carried_state == "stowed" or record.item_reference is None:
                continue
            metadata = equipment.get(record.item_reference.identity)
            if metadata is None or metadata[5] != "weapon":
                if record.equipped and metadata is None:
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "stale_reference",
                            "Equipped or carried item has no current weapon metadata.",
                            record.item_reference.identity,
                        )
                    )
                continue
            identity = record.item_reference.identity
            properties, missing_properties, mastery_labels = equipment_labels.get(
                identity, ((), (), ())
            )
            if missing_properties:
                reason = "weapon property reference is unavailable"
                issues.append(DerivedIssue("warning", "unresolved_weapon", reason, identity))
                results.append(
                    AttackSummary(
                        record.item_reference,
                        None,
                        _weapon_is_proficient(
                            identity, str(metadata[1]), metadata[6], properties, profs
                        ),
                        None,
                        str(metadata[8]) if metadata[8] is not None else None,
                        str(metadata[9]) if metadata[9] is not None else None,
                        _integer_list(metadata[10]),
                        properties,
                        (),
                        "unresolved",
                        f"{reason}: {', '.join(missing_properties)}",
                    )
                )
                continue
            attack_type = str(metadata[7]) if metadata[7] is not None else None
            if attack_type not in {"melee", "ranged"}:
                reason = "weapon attack_type is unavailable in structured metadata"
                issues.append(DerivedIssue("warning", "unresolved_weapon", reason, identity))
                results.append(
                    AttackSummary(
                        record.item_reference,
                        None,
                        None,
                        None,
                        metadata[8],
                        metadata[9],
                        _integer_list(metadata[10]),
                        properties,
                        (),
                        "unresolved",
                        reason,
                    )
                )
                continue
            normalized_properties = {value.casefold().replace(" ", "_") for value in properties}
            ability = "dex" if attack_type == "ranged" else "str"
            if "finesse" in normalized_properties:
                str_mod, dex_mod = ability_mods.get("str"), ability_mods.get("dex")
                if str_mod is None or dex_mod is None:
                    ability = "finesse"
                else:
                    ability = "str" if str_mod >= dex_mod else "dex"
            proficiency = _weapon_is_proficient(
                identity, str(metadata[1]), metadata[6], properties, profs
            )
            mod = ability_mods.get(ability) if ability != "finesse" else None
            if mod is None:
                issue = (
                    "attack ability score is unavailable"
                    if ability != "finesse"
                    else "Finesse ability cannot be selected without Strength and Dexterity scores"
                )
                results.append(
                    AttackSummary(
                        record.item_reference,
                        None if ability == "finesse" else ability,
                        proficiency,
                        None,
                        metadata[8],
                        metadata[9],
                        tuple(json.loads(str(metadata[10]))),
                        properties,
                        (),
                        "unresolved",
                        issue,
                    )
                )
                continue
            pb = proficiency_bonus.value or 0
            bonus = mod + (pb if proficiency else 0)
            known_mastery = tuple(
                sorted(
                    label
                    for ref, label in mastery_labels
                    if any(_mastery_matches(key, ref, label) for key in masteries)
                )
            )
            results.append(
                AttackSummary(
                    record.item_reference,
                    ability,
                    proficiency,
                    bonus,
                    str(metadata[8]) if metadata[8] is not None else None,
                    str(metadata[9]) if metadata[9] is not None else None,
                    _integer_list(metadata[10]),
                    properties,
                    known_mastery,
                    "partial" if attack_effects else "complete",
                    ("a prose-only attack bonus may apply" if attack_effects else None),
                )
            )
        return tuple(results)

    def _spellcasting(
        self,
        character: Character,
        loaded: dict[str, object],
        ability_mods: dict[str, int | None],
        proficiency_bonus: DerivedValue,
        granted_spells: dict[str, list[SpellSelectionResult]],
        issues: list[DerivedIssue],
    ) -> tuple[
        tuple[SpellcastingProfile, ...],
        tuple[SpellSlot, ...],
        str,
        int,
        tuple[PactMagicResult, ...],
    ]:
        rules: tuple[_SpellcastingRule, ...] = loaded["spellcasting"]
        profiles: list[SpellcastingProfile] = []
        contributing: dict[str, _SpellcastingRule] = {}
        pact_magic: list[PactMagicResult] = []
        for rule in rules:
            level = rule.owner.class_level or 0
            if level <= 0:
                continue
            individual_kind = "pact" if rule.model == "pact_magic" else "standalone"
            progression = rule.slots.get(individual_kind, {}).get(level, {})
            slots = tuple(
                SpellSlot(spell_level, count)
                for spell_level, count in sorted(progression.items())
                if count > 0
            )
            accessible = tuple(slot.spell_level for slot in slots)
            counts = _level_counts(rule.levels, level)
            modifier = ability_mods.get(rule.ability)
            pb = proficiency_bonus.value
            if modifier is None or pb is None:
                dc = DerivedValue(
                    None,
                    "unresolved",
                    reason="spellcasting ability or character proficiency bonus is unavailable",
                )
                attack = DerivedValue(
                    None,
                    "unresolved",
                    reason="spellcasting ability or character proficiency bonus is unavailable",
                )
            else:
                dc = DerivedValue(
                    8 + modifier + pb,
                    "complete",
                    (
                        CalculationComponent("Base DC", 8),
                        CalculationComponent(_ABILITY_NAME[rule.ability], modifier),
                        CalculationComponent("Proficiency", pb),
                    ),
                    "8 + spellcasting ability modifier + proficiency",
                )
                attack = DerivedValue(
                    modifier + pb,
                    "complete",
                    (
                        CalculationComponent(_ABILITY_NAME[rule.ability], modifier),
                        CalculationComponent("Proficiency", pb),
                    ),
                    "spellcasting ability modifier + proficiency",
                )
            spell_results = self._spell_selections_for_profile(
                character, rule, loaded, accessible, issues
            )
            spell_results.extend(
                self._structured_access_spells(rule.owner, character, loaded, issues)
            )
            spell_results.extend(granted_spells.get(rule.owner.identity, ()))
            spell_results = _unique_spell_results(spell_results)
            profiles.append(
                SpellcastingProfile(
                    rule.owner.owner_type,
                    PublishedReference(
                        rule.owner.owner_type, rule.owner.identity, rule.owner.name, "2024", False
                    ),
                    PublishedReference(
                        "class",
                        rule.owner.class_identity or rule.owner.identity,
                        _class_name(character, rule.owner.class_identity or rule.owner.identity),
                        "2024",
                        False,
                    ),
                    level,
                    rule.ability,
                    dc,
                    attack,
                    rule.acquisition,
                    counts[0],
                    counts[1],
                    counts[2],
                    accessible,
                    slots,
                    tuple(
                        sorted(
                            spell_results,
                            key=lambda row: (
                                row.acquisition,
                                row.spell.identity,
                                row.source_rule or "",
                            ),
                        )
                    ),
                )
            )
            if rule.model == "pact_magic":
                if slots:
                    slot_level = max(slot.spell_level for slot in slots)
                    count = next(slot.count for slot in slots if slot.spell_level == slot_level)
                    pact_magic.append(
                        PactMagicResult(count, slot_level, level, rule.owner.identity)
                    )
                else:
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "unresolved_spell_rule",
                            "Pact Magic progression has no slot at the current class level.",
                            rule.owner.identity,
                        )
                    )
            elif rule.contribution not in {"none", "pact_magic_separate"}:
                class_identity = rule.owner.class_identity or rule.owner.identity
                if class_identity in contributing:
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "unresolved_spell_rule",
                            "Multiple standard slot contributions share one class track.",
                            class_identity,
                        )
                    )
                else:
                    contributing[class_identity] = rule
        effective = 0
        slot_state = "complete"
        if len(contributing) >= 2:
            contributions = [
                _caster_contribution(rule.contribution, rule.owner.class_level or 0)
                for rule in contributing.values()
            ]
            if any(value is None for value in contributions):
                slot_state = "unresolved"
                effective = sum(value for value in contributions if value is not None)
                standard = ()
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_spell_rule",
                        "A standard caster contribution mode has no structured rounding rule.",
                    )
                )
            else:
                effective = sum(contributions)
            if slot_state == "unresolved":
                pass
            elif 1 <= effective <= 20:
                standard = tuple(
                    SpellSlot(index + 1, count)
                    for index, count in enumerate(_MULTICLASS_SLOTS[effective - 1])
                    if count > 0
                )
            else:
                standard = ()
                if effective > 20:
                    slot_state = "unresolved"
                    issues.append(
                        DerivedIssue(
                            "warning",
                            "unresolved_spell_rule",
                            "Combined standard caster level exceeds the published progression.",
                        )
                    )
        elif contributing:
            rule = next(iter(contributing.values()))
            level = rule.owner.class_level or 0
            contribution = _caster_contribution(rule.contribution, level)
            if contribution is None:
                slot_state = "partial"
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_spell_rule",
                        "Caster contribution mode is unknown; standalone slots are shown.",
                        rule.owner.identity,
                    )
                )
            else:
                effective = contribution
            standard = tuple(
                SpellSlot(spell_level, count)
                for spell_level, count in sorted(
                    rule.slots.get("standalone", {}).get(level, {}).items()
                )
                if count > 0
            )
        else:
            standard = ()
            effective = 0
        profiles.extend(
            self._source_spellcasting_profiles(
                character,
                loaded,
                ability_mods,
                proficiency_bonus,
                granted_spells,
                issues,
            )
        )
        return (
            tuple(
                sorted(
                    profiles,
                    key=lambda row: (
                        row.class_reference.identity if row.class_reference else "",
                        row.owner_type,
                        row.owner.identity,
                    ),
                )
            ),
            standard,
            slot_state,
            effective,
            tuple(sorted(pact_magic, key=lambda row: row.source)),
        )

    def _spell_selections_for_profile(
        self,
        character: Character,
        rule: _SpellcastingRule,
        loaded: dict[str, object],
        accessible: tuple[int, ...],
        issues: list[DerivedIssue],
    ) -> list[SpellSelectionResult]:
        result: list[SpellSelectionResult] = []
        spell_rows = loaded["spell_rows"]
        spell_lists = loaded["spell_lists"]
        for selection in character.spells:
            selected_owner = (
                selection.source_class_reference.identity
                if selection.source_class_reference
                else None
            )
            saved_owner = (
                selection.owner_dataset_id,
                selection.owner_type,
                selection.owner_key,
            )
            matches = (
                saved_owner == rule.owner.key
                if all(saved_owner)
                else rule.owner.owner_type == "class"
                and selected_owner == rule.owner.class_identity
            )
            if not matches:
                continue
            spell = selection.spell_reference
            if _saved_spell_is_structured_access(
                selection, rule.owner, loaded["spell_access"], character
            ):
                continue
            row = spell_rows.get(spell.identity)
            issue: str | None = None
            valid: bool | None = True
            if spell.missing or row is None:
                issue = "exact spell reference is missing"
                valid = None
                issues.append(DerivedIssue("warning", "stale_reference", issue, spell.identity))
            elif str(row[3]) != "2024" or spell.edition != "2024":
                issue = "spell is not from the 2024 edition"
                valid = False
            else:
                level = int(row[4])
                if selection.acquisition == "cantrip" and level != 0:
                    issue = "cantrip selection references a leveled spell"
                    valid = False
                elif selection.acquisition not in {"cantrip", "innate"} and level == 0:
                    issue = "leveled spell acquisition references a cantrip"
                    valid = False
                elif level > 0 and (not accessible or level > max(accessible)):
                    issue = "spell level exceeds this class or subclass's individual spell access"
                    valid = False
                elif not _spell_acquisition_is_supported(selection.acquisition, rule):
                    issue = "spell acquisition does not match this profile's choice semantics"
                    valid = False
                elif (
                    selected_owner
                    and selection.owner_type != "subclass"
                    and (spell.identity, selected_owner) not in spell_lists
                ):
                    issue = "spell is not on the exact class spell list"
                    valid = False
            if issue:
                issues.append(DerivedIssue("warning", "spell_validation", issue, spell.identity))
            result.append(
                SpellSelectionResult(
                    spell,
                    selection.acquisition,
                    selection.source_rule,
                    valid,
                    issue,
                    int(row[4]) if row is not None else None,
                    str(row[5]) if row is not None else None,
                )
            )
        return result

    def _structured_access_spells(
        self,
        owner: _Owner,
        character: Character,
        loaded: dict[str, object],
        issues: list[DerivedIssue],
    ) -> list[SpellSelectionResult]:
        """Return exact level-gated spells granted by the active structured access rules."""

        results: list[SpellSelectionResult] = []
        spell_rows = loaded["spell_rows"]
        for access in loaded["spell_access"]:
            if (str(access[0]), str(access[1]), str(access[2])) != owner.key:
                continue
            scope = str(access[6])
            current_level = character.total_level if scope == "total" else owner.class_level
            if current_level is None:
                current_level = character.total_level
            if int(access[5]) > current_level:
                continue
            access_type = str(access[4])
            if access_type == "expanded":
                continue
            try:
                payload = json.loads(str(access[8]))
            except (TypeError, ValueError, json.JSONDecodeError):
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_spell_rule",
                        "Structured spell access metadata is malformed.",
                        owner.identity,
                    )
                )
                continue
            acquisition = (
                "always_prepared"
                if access_type == "prepared"
                else access_type
                if access_type in {"known", "spellbook", "innate"}
                else "innate"
            )
            for reference in payload.get("spells", []):
                if not isinstance(reference, dict) or not isinstance(
                    reference.get("identity"), str
                ):
                    continue
                identity = str(reference["identity"])
                live = spell_rows.get(identity)
                is_missing = live is None or str(live[3]) != "2024"
                name = str(live[2]) if live is not None else identity.rsplit("/", 1)[-1]
                published = PublishedReference("spell", identity, name, "2024", is_missing)
                issue = "exact granted spell reference is missing" if is_missing else None
                if issue:
                    issues.append(DerivedIssue("warning", "stale_reference", issue, identity))
                results.append(
                    SpellSelectionResult(
                        published,
                        acquisition,
                        str(access[7]),
                        None if is_missing else True,
                        issue,
                        int(live[4]) if live is not None else None,
                        str(live[5]) if live is not None else None,
                    )
                )
        return results

    def _source_spellcasting_profiles(
        self,
        character: Character,
        loaded: dict[str, object],
        ability_mods: dict[str, int | None],
        proficiency_bonus: DerivedValue,
        granted_spells: dict[str, list[SpellSelectionResult]],
        issues: list[DerivedIssue],
    ) -> tuple[SpellcastingProfile, ...]:
        """Expose spell access from species and feats without merging it into class profiles."""

        profiles: list[SpellcastingProfile] = []
        owners: tuple[_Owner, ...] = loaded["owners"]
        by_key: dict[tuple[str, str, str], _Owner] = {}
        for owner in owners:
            if owner.owner_type in {"species", "background", "feat"}:
                previous = by_key.get(owner.key)
                if previous is None or (owner.class_level or 0) > (previous.class_level or 0):
                    by_key[owner.key] = owner
        spell_rows = loaded["spell_rows"]
        for owner in sorted(by_key.values(), key=lambda item: (item.owner_type, item.identity)):
            access_rows = [
                row
                for row in loaded["spell_access"]
                if (str(row[0]), str(row[1]), str(row[2])) == owner.key
                and (
                    str(row[6]) == "total"
                    and int(row[5]) <= character.total_level
                    or str(row[6]) == "class"
                    and int(row[5]) <= (owner.class_level or character.total_level)
                )
            ]
            source_identity = owner.identity
            saved = [
                selection
                for selection in character.spells
                if selection.owner_dataset_id == owner.dataset_id
                and selection.owner_type == owner.owner_type
                and selection.owner_key == owner.owner_key
            ]
            direct_grants = list(granted_spells.get(source_identity, ()))
            if not access_rows and not saved and not direct_grants:
                continue
            spell_results = []
            for selection in saved:
                if _saved_spell_is_structured_access(
                    selection, owner, tuple(access_rows), character
                ):
                    continue
                spell_results.append(
                    self._validate_source_spell(
                        selection, owner, tuple(access_rows), loaded, issues
                    )
                )
            spell_results.extend(self._structured_access_spells(owner, character, loaded, issues))
            spell_results.extend(direct_grants)
            spell_results = _unique_spell_results(spell_results)
            ability = self._source_spellcasting_ability(owner, access_rows, character, loaded)
            modifier = ability_mods.get(ability) if ability else None
            pb = proficiency_bonus.value
            if modifier is None or pb is None:
                dc = DerivedValue(
                    None,
                    "unresolved",
                    reason="source spellcasting ability or proficiency bonus is unavailable",
                )
                attack = DerivedValue(
                    None,
                    "unresolved",
                    reason="source spellcasting ability or proficiency bonus is unavailable",
                )
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_spell_rule",
                        "Spell access from this source has no resolved spellcasting ability.",
                        owner.identity,
                    )
                )
            else:
                dc = DerivedValue(
                    8 + modifier + pb,
                    "complete",
                    (
                        CalculationComponent("Base DC", 8),
                        CalculationComponent(_ABILITY_NAME[ability], modifier),
                        CalculationComponent("Proficiency", pb),
                    ),
                    "8 + spellcasting ability modifier + proficiency",
                )
                attack = DerivedValue(
                    modifier + pb,
                    "complete",
                    (
                        CalculationComponent(_ABILITY_NAME[ability], modifier),
                        CalculationComponent("Proficiency", pb),
                    ),
                    "spellcasting ability modifier + proficiency",
                )
            counts = _spell_acquisition_counts(spell_results)
            levels = tuple(
                sorted(
                    {
                        int(spell_rows[row.spell.identity][4])
                        for row in spell_results
                        if row.spell.identity in spell_rows
                    }
                )
            )
            acquisitions = sorted({row.acquisition for row in spell_results})
            profiles.append(
                SpellcastingProfile(
                    owner.owner_type,
                    PublishedReference(
                        owner.owner_type,
                        owner.identity,
                        owner.name,
                        "2024",
                        owner.key not in loaded["owner_rows"],
                    ),
                    None,
                    owner.class_level,
                    ability,
                    dc,
                    attack,
                    acquisitions[0] if len(acquisitions) == 1 else "source-specific",
                    counts[0],
                    counts[1],
                    counts[2],
                    levels,
                    (),
                    tuple(
                        sorted(
                            spell_results,
                            key=lambda row: (
                                row.acquisition,
                                row.spell.identity,
                                row.source_rule or "",
                            ),
                        )
                    ),
                )
            )
        return tuple(profiles)

    @staticmethod
    def _validate_source_spell(
        selection: object,
        owner: _Owner,
        access_rows: tuple[sqlite3.Row, ...],
        loaded: dict[str, object],
        issues: list[DerivedIssue],
    ) -> SpellSelectionResult:
        spell = selection.spell_reference
        row = loaded["spell_rows"].get(spell.identity)
        reason: str | None = None
        valid: bool | None = True
        if spell.missing or row is None:
            reason = "exact spell reference is missing"
            valid = None
            issues.append(DerivedIssue("warning", "stale_reference", reason, spell.identity))
        elif str(row[3]) != "2024" or spell.edition != "2024":
            reason = "spell is not from the 2024 edition"
            valid = False
        elif not access_rows:
            reason = "published spell access rule is unavailable for this source"
            valid = None
            issues.append(DerivedIssue("warning", "unresolved_spell_rule", reason, owner.identity))
        else:
            spell_level = int(row[4])
            if selection.acquisition == "cantrip" and spell_level != 0:
                reason = "cantrip selection references a leveled spell"
            elif selection.acquisition not in {"cantrip", "innate"} and spell_level == 0:
                reason = "leveled spell acquisition references a cantrip"
            else:
                matching_rows = [
                    access
                    for access in access_rows
                    if selection.source_rule is None or selection.source_rule == str(access[7])
                ]
                if not matching_rows:
                    reason = "saved spell selection has no matching published access rule"
                elif not any(
                    _access_acquisition_matches(selection.acquisition, str(access[4]))
                    for access in matching_rows
                ):
                    reason = "spell acquisition does not match this source's choice semantics"
                if reason is None:
                    for access in matching_rows:
                        try:
                            payload = json.loads(str(access[8]))
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        criteria = payload.get("criteria")
                        if not isinstance(criteria, dict):
                            continue
                        filters = criteria.get("filters")
                        if not isinstance(filters, dict):
                            continue
                        levels = filters.get("spell_level")
                        if isinstance(levels, list):
                            try:
                                allowed_levels = {int(value) for value in levels}
                            except (TypeError, ValueError):
                                reason = "published spell level filter is malformed"
                                break
                            if spell_level not in allowed_levels:
                                reason = "spell level is outside this source's structured access"
                                break
                        if criteria.get("kind") == "spell_list":
                            lists = {
                                value if ":" in value else f"{owner.dataset_id}:{value}"
                                for value in criteria.get("values", [])
                                if isinstance(value, str)
                            }
                            if lists and not any(
                                (spell.identity, identity) in loaded["spell_lists"]
                                for identity in lists
                            ):
                                reason = "spell is not on an allowed exact spell list"
                                break
            if reason:
                valid = False
        if reason:
            issues.append(DerivedIssue("warning", "spell_validation", reason, spell.identity))
        return SpellSelectionResult(
            spell,
            selection.acquisition,
            selection.source_rule,
            valid,
            reason,
            int(row[4]) if row is not None else None,
            str(row[5]) if row is not None else None,
        )

    @staticmethod
    def _source_spellcasting_ability(
        owner: _Owner,
        access_rows: list[sqlite3.Row],
        character: Character,
        loaded: dict[str, object],
    ) -> str | None:
        abilities: set[str] = set()
        class_rules: tuple[_SpellcastingRule, ...] = loaded["spellcasting"]
        for row in access_rows:
            try:
                payload = json.loads(str(row[8]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            ability = payload.get("ability")
            if ability in ABILITIES:
                abilities.add(str(ability))
                continue
            if payload.get("ability_selection") == "choice":
                group = str(payload.get("choice_group") or row[3])
                choice_key = f"{group}/ability"
                selected = next(
                    (
                        choice.selected_value
                        for choice in character.choices
                        if choice.owner_dataset_id == owner.dataset_id
                        and choice.owner_type == owner.owner_type
                        and choice.owner_key == owner.owner_key
                        and choice.choice_key == choice_key
                        and choice.resolution_state == "resolved"
                    ),
                    None,
                )
                if selected in ABILITIES:
                    abilities.add(str(selected))
            elif payload.get("ability_selection") in {"class", "inherited"}:
                class_rule = next(
                    (
                        rule
                        for rule in class_rules
                        if rule.owner.class_identity == owner.class_identity
                    ),
                    None,
                )
                if class_rule is not None:
                    abilities.add(class_rule.ability)
        return next(iter(abilities)) if len(abilities) == 1 else None

    def _multiclass_validation(
        self,
        db: sqlite3.Connection,
        character: Character,
        loaded: dict[str, object],
        abilities: dict[str, AbilityScoreResult],
        profs: dict[tuple[str, str], EffectiveProficiency],
        issues: list[DerivedIssue],
    ) -> tuple[MulticlassValidation, ...]:
        ordered_classes: list[PublishedReference] = []
        seen: set[str] = set()
        for level in character.levels:
            if level.class_reference.identity not in seen:
                seen.add(level.class_reference.identity)
                ordered_classes.append(level.class_reference)
        if len(ordered_classes) <= 1:
            return ()
        all_class_keys = [
            parts
            for reference in ordered_classes
            if (parts := _direct_identity(reference.identity)) is not None
        ]
        class_keys = [
            parts
            for reference in ordered_classes[1:]
            if (parts := _direct_identity(reference.identity)) is not None
        ]
        if not class_keys:
            return ()
        pair_clause, params = _pairs_predicate(all_class_keys)
        rows = db.execute(
            "SELECT o.dataset_id,o.owner_key,r.payload_json,o.metadata_json "
            "FROM character_builder_owners o "
            "LEFT JOIN character_rule_requirements r ON r.dataset_id=o.dataset_id "
            "AND r.owner_type=o.owner_type AND r.owner_key=o.owner_key "
            "AND r.scope='multiclass_entry' "
            "WHERE o.owner_type='class' AND "
            + pair_clause.replace("dataset_id", "o.dataset_id").replace("local_key", "o.owner_key"),
            params,
        ).fetchall()
        rule_rows = {(str(row[0]), str(row[1])): row for row in rows}
        result: list[MulticlassValidation] = []
        class_primary: dict[str, list[list[Ability]]] = {}
        for row in rows:
            try:
                primary = json.loads(str(row[3])).get("primary_ability_options", [])
                class_primary[str(row[1])] = [
                    [Ability(value) for value in option] for option in primary
                ]
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        prof_keys = {f"{kind}:{key}" for (kind, key), row in profs.items() if row.level != "none"}
        spell_rules: tuple[_SpellcastingRule, ...] = loaded["spellcasting"]
        spellcasting_classes = {
            rule.owner.owner_key for rule in spell_rules if rule.model == "spellcasting"
        }
        pact_classes = {rule.owner.owner_key for rule in spell_rules if rule.model == "pact_magic"}
        context_scores = {
            Ability(key): row.final for key, row in abilities.items() if row.final is not None
        }
        for reference in ordered_classes[1:]:
            parts = _direct_identity(reference.identity)
            if parts is None or reference.missing:
                validation = MulticlassValidation(
                    reference, "unresolved", "exact class reference is missing"
                )
            else:
                row = rule_rows.get(parts)
                if row is None or row[2] is None:
                    validation = MulticlassValidation(
                        reference,
                        "unresolved",
                        "published multiclass entry requirement is unavailable",
                    )
                else:
                    try:
                        requirement = Requirement.model_validate_json(str(row[2]))
                        context = CharacterRuleContext(
                            edition=character.edition,
                            total_level=character.total_level,
                            class_levels={
                                identity.split(":", 1)[-1]: count
                                for identity, count in character.class_levels.items()
                            },
                            ability_scores=context_scores,
                            proficiencies=prof_keys,
                            feats={feat.feat_reference.identity for feat in character.feats},
                            spellcasting_classes=spellcasting_classes,
                            pact_magic_classes=pact_classes,
                            subclasses={
                                _direct_identity(sub.class_reference.identity)[
                                    1
                                ]: _subclass_identity(sub.subclass_reference.identity)[2]
                                for sub in character.subclasses
                                if _direct_identity(sub.class_reference.identity)
                                and _subclass_identity(sub.subclass_reference.identity)
                            },
                            class_primary_abilities=class_primary,
                            entry_mode="multiclass",
                            target_class_key=parts[1],
                        )
                        evaluation = evaluate_requirement(requirement, context)
                        validation = MulticlassValidation(
                            reference, str(evaluation.status), evaluation.reason
                        )
                    except (ValueError, TypeError):
                        validation = MulticlassValidation(
                            reference, "unresolved", "published multiclass requirement is malformed"
                        )
            result.append(validation)
            if validation.status != "satisfied":
                issues.append(
                    DerivedIssue(
                        "warning", "multiclass_validation", validation.reason, reference.identity
                    )
                )
        return tuple(result)

    @staticmethod
    def _prose_effect_issues(loaded: dict[str, object], issues: list[DerivedIssue]) -> None:
        for identity, _kind, body, _name in loaded["active_features"]:
            if re.search(r"armor class|unarmored defense", body, re.I):
                # AC calculation handles this before selecting a formula; do not duplicate it.
                continue
            if re.search(r"\binitiative\b", body, re.I):
                # Initiative calculation handles this as a partial baseline.
                continue
            if re.search(r"\bexpertise\b", body, re.I):
                issues.append(
                    DerivedIssue(
                        "warning",
                        "unresolved_feature",
                        "An active feature describes Expertise only in prose; no doubling applied.",
                        identity,
                    )
                )


def _feature_may_modify_attack_bonus(body: str) -> bool:
    return bool(
        re.search(
            r"\b(?:bonus|add(?:s)?|increase(?:s)?|decrease(?:s)?)\b[^.!?;]{0,80}"
            r"\b(?:attack rolls?|attack bonus|spell attack)\b|"
            r"\b(?:attack rolls?|attack bonus|spell attack)\b[^.!?;]{0,80}"
            r"\b(?:bonus|add(?:s)?|increase(?:s)?|decrease(?:s)?)\b",
            body,
            re.I,
        )
    )


def _feature_may_modify_hit_point_maximum(body: str) -> bool:
    return bool(
        re.search(
            r"\b(?:bonus|add(?:s)?|increase(?:s)?|decrease(?:s)?|reduce(?:s)?)\b"
            r"[^.!?;]{0,80}\b(?:maximum hit points?|hit point maximum)\b|"
            r"\b(?:maximum hit points?|hit point maximum)\b[^.!?;]{0,80}"
            r"\b(?:bonus|add(?:s)?|increase(?:s)?|decrease(?:s)?|reduce(?:s)?)\b",
            body,
            re.I,
        )
    )


def _feature_may_modify_speed(body: str) -> bool:
    return bool(
        re.search(
            r"\b(?:walking|walk|flying|fly|swimming|swim|climbing|climb|burrowing|burrow)?"
            r"\s*speed\b[^.!?;]{0,80}\b(?:increases?|decreases?|increased|reduced|bonus)\b|"
            r"\b(?:increase(?:s)?|decrease(?:s)?|add(?:s)?|reduce(?:s)?)\b"
            r"[^.!?;]{0,80}\b(?:your\s+)?(?:walking\s+|fly\s+|swim\s+|climb\s+)?speed\b",
            body,
            re.I,
        )
    )


def _owner_predicate(keys: list[tuple[str, str, str]]) -> tuple[str, list[str]]:
    terms = ["(dataset_id=? AND owner_type=? AND owner_key=?)" for _ in keys]
    return " OR ".join(terms), [part for key in keys for part in key]


def _pairs_predicate(keys: list[tuple[str, str]]) -> tuple[str, list[str]]:
    terms = ["(dataset_id=? AND local_key=?)" for _ in keys]
    return " OR ".join(terms), [part for key in keys for part in key]


def _direct_identity(identity: str) -> tuple[str, str] | None:
    dataset_id, separator, local_key = identity.partition(":")
    if not separator or not dataset_id or not local_key or ":" in local_key:
        return None
    return dataset_id, local_key


def _subclass_identity(identity: str) -> tuple[str, str, str] | None:
    dataset_id, separator, rest = identity.partition(":subclass:")
    if not separator or not dataset_id:
        return None
    parent_key, separator, subclass_key = rest.partition(":")
    if not separator or not parent_key or not subclass_key:
        return None
    return dataset_id, parent_key, subclass_key


def _owner_identity(dataset_id: str, owner_type: str, owner_key: str) -> str:
    if owner_type == "subclass":
        return f"{dataset_id}:subclass:{owner_key}"
    return f"{dataset_id}:{owner_key}"


def _provenance_identity(
    dataset: str | None, owner_type: str | None, key: str | None
) -> str | None:
    if dataset is None or owner_type is None or key is None:
        return None
    return _owner_identity(dataset, owner_type, key)


def _normalize_proficiency(kind: str, value: str) -> tuple[str, str]:
    normalized_kind = kind.casefold().replace("-", "_")
    normalized_value = value.casefold().strip()
    prefixes = {
        "skill": "skill",
        "saving_throw": "saving_throw",
        "armor": "armor",
        "weapon": "weapon",
        "tool": "tool",
        "language": "language",
        "weapon_mastery": "weapon_mastery",
    }
    if ":" in normalized_value:
        prefix, target = normalized_value.split(":", 1)
        if prefix in prefixes:
            return prefixes[prefix], target
    return normalized_kind, normalized_value


def _choice_proficiency_kind(
    choice_type: str, criterion: str, value: str, skills: dict[str, tuple[str, str]]
) -> str | None:
    criteria_map = {
        "skill": "skill",
        "tool_group": "tool",
        "weapon_category": "weapon",
        "weapon_mastery": "weapon_mastery",
        "language": "language",
    }
    if criterion in criteria_map:
        return criteria_map[criterion]
    if choice_type == "weapon_mastery":
        return "weapon_mastery"
    if choice_type != "proficiency":
        return None
    prefix = value.partition(":")[0].casefold()
    if prefix in {"skill", "tool", "weapon", "armor", "saving_throw", "language", "weapon_mastery"}:
        return prefix
    if value in skills:
        return "skill"
    return None


def _unique_sources(sources: Iterable[ProficiencySource]) -> tuple[ProficiencySource, ...]:
    unique = {
        (source.label, source.source_identity, source.source_rule): source for source in sources
    }
    return tuple(
        unique[key] for key in sorted(unique, key=lambda value: tuple(item or "" for item in value))
    )


def _unique_issues(issues: Iterable[DerivedIssue]) -> tuple[DerivedIssue, ...]:
    unique = {
        (issue.severity, issue.category, issue.message, issue.source_identity): issue
        for issue in issues
    }
    return tuple(
        unique[key] for key in sorted(unique, key=lambda value: tuple(item or "" for item in value))
    )


def _level_counts(
    levels: dict[int, tuple[int | None, int | None, int | None]], current_level: int
) -> tuple[int | None, int | None, int | None]:
    available = [level for level in levels if level <= current_level]
    if not available:
        return None, None, None
    return levels[max(available)]


def _unique_spell_results(
    spells: Iterable[SpellSelectionResult],
) -> list[SpellSelectionResult]:
    unique = {
        (spell.spell.identity, spell.acquisition, spell.source_rule): spell for spell in spells
    }
    return [
        unique[key] for key in sorted(unique, key=lambda value: tuple(item or "" for item in value))
    ]


def _spell_acquisition_counts(
    spells: Iterable[SpellSelectionResult],
) -> tuple[int, int, int]:
    rows = tuple(spells)
    cantrips = sum(row.acquisition == "cantrip" for row in rows)
    prepared = sum(row.acquisition in {"prepared", "always_prepared"} for row in rows)
    known = sum(row.acquisition in {"known", "pact_magic"} for row in rows)
    return cantrips, prepared, known


def _caster_contribution(mode: str, level: int) -> int | None:
    if mode == "full":
        return level
    if mode == "half_round_up":
        return (level + 1) // 2
    if mode == "half_round_down":
        return level // 2
    if mode == "third_round_down":
        return level // 3
    return None


def _integer_list(payload: object) -> tuple[int, ...]:
    try:
        values = json.loads(str(payload))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, int))


def _spell_acquisition_is_supported(acquisition: str, rule: _SpellcastingRule) -> bool:
    supported = {rule.acquisition, "cantrip", "innate", "always_prepared"}
    if rule.acquisition == "spellbook":
        supported.add("prepared")
    if rule.model == "pact_magic":
        supported.add("pact_magic")
    return acquisition in supported


def _saved_spell_is_structured_access(
    selection: object,
    owner: _Owner,
    access_rows: tuple[sqlite3.Row, ...],
    character: Character,
) -> bool:
    if (selection.owner_dataset_id, selection.owner_type, selection.owner_key) != owner.key:
        return False
    for row in access_rows:
        if (str(row[0]), str(row[1]), str(row[2])) != owner.key:
            continue
        active_level = character.total_level if str(row[6]) == "total" else owner.class_level
        if active_level is None:
            active_level = character.total_level
        if int(row[5]) > active_level:
            continue
        if selection.source_rule != str(row[7]):
            continue
        try:
            payload = json.loads(str(row[8]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if any(
            isinstance(spell, dict) and spell.get("identity") == selection.spell_reference.identity
            for spell in payload.get("spells", [])
        ):
            return True
    return False


def _access_acquisition_matches(acquisition: str, access_type: str) -> bool:
    valid = {
        "known": {"known"},
        "prepared": {"prepared", "always_prepared"},
        "spellbook": {"spellbook"},
        "innate": {"innate"},
    }
    return acquisition in valid.get(access_type, {"cantrip", "innate"})


def _mastery_matches(key: str, identity: str, label: str) -> bool:
    normalized_key = key.casefold()
    normalized_identity = identity.casefold()
    local_identity = normalized_identity.partition(":")[2]
    local_name = local_identity.rsplit("/", 1)[-1]
    return normalized_key in {
        normalized_identity,
        local_identity,
        local_name,
        label.casefold(),
    }


def _class_name(character: Character, identity: str) -> str:
    return next(
        (
            row.class_reference.name
            for row in character.levels
            if row.class_reference.identity == identity
        ),
        "Missing Class",
    )


def _weapon_is_proficient(
    identity: str,
    dataset_id: str,
    category: object,
    properties: tuple[str, ...],
    profs: dict[tuple[str, str], EffectiveProficiency],
) -> bool:
    if ("weapon", str(category)) in profs:
        return True
    local_key = identity.partition(":")[2]
    if ("weapon", identity.casefold()) in profs or ("weapon", local_key.casefold()) in profs:
        return True
    normalized = {value.casefold().replace(" ", "_") for value in properties}
    for (kind, key), proficiency in profs.items():
        if kind != "weapon" or proficiency.level == "none":
            continue
        if key.startswith("weapon:") and key.split(":", 1)[1] == identity.casefold():
            return True
        if key == "martial-weapons-that-have-the-finesse-or-light-property":
            if category == "martial" and {"finesse", "light"} & normalized:
                return True
        if key == "martial-weapons-that-have-the-light-property":
            if category == "martial" and "light" in normalized:
                return True
    return False
