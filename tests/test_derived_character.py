from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from dndref.character_creation import CharacterCreationService
from dndref.characters import CharacterService
from dndref.derived_character import DerivedCharacterService, _caster_contribution, ability_modifier
from dndref.importer import import_dataset, load_dataset
from dndref.models import AbilityChange, PublishedReference
from dndref.performance import PerformanceProfiler
from dndref.storage.database import Database

PRODUCTION = Path("src/dndref/datasets/official-5etools-2024")
PRODUCTION_DATASET_ID = "official-5etools-2024"
BASE_SCORES = {"str": 14, "dex": 14, "con": 14, "int": 14, "wis": 14, "cha": 14}


@pytest.fixture(scope="module")
def production_database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    database = Database(tmp_path_factory.mktemp("derived-rules") / "rules.sqlite3")
    import_dataset(database, load_dataset(PRODUCTION))
    return database


def _owner(database: Database, owner_type: str, name: str) -> PublishedReference:
    with database.connection() as db:
        row = db.execute(
            "SELECT dataset_id,owner_key,name,edition FROM character_builder_owners "
            "WHERE dataset_id=? AND owner_type=? AND name=? ORDER BY owner_key LIMIT 1",
            (PRODUCTION_DATASET_ID, owner_type, name),
        ).fetchone()
    assert row is not None, f"missing production {owner_type} {name}"
    return PublishedReference(owner_type, f"{row[0]}:{row[1]}", str(row[2]), str(row[3]))


def _entry(database: Database, kind: str, name: str) -> PublishedReference:
    with database.connection() as db:
        row = db.execute(
            "SELECT e.dataset_id,e.local_key,e.name,s.edition FROM entries e "
            "JOIN sources s ON s.id=e.source_id WHERE e.dataset_id=? AND e.kind=? AND e.name=? "
            "ORDER BY e.local_key LIMIT 1",
            (PRODUCTION_DATASET_ID, kind, name),
        ).fetchone()
    assert row is not None, f"missing production {kind} {name}"
    return PublishedReference(kind, f"{row[0]}:{row[1]}", str(row[2]), str(row[3]))


def _new_character(
    database: Database,
    class_history: tuple[str, ...] = (),
    *,
    scores: dict[str, int] | None = None,
    modifications: tuple[AbilityChange, ...] = (),
) -> tuple[str, CharacterService]:
    service = CharacterService(database)
    character = service.create_character("Derived Test")
    for class_name in class_history:
        service.add_class_level(character.character_id, _owner(database, "class", class_name))
    service.set_ability_state(
        character.character_id,
        scores or BASE_SCORES,
        modifications,
        method="manual",
    )
    return character.character_id, service


def _start_with_creation(
    database: Database,
    class_name: str,
    *,
    background: str | None = None,
) -> tuple[str, CharacterCreationService, CharacterService]:
    characters = CharacterService(database)
    creation = CharacterCreationService(database, characters)
    character = creation.create_draft()
    if background is not None:
        creation.choose_background(
            character.character_id, _owner(database, "background", background).identity
        )
    creation.choose_starting_class(
        character.character_id, _owner(database, "class", class_name).identity
    )
    characters.set_ability_state(character.character_id, BASE_SCORES, method="manual")
    return character.character_id, creation, characters


def _subclass(database: Database, class_name: str, subclass_name: str) -> PublishedReference:
    class_reference = _owner(database, "class", class_name)
    dataset_id, class_key = class_reference.identity.split(":", 1)
    with database.connection() as db:
        row = db.execute(
            "SELECT s.subclass_key,s.name,src.edition FROM subclasses s "
            "JOIN classes c ON c.entry_id=s.class_id JOIN entries e ON e.id=c.entry_id "
            "JOIN sources src ON src.id=e.source_id WHERE s.dataset_id=? AND e.local_key=? "
            "AND s.name=? ORDER BY s.subclass_key LIMIT 1",
            (dataset_id, class_key, subclass_name),
        ).fetchone()
    assert row is not None, f"missing production subclass {subclass_name}"
    return PublishedReference(
        "subclass",
        f"{dataset_id}:subclass:{class_key}:{row[0]}",
        str(row[1]),
        str(row[2]),
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [(1, -5), (3, -4), (9, -1), (10, 0), (11, 0), (12, 1), (30, 10)],
)
def test_ability_modifier_boundaries(score: int, expected: int) -> None:
    assert ability_modifier(score) == expected


