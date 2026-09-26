"""Metadata-driven level-1 character creation over rules and persistence services."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from .character_builder import (
    CharacterBuilderRules,
    evaluate_requirement,
    normalize_feat_category,
)
from .characters import CharacterService
from .models.character import (
    AbilityChange,
    Character,
    CharacterValidationIssue,
    CharacterValidationReport,
    DecisionProvenance,
    PublishedReference,
)
from .models.character_builder import (
    Ability,
    AbilityIncrease,
    CharacterRuleContext,
    Choice,
    ChoiceKind,
    ChoiceOption,
    Grant,
    GrantKind,
    OptionCriteria,
    OptionCriteriaKind,
    Requirement,
    RequirementStatus,
    RuleReference,
)
from .storage.database import Database

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)
POINT_BUY_BUDGET = 27
POINT_BUY_COSTS = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}


@dataclass(frozen=True)
class BuilderOwner:
    owner_type: str
    owner_key: str
    dataset_id: str
    identity: str
    name: str
    source_key: str
    source_name: str
    edition: str
    description: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class BuilderOption:
    identity: str
    name: str
    source_name: str
    source_key: str
    dataset_id: str
    edition: str
    subtitle: str = ""
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class BuilderChoice:
    owner: BuilderOwner
    definition: Choice
    scope: str
    event_key: str | None
    character_level: int
    class_identity: str | None
    class_level: int | None

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            self.owner.dataset_id,
            self.owner.owner_type,
            self.owner.owner_key,
            str(self.definition.choice_key),
        )


@dataclass(frozen=True)
class BuilderChoiceOption:
    option_key: str | None
    label: str
    value: str | None = None
    reference: PublishedReference | None = None
    resolved: bool = True
    summary: str = ""


@dataclass(frozen=True)
class BuilderGrant:
    grant: Grant
    owner: BuilderOwner
    scope: str
    choice_key: str | None = None
    option_key: str | None = None


@dataclass(frozen=True)
class SpellChoiceGroup:
    key: str
    label: str
    acquisition: str
    count: int
    class_reference: PublishedReference | None
    max_spell_level: int
    source_rule: str
    owner_dataset_id: str = ""
    owner_type: str = "class"
    owner_key: str = ""
    choice_key: str = ""
    option_key: str | None = None
    spell_class_identities: tuple[str, ...] = ()
    spell_levels: tuple[int, ...] = ()
    character_level: int = 1
    character_class_identity: str | None = None
    class_level: int | None = None


@dataclass(frozen=True)
class CreationStep:
    key: str
    title: str


class CharacterCreationService:
    """Orchestrate rules and saved state while reading only selected rule owners."""

    def __init__(self, database: Database, characters: CharacterService | None = None) -> None:
        self.database = database
        self.characters = characters or CharacterService(database)

    def create_draft(self) -> Character:
        return self.characters.create_character("New Character", name_confirmed=False)

    def list_options(
        self,
        owner_type: str,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[BuilderOption, ...]:
        if owner_type not in {"species", "background", "class"}:
            raise ValueError("creation options support Species, Background, and Class")
        _validate_page(offset, limit)
        search = f"%{query.strip()}%"
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT b.dataset_id,b.owner_key,b.name,src.source_key,src.title,src.edition,"
                "d.title AS dataset_title,b.metadata_json "
                "FROM character_builder_owners b JOIN sources src ON src.id=b.source_id "
                "JOIN datasets d ON d.dataset_id=b.dataset_id "
                "WHERE b.owner_type=? AND b.edition='2024' AND "
                "(?='' OR b.name LIKE ? OR src.title LIKE ?) "
                "ORDER BY b.name COLLATE NOCASE,src.title COLLATE NOCASE,b.dataset_id,b.owner_key "
                "LIMIT ? OFFSET ?",
                (owner_type, query.strip(), search, search, limit, offset),
            ).fetchall()
        return tuple(
            BuilderOption(
                f"{row[0]}:{row[1]}",
                str(row[2]),
                str(row[4]),
                str(row[3]),
                str(row[0]),
                str(row[5]),
                _owner_subtitle(owner_type, json.loads(str(row[7]))),
                json.loads(str(row[7])),
            )
            for row in rows
        )

    def get_owner(self, owner_type: str, identity: str) -> BuilderOwner | None:
        dataset_id, owner_key = _split_direct_identity(identity)
        with self.database.connection() as db:
            row = db.execute(
                "SELECT b.name,src.source_key,src.title,src.edition,b.description,b.metadata_json "
                "FROM character_builder_owners b JOIN sources src ON src.id=b.source_id "
                "WHERE b.dataset_id=? AND b.owner_type=? AND b.owner_key=? AND b.edition='2024'",
                (dataset_id, owner_type, owner_key),
            ).fetchone()
        if row is None:
            return None
        return BuilderOwner(
            owner_type,
            owner_key,
            dataset_id,
            identity,
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            json.loads(str(row[5])),
        )

    def choose_species(self, character_id: str, identity: str) -> Character:
        owner = self.get_owner("species", identity)
        if owner is None:
            raise ValueError("That exact Species reference is no longer available.")

        def apply_change() -> Character:
            before = self.characters.get_character(character_id)
            changed = before.species is None or before.species.identity != identity
            self.characters.replace_species(
                character_id,
                PublishedReference("species", identity, owner.name, owner.edition),
            )
            if changed:
                self._apply_owner_grants(character_id, owner, {"species"}, "species")
                self._apply_spell_access(character_id, owner, "species")
            return self.characters.get_character(character_id)

        return self.characters.run_with_rollback(character_id, apply_change)

    def choose_background(self, character_id: str, identity: str) -> Character:
        owner = self.get_owner("background", identity)
        if owner is None:
            raise ValueError("That exact Background reference is no longer available.")

        def apply_change() -> Character:
            before = self.characters.get_character(character_id)
            changed = before.background is None or before.background.identity != identity
            self.characters.replace_background(
                character_id,
                PublishedReference("background", identity, owner.name, owner.edition),
            )
            if changed:
                self._apply_owner_grants(character_id, owner, {"background"}, "background")
                self._apply_background_feat(character_id, owner)
                self._refresh_ability_modifications(character_id)
            return self.characters.get_character(character_id)

        return self.characters.run_with_rollback(character_id, apply_change)

    def choose_starting_class(self, character_id: str, identity: str) -> Character:
        owner = self.get_owner("class", identity)
        if owner is None:
            raise ValueError("That exact Class reference is no longer available.")

        def apply_change() -> Character:
            before = self.characters.get_character(character_id)
            changed = before.starting_class is None or before.starting_class.identity != identity
            self.characters.replace_starting_class(
                character_id,
                PublishedReference("class", identity, owner.name, owner.edition),
            )
            if changed:
                self._apply_owner_grants(character_id, owner, {"starting_class"}, "class_level")
                self._apply_spell_access(character_id, owner, "class")
            return self.characters.get_character(character_id)

        return self.characters.run_with_rollback(character_id, apply_change)

    def choices_for_step(self, character_id: str, step: str) -> tuple[BuilderChoice, ...]:
        character = self.characters.get_character(character_id)
        owners: list[tuple[str, str, set[str]]]
        if step == "species_choices" and character.species is not None:
            owners = [("species", character.species.identity, {"species"})]
        elif step == "background_choices" and character.background is not None:
            owners = [("background", character.background.identity, {"background"})]
        elif step == "class_choices" and character.starting_class is not None:
            owners = [
                (
                    "class",
                    character.starting_class.identity,
                    {"starting_class", "progression_event"},
                )
            ]
        elif step == "equipment":
            owners = []
            if character.background is not None:
                owners.append(("background", character.background.identity, {"starting_equipment"}))
            if character.starting_class is not None:
                owners.append(("class", character.starting_class.identity, {"starting_equipment"}))
        elif step == "spells":
            owners = []
            if character.species is not None:
                owners.append(("species", character.species.identity, {"species"}))
            if character.starting_class is not None:
                owners.append(("class", character.starting_class.identity, {"spellcasting"}))
            owners.extend(
                ("feat", row.feat_reference.identity, {"spellcasting"}) for row in character.feats
            )
        else:
            return ()

        found: list[BuilderChoice] = []
        for owner_type, identity, scopes in owners:
            owner = self.get_owner(owner_type, identity)
            if owner is None:
                continue
            for item in self._owner_choices(owner, scopes, character):
                kind = item.definition.kind
                if step == "equipment":
                    include = kind in {ChoiceKind.EQUIPMENT, ChoiceKind.EQUIPMENT_PACKAGE}
                elif step == "spells":
                    include = kind in {ChoiceKind.SPELL, ChoiceKind.CANTRIP}
                else:
                    include = kind not in {
                        ChoiceKind.SPELL,
                        ChoiceKind.CANTRIP,
                        ChoiceKind.EQUIPMENT,
                        ChoiceKind.EQUIPMENT_PACKAGE,
                    }
                if include and self._choice_is_active(character, item):
                    found.append(item)
        if step == "spells":
            found.extend(self._spell_access_ability_choices(character))
        return tuple(found)

    def _spell_access_ability_choices(self, character: Character) -> list[BuilderChoice]:
        result: dict[tuple[str, str, str], BuilderChoice] = {}
        for owner, feat in self._spell_access_owners(character):
            with self.database.connection() as db:
                rows = db.execute(
                    "SELECT access_key,payload_json FROM character_builder_spell_access "
                    "WHERE dataset_id=? AND owner_type=? AND owner_key=? AND class_level<=1 "
                    "ORDER BY access_key",
                    (owner.dataset_id, owner.owner_type, owner.owner_key),
                ).fetchall()
            options_by_group: dict[str, set[str]] = {}
            source_by_group: dict[str, str] = {}
            for row in rows:
                try:
                    access = json.loads(str(row[1]))
                except json.JSONDecodeError:
                    continue
                if not isinstance(access, dict) or access.get("ability_selection") != "choice":
                    continue
                choice_group = str(access.get("choice_group") or row[0])
                option_key = (
                    str(access.get("choice_option"))
                    if access.get("choice_option") is not None
                    else None
                )
                if option_key is not None and not self._access_option_selected(
                    character, owner, choice_group, option_key, feat
                ):
                    continue
                values = access.get("ability_options")
                if not isinstance(values, list):
                    continue
                options_by_group.setdefault(choice_group, set()).update(
                    str(value).casefold() for value in values if str(value).casefold() in ABILITIES
                )
                source_by_group[choice_group] = str(access.get("source_rule", "Spellcasting"))
            context = self._choice_context(owner, character)
            for group, values in options_by_group.items():
                key = f"{group}/ability"
                options = [
                    ChoiceOption(
                        option_key=f"option/ability/{ability}",
                        label=_ability_name(ability),
                        value=ability,
                    )
                    for ability in ABILITIES
                    if ability in values
                ]
                if not options:
                    continue
                definition = Choice(
                    choice_key=key,
                    kind=ChoiceKind.OTHER,
                    count=1,
                    options=options,
                    source_rule=source_by_group[group],
                )
                result[(owner.dataset_id, owner.owner_type, key)] = BuilderChoice(
                    owner, definition, "spellcasting_ability", None, *context
                )
        return list(result.values())

    def choice_options(
        self,
        character_id: str,
        choice: BuilderChoice,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[BuilderChoiceOption, ...]:
        _validate_page(offset, limit, maximum=50)
        definition = choice.definition
        needle = query.casefold().strip()
        if definition.options:
            values = tuple(
                BuilderChoiceOption(
                    str(option.option_key),
                    option.label,
                    str(option.value) if option.value is not None else None,
                    self._reference_from_rule(choice.owner.dataset_id, option.reference),
                    option.resolved,
                    " · ".join(
                        f"+{increase.amount} {increase.ability.value.upper()}"
                        for increase in option.ability_increases
                    ),
                )
                for option in definition.options
                if not needle or needle in option.label.casefold()
            )
            return values[offset : offset + limit]
        if definition.criteria is None:
            return ()
        return self._criteria_options(choice, needle, offset, limit)

    def select_choice_option(
        self,
        character_id: str,
        choice: BuilderChoice,
        option: BuilderChoiceOption,
    ) -> Character:
        character = self.characters.get_character(character_id)
        selected = self._selected_for_choice(character, choice)
        if any(_same_selection(row, option) for row in selected):
            resolution = next(row for row in selected if _same_selection(row, option))
            self.characters.remove_choice_resolution(resolution.resolution_id)
            if choice.scope == "spellcasting":
                self.characters.remove_spell_access_ability_choice(
                    character_id,
                    owner_dataset_id=choice.owner.dataset_id,
                    owner_type=choice.owner.owner_type,
                    owner_key=choice.owner.owner_key,
                    choice_key=f"{choice.definition.choice_key}/ability",
                    character_level=choice.character_level,
                    class_identity=choice.class_identity,
                    class_level=choice.class_level,
                )
            self._refresh_ability_modifications(character_id)
            return self.characters.get_character(character_id)

        if choice.scope == "spellcasting_ability":
            self.characters.resolve_spell_access_ability(
                character_id,
                owner_dataset_id=choice.owner.dataset_id,
                owner_type=choice.owner.owner_type,
                owner_key=choice.owner.owner_key,
                choice_key=str(choice.definition.choice_key),
                selected_option_key=option.option_key or "",
                selected_value=option.value or "",
                character_level=choice.character_level,
                class_identity=choice.class_identity,
                class_level=choice.class_level,
                source_rule=choice.definition.source_rule,
            )
            return self.characters.get_character(character_id)

        rules = CharacterBuilderRules.for_choice_definitions(
            choice.owner.owner_type, choice.owner.owner_key, (choice.definition,)
        )
        replace_existing = choice.definition.count == 1 and bool(selected)
        allow_unresolved = not option.resolved
        resolution = self.characters.resolve_choice(
            character_id,
            rules=rules,
            rules_dataset_id=choice.owner.dataset_id,
            owner_type=choice.owner.owner_type,
            owner_key=choice.owner.owner_key,
            choice_key=str(choice.definition.choice_key),
            selected_option_key=option.option_key,
            selected_reference=option.reference,
            selected_value=option.value,
            character_level=choice.character_level,
            class_identity=choice.class_identity,
            class_level=choice.class_level,
            allow_unresolved=allow_unresolved,
            replace_existing=replace_existing,
        )
        if replace_existing and choice.scope == "spellcasting":
            self.characters.remove_spell_access_ability_choice(
                character_id,
                owner_dataset_id=choice.owner.dataset_id,
                owner_type=choice.owner.owner_type,
                owner_key=choice.owner.owner_key,
                choice_key=f"{choice.definition.choice_key}/ability",
                character_level=choice.character_level,
                class_identity=choice.class_identity,
                class_level=choice.class_level,
            )
        if resolution.resolution_state == "resolved" or choice.definition.kind in {
            ChoiceKind.EQUIPMENT,
            ChoiceKind.EQUIPMENT_PACKAGE,
        }:
            self._apply_choice_option_effects(character_id, choice, option)
        self._refresh_ability_modifications(character_id)
        return self.characters.get_character(character_id)

    def spell_groups(self, character_id: str) -> tuple[SpellChoiceGroup, ...]:
        character = self.characters.get_character(character_id)
        groups: list[SpellChoiceGroup] = []
        class_ref = character.starting_class
        if class_ref is not None and not class_ref.missing:
            dataset_id, class_key = _split_direct_identity(class_ref.identity)
            with self.database.connection() as db:
                casting = db.execute(
                    "SELECT spellcasting_model,acquisition,source_rule FROM class_spellcasting "
                    "WHERE dataset_id=? AND owner_type='class' AND owner_key=?",
                    (dataset_id, class_key),
                ).fetchone()
                levels = db.execute(
                    "SELECT cantrips_known,prepared_spells,known_spells "
                    "FROM class_spellcasting_levels WHERE dataset_id=? AND owner_type='class' "
                    "AND owner_key=? AND class_level=1",
                    (dataset_id, class_key),
                ).fetchone()
                if casting is not None and levels is not None:
                    progression_kind = "pact" if str(casting[0]) == "pact_magic" else "standalone"
                    max_level = int(
                        db.execute(
                            "SELECT COALESCE(MAX(spell_level),0) FROM class_spell_slots "
                            "WHERE dataset_id=? AND owner_type='class' AND owner_key=? "
                            "AND progression_kind=? AND class_level=1 AND slot_count>0",
                            (dataset_id, class_key, progression_kind),
                        ).fetchone()[0]
                    )
                    model, acquisition = str(casting[0]), str(casting[1])
                    shared = {
                        "owner_dataset_id": dataset_id,
                        "owner_type": "class",
                        "owner_key": class_key,
                        "spell_class_identities": (class_ref.identity,),
                        "character_class_identity": class_ref.identity,
                        "class_level": 1,
                    }
                    if levels[0] is not None and int(levels[0]) > 0:
                        groups.append(
                            SpellChoiceGroup(
                                "cantrip",
                                "Choose Cantrips",
                                "cantrip",
                                int(levels[0]),
                                class_ref,
                                0,
                                str(casting[2]),
                                choice_key="cantrip",
                                spell_levels=(0,),
                                **shared,
                            )
                        )
                    spell_acquisition = "pact_magic" if model == "pact_magic" else acquisition
                    if acquisition == "spellbook" and levels[2] is not None and int(levels[2]) > 0:
                        groups.append(
                            SpellChoiceGroup(
                                "spellbook",
                                "Add Starting Spellbook Spells",
                                "spellbook",
                                int(levels[2]),
                                class_ref,
                                max(1, max_level),
                                str(casting[2]),
                                choice_key="spellbook",
                                spell_levels=(1,),
                                **shared,
                            )
                        )
                        if levels[1] is not None and int(levels[1]) > 0:
                            groups.append(
                                SpellChoiceGroup(
                                    "prepared",
                                    "Choose Prepared Spells",
                                    "prepared",
                                    int(levels[1]),
                                    class_ref,
                                    max(1, max_level),
                                    str(casting[2]),
                                    choice_key="prepared",
                                    spell_levels=(1,),
                                    **shared,
                                )
                            )
                    else:
                        count = levels[1] if levels[1] is not None else levels[2]
                        if count is not None and int(count) > 0:
                            title = {
                                "known": "Choose Known Spells",
                                "prepared": "Choose Prepared Spells",
                                "special": "Choose Starting Spells",
                                "pact_magic": "Choose Pact Magic Spells",
                            }.get(spell_acquisition, "Choose Starting Spells")
                            acquisition_key = "pact_magic" if model == "pact_magic" else acquisition
                            groups.append(
                                SpellChoiceGroup(
                                    acquisition_key,
                                    title,
                                    acquisition_key,
                                    int(count),
                                    class_ref,
                                    max(1, max_level),
                                    str(casting[2]),
                                    choice_key=acquisition_key,
                                    spell_levels=(1,),
                                    **shared,
                                )
                            )
        groups.extend(self._metadata_spell_groups(character))
        return tuple(groups)

    def spell_options(
        self,
        character_id: str,
        group: SpellChoiceGroup,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[BuilderChoiceOption, ...]:
        _validate_page(offset, limit, maximum=50)
        search = f"%{query.strip()}%"
        class_identities = group.spell_class_identities or (
            (group.class_reference.identity,) if group.class_reference is not None else ()
        )
        class_keys = [_split_direct_identity(identity) for identity in class_identities]
        if not class_keys:
            return ()
        with self.database.connection() as db:
            class_clause = " OR ".join(
                "(class_entry.dataset_id=? AND class_entry.local_key=?)" for _ in class_keys
            )
            params: list[object] = []
            for dataset_id, class_key in class_keys:
                params.extend([dataset_id, class_key])
            spellbook_join = ""
            if (
                group.key == "prepared"
                and group.class_reference is not None
                and group.owner_type == "class"
                and self._class_has_spellbook(
                    *_split_direct_identity(group.class_reference.identity)
                )
            ):
                spellbook_join = (
                    "JOIN user_character_spells spellbook ON spellbook.character_id=? "
                    "AND spellbook.spell_identity=spell_entry.dataset_id || ':' "
                    "|| spell_entry.local_key "
                    "AND spellbook.source_class_identity=? AND spellbook.acquisition='spellbook' "
                )
                params[0:0] = [character_id, group.class_reference.identity]
            if group.spell_levels:
                level_clause = (
                    " AND CAST(sp.level AS TEXT) IN ("
                    + ",".join("?" for _ in group.spell_levels)
                    + ")"
                )
                params.extend(str(level) for level in group.spell_levels)
            else:
                level_clause = " AND sp.level BETWEEN ? AND ?"
                params.extend([0, group.max_spell_level])
            params.extend([query.strip(), search, limit, offset])
            rows = db.execute(
                "SELECT spell_entry.dataset_id,spell_entry.local_key,spell_entry.name,sp.level,"
                "sp.school,src.title FROM entries spell_entry JOIN spells sp "
                "ON sp.entry_id=spell_entry.id JOIN sources src ON src.id=spell_entry.source_id "
                "JOIN spell_classes sc ON sc.spell_id=sp.entry_id "
                "JOIN classes c ON c.entry_id=sc.class_id "
                "JOIN entries class_entry ON class_entry.id=c.entry_id "
                f"{spellbook_join}"
                "WHERE (" + class_clause + ") AND src.edition='2024' " + level_clause + " "
                "AND (?='' OR spell_entry.name LIKE ?) "
                "ORDER BY spell_entry.name COLLATE NOCASE,src.title LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return tuple(
            BuilderChoiceOption(
                None,
                str(row[2]),
                reference=PublishedReference("spell", f"{row[0]}:{row[1]}", str(row[2]), "2024"),
                summary=f"{_spell_level_label(int(row[3]))} · {row[4]} · {row[5]}",
            )
            for row in rows
        )

    def select_spell(
        self, character_id: str, group: SpellChoiceGroup, spell: PublishedReference
    ) -> Character:
        if spell.kind != "spell" or spell.edition != "2024":
            raise ValueError("Choose an exact 2024 Spell reference.")
        options = self.spell_options(character_id, group, query=spell.name, limit=50)
        if not any(
            option.reference and option.reference.identity == spell.identity for option in options
        ):
            raise ValueError("That spell is not in the published list for this choice.")
        character = self.characters.get_character(character_id)
        current_count = self._spell_group_count(character, group)
        if any(
            row.spell_reference.identity == spell.identity and _spell_row_matches_group(row, group)
            for row in character.spells
        ):
            raise ValueError("That spell is already selected for this choice.")
        if current_count >= group.count:
            raise ValueError("The published spell selection limit has been reached.")
        provenance = DecisionProvenance(
            owner_dataset_id=group.owner_dataset_id,
            owner_type=group.owner_type,
            owner_key=group.owner_key,
            choice_key=group.choice_key,
            option_key=group.option_key,
            character_level=group.character_level,
            class_identity=group.character_class_identity,
            class_level=group.class_level,
            source_rule=group.source_rule,
        )
        self.characters.add_spell(
            character_id,
            spell,
            acquisition=group.acquisition,
            source_class=group.class_reference,
            character_level=group.character_level,
            class_level=group.class_level,
            provenance=provenance,
        )
        return self.characters.get_character(character_id)

    def _metadata_spell_groups(self, character: Character) -> list[SpellChoiceGroup]:
        result: list[SpellChoiceGroup] = []
        for owner, feat in self._spell_access_owners(character):
            with self.database.connection() as db:
                rows = db.execute(
                    "SELECT access_key,access_type,class_level,source_rule,payload_json "
                    "FROM character_builder_spell_access WHERE dataset_id=? AND owner_type=? "
                    "AND owner_key=? AND class_level<=1 ORDER BY access_key",
                    (owner.dataset_id, owner.owner_type, owner.owner_key),
                ).fetchall()
            for row in rows:
                try:
                    access = json.loads(str(row[4]))
                except json.JSONDecodeError:
                    continue
                if not isinstance(access, dict) or str(access.get("access")) == "expanded":
                    continue
                criteria = access.get("criteria")
                if not isinstance(criteria, dict) or criteria.get("kind") != "spell_list":
                    continue
                count = access.get("count")
                if not isinstance(count, int) or count < 1:
                    continue
                group_key = str(access.get("choice_group") or row[0])
                option_key = (
                    str(access.get("choice_option"))
                    if access.get("choice_option") is not None
                    else None
                )
                if option_key is not None and not self._access_option_selected(
                    character, owner, group_key, option_key, feat
                ):
                    continue
                class_values = criteria.get("values")
                if not isinstance(class_values, list) or not class_values:
                    continue
                class_refs = []
                for value in class_values:
                    identity = str(value)
                    if ":" not in identity:
                        identity = f"{owner.dataset_id}:{identity}"
                    reference = self._reference_for_identity("class", identity)
                    if reference is not None and not reference.missing:
                        class_refs.append(reference)
                if not class_refs:
                    continue
                filters = criteria.get("filters", {})
                levels = filters.get("spell_level", []) if isinstance(filters, dict) else []
                spell_levels = (
                    tuple(sorted({int(level) for level in levels if str(level).isdigit()}))
                    if isinstance(levels, list)
                    else ()
                )
                acquisition = str(row[1])
                if spell_levels == (0,):
                    acquisition = "cantrip"
                    label = "Choose Cantrips"
                else:
                    label = {
                        "known": "Choose Known Spells",
                        "prepared": "Choose Prepared Spells",
                        "innate": "Choose Innate Spells",
                        "spellbook": "Add Starting Spellbook Spells",
                    }.get(acquisition, "Choose Starting Spells")
                list_ref = class_refs[0] if len(class_refs) == 1 else None
                result.append(
                    SpellChoiceGroup(
                        key=f"{owner.owner_type}:{owner.owner_key}:{row[0]}",
                        label=label,
                        acquisition=acquisition,
                        count=count,
                        class_reference=list_ref,
                        max_spell_level=max(spell_levels, default=1),
                        source_rule=str(row[3]),
                        owner_dataset_id=owner.dataset_id,
                        owner_type=owner.owner_type,
                        owner_key=owner.owner_key,
                        choice_key=group_key,
                        option_key=option_key,
                        spell_class_identities=tuple(ref.identity for ref in class_refs),
                        spell_levels=spell_levels,
                        character_level=feat.character_level if feat else 1,
                        character_class_identity=feat.class_identity
                        if feat
                        else (
                            character.starting_class.identity
                            if owner.owner_type == "class" and character.starting_class
                            else None
                        ),
                        class_level=feat.class_level
                        if feat
                        else (1 if owner.owner_type == "class" else None),
                    )
                )
        return result

    def _spell_access_owners(
        self, character: Character
    ) -> tuple[tuple[BuilderOwner, object | None], ...]:
        result: list[tuple[BuilderOwner, object | None]] = []
        for reference in (character.species, character.starting_class):
            if reference is None or reference.missing:
                continue
            owner_type = "species" if reference.kind == "species" else "class"
            owner = self.get_owner(owner_type, reference.identity)
            if owner is not None:
                result.append((owner, None))
        for feat in character.feats:
            if feat.feat_reference.missing:
                continue
            owner = self.get_owner("feat", feat.feat_reference.identity)
            if owner is not None:
                result.append((owner, feat))
        return tuple(result)

    @staticmethod
    def _access_option_selected(
        character: Character,
        owner: BuilderOwner,
        choice_key: str,
        option_key: str,
        feat: object | None,
    ) -> bool:
        return any(
            row.owner_dataset_id == owner.dataset_id
            and row.owner_type == owner.owner_type
            and row.owner_key == owner.owner_key
            and row.choice_key == choice_key
            and row.selected_option_key == option_key
            and (feat is None or row.character_level == getattr(feat, "character_level", 1))
            and (feat is None or row.class_identity == getattr(feat, "class_identity", None))
            and (feat is None or row.class_level == getattr(feat, "class_level", None))
            for row in character.choices
        )

    def ability_scores(
        self,
        character_id: str,
        method: str,
        scores: dict[str, int],
    ) -> Character:
        validate_ability_scores(method, scores, require_complete=False)
        character = self.characters.get_character(character_id)
        changes = self._ability_changes(character)
        return self.characters.set_ability_state(character_id, scores, changes, method=method)

    def save_ability_score_inputs(
        self, character_id: str, method: str, scores: dict[str, int]
    ) -> Character:
        """Persist valid individual inputs while the user is still filling the six scores."""

        if method not in {"standard_array", "point_buy", "manual"}:
            raise ValueError("Choose Standard Array, Point Buy, or Manual Entry.")
        limits = {
            "standard_array": set(STANDARD_ARRAY),
            "point_buy": set(POINT_BUY_COSTS),
            "manual": set(range(3, 19)),
        }[method]
        if set(scores) - set(ABILITIES):
            raise ValueError("Unknown ability score.")
        if any(not isinstance(value, int) or value not in limits for value in scores.values()):
            raise ValueError("One or more ability scores are outside the selected method's range.")
        if method == "standard_array" and len(set(scores.values())) != len(scores):
            raise ValueError("Each Standard Array score can be assigned only once.")
        character = self.characters.get_character(character_id)
        return self.characters.set_ability_state(
            character_id,
            scores,
            self._ability_changes(character),
            method=method,
        )

    def steps(self, character_id: str) -> tuple[CreationStep, ...]:
        character = self.characters.get_character(character_id)
        result = [CreationStep("name", "Name"), CreationStep("species", "Species")]
        if self.choices_for_step(character_id, "species_choices"):
            result.append(CreationStep("species_choices", "Species Choices"))
        result.append(CreationStep("background", "Background"))
        if self.choices_for_step(character_id, "background_choices"):
            result.append(CreationStep("background_choices", "Background Choices"))
        result.append(CreationStep("class", "Starting Class"))
        result.append(CreationStep("abilities", "Ability Scores"))
        if self.choices_for_step(character_id, "class_choices"):
            result.append(CreationStep("class_choices", "Character Choices"))
        if (
            self.choices_for_step(character_id, "equipment")
            or character.equipment
            or character.currency
        ):
            result.append(CreationStep("equipment", "Starting Equipment"))
        if (
            self.choices_for_step(character_id, "spells")
            or self.spell_groups(character_id)
            or self.unresolved_spell_choices(character_id)
        ):
            result.append(CreationStep("spells", "Spells"))
        result.append(CreationStep("review", "Review"))
        return tuple(result)

    def resume_step(self, character_id: str) -> str:
        character = self.characters.get_character(character_id)
        if character.state == "complete":
            return "review"
        if not character.name_confirmed:
            return "name"
        if character.species is None or character.species.missing:
            return "species"
        if self._has_required_choices(character_id, "species_choices"):
            return "species_choices"
        if character.background is None or character.background.missing:
            return "background"
        if self._has_required_choices(character_id, "background_choices"):
            return "background_choices"
        if character.starting_class is None or character.starting_class.missing:
            return "class"
        if not self._scores_complete(character):
            return "abilities"
        if self._has_required_choices(character_id, "class_choices"):
            return "class_choices"
        if self._has_required_choices(character_id, "equipment"):
            return "equipment"
        if (
            self._has_required_choices(character_id, "spells")
            or self._spells_incomplete(character_id)
            or self.unresolved_spell_choices(character_id)
        ):
            return "spells"
        return "review"

    def validate_creation(self, character_id: str) -> CharacterValidationReport:
        character = self.characters.get_character(character_id)
        issues = list(self.characters.validate_character(character_id).issues)
        issues = [issue for issue in issues if issue.code != "missing_required_choice"]
        if not character.name_confirmed:
            issues.append(_issue("name_required", "Enter a character name before completing."))
        for label, reference in (
            ("Species", character.species),
            ("Background", character.background),
            ("Starting Class", character.starting_class),
        ):
            if reference is None:
                issues.append(
                    _issue(
                        f"missing_{label.casefold().replace(' ', '_')}",
                        f"Choose a {label}.",
                        "error",
                    )
                )
            elif reference.missing:
                issues.append(
                    _issue(
                        "stale_reference",
                        f"The saved {label} reference '{reference.name}' is no longer available.",
                    )
                )
        if len(character.levels) != 1 or character.levels[0].class_level != 1:
            issues.append(
                _issue(
                    "level_one_required",
                    "Character creation must have exactly one starting class level.",
                )
            )
        if not self._scores_complete(character):
            issues.append(_issue("ability_scores_required", "Assign all six base ability scores."))
        elif character.ability_score_method is not None:
            try:
                validate_ability_scores(
                    character.ability_score_method,
                    dict(character.base_ability_scores),
                    require_complete=True,
                )
            except ValueError as exc:
                issues.append(_issue("ability_scores_invalid", str(exc)))
        for step in (
            "species_choices",
            "background_choices",
            "class_choices",
            "equipment",
            "spells",
        ):
            for choice in self.choices_for_step(character_id, step):
                selected = self._selected_for_choice(character, choice)
                remaining = choice.definition.count - len(selected)
                if remaining > 0:
                    if not self.choice_options(character_id, choice, limit=1):
                        choice_kind = choice.definition.kind.value.replace("_", " ")
                        message = (
                            f"{choice.owner.name} has a required {choice_kind} choice "
                            "that is not available in the structured rules data."
                        )
                    else:
                        message = _choice_missing_message(choice, remaining)
                    issues.append(_issue("missing_choice", message))
                elif any(row.resolution_state == "unresolved" for row in selected):
                    severity = (
                        "warning"
                        if choice.definition.kind
                        in {ChoiceKind.EQUIPMENT, ChoiceKind.EQUIPMENT_PACKAGE}
                        else "error"
                    )
                    kind_label = choice.definition.kind.value.replace("_", " ")
                    issues.append(
                        _issue(
                            "choice_unresolved",
                            f"{choice.owner.name}: a required {kind_label} choice is unresolved.",
                            severity,
                        )
                    )
        for group in self.spell_groups(character_id):
            selected_count = self._spell_group_count(character, group)
            if selected_count < group.count:
                missing = group.count - selected_count
                choice_name = group.label.removeprefix("Choose ").removeprefix("Add Starting ")
                issues.append(
                    _issue(
                        "missing_spell_choice",
                        f"Select {missing} more {choice_name}.",
                    )
                )
        for message in self.unresolved_spell_choices(character_id):
            issues.append(_issue("spell_choice_unresolved", message))
        for equipment in character.equipment:
            if equipment.resolution_state == "unresolved":
                label = equipment.unresolved_selection or "selection"
                issues.append(
                    _issue(
                        "equipment_unresolved",
                        f"Equipment '{label}' is saved without an exact Item reference.",
                        "warning",
                    )
                )
        unique = {(item.code, item.message): item for item in issues}
        return CharacterValidationReport(character_id, tuple(unique.values()))

    def complete_character(self, character_id: str) -> Character:
        report = self.validate_creation(character_id)
        errors = [issue for issue in report.issues if issue.severity == "error"]
        if errors:
            raise ValueError(errors[0].message)
        return self.characters.set_character_state(character_id, "complete")

    def unresolved_spell_choices(self, character_id: str) -> tuple[str, ...]:
        """Return required level-one spell choices whose filters are still prose-only."""

        character = self.characters.get_character(character_id)
        messages: set[str] = set()
        context = self._requirement_context(character)
        for owner, feat in self._spell_access_owners(character):
            with self.database.connection() as db:
                rows = db.execute(
                    "SELECT payload_json FROM character_builder_spell_access "
                    "WHERE dataset_id=? AND owner_type=? AND owner_key=? AND class_level<=1",
                    (owner.dataset_id, owner.owner_type, owner.owner_key),
                ).fetchall()
            for row in rows:
                try:
                    access = json.loads(str(row[0]))
                except json.JSONDecodeError:
                    continue
                if (
                    not isinstance(access, dict)
                    or not access.get("unresolved_details")
                    or str(access.get("access")) == "expanded"
                ):
                    continue
                requirement_value = access.get("requirement")
                if isinstance(requirement_value, dict):
                    requirement = Requirement.model_validate(requirement_value)
                    if (
                        evaluate_requirement(requirement, context).status
                        is RequirementStatus.UNSATISFIED
                    ):
                        continue
                group = access.get("choice_group")
                option = access.get("choice_option")
                if group and option and not self._access_option_selected(
                    character, owner, str(group), str(option), feat
                ):
                    continue
                messages.add(
                    f"{owner.name} has a required spell choice that cannot be filtered "
                    "from the available rules data."
                )
        return tuple(sorted(messages))

    def _owner_choices(
        self, owner: BuilderOwner, scopes: set[str], character: Character
    ) -> tuple[BuilderChoice, ...]:
        if not scopes:
            return ()
        marks = ",".join("?" for _ in scopes)
        event_keys: set[str] = set()
        if owner.owner_type == "class" and "progression_event" in scopes:
            with self.database.connection() as db:
                event_keys = {
                    str(row[0])
                    for row in db.execute(
                        "SELECT event_key FROM class_progression_events WHERE dataset_id=? "
                        "AND owner_type='class' AND owner_key=? AND class_level=1",
                        (owner.dataset_id, owner.owner_key),
                    ).fetchall()
                }
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT choice_key,scope,event_key,choice_type,choice_count,criteria_kind,"
                "criteria_values_json,criteria_filters_json,requirement_key,depends_on_choice,"
                "depends_on_option,exclusions_json,source_rule FROM character_rule_choices "
                "WHERE dataset_id=? AND owner_type=? AND owner_key=? "
                f"AND scope IN ({marks}) ORDER BY scope,choice_key",
                (owner.dataset_id, owner.owner_type, owner.owner_key, *sorted(scopes)),
            ).fetchall()
            result: list[BuilderChoice] = []
            for row in rows:
                scope, event_key = str(row[1]), (str(row[2]) if row[2] is not None else None)
                if scope == "progression_event" and event_key not in event_keys:
                    continue
                criterion = None
                if row[5] is not None:
                    criterion = OptionCriteria(
                        kind=str(row[5]),
                        values=json.loads(str(row[6])),
                        filters=json.loads(str(row[7])) if row[7] is not None else {},
                    )
                requirement = None
                if row[8] is not None:
                    requirement_row = db.execute(
                        "SELECT payload_json FROM character_rule_requirements WHERE dataset_id=? "
                        "AND owner_type=? AND owner_key=? AND requirement_key=?",
                        (owner.dataset_id, owner.owner_type, owner.owner_key, row[8]),
                    ).fetchone()
                    if requirement_row is not None:
                        requirement = Requirement.model_validate(
                            json.loads(str(requirement_row[0]))
                        )
                option_rows = db.execute(
                    "SELECT option_key,label,value,reference_kind,reference_identity,"
                    "ability_increases_json,group_name,resolved FROM character_rule_choice_options "
                    "WHERE dataset_id=? AND owner_type=? AND owner_key=? AND choice_key=? "
                    "ORDER BY option_key",
                    (owner.dataset_id, owner.owner_type, owner.owner_key, row[0]),
                ).fetchall()
                options = []
                for option_row in option_rows:
                    reference = (
                        RuleReference(
                            kind=str(option_row[3]),
                            identity=str(option_row[4]),
                            resolved=bool(option_row[7]),
                        )
                        if option_row[3] is not None
                        else None
                    )
                    grants = self._choice_option_grants(db, owner, str(row[0]), str(option_row[0]))
                    options.append(
                        ChoiceOption(
                            option_key=str(option_row[0]),
                            label=str(option_row[1]),
                            value=str(option_row[2]) if option_row[2] is not None else None,
                            reference=reference,
                            ability_increases=[
                                AbilityIncrease.model_validate(value)
                                for value in json.loads(str(option_row[5]))
                            ],
                            group=str(option_row[6]) if option_row[6] is not None else None,
                            grants=grants,
                            resolved=bool(option_row[7]),
                        )
                    )
                definition = Choice(
                    choice_key=str(row[0]),
                    kind=str(row[3]),
                    count=int(row[4]),
                    options=options,
                    criteria=criterion,
                    requirement=requirement,
                    exclusions=[
                        RuleReference.model_validate(value) for value in json.loads(str(row[11]))
                    ],
                    source_rule=str(row[12]),
                    depends_on_choice=(str(row[9]) if row[9] is not None else None),
                    depends_on_option=(str(row[10]) if row[10] is not None else None),
                )
                character_level, class_identity, class_level = self._choice_context(
                    owner, character
                )
                result.append(
                    BuilderChoice(
                        owner,
                        definition,
                        scope,
                        event_key,
                        character_level,
                        class_identity,
                        class_level,
                    )
                )
        return tuple(result)

    def _choice_option_grants(
        self, db, owner: BuilderOwner, choice_key: str, option_key: str
    ) -> list[Grant]:
        rows = db.execute(
            "SELECT grant_key,grant_type,value,reference_kind,reference_identity,proficiency_kind,"
            "quantity,unit,source_rule,unresolved FROM character_rule_grants WHERE dataset_id=? "
            "AND owner_type=? AND owner_key=? AND scope='choice_option' AND choice_key=? "
            "AND option_key=? ORDER BY grant_key",
            (owner.dataset_id, owner.owner_type, owner.owner_key, choice_key, option_key),
        ).fetchall()
        result = []
        for row in rows:
            reference = (
                RuleReference(
                    kind=str(row[3]),
                    identity=str(row[4]),
                    resolved=not bool(row[9]),
                )
                if row[3] is not None
                else None
            )
            result.append(
                Grant(
                    grant_key=str(row[0]),
                    kind=str(row[1]),
                    value=str(row[2]) if row[2] is not None else None,
                    reference=reference,
                    proficiency_kind=(str(row[5]) if row[5] is not None else None),
                    quantity=int(row[6]) if row[6] is not None else None,
                    unit=str(row[7]) if row[7] is not None else None,
                    source_rule=str(row[8]),
                    unresolved=bool(row[9]),
                )
            )
        return result

    def _criteria_options(
        self, choice: BuilderChoice, query: str, offset: int, limit: int
    ) -> tuple[BuilderChoiceOption, ...]:
        criteria = choice.definition.criteria
        assert criteria is not None
        dataset_id = choice.owner.dataset_id
        marks = ",".join("?" for _ in criteria.values)
        search = f"%{query}%"
        rows = []
        with self.database.connection() as db:
            if criteria.kind is OptionCriteriaKind.SKILL:
                skill_filter = ""
                params: list[object] = [dataset_id]
                if not any(value.casefold() == "all" for value in criteria.values):
                    skill_filter = " AND skill_key IN (" + marks + ")"
                    params.extend(criteria.values)
                params.extend([query, search, limit, offset])
                rows = db.execute(
                    "SELECT skill_key,name FROM character_builder_skills WHERE dataset_id=? "
                    + skill_filter
                    + " AND (?='' OR name LIKE ?) "
                    "ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?",
                    params,
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(None, str(row[1]), value=str(row[0])) for row in rows
                )
            if criteria.kind is OptionCriteriaKind.SPELL_LIST:
                values = [_class_local_key(value, dataset_id) for value in criteria.values]
                level_values = criteria.filters.get("spell_level", [])
                if not values:
                    return ()
                class_marks = ",".join("?" for _ in values)
                params: list[object] = [dataset_id, *values, dataset_id]
                level_clause = ""
                if isinstance(level_values, list) and level_values:
                    level_marks = ",".join("?" for _ in level_values)
                    level_clause = f" AND CAST(sp.level AS TEXT) IN ({level_marks})"
                    params.extend(str(level) for level in level_values)
                params.extend([query, search, limit, offset])
                rows = db.execute(
                    "SELECT DISTINCT se.dataset_id,se.local_key,se.name,sp.level,"
                    "sp.school,src.title "
                    "FROM entries se JOIN spells sp ON sp.entry_id=se.id "
                    "JOIN sources src ON src.id=se.source_id JOIN spell_classes sc "
                    "ON sc.spell_id=sp.entry_id JOIN classes c ON c.entry_id=sc.class_id "
                    "JOIN entries ce ON ce.id=c.entry_id WHERE ce.dataset_id=? "
                    f"AND ce.local_key IN ({class_marks}) AND se.dataset_id=? "
                    "AND src.edition='2024'" + level_clause + " AND (?='' OR se.name LIKE ?) "
                    "ORDER BY se.name COLLATE NOCASE,src.title LIMIT ? OFFSET ?",
                    params,
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(
                        None,
                        str(row[2]),
                        reference=PublishedReference(
                            "spell", f"{row[0]}:{row[1]}", str(row[2]), "2024"
                        ),
                        summary=(f"{_spell_level_label(int(row[3]))} · {row[4]} · {row[5]}"),
                    )
                    for row in rows
                )
            if criteria.kind is OptionCriteriaKind.FEAT_CATEGORY:
                rows = db.execute(
                    "SELECT e.dataset_id,e.local_key,e.name,f.category,src.title FROM entries e "
                    "JOIN feats f ON f.entry_id=e.id JOIN sources src ON src.id=e.source_id "
                    "WHERE e.kind='feat' AND e.dataset_id=? AND lower(f.category) IN ("
                    + marks
                    + ") "
                    "AND src.edition='2024' AND (?='' OR e.name LIKE ?) "
                    "ORDER BY e.name COLLATE NOCASE LIMIT ? OFFSET ?",
                    (
                        dataset_id,
                        *[normalize_feat_category(value) for value in criteria.values],
                        query,
                        search,
                        limit,
                        offset,
                    ),
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(
                        None,
                        str(row[2]),
                        reference=PublishedReference(
                            "feat", f"{row[0]}:{row[1]}", str(row[2]), "2024"
                        ),
                        summary=f"{row[3]} · {row[4]}",
                    )
                    for row in rows
                )
            if criteria.kind is OptionCriteriaKind.ITEM_TYPE:
                normalized = {_normalize_option_type(value) for value in criteria.values}
                type_clause = ""
                params: list[object] = [dataset_id]
                if "all" not in normalized:
                    type_clause = (
                        " AND lower(replace(i.item_type,' ','')) IN ("
                        + ",".join("?" for _ in normalized)
                        + ")"
                    )
                    params.extend(sorted(normalized))
                params.extend([query, search, limit, offset])
                rows = db.execute(
                    "SELECT e.dataset_id,e.local_key,e.name,i.item_type,src.title FROM entries e "
                    "JOIN items i ON i.entry_id=e.id JOIN sources src ON src.id=e.source_id "
                    "WHERE e.kind='item' AND e.dataset_id=? AND src.edition='2024' "
                    + type_clause
                    + " AND (?='' OR e.name LIKE ?) "
                    "ORDER BY e.name COLLATE NOCASE LIMIT ? OFFSET ?",
                    params,
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(
                        None,
                        str(row[2]),
                        reference=PublishedReference(
                            "item", f"{row[0]}:{row[1]}", str(row[2]), "2024"
                        ),
                        summary=f"{row[3]} · {row[4]}",
                    )
                    for row in rows
                )
            if criteria.kind is OptionCriteriaKind.TOOL_GROUP:
                values = {value.casefold() for value in criteria.values}
                if "all" in values:
                    types_clause = (
                        "(lower(i.item_type) LIKE '%tool%' "
                        "OR lower(i.item_type) LIKE '%instrument%' "
                        "OR lower(i.item_type) LIKE '%set%')"
                    )
                    params = [dataset_id, query, search, limit, offset]
                elif "artisan" in values:
                    types_clause = "lower(i.item_type) LIKE '%artisan%'"
                    params = [dataset_id, query, search, limit, offset]
                else:
                    types_clause = "lower(i.item_type) IN (" + ",".join("?" for _ in values) + ")"
                    params = [dataset_id, *sorted(values), query, search, limit, offset]
                rows = db.execute(
                    "SELECT e.dataset_id,e.local_key,e.name,i.item_type,src.title FROM entries e "
                    "JOIN items i ON i.entry_id=e.id JOIN sources src ON src.id=e.source_id "
                    "WHERE e.kind='item' AND e.dataset_id=? AND src.edition='2024' AND "
                    + types_clause
                    + " AND (?='' OR e.name LIKE ?) "
                    "ORDER BY e.name COLLATE NOCASE LIMIT ? OFFSET ?",
                    params,
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(
                        None,
                        str(row[2]),
                        reference=PublishedReference(
                            "item", f"{row[0]}:{row[1]}", str(row[2]), "2024"
                        ),
                        summary=f"{row[3]} · {row[4]}",
                    )
                    for row in rows
                )
            if criteria.kind is OptionCriteriaKind.WEAPON_MASTERY:
                values = [_local_reference(value) for value in criteria.values]
                requires_proficiency = bool(
                    criteria.filters.get("requires_weapon_proficiency", False)
                )
                proficiency_clause = ""
                proficiency_params: list[object] = []
                if requires_proficiency:
                    proficiencies = db.execute(
                        "SELECT lower(value) FROM character_rule_grants "
                        "WHERE dataset_id=? AND owner_type='class' AND owner_key=? "
                        "AND scope='starting_class' AND proficiency_kind='weapon' "
                        "AND value IS NOT NULL",
                        (choice.owner.dataset_id, choice.owner.owner_key),
                    ).fetchall()
                    weapon_categories = {
                        str(row[0]) for row in proficiencies if str(row[0]) in {"simple", "martial"}
                    }
                    if "all" in {str(row[0]) for row in proficiencies}:
                        weapon_categories = {"simple", "martial"}
                    if not weapon_categories:
                        return ()
                    proficiency_clause = (
                        " AND lower(b.weapon_category) IN ("
                        + ",".join("?" for _ in weapon_categories)
                        + ")"
                    )
                    proficiency_params = sorted(weapon_categories)
                rows = db.execute(
                    "SELECT DISTINCT e.dataset_id,e.local_key,e.name,b.weapon_category,src.title "
                    "FROM character_builder_equipment b "
                    "JOIN json_each(b.mastery_references_json) mastery "
                    "JOIN entries e ON e.dataset_id=b.dataset_id AND e.local_key=b.item_key "
                    "JOIN items i ON i.entry_id=e.id "
                    "JOIN sources src ON src.id=e.source_id "
                    "WHERE b.dataset_id=? AND b.category='weapon' AND i.rarity IS NULL "
                    "AND lower(substr(json_extract(mastery.value,'$.identity'), "
                    "instr(json_extract(mastery.value,'$.identity'), ':')+1)) IN ("
                    + ",".join("?" for _ in values)
                    + ")" + proficiency_clause
                    + " AND src.edition='2024' AND (?='' OR e.name LIKE ?) "
                    "ORDER BY e.name COLLATE NOCASE LIMIT ? OFFSET ?",
                    (
                        dataset_id,
                        *values,
                        *proficiency_params,
                        query,
                        search,
                        limit,
                        offset,
                    ),
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(
                        None,
                        str(row[2]),
                        reference=PublishedReference(
                            "item", f"{row[0]}:{row[1]}", str(row[2]), "2024"
                        ),
                        summary=f"{row[3]} weapon · {row[4]}",
                    )
                    for row in rows
                )
            if criteria.kind is OptionCriteriaKind.LANGUAGE:
                values = [value for value in criteria.values if value.casefold() != "all"]
                with_values = ""
                params: list[object] = [dataset_id]
                if values:
                    with_values = " AND lower(value) IN (" + ",".join("?" for _ in values) + ")"
                    params.extend(value.casefold() for value in values)
                params.extend([query, search, limit, offset])
                rows = db.execute(
                    "SELECT DISTINCT value FROM character_rule_grants WHERE dataset_id=? "
                    "AND proficiency_kind='language' AND value IS NOT NULL"
                    + with_values
                    + " AND (?='' OR value LIKE ?) ORDER BY value COLLATE NOCASE LIMIT ? OFFSET ?",
                    params,
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(None, str(row[0]), value=str(row[0])) for row in rows
                )
            if criteria.kind is OptionCriteriaKind.WEAPON_CATEGORY:
                rows = db.execute(
                    "SELECT e.dataset_id,e.local_key,e.name,b.weapon_category,src.title "
                    "FROM character_builder_equipment b "
                    "JOIN entries e ON e.dataset_id=b.dataset_id "
                    "AND e.local_key=b.item_key JOIN sources src ON src.id=e.source_id "
                    "WHERE b.dataset_id=? AND lower(b.weapon_category) IN (" + marks + ") "
                    "AND (?='' OR e.name LIKE ?) ORDER BY e.name COLLATE NOCASE LIMIT ? OFFSET ?",
                    (
                        dataset_id,
                        *[value.casefold() for value in criteria.values],
                        query,
                        search,
                        limit,
                        offset,
                    ),
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(
                        None,
                        str(row[2]),
                        reference=PublishedReference(
                            "item", f"{row[0]}:{row[1]}", str(row[2]), "2024"
                        ),
                        summary=f"{row[3]} weapon · {row[4]}",
                    )
                    for row in rows
                )
            if criteria.kind is OptionCriteriaKind.OPTIONAL_FEATURE_TYPE:
                rows = db.execute(
                    "SELECT b.dataset_id,b.owner_key,b.name,"
                    "json_extract(b.metadata_json,'$.option_type'),src.title "
                    "FROM character_builder_owners b JOIN sources src ON src.id=b.source_id "
                    "WHERE b.dataset_id=? AND b.owner_type='optional_feature' "
                    "AND lower(json_extract(b.metadata_json,'$.option_type')) IN (" + marks + ") "
                    "AND (?='' OR b.name LIKE ?) ORDER BY b.name COLLATE NOCASE LIMIT ? OFFSET ?",
                    (
                        dataset_id,
                        *[value.casefold() for value in criteria.values],
                        query,
                        search,
                        limit,
                        offset,
                    ),
                ).fetchall()
                return tuple(
                    BuilderChoiceOption(
                        None,
                        str(row[2]),
                        reference=PublishedReference(
                            "optional_feature", f"{row[0]}:{row[1]}", str(row[2]), "2024"
                        ),
                        summary=f"{row[3]} · {row[4]}",
                    )
                    for row in rows
                )
        return ()

    def _apply_owner_grants(
        self, character_id: str, owner: BuilderOwner, scopes: set[str], provenance_kind: str
    ) -> None:
        for row in self._owner_grants(owner, scopes):
            self._apply_grant(
                character_id,
                row,
                DecisionProvenance(
                    owner_dataset_id=owner.dataset_id,
                    owner_type=owner.owner_type,
                    owner_key=owner.owner_key,
                    character_level=1,
                    source_rule=row.grant.source_rule,
                ),
                provenance_kind,
            )

    def _owner_grants(self, owner: BuilderOwner, scopes: set[str]) -> tuple[BuilderGrant, ...]:
        marks = ",".join("?" for _ in scopes)
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT grant_key,scope,choice_key,option_key,grant_type,value,reference_kind,"
                "reference_identity,proficiency_kind,quantity,unit,source_rule,unresolved "
                "FROM character_rule_grants WHERE dataset_id=? AND owner_type=? AND owner_key=? "
                f"AND scope IN ({marks}) ORDER BY grant_key",
                (owner.dataset_id, owner.owner_type, owner.owner_key, *sorted(scopes)),
            ).fetchall()
        result = []
        for row in rows:
            reference = (
                RuleReference(
                    kind=str(row[6]),
                    identity=str(row[7]),
                    resolved=not bool(row[12]),
                )
                if row[6] is not None
                else None
            )
            result.append(
                BuilderGrant(
                    Grant(
                        grant_key=str(row[0]),
                        kind=str(row[4]),
                        value=str(row[5]) if row[5] is not None else None,
                        reference=reference,
                        proficiency_kind=(str(row[8]) if row[8] is not None else None),
                        quantity=int(row[9]) if row[9] is not None else None,
                        unit=str(row[10]) if row[10] is not None else None,
                        source_rule=str(row[11]),
                        unresolved=bool(row[12]),
                    ),
                    owner,
                    str(row[1]),
                    str(row[2]) if row[2] is not None else None,
                    str(row[3]) if row[3] is not None else None,
                )
            )
        return tuple(result)

    def _apply_grant(
        self,
        character_id: str,
        grant_row: BuilderGrant,
        provenance: DecisionProvenance,
        provenance_kind: str,
    ) -> None:
        grant = grant_row.grant
        owner = grant_row.owner
        source_label = f"{owner.name}"
        reference = self._reference_from_rule(owner.dataset_id, grant.reference)
        if grant.kind is GrantKind.EQUIPMENT:
            if reference is not None and grant.reference is not None and grant.reference.resolved:
                self.characters.add_equipment(
                    character_id,
                    item_reference=reference,
                    quantity=grant.quantity or 1,
                    provenance_kind=provenance_kind,
                    provenance_label=source_label,
                    provenance=replace(
                        provenance,
                        choice_key=grant_row.choice_key or provenance.choice_key,
                        option_key=grant_row.option_key or provenance.option_key,
                        source_rule=grant.source_rule,
                    ),
                    allow_unresolved=True,
                )
            else:
                label = grant.value or (
                    grant.reference.identity if grant.reference else "Unresolved item"
                )
                self.characters.add_equipment(
                    character_id,
                    unresolved_selection=label,
                    quantity=grant.quantity or 1,
                    provenance_kind=provenance_kind,
                    provenance_label=source_label,
                    provenance=replace(
                        provenance,
                        choice_key=grant_row.choice_key or provenance.choice_key,
                        option_key=grant_row.option_key or provenance.option_key,
                        source_rule=grant.source_rule,
                    ),
                    allow_unresolved=True,
                )
        elif grant.kind is GrantKind.CURRENCY:
            currency_amount = grant.quantity or _integer_or_none(grant.value)
            currency_key = grant.unit.casefold() if grant.unit else None
            if currency_amount and currency_key in {"cp", "sp", "ep", "gp", "pp"}:
                self.characters.add_currency(
                    character_id,
                    currency_key,
                    currency_amount,
                    provenance_kind=provenance_kind,
                    provenance_label=source_label,
                    provenance=replace(
                        provenance,
                        choice_key=grant_row.choice_key or provenance.choice_key,
                        option_key=grant_row.option_key or provenance.option_key,
                        source_rule=grant.source_rule,
                    ),
                    source_rule=grant.source_rule,
                )
        elif grant.kind is GrantKind.FEAT and reference is not None:
            feat = self.characters.add_feat(
                character_id,
                reference,
                provenance_kind=provenance_kind,
                provenance_label=source_label,
                provenance=replace(
                    provenance,
                    choice_key=grant_row.choice_key or provenance.choice_key,
                    option_key=grant_row.option_key or provenance.option_key,
                    source_rule=grant.source_rule,
                ),
                allow_unresolved=True,
            )
            feat_owner = self.get_owner("feat", feat.feat_reference.identity)
            if feat_owner is not None:
                self._apply_spell_access(character_id, feat_owner, "feat")
        elif grant.kind in {GrantKind.SPELL, GrantKind.CANTRIP} and reference is not None:
            source_class = self.characters.get_character(character_id).starting_class
            self.characters.add_spell(
                character_id,
                reference,
                acquisition="cantrip" if grant.kind is GrantKind.CANTRIP else "always_prepared",
                source_class=source_class,
                character_level=1,
                class_level=1 if source_class else None,
                provenance=replace(
                    provenance,
                    choice_key=grant_row.choice_key or provenance.choice_key,
                    option_key=grant_row.option_key or provenance.option_key,
                    source_rule=grant.source_rule,
                ),
                allow_unresolved=True,
            )

    def _apply_choice_option_effects(
        self, character_id: str, choice: BuilderChoice, option: BuilderChoiceOption
    ) -> None:
        if option.option_key is None:
            # Criteria selections are stored as references or values; only explicit source options
            # carry option-grant bundles in the normalized catalog.
            if option.reference is not None and option.reference.kind in {"spell", "feat"}:
                self._apply_selected_reference(character_id, choice, option)
            elif (
                option.reference is not None
                and option.reference.kind == "item"
                and choice.definition.kind in {ChoiceKind.EQUIPMENT, ChoiceKind.EQUIPMENT_PACKAGE}
            ):
                self._apply_selected_reference(character_id, choice, option)
            return
        with self.database.connection() as db:
            grants = self._choice_option_grants(
                db,
                choice.owner,
                str(choice.definition.choice_key),
                option.option_key,
            )
        provenance = DecisionProvenance(
            owner_dataset_id=choice.owner.dataset_id,
            owner_type=choice.owner.owner_type,
            owner_key=choice.owner.owner_key,
            choice_key=str(choice.definition.choice_key),
            option_key=option.option_key,
            character_level=choice.character_level,
            class_identity=choice.class_identity,
            class_level=choice.class_level,
            source_rule=choice.definition.source_rule,
        )
        for grant in grants:
            self._apply_grant(
                character_id,
                BuilderGrant(
                    grant,
                    choice.owner,
                    "choice_option",
                    str(choice.definition.choice_key),
                    option.option_key,
                ),
                provenance,
                "background" if choice.owner.owner_type == "background" else "class_level",
            )
        if option.reference is not None and option.reference.kind in {"spell", "feat"}:
            self._apply_selected_reference(character_id, choice, option)
        elif (
            option.reference is not None
            and option.reference.kind == "item"
            and choice.definition.kind in {ChoiceKind.EQUIPMENT, ChoiceKind.EQUIPMENT_PACKAGE}
        ):
            self._apply_selected_reference(character_id, choice, option)

    def _apply_selected_reference(
        self, character_id: str, choice: BuilderChoice, option: BuilderChoiceOption
    ) -> None:
        if option.reference is None:
            return
        provenance = DecisionProvenance(
            owner_dataset_id=choice.owner.dataset_id,
            owner_type=choice.owner.owner_type,
            owner_key=choice.owner.owner_key,
            choice_key=str(choice.definition.choice_key),
            option_key=option.option_key,
            character_level=choice.character_level,
            class_identity=choice.class_identity,
            class_level=choice.class_level,
            source_rule=choice.definition.source_rule,
        )
        if option.reference.kind == "feat":
            feat = self.characters.add_feat(
                character_id,
                option.reference,
                provenance_kind="class_level"
                if choice.owner.owner_type == "class"
                else "background",
                provenance_label=choice.owner.name,
                provenance=provenance,
                allow_unresolved=not option.resolved,
            )
            feat_owner = self.get_owner("feat", feat.feat_reference.identity)
            if feat_owner is not None:
                self._apply_spell_access(character_id, feat_owner, "feat")
        elif option.reference.kind == "spell":
            self.characters.add_spell(
                character_id,
                option.reference,
                acquisition=(
                    "cantrip" if choice.definition.kind is ChoiceKind.CANTRIP else "known"
                ),
                source_class=(
                    self.characters.get_character(character_id).starting_class
                    if choice.owner.owner_type == "class"
                    else None
                ),
                character_level=choice.character_level,
                class_level=choice.class_level,
                provenance=provenance,
                allow_unresolved=not option.resolved,
            )
        elif option.reference.kind == "item":
            self.characters.add_equipment(
                character_id,
                item_reference=option.reference,
                quantity=1,
                provenance_kind=(
                    "background" if choice.owner.owner_type == "background" else "class_level"
                ),
                provenance_label=choice.owner.name,
                provenance=provenance,
                allow_unresolved=not option.resolved,
            )

    def _apply_background_feat(self, character_id: str, owner: BuilderOwner) -> None:
        value = owner.metadata.get("origin_feat")
        if not isinstance(value, dict) or not value.get("identity"):
            return
        identity = str(value["identity"])
        reference = self._reference_for_identity(str(value.get("kind", "feat")), identity)
        if reference is None:
            return
        existing = next(
            (
                feat
                for feat in self.characters.get_character(character_id).feats
                if feat.owner_dataset_id == owner.dataset_id
                and feat.owner_type == "background"
                and feat.owner_key == owner.owner_key
                and feat.feat_reference.identity == reference.identity
            ),
            None,
        )
        if existing:
            added = existing
        else:
            added = self.characters.add_feat(
                character_id,
                reference,
                provenance_kind="background",
                provenance_label=owner.name,
                provenance=DecisionProvenance(
                    owner_dataset_id=owner.dataset_id,
                    owner_type="background",
                    owner_key=owner.owner_key,
                    character_level=1,
                    source_rule=f"background/{owner.owner_key}.origin_feat",
                ),
                allow_unresolved=not bool(value.get("resolved", True)),
            )
        feat_owner = self.get_owner("feat", added.feat_reference.identity)
        if feat_owner is not None:
            if not existing:
                self._apply_spell_access(character_id, feat_owner, "feat")
            variant = owner.metadata.get("origin_feat_variant")
            if isinstance(variant, str) and variant.strip():
                character = self.characters.get_character(character_id)
                for choice in self._owner_choices(feat_owner, {"spellcasting"}, character):
                    matching = [
                        option
                        for option in choice.definition.options
                        if option.label.casefold().split(maxsplit=1)[0] == variant.casefold()
                    ]
                    if len(matching) == 1:
                        self.select_choice_option(
                            character_id,
                            choice,
                            BuilderChoiceOption(
                                str(matching[0].option_key),
                                matching[0].label,
                                str(matching[0].value) if matching[0].value is not None else None,
                                self._reference_from_rule(
                                    feat_owner.dataset_id, matching[0].reference
                                ),
                                matching[0].resolved,
                            ),
                        )

    def _apply_spell_access(self, character_id: str, owner: BuilderOwner, owner_type: str) -> None:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT access_type,level_scope,source_rule,payload_json FROM "
                "character_builder_spell_access WHERE dataset_id=? "
                "AND owner_type=? AND owner_key=? "
                "AND class_level<=1 ORDER BY access_key",
                (owner.dataset_id, owner_type, owner.owner_key),
            ).fetchall()
        for row in rows:
            if str(row[0]) == "expanded":
                continue
            try:
                access = json.loads(str(row[3]))
            except json.JSONDecodeError:
                continue
            for value in access.get("spells", []):
                if not isinstance(value, dict) or not value.get("identity"):
                    continue
                reference = self._reference_for_identity(
                    str(value.get("kind", "spell")), str(value["identity"])
                )
                if reference is None:
                    continue
                acquisition = "always_prepared" if row[0] == "prepared" else str(row[0])
                source_class = (
                    self.characters.get_character(character_id).starting_class
                    if owner_type == "class"
                    else None
                )
                provenance = DecisionProvenance(
                    owner_dataset_id=owner.dataset_id,
                    owner_type=owner_type,
                    owner_key=owner.owner_key,
                    character_level=1,
                    class_identity=source_class.identity if source_class else None,
                    class_level=1 if source_class else None,
                    source_rule=str(row[2]),
                )
                self.characters.add_spell(
                    character_id,
                    reference,
                    acquisition=acquisition
                    if acquisition in {"known", "prepared", "spellbook", "innate"}
                    else "innate",
                    source_class=source_class,
                    character_level=1,
                    class_level=1 if source_class else None,
                    provenance=provenance,
                    allow_unresolved=not bool(value.get("resolved", True)),
                )

    def _reference_from_rule(
        self, dataset_id: str, value: RuleReference | None
    ) -> PublishedReference | None:
        if value is None:
            return None
        identity = value.identity
        if ":" not in identity:
            identity = f"{dataset_id}:{identity}"
        return self._reference_for_identity(str(value.kind), identity)

    def _reference_for_identity(self, kind: str, identity: str) -> PublishedReference | None:
        dataset_id, local_key = _split_direct_identity(identity)
        if kind == "subclass":
            return None
        with self.database.connection() as db:
            row = db.execute(
                "SELECT e.name,src.edition FROM entries e JOIN sources src ON src.id=e.source_id "
                "WHERE e.dataset_id=? AND e.local_key=? AND e.kind=?",
                (dataset_id, local_key, kind),
            ).fetchone()
            if row is None and kind in {"species", "background"}:
                row = db.execute(
                    "SELECT b.name,b.edition FROM character_builder_owners b "
                    "WHERE b.dataset_id=? AND b.owner_key=? AND b.owner_type=?",
                    (dataset_id, local_key, kind),
                ).fetchone()
        if row is None:
            return PublishedReference(kind, identity, local_key.rsplit("/", 1)[-1], "2024", True)
        return PublishedReference(kind, identity, str(row[0]), str(row[1]))

    def _choice_context(
        self, owner: BuilderOwner, character: Character
    ) -> tuple[int, str | None, int | None]:
        if owner.owner_type in {"class", "subclass"} and character.starting_class is not None:
            return 1, character.starting_class.identity, 1
        if owner.owner_type == "feat":
            feat = next(
                (row for row in character.feats if row.feat_reference.identity == owner.identity),
                None,
            )
            if feat is not None:
                return feat.character_level or 1, feat.class_identity, feat.class_level
        return 1, None, None

    def _choice_is_active(self, character: Character, choice: BuilderChoice) -> bool:
        definition = choice.definition
        if definition.depends_on_choice is not None:
            parent = next(
                (
                    row
                    for row in character.choices
                    if row.owner_dataset_id == choice.owner.dataset_id
                    and row.owner_type == choice.owner.owner_type
                    and row.owner_key == choice.owner.owner_key
                    and row.choice_key == str(definition.depends_on_choice)
                    and row.selected_option_key == str(definition.depends_on_option)
                    and row.character_level == choice.character_level
                    and row.class_identity == choice.class_identity
                    and row.class_level == choice.class_level
                ),
                None,
            )
            if parent is None:
                return False
        if definition.requirement is not None:
            result = evaluate_requirement(
                definition.requirement, self._requirement_context(character)
            )
            if result.status is RequirementStatus.UNSATISFIED:
                return False
        return True

    def _requirement_context(self, character: Character) -> CharacterRuleContext:
        scores = {Ability(key): value for key, value in character.base_ability_scores}
        for modification in character.ability_modifications:
            ability = Ability(modification.ability)
            scores[ability] = scores.get(ability, 0) + modification.amount
        class_levels = {
            _split_direct_identity(identity)[1]: level
            for identity, level in character.class_levels.items()
        }
        refs: set[str] = set()
        for choice in character.choices:
            if choice.selected_value:
                refs.add(choice.selected_value)
        return CharacterRuleContext(
            edition=character.edition,
            total_level=character.total_level,
            class_levels=class_levels,
            ability_scores=scores,
            proficiencies=refs,
            feats={
                _split_direct_identity(feat.feat_reference.identity)[1] for feat in character.feats
            },
        )

    def _selected_for_choice(self, character: Character, choice: BuilderChoice):
        return tuple(
            row
            for row in character.choices
            if row.owner_dataset_id == choice.owner.dataset_id
            and row.owner_type == choice.owner.owner_type
            and row.owner_key == choice.owner.owner_key
            and row.choice_key == str(choice.definition.choice_key)
            and row.character_level == choice.character_level
            and row.class_identity == choice.class_identity
            and row.class_level == choice.class_level
        )

    def _refresh_ability_modifications(self, character_id: str) -> None:
        character = self.characters.get_character(character_id)
        self.characters.set_ability_state(
            character_id,
            dict(character.base_ability_scores),
            self._ability_changes(character),
            method=character.ability_score_method,
        )

    def _ability_changes(self, character: Character) -> tuple[AbilityChange, ...]:
        changes: list[AbilityChange] = []
        for resolution in character.choices:
            if resolution.selected_option_key is None:
                continue
            owner = self.get_owner(
                resolution.owner_type, f"{resolution.owner_dataset_id}:{resolution.owner_key}"
            )
            if owner is None:
                continue
            choices = self._owner_choices(
                owner,
                {"background", "ability_score", "spellcasting", "starting_class"},
                character,
            )
            definition = next(
                (
                    item
                    for item in choices
                    if str(item.definition.choice_key) == resolution.choice_key
                ),
                None,
            )
            if definition is None:
                continue
            option = next(
                (
                    item
                    for item in definition.definition.options
                    if str(item.option_key) == resolution.selected_option_key
                ),
                None,
            )
            if option is None:
                continue
            for increase in option.ability_increases:
                changes.append(
                    AbilityChange(
                        str(increase.ability),
                        increase.amount,
                        "background" if owner.owner_type == "background" else "feat",
                        owner.name,
                        DecisionProvenance(
                            owner_dataset_id=owner.dataset_id,
                            owner_type=owner.owner_type,
                            owner_key=owner.owner_key,
                            choice_key=resolution.choice_key,
                            option_key=resolution.selected_option_key,
                            character_level=resolution.character_level,
                            class_identity=resolution.class_identity,
                            class_level=resolution.class_level,
                            source_rule=definition.definition.source_rule,
                        ),
                    )
                )
        return tuple(changes)

    def _has_required_choices(self, character_id: str, step: str) -> bool:
        character = self.characters.get_character(character_id)
        return any(
            len(self._selected_for_choice(character, choice)) < choice.definition.count
            for choice in self.choices_for_step(character_id, step)
        )

    def _scores_complete(self, character: Character) -> bool:
        return {key for key, _score in character.base_ability_scores} == set(ABILITIES)

    def _spells_incomplete(self, character_id: str) -> bool:
        character = self.characters.get_character(character_id)
        return any(
            self._spell_group_count(character, group) < group.count
            for group in self.spell_groups(character_id)
        )

    def _spell_group_count(self, character: Character, group: SpellChoiceGroup) -> int:
        return sum(1 for row in character.spells if _spell_row_matches_group(row, group))

    def _class_has_spellbook(self, dataset_id: str, class_key: str) -> bool:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT acquisition FROM class_spellcasting WHERE dataset_id=? "
                "AND owner_type='class' AND owner_key=?",
                (dataset_id, class_key),
            ).fetchone()
        return row is not None and str(row[0]) == "spellbook"


def point_buy_cost(scores: dict[str, int]) -> int:
    unknown = set(scores) - set(ABILITIES)
    if unknown:
        raise ValueError(f"Unknown ability score: {sorted(unknown)[0]}.")
    if any(not isinstance(value, int) or value not in POINT_BUY_COSTS for value in scores.values()):
        raise ValueError("Point Buy scores must be between 8 and 15.")
    return sum(POINT_BUY_COSTS[value] for value in scores.values())


def validate_ability_scores(method: str, scores: dict[str, int], *, require_complete: bool) -> None:
    if method not in {"standard_array", "point_buy", "manual"}:
        raise ValueError("Choose Standard Array, Point Buy, or Manual Entry.")
    unknown = set(scores) - set(ABILITIES)
    if unknown:
        raise ValueError(f"Unknown ability score: {sorted(unknown)[0]}.")
    if any(not isinstance(score, int) for score in scores.values()):
        raise ValueError("Ability scores must be whole numbers.")
    if require_complete and set(scores) != set(ABILITIES):
        raise ValueError("Assign a score to all six abilities.")
    if method == "standard_array":
        if any(value not in STANDARD_ARRAY for value in scores.values()):
            raise ValueError("Standard Array uses 15, 14, 13, 12, 10, and 8.")
        if len(set(scores.values())) != len(scores):
            raise ValueError("Each Standard Array score can be assigned only once.")
        if require_complete and set(scores.values()) != set(STANDARD_ARRAY):
            raise ValueError("Use each Standard Array score once.")
    elif method == "point_buy":
        if any(value not in POINT_BUY_COSTS for value in scores.values()):
            raise ValueError("Point Buy scores must be between 8 and 15.")
        spent = sum(POINT_BUY_COSTS[value] for value in scores.values())
        if spent > POINT_BUY_BUDGET:
            raise ValueError("Point Buy cannot spend more than 27 points.")
        if require_complete and spent != POINT_BUY_BUDGET:
            raise ValueError("Spend exactly 27 points before continuing.")
    elif any(not 3 <= value <= 18 for value in scores.values()):
        raise ValueError("Manual scores must be between 3 and 18.")


def _validate_page(offset: int, limit: int, maximum: int = 200) -> None:
    if offset < 0:
        raise ValueError("page offset must not be negative")
    if not 1 <= limit <= maximum:
        raise ValueError(f"page size must be between 1 and {maximum}")


def _split_direct_identity(identity: str) -> tuple[str, str]:
    dataset_id, separator, key = identity.partition(":")
    if not separator or not dataset_id or not key:
        raise ValueError(f"invalid published reference identity: {identity}")
    return dataset_id, key


def _class_local_key(identity: str, dataset_id: str) -> str:
    if ":" not in identity:
        return identity
    referenced_dataset, local_key = _split_direct_identity(identity)
    return local_key if referenced_dataset == dataset_id else ""


def _owner_subtitle(owner_type: str, metadata: dict[str, object]) -> str:
    if owner_type == "class":
        values = metadata.get("primary_ability_options", [])
        primary = " / ".join(
            "+".join(str(item).upper() for item in group)
            for group in values
            if isinstance(group, list)
        )
        return f"Primary ability: {primary}" if primary else "2024 Class"
    if owner_type == "background":
        feat = metadata.get("origin_feat")
        if isinstance(feat, dict):
            return f"Origin feat: {str(feat.get('identity', '')).rsplit('/', 1)[-1]}"
    return "2024 Published"


def _integer_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _spell_level_label(level: int) -> str:
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(level, "th")
    return "Cantrip" if level == 0 else f"{level}{suffix}-level"


def _ability_name(ability: str) -> str:
    return {
        "str": "Strength",
        "dex": "Dexterity",
        "con": "Constitution",
        "int": "Intelligence",
        "wis": "Wisdom",
        "cha": "Charisma",
    }[ability]


def _normalize_option_type(value: str) -> str:
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    return {
        "instrumentmusical": "musicalinstrument",
        "setgaming": "gamingset",
        "gamingset": "gamingset",
    }.get(normalized, normalized)


def _local_reference(value: str) -> str:
    return value.split(":", 1)[1] if ":" in value else value


def _same_selection(resolution, option: BuilderChoiceOption) -> bool:
    if option.option_key is not None:
        return resolution.selected_option_key == option.option_key
    if option.reference is not None:
        return (
            resolution.selected_reference is not None
            and resolution.selected_reference.identity == option.reference.identity
        )
    return resolution.selected_value == option.value


def _spell_row_matches_group(row, group: SpellChoiceGroup) -> bool:
    return (
        row.owner_dataset_id == group.owner_dataset_id
        and row.owner_type == group.owner_type
        and row.owner_key == group.owner_key
        and row.choice_key == group.choice_key
        and row.option_key == group.option_key
        and row.acquisition == group.acquisition
        and row.source_rule == group.source_rule
    )


def _choice_missing_message(choice: BuilderChoice, remaining: int) -> str:
    title = choice.owner.name
    kind = choice.definition.kind.value.replace("_", " ")
    quantity = "1 more" if remaining == 1 else f"{remaining} more"
    if choice.scope == "spellcasting_ability":
        return f"Choose a spellcasting ability for {title}."
    if choice.definition.kind is ChoiceKind.ABILITY_SCORE:
        return f"Background ability increases are incomplete for {title}."
    if choice.definition.kind in {ChoiceKind.SPELL, ChoiceKind.CANTRIP}:
        label = "cantrip" if choice.definition.kind is ChoiceKind.CANTRIP else "spell"
        return f"Select {quantity} {label}{'' if remaining == 1 else 's'} for {title}."
    if choice.definition.kind in {ChoiceKind.EQUIPMENT, ChoiceKind.EQUIPMENT_PACKAGE}:
        return f"Make {quantity} starting equipment choice for {title}."
    return f"Choose {quantity} {title} {kind} selection{'' if remaining == 1 else 's'}."


def _issue(code: str, message: str, severity: str = "error") -> CharacterValidationIssue:
    return CharacterValidationIssue(code, severity, message)