def test_ability_adjustments_and_total_level_proficiency_are_derived(
    production_database: Database,
) -> None:
    character_id, characters = _new_character(
        production_database,
        ("Fighter", "Fighter", "Wizard", "Wizard", "Cleric"),
        scores={"str": 15, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8},
        modifications=(
            AbilityChange("wis", 2, "background", "Ability Score Increase"),
            AbilityChange("int", 1, "feat", "Keen Mind"),
        ),
    )
    saved_before = characters.get_character(character_id).to_dict()

    result = DerivedCharacterService(production_database, characters).derive_character(character_id)
    by_ability = {row.ability: row for row in result.ability_scores}

    assert (by_ability["str"].base, by_ability["str"].final, by_ability["str"].modifier) == (
        15,
        15,
        2,
    )
    assert (by_ability["wis"].base, by_ability["wis"].final, by_ability["wis"].modifier) == (
        10,
        12,
        1,
    )
    assert by_ability["wis"].adjustments[0].label == "Ability Score Increase"
    assert (result.total_level, result.proficiency_bonus.value) == (5, 3)
    assert characters.get_character(character_id).to_dict() == saved_before


def test_starting_class_saves_and_overlapping_skill_grants(
    production_database: Database,
) -> None:
    character_id, creation, characters = _start_with_creation(
        production_database, "Fighter", background="Soldier"
    )
    fighter = _owner(production_database, "class", "Fighter")
    skill_choice = next(
        choice
        for choice in creation.choices_for_step(character_id, "class_choices")
        if choice.definition.kind.value == "proficiency"
        and "starting-skills" in str(choice.definition.choice_key)
    )
    athletics = next(
        option
        for option in creation.choice_options(character_id, skill_choice, limit=50)
        if option.label == "Athletics"
    )
    creation.select_choice_option(character_id, skill_choice, athletics)
    wizard = _owner(production_database, "class", "Wizard")
    characters.add_class_level(character_id, wizard)

    with production_database.connection() as db, db:
        for number in (1, 2):
            db.execute(
                "INSERT INTO character_rule_grants "
                "(dataset_id,owner_type,owner_key,grant_key,scope,grant_type,value,"
                "proficiency_kind,source_rule,unresolved) "
                "VALUES (?,'class',?,?,'starting_class','expertise','skill:athletics',"
                "'skill',?,0)",
                (
                    PRODUCTION_DATASET_ID,
                    fighter.identity.split(":", 1)[1],
                    f"test-expertise-{number}",
                    f"test structured expertise {number}",
                ),
            )
    try:
        result = DerivedCharacterService(production_database, characters).derive_character(
            character_id
        )
    finally:
        with production_database.connection() as db, db:
            db.execute(
                "DELETE FROM character_rule_grants WHERE dataset_id=? AND owner_type='class' "
                "AND owner_key=? AND grant_key LIKE 'test-expertise-%'",
                (PRODUCTION_DATASET_ID, fighter.identity.split(":", 1)[1]),
            )

    saves = {row.ability: row for row in result.saving_throws}
    skills = {row.key: row for row in result.skills}
    athletics_result = skills["athletics"]
    assert saves["str"].proficiency == "proficient"
    assert saves["con"].proficiency == "proficient"
    assert saves["int"].proficiency == saves["wis"].proficiency == "none"
    assert len(result.skills) == 18
    assert athletics_result.proficiency == "expertise"
    assert athletics_result.proficiency_bonus == 4
    assert athletics_result.result.value == 6
    athletics_proficiency = next(
        row for row in result.proficiencies if (row.kind, row.key) == ("skill", "athletics")
    )
    assert len(athletics_proficiency.sources) >= 2


def test_initiative_passive_perception_and_species_speed_use_structured_inputs(
    production_database: Database,
) -> None:
    character_id, creation, _characters = _start_with_creation(production_database, "Fighter")
    dwarf = _owner(production_database, "species", "Dwarf")
    creation.choose_species(character_id, dwarf.identity)

    with production_database.connection() as db:
        row = db.execute(
            "SELECT metadata_json FROM character_builder_owners WHERE dataset_id=? "
            "AND owner_type='species' AND owner_key=?",
            (PRODUCTION_DATASET_ID, dwarf.identity.split(":", 1)[1]),
        ).fetchone()
    assert row is not None
    movement = json.loads(str(row[0]))["movement"]
    result = DerivedCharacterService(production_database).derive_character(character_id)

    assert result.initiative.value == 2
    assert result.passive_perception.value == 12
    assert [(row.kind, row.feet) for row in result.speed] == [
        (entry["kind"], entry["feet"]) for entry in movement
    ]

    monk_id, monk_creation, monk_characters = _start_with_creation(production_database, "Monk")
    monk_characters.add_class_level(monk_id, _owner(production_database, "class", "Monk"))
    monk_creation.choose_species(monk_id, dwarf.identity)
    monk = DerivedCharacterService(production_database, monk_characters).derive_character(monk_id)
    assert monk.speed[0].feet == movement[0]["feet"]
    assert monk.speed[0].state == "partial"
    assert any(issue.category == "unresolved_speed" for issue in monk.issues)


def test_armor_class_handles_unarmored_armor_shield_conflict_and_stale_reference(
    production_database: Database,
) -> None:
    service = DerivedCharacterService(production_database)
    cases = (
        ((), 14),
        (("Leather Armor",), 15),
        (("Scale Mail",), 16),
        (("Plate Armor",), 18),
        (("Shield",), 16),
        (("Scale Mail", "Shield"), 18),
    )
    for item_names, expected in cases:
        character_id, characters = _new_character(
            production_database,
            ("Fighter",),
            scores={"str": 10, "dex": 18, "con": 14, "int": 10, "wis": 10, "cha": 10},
        )
        for item_name in item_names:
            characters.add_equipment(
                character_id,
                item_reference=_entry(production_database, "item", item_name),
                equipped=True,
            )
        derived = service.derive_character(character_id)
        assert derived.armor_class.value == expected, item_names

    conflict_id, conflict_service = _new_character(production_database, ("Fighter",))
    for item_name in ("Leather Armor", "Plate Armor"):
        conflict_service.add_equipment(
            conflict_id,
            item_reference=_entry(production_database, "item", item_name),
            equipped=True,
        )
    conflict = service.derive_character(conflict_id)
    assert conflict.armor_class.state == "unresolved"
    assert any(issue.category == "conflicting_equipment" for issue in conflict.issues)

    missing_id, missing_service = _new_character(production_database, ("Fighter",))
    missing = PublishedReference(
        "item", f"{PRODUCTION_DATASET_ID}:item/xphb/removed-plate-armor", "Plate Armor", "2024"
    )
    missing_service.add_equipment(
        missing_id, item_reference=missing, equipped=True, allow_unresolved=True
    )
    missing_result = service.derive_character(missing_id)
    assert missing_result.armor_class.state == "unresolved"
    assert any(issue.category == "stale_reference" for issue in missing_result.issues)

    monk_id, _ = _new_character(
        production_database,
        ("Monk",),
        scores={"str": 10, "dex": 16, "con": 12, "int": 12, "wis": 14, "cha": 10},
    )
    monk = service.derive_character(monk_id)
    assert monk.armor_class.state == "unresolved"
    assert any(issue.category == "unresolved_armor_class" for issue in monk.issues)


def test_hp_uses_ordered_multiclass_history_and_recalculates_constitution(
    production_database: Database,
) -> None:
    character_id, characters = _new_character(
        production_database,
        ("Fighter", "Wizard", "Cleric"),
        scores={"str": 10, "dex": 10, "con": 14, "int": 14, "wis": 14, "cha": 10},
    )
    for total_level, choice, amount in ((2, "fixed_average", 4), (3, "rolled", 5)):
        characters.set_hp_choice(character_id, total_level, choice, amount)

    service = DerivedCharacterService(production_database, characters)
    original = service.derive_character(character_id)
    assert original.hit_points.maximum == 25
    assert [(row.die_size, row.count) for row in original.hit_dice] == [(6, 1), (8, 1), (10, 1)]
    assert [row.class_reference.name for row in original.hit_points.levels] == [
        "Fighter",
        "Wizard",
        "Cleric",
    ]

    characters.set_ability_state(
        character_id,
        {"str": 10, "dex": 10, "con": 10, "int": 14, "wis": 14, "cha": 10},
    )
    recalculated = service.derive_character(character_id)
    assert recalculated.hit_points.maximum == 19


def test_level_20_hit_dice_and_hit_points(production_database: Database) -> None:
    character_id, characters = _new_character(
        production_database,
        ("Fighter",) * 20,
        scores={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
    )
    for total_level in range(2, 21):
        characters.set_hp_choice(character_id, total_level, "fixed_average", 6)

    result = DerivedCharacterService(production_database, characters).derive_character(character_id)
    assert result.proficiency_bonus.value == 6
    assert [(pool.die_size, pool.count) for pool in result.hit_dice] == [(10, 20)]
    assert result.hit_points.maximum == 124


def test_attacks_resolve_finesse_weapon_labels_and_keep_missing_type_unresolved(
    production_database: Database,
) -> None:
    character_id, characters = _new_character(
        production_database,
        ("Fighter",),
        scores={"str": 8, "dex": 16, "con": 12, "int": 12, "wis": 12, "cha": 12},
    )
    for name in ("Dagger", "Rapier", "Longbow"):
        characters.add_equipment(
            character_id, item_reference=_entry(production_database, "item", name)
        )
    with production_database.connection() as db:
        row = db.execute(
            "SELECT e.dataset_id,e.local_key,e.name,s.edition FROM entries e "
            "JOIN character_builder_equipment m ON m.dataset_id=e.dataset_id "
            "AND m.item_key=e.local_key JOIN sources s ON s.id=e.source_id "
            "WHERE e.dataset_id=? AND e.kind='item' AND m.category='weapon' "
            "AND m.attack_type IS NULL AND s.edition='2024' ORDER BY e.local_key LIMIT 1",
            (PRODUCTION_DATASET_ID,),
        ).fetchone()
    assert row is not None
    characters.add_equipment(
        character_id,
        item_reference=PublishedReference("item", f"{row[0]}:{row[1]}", str(row[2]), str(row[3])),
    )
    fighter = _owner(production_database, "class", "Fighter")
    vex = _entry(production_database, "rule", "Vex")
    owner_key = fighter.identity.split(":", 1)[1]
    with production_database.connection() as db, db:
        db.execute(
            "INSERT INTO character_rule_grants "
            "(dataset_id,owner_type,owner_key,grant_key,scope,grant_type,value,"
            "reference_kind,reference_identity,proficiency_kind,source_rule,unresolved) "
            "VALUES (?,'class',?,?,'starting_class','weapon_mastery','Vex','rule',?,"
            "'weapon_mastery','test structured mastery',0)",
            (PRODUCTION_DATASET_ID, owner_key, "test-rapier-mastery", vex.identity),
        )
    try:
        result = DerivedCharacterService(production_database, characters).derive_character(
            character_id
        )
    finally:
        with production_database.connection() as db, db:
            db.execute(
                "DELETE FROM character_rule_grants WHERE dataset_id=? AND owner_type='class' "
                "AND owner_key=? AND grant_key='test-rapier-mastery'",
                (PRODUCTION_DATASET_ID, owner_key),
            )
    attacks = {row.weapon.name: row for row in result.attacks}
    assert attacks["Dagger"].ability == "dex"
    assert attacks["Rapier"].ability == "dex"
    assert attacks["Longbow"].ability == "dex"
    assert all(attacks[name].proficient for name in ("Dagger", "Rapier", "Longbow"))
    assert attacks["Rapier"].attack_bonus == 5
    assert attacks["Rapier"].mastery == ("Vex",)
    unresolved = attacks[str(row[2])]
    assert unresolved.state == "unresolved"
    assert "attack_type" in (unresolved.reason or "")

    nonproficient_id, nonproficient_service = _new_character(
        production_database,
        ("Wizard",),
        scores={"str": 8, "dex": 16, "con": 12, "int": 12, "wis": 12, "cha": 12},
    )
    nonproficient_service.add_equipment(
        nonproficient_id, item_reference=_entry(production_database, "item", "Rapier")
    )
    nonproficient = DerivedCharacterService(
        production_database, nonproficient_service
    ).derive_character(nonproficient_id)
    rapier = nonproficient.attacks[0]
    assert rapier.proficient is False
    assert rapier.attack_bonus == 3


def test_full_caster_multiclass_keeps_profile_access_separate_from_shared_slots(
    production_database: Database,
) -> None:
    character_id, characters = _new_character(
        production_database, ("Wizard",) * 3 + ("Cleric",) * 3
    )
    wizard = _owner(production_database, "class", "Wizard")
    fireball = _entry(production_database, "spell", "Fireball")
    magic_missile = _entry(production_database, "spell", "Magic Missile")
    characters.add_spell(
        character_id,
        fireball,
        acquisition="prepared",
        source_class=wizard,
        character_level=3,
        class_level=3,
    )
    characters.add_spell(
        character_id,
        magic_missile,
        acquisition="known",
        source_class=wizard,
        character_level=3,
        class_level=3,
    )
    saved_before = characters.get_character(character_id).to_dict()
    engine = DerivedCharacterService(production_database, characters)

    result = engine.derive_character(character_id)
    repeated = engine.derive_character(character_id)
    profiles = {row.owner.name: row for row in result.spellcasting_profiles}

    assert result.to_dict() == repeated.to_dict()
    assert characters.get_character(character_id).to_dict() == saved_before
    assert result.effective_caster_level == 6
    assert result.spell_slots_state == "complete"
    assert [(row.spell_level, row.count) for row in result.spell_slots] == [
        (1, 4),
        (2, 3),
        (3, 3),
    ]
    assert profiles["Wizard"].spellcasting_ability == "int"
    assert profiles["Cleric"].spellcasting_ability == "wis"
    assert profiles["Wizard"].spell_save_dc.value == 13
    assert profiles["Cleric"].spell_save_dc.value == 13
    assert profiles["Wizard"].accessible_spell_levels == (1, 2)
    assert {row.spell.name: row.valid for row in profiles["Wizard"].spells} == {
        "Fireball": False,
        "Magic Missile": False,
    }
    fireball_result = next(row for row in profiles["Wizard"].spells if row.spell.name == "Fireball")
    assert (fireball_result.level, fireball_result.school) == (3, "evocation")
    assert len(result.multiclass_validation) == 1
    assert result.multiclass_validation[0].status == "satisfied"


def test_artificer_metadata_contributes_half_rounded_up(
    production_database: Database,
) -> None:
    character_id, characters = _new_character(
        production_database, ("Artificer", "Wizard", "Wizard", "Wizard")
    )
    result = DerivedCharacterService(production_database, characters).derive_character(character_id)
    profiles = {row.class_reference.name: row for row in result.spellcasting_profiles}
    assert _caster_contribution("half_round_up", 1) == 1
    assert result.effective_caster_level == 4
    assert [(row.spell_level, row.count) for row in result.spell_slots] == [(1, 4), (2, 3)]
    assert profiles["Artificer"].spellcasting_ability == "int"
    assert profiles["Artificer"].class_level == 1


def test_single_class_casters_use_individual_slots_and_pact_progression(
    production_database: Database,
) -> None:
    wizard_id, _ = _new_character(production_database, ("Wizard",))
    wizard = DerivedCharacterService(production_database).derive_character(wizard_id)
    assert [(row.spell_level, row.count) for row in wizard.spell_slots] == [(1, 2)]
    assert wizard.effective_caster_level == 1
    assert wizard.spellcasting_profiles[0].owner.name == "Wizard"

    cleric_id, _ = _new_character(production_database, ("Cleric",))
    cleric = DerivedCharacterService(production_database).derive_character(cleric_id)
    assert [(row.spell_level, row.count) for row in cleric.spell_slots] == [(1, 2)]
    assert cleric.spellcasting_profiles[0].spellcasting_ability == "wis"

    warlock_id, _ = _new_character(production_database, ("Warlock",))
    warlock = DerivedCharacterService(production_database).derive_character(warlock_id)
    assert warlock.spell_slots == ()
    assert [(row.slot_count, row.slot_level) for row in warlock.pact_magic] == [(1, 1)]
    assert warlock.spellcasting_profiles[0].owner.name == "Warlock"


def test_pact_magic_stays_separate_in_three_class_build(
    production_database: Database,
) -> None:
    history = ("Warlock",) * 5 + ("Wizard",) * 3 + ("Cleric",) * 2
    character_id, characters = _new_character(production_database, history)

    result = DerivedCharacterService(production_database, characters).derive_character(character_id)
    profiles = {row.owner.name: row for row in result.spellcasting_profiles}

    assert result.effective_caster_level == 5
    assert [(row.spell_level, row.count) for row in result.spell_slots] == [
        (1, 4),
        (2, 3),
        (3, 2),
    ]
    assert [(row.class_level, row.slot_count, row.slot_level) for row in result.pact_magic] == [
        (5, 2, 3)
    ]
    assert profiles["Warlock"].spellcasting_ability == "cha"
    assert [(row.spell_level, row.count) for row in profiles["Warlock"].individual_slots] == [
        (3, 2)
    ]
    assert {profile.owner.name for profile in result.spellcasting_profiles} == {
        "Cleric",
        "Warlock",
        "Wizard",
    }


def test_half_caster_and_third_caster_metadata_and_noncasters(
    production_database: Database,
) -> None:
    half_id, _ = _new_character(production_database, ("Wizard",) * 3 + ("Paladin",) * 3)
    half = DerivedCharacterService(production_database).derive_character(half_id)
    assert half.effective_caster_level == 5
    assert [(row.spell_level, row.count) for row in half.spell_slots] == [
        (1, 4),
        (2, 3),
        (3, 2),
    ]

    third_id, third_service = _new_character(
        production_database, ("Fighter",) * 3 + ("Wizard",) * 2
    )
    fighter = _owner(production_database, "class", "Fighter")
    third_service.set_subclass(
        third_id,
        fighter.identity,
        _subclass(production_database, "Fighter", "Eldritch Knight"),
    )
    third = DerivedCharacterService(production_database, third_service).derive_character(third_id)
    assert _caster_contribution("third_round_down", 3) == 1
    assert third.effective_caster_level == 3
    assert [(row.spell_level, row.count) for row in third.spell_slots] == [(1, 4), (2, 2)]
    assert {row.owner_type for row in third.spellcasting_profiles} == {"class", "subclass"}

    noncaster_id, _ = _new_character(production_database, ("Fighter", "Rogue"))
    noncaster = DerivedCharacterService(production_database).derive_character(noncaster_id)
    assert noncaster.spellcasting_profiles == ()
    assert noncaster.spell_slots == ()
    assert noncaster.effective_caster_level == 0


def test_structured_species_spell_grants_keep_always_prepared_semantics(
    production_database: Database,
) -> None:
    characters = CharacterService(production_database)
    creation = CharacterCreationService(production_database, characters)
    character = creation.create_draft()
    creation.choose_starting_class(
        character.character_id, _owner(production_database, "class", "Druid").identity
    )
    characters.set_ability_state(character.character_id, BASE_SCORES)

    result = DerivedCharacterService(production_database, characters).derive_character(
        character.character_id
    )
    profile = next(row for row in result.spellcasting_profiles if row.owner.name == "Druid")
    speak = [row for row in profile.spells if row.spell.name == "Speak with Animals"]

    assert len(speak) == 1
    assert speak[0].acquisition == "always_prepared"
    assert speak[0].valid is True


def test_invalid_multiclass_and_stale_spell_references_remain_saved(
    production_database: Database,
) -> None:
    character_id, characters = _new_character(
        production_database,
        ("Wizard", "Fighter"),
        scores={"str": 8, "dex": 8, "con": 8, "int": 8, "wis": 8, "cha": 8},
    )
    characters.set_character_state(character_id, "complete")
    wizard = _owner(production_database, "class", "Wizard")
    missing_spell = PublishedReference(
        "spell",
        f"{PRODUCTION_DATASET_ID}:spell/xphb/removed-spell",
        "Remembered Name",
        "2024",
    )
    characters.add_spell(
        character_id,
        missing_spell,
        acquisition="prepared",
        source_class=wizard,
        allow_unresolved=True,
    )
    before = characters.get_character(character_id).to_dict()

    result = DerivedCharacterService(production_database, characters).derive_character(character_id)
    profile = next(row for row in result.spellcasting_profiles if row.owner.name == "Wizard")
    missing = next(row for row in profile.spells if row.spell.name == "Remembered Name")

    assert result.multiclass_validation[0].status == "unsatisfied"
    assert missing.valid is None
    assert missing.spell.identity.endswith("removed-spell")
    assert characters.get_character(character_id).to_dict() == before
    assert characters.get_character(character_id).state == "complete"

    unsupported = DerivedCharacterService(production_database, characters).derive(
        replace(characters.get_character(character_id), edition="2014")
    )
    assert unsupported.spell_slots_state == "unresolved"
    assert unsupported.spell_slots == ()
    assert [issue.category for issue in unsupported.issues] == ["unsupported_edition"]


def test_missing_class_feature_reference_is_reported_without_name_relinking(
    production_database: Database,
) -> None:
    character_id, characters = _new_character(production_database, ("Fighter",))
    fighter = _owner(production_database, "class", "Fighter")
    missing_feature = f"{fighter.identity}#classfeature/xphb/removed-feature"
    owner_key = fighter.identity.split(":", 1)[1]
    with production_database.connection() as db, db:
        db.execute(
            "INSERT INTO character_rule_grants "
            "(dataset_id,owner_type,owner_key,grant_key,scope,grant_type,reference_kind,"
            "reference_identity,source_rule,unresolved) "
            "VALUES (?,'class',?,?,'starting_class','feature','class_feature',?, ?,0)",
            (
                PRODUCTION_DATASET_ID,
                owner_key,
                "test-missing-feature",
                missing_feature,
                "test missing exact feature",
            ),
        )
    try:
        result = DerivedCharacterService(production_database, characters).derive_character(
            character_id
        )
    finally:
        with production_database.connection() as db, db:
            db.execute(
                "DELETE FROM character_rule_grants WHERE dataset_id=? AND owner_type='class' "
                "AND owner_key=? AND grant_key='test-missing-feature'",
                (PRODUCTION_DATASET_ID, owner_key),
            )

    stale_feature = next(
        row for row in result.features if row.reference.identity == missing_feature
    )
    assert stale_feature.reference.missing
    assert any(issue.category == "stale_reference" for issue in result.issues)


def test_derived_queries_do_not_scale_with_spells_or_items(
    production_database: Database,
) -> None:
    small_id, small_service = _new_character(production_database, ("Wizard",))
    large_id, large_service = _new_character(production_database, ("Wizard",))
    wizard = _owner(production_database, "class", "Wizard")
    with production_database.connection() as db:
        spells = db.execute(
            "SELECT e.dataset_id,e.local_key,e.name,s.edition FROM entries e "
            "JOIN spells sp ON sp.entry_id=e.id JOIN sources s ON s.id=e.source_id "
            "WHERE e.dataset_id=? AND e.kind='spell' AND s.edition='2024' "
            "ORDER BY e.local_key LIMIT 20",
            (PRODUCTION_DATASET_ID,),
        ).fetchall()
        items = db.execute(
            "SELECT e.dataset_id,e.local_key,e.name,s.edition FROM entries e "
            "JOIN items i ON i.entry_id=e.id JOIN sources s ON s.id=e.source_id "
            "WHERE e.dataset_id=? AND e.kind='item' AND s.edition='2024' "
            "ORDER BY e.local_key LIMIT 20",
            (PRODUCTION_DATASET_ID,),
        ).fetchall()
    assert len(spells) == len(items) == 20
    for row in (spells[0],):
        small_service.add_spell(
            small_id,
            PublishedReference("spell", f"{row[0]}:{row[1]}", str(row[2]), str(row[3])),
            acquisition="prepared",
            source_class=wizard,
        )
    for row in (items[0],):
        small_service.add_equipment(
            small_id,
            item_reference=PublishedReference(
                "item", f"{row[0]}:{row[1]}", str(row[2]), str(row[3])
            ),
        )
    for row in spells:
        large_service.add_spell(
            large_id,
            PublishedReference("spell", f"{row[0]}:{row[1]}", str(row[2]), str(row[3])),
            acquisition="prepared",
            source_class=wizard,
        )
    for row in items:
        large_service.add_equipment(
            large_id,
            item_reference=PublishedReference(
                "item", f"{row[0]}:{row[1]}", str(row[2]), str(row[3])
            ),
        )

    profiler = PerformanceProfiler()
    profiled_database = Database(production_database.path, profiler=profiler)
    engine = DerivedCharacterService(profiled_database)
    engine.derive_character(small_id)
    small_queries = profiler.statement_count("character.derive")
    engine.derive_character(large_id)
    large_queries = profiler.statement_count("character.derive") - small_queries

    assert large_queries == small_queries
