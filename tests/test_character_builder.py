from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from dndref.character_builder import CharacterBuilderRules
from dndref.cli import initialize_application
from dndref.config import ApplicationPaths
from dndref.importer import import_dataset, legacy_pre_character_builder_hash, load_dataset
from dndref.models.character_builder import (
    Ability,
    BackgroundBuilderRecord,
    CharacterBuilderCatalog,
    CharacterRuleContext,
    Choice,
    ChoiceKind,
    ChoiceOption,
    GrantKind,
    MulticlassContribution,
    OptionCriteria,
    OptionCriteriaKind,
    ProficiencyKind,
    ReferenceKind,
    RequirementStatus,
    SpeciesBuilderRecord,
    SpellAcquisition,
    SpellcastingModel,
)
from dndref.models.dataset import validate_dataset
from dndref.personal import PersonalDataService
from dndref.search import SearchService
from dndref.storage.database import MIGRATIONS_DIR, Database

PRODUCTION = Path("src/dndref/datasets/official-5etools-2024")
PRODUCTION_DATASET_ID = "official-5etools-2024"
BUILDER_TABLE_COUNTS = {
    "character_builder_owners": 2104,
    "character_rule_requirements": 189,
    "class_progression_events": 741,
    "character_rule_choices": 433,
    "character_rule_choice_options": 1321,
    "character_rule_grants": 1610,
    "character_builder_traits": 71,
    "class_spellcasting": 12,
    "class_spellcasting_levels": 240,
    "class_spell_slots": 1520,
    "character_builder_spell_access": 307,
    "character_builder_equipment": 1682,
    "character_builder_skills": 18,
}
EXPECTED_2024_CLASSES = {
    "Artificer",
    "Barbarian",
    "Bard",
    "Cleric",
    "Druid",
    "Fighter",
    "Monk",
    "Paladin",
    "Ranger",
    "Rogue",
    "Sorcerer",
    "Warlock",
    "Wizard",
}


def _choice(
    key: str,
    label: str,
    *,
    parent_key: str | None = None,
    parent_option: str | None = None,
) -> Choice:
    return Choice(
        choice_key=key,
        kind=ChoiceKind.OTHER,
        count=1,
        options=[ChoiceOption(option_key=f"option/{label}", label=label, value=label)],
        criteria=None,
        source_rule=f"test.{label}",
        depends_on_choice=parent_key,
        depends_on_option=parent_option,
    )


def _species(key: str, choices: list[Choice]) -> SpeciesBuilderRecord:
    return SpeciesBuilderRecord(
        species_key=key,
        name=key,
        source="XPHB",
        creature_types=["humanoid"],
        sizes=["medium"],
        movement=[{"kind": "walk", "feet": 30}],
        choices=choices,
    )


def _builder_counts(database: Database) -> dict[str, int]:
    with database.connection() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in BUILDER_TABLE_COUNTS
        }


def _reference_rows(value: object):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        if {"kind", "identity", "resolved"} <= value.keys():
            yield ReferenceKind(value["kind"]), str(value["identity"]), bool(value["resolved"])
            return
        for child in value.values():
            yield from _reference_rows(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _reference_rows(child)


def _assert_resolved_references_exist(loaded, database: Database) -> None:
    with database.connection() as connection:
        targets = {
            ReferenceKind.ITEM: {
                str(row[0])
                for row in connection.execute(
                    "SELECT local_key FROM entries WHERE dataset_id=? AND kind='item'",
                    (loaded.dataset_id,),
                )
            },
            ReferenceKind.SPELL: {
                str(row[0])
                for row in connection.execute(
                    "SELECT local_key FROM entries WHERE dataset_id=? AND kind='spell'",
                    (loaded.dataset_id,),
                )
            },
            ReferenceKind.FEAT: {
                str(row[0])
                for row in connection.execute(
                    "SELECT local_key FROM entries WHERE dataset_id=? AND kind='feat'",
                    (loaded.dataset_id,),
                )
            },
            ReferenceKind.CLASS: {
                str(row[0])
                for row in connection.execute(
                    "SELECT local_key FROM entries WHERE dataset_id=? AND kind='class'",
                    (loaded.dataset_id,),
                )
            },
            ReferenceKind.SUBCLASS: {
                str(row[0])
                for row in connection.execute(
                    "SELECT subclass_key FROM subclasses WHERE dataset_id=?",
                    (loaded.dataset_id,),
                )
            },
            ReferenceKind.RULE: {
                str(row[0])
                for row in connection.execute(
                    "SELECT local_key FROM entries WHERE dataset_id=? AND kind='rule'",
                    (loaded.dataset_id,),
                )
            },
            ReferenceKind.CLASS_FEATURE: {
                str(row[0])
                for row in connection.execute(
                    "SELECT class_entry.local_key || '#' || feature.feature_key "
                    "FROM class_features AS feature "
                    "JOIN classes AS class ON class.entry_id=feature.class_id "
                    "JOIN entries AS class_entry ON class_entry.id=class.entry_id "
                    "WHERE feature.dataset_id=?",
                    (loaded.dataset_id,),
                )
            },
            ReferenceKind.SUBCLASS_FEATURE: {
                str(row[0])
                for row in connection.execute(
                    "SELECT subclass.subclass_key || '#' || feature.feature_key "
                    "FROM subclass_features AS feature "
                    "JOIN subclasses AS subclass ON subclass.id=feature.subclass_id "
                    "WHERE feature.dataset_id=?",
                    (loaded.dataset_id,),
                )
            },
            ReferenceKind.OPTIONAL_FEATURE: {
                str(row[0])
                for row in connection.execute(
                    "SELECT owner_key FROM character_builder_owners "
                    "WHERE dataset_id=? AND owner_type='optional_feature'",
                    (loaded.dataset_id,),
                )
            },
        }
    unresolved = []
    for kind, identity, resolved in _reference_rows(loaded.pack.character_builder):
        if not resolved:
            unresolved.append((kind, identity))
            continue
        reference_dataset, separator, local_key = identity.partition(":")
        assert separator and reference_dataset == loaded.dataset_id
        assert local_key in targets[kind], (kind, identity)
    unresolved_counts = Counter(kind for kind, _ in unresolved)
    assert unresolved_counts == {ReferenceKind.ITEM: 5}
    assert {identity for kind, identity in unresolved if kind is ReferenceKind.ITEM} <= {
        "holy symbol|xphb",
        "druidic focus|xphb",
        "arcane focus|xphb",
    }


def test_choice_identity_is_scoped_to_owner_and_parent_lookup_is_local() -> None:
    parent = _choice("choice/shared", "species-option")
    child = Choice(
        choice_key="choice/child",
        kind=ChoiceKind.EQUIPMENT,
        count=1,
        criteria=OptionCriteria(kind=OptionCriteriaKind.ITEM_TYPE, values=["simple"]),
        source_rule="test.child",
        depends_on_choice="choice/shared",
        depends_on_option="option/species-option",
    )
    background_choice = _choice("choice/shared", "background-option")
    catalog = CharacterBuilderCatalog(
        species=[_species("species/one", [parent, child])],
        backgrounds=[
            BackgroundBuilderRecord(
                background_key="background/one",
                name="Test Background",
                source="XPHB",
                choices=[background_choice],
            )
        ],
    )

    rules = CharacterBuilderRules(catalog)

    assert rules.get_choice_definition("species", "species/one", "choice/shared") is parent
    assert (
        rules.get_choice_definition("background", "background/one", "choice/shared")
        is background_choice
    )
    assert rules.get_choice_definition("species", "background/one", "choice/shared") is None


def test_conditional_choice_cannot_resolve_parent_from_another_owner() -> None:
    child = Choice(
        choice_key="choice/child",
        kind=ChoiceKind.EQUIPMENT,
        count=1,
        criteria=OptionCriteria(kind=OptionCriteriaKind.ITEM_TYPE, values=["simple"]),
        source_rule="test.child",
        depends_on_choice="choice/parent",
        depends_on_option="option/parent-option",
    )
    parent = _choice("choice/parent", "parent-option")

    with pytest.raises(ValidationError, match="within species 'species/one'"):
        CharacterBuilderCatalog(
            species=[_species("species/one", [child])],
            backgrounds=[
                BackgroundBuilderRecord(
                    background_key="background/one",
                    name="Test Background",
                    source="XPHB",
                    choices=[parent],
                )
            ],
        )


def test_choice_keys_remain_unique_within_one_owner() -> None:
    choice = _choice("choice/shared", "first")

    with pytest.raises(ValidationError, match="unique within species 'species/one'"):
        CharacterBuilderCatalog(
            species=[_species("species/one", [choice, _choice("choice/shared", "second")])]
        )


def test_packaged_production_catalog_behaves_through_character_builder_rules() -> None:
    loaded = load_dataset(PRODUCTION)
    catalog = loaded.pack.character_builder
    assert catalog is not None
    assert validate_dataset(loaded.pack) == loaded.pack
    assert {row.name for row in catalog.classes} == EXPECTED_2024_CLASSES
    assert len(catalog.subclasses) == 76
    assert len(catalog.species) == 19
    assert len(catalog.backgrounds) == 65
    assert len(catalog.feats) == 179
    assert len(catalog.optional_features) == 70
    assert len(catalog.skills) == 18

    rules = CharacterBuilderRules(catalog)
    classes = {row.name: row for row in catalog.classes}
    fighter = classes["Fighter"]
    rogue = classes["Rogue"]
    starting_grants = rules.get_starting_class_grants(str(fighter.class_key))
    multiclass_grants = rules.get_multiclass_grants(str(fighter.class_key))
    assert starting_grants and multiclass_grants
    assert rules.get_multiclass_requirements(str(fighter.class_key)) is not None
    assert rules.get_level_events(str(fighter.class_key), 5)
    assert not rules.get_level_events(str(fighter.class_key), 21)
    assert any(
        grant.proficiency_kind is ProficiencyKind.SAVING_THROW
        for grant in starting_grants
        if grant.kind is GrantKind.PROFICIENCY
    )
    assert not any(
        grant.proficiency_kind is ProficiencyKind.SAVING_THROW for grant in multiclass_grants
    )
    assert starting_grants != multiclass_grants

    eligible = CharacterRuleContext(
        edition="2024",
        total_level=2,
        class_levels={str(rogue.class_key): 1},
        ability_scores={Ability.DEX: 14},
    )
    ineligible = eligible.model_copy(update={"ability_scores": {Ability.DEX: 12}})
    assert (
        rules.can_enter_class(str(fighter.class_key), eligible).status
        is RequirementStatus.SATISFIED
    )
    assert (
        rules.can_enter_class(str(fighter.class_key), ineligible).status
        is RequirementStatus.UNSATISFIED
    )

    eldritch_knight = next(row for row in catalog.subclasses if row.name == "Eldritch Knight")
    assert str(eldritch_knight.class_key) == str(fighter.class_key)
    assert eldritch_knight.selection_level == 3
    assert not rules.get_available_subclasses(str(fighter.class_key), 2)
    assert eldritch_knight in rules.get_available_subclasses(str(fighter.class_key), 3)
    assert eldritch_knight.progression_events
    assert eldritch_knight.spellcasting is not None
    assert (
        eldritch_knight.spellcasting.multiclass_contribution
        is MulticlassContribution.THIRD_ROUND_DOWN
    )

    aasimar = next(row for row in catalog.species if row.name == "Aasimar")
    assert rules.get_species(str(aasimar.species_key)) is aasimar
    assert aasimar.grants and aasimar.traits and aasimar.choices
    assert aasimar.darkvision_feet == 60

    acolyte = next(row for row in catalog.backgrounds if row.name == "Acolyte")
    skill_grants = [
        grant
        for grant in acolyte.grants
        if grant.kind is GrantKind.PROFICIENCY and grant.proficiency_kind is ProficiencyKind.SKILL
    ]
    assert {grant.value for grant in skill_grants} == {"insight", "religion"}
    assert any(choice.kind is ChoiceKind.ABILITY_SCORE for choice in acolyte.choices)
    assert acolyte.origin_feat is not None and acolyte.origin_feat.resolved
    assert acolyte.starting_equipment.choices
    assert rules.get_background(str(acolyte.background_key)) is acolyte

    pack_feat_names = {str(row.local_key): row.name for row in loaded.pack.feats}
    magic_initiate = next(
        feat for feat in catalog.feats if pack_feat_names[str(feat.feat_key)] == "Magic Initiate"
    )
    assert rules.get_feat_rules(str(magic_initiate.feat_key)) is magic_initiate
    assert magic_initiate.spell_choices or magic_initiate.spell_access

    spellcasters = {row.name: row.spellcasting for row in catalog.classes if row.spellcasting}
    wizard = spellcasters["Wizard"]
    bard = spellcasters["Bard"]
    warlock = spellcasters["Warlock"]
    artificer = spellcasters["Artificer"]
    assert wizard is not None and wizard.model is SpellcastingModel.SPELLCASTING
    assert wizard.acquisition is SpellAcquisition.SPELLBOOK and wizard.standalone_slots
    assert bard is not None and bard.acquisition is SpellAcquisition.PREPARED
    bard_spell_filter = next(
        access
        for access in bard.additional_spells
        if access.criteria is not None and access.criteria.kind is OptionCriteriaKind.SPELL_LIST
    )
    assert bard_spell_filter.criteria is not None
    assert set(bard_spell_filter.criteria.values) == {
        str(classes[name].class_key) for name in ("Cleric", "Druid", "Wizard")
    }
    assert bard_spell_filter.criteria.filters["spell_level"] == ["1", "2", "3", "4", "5"]
    assert warlock is not None and warlock.model is SpellcastingModel.PACT_MAGIC
    assert warlock.pact_slots and not warlock.standalone_slots
    assert warlock.multiclass_contribution is MulticlassContribution.PACT_MAGIC_SEPARATE
    assert artificer is not None
    assert artificer.multiclass_contribution is MulticlassContribution.HALF_ROUND_UP
    assert any(access.level_scope == "total" for access in aasimar.spell_access)

    item_metadata = {str(row.item_key): row for row in catalog.equipment}
    items_by_name = {row.name: row for row in loaded.pack.items.items}
    scale_mail = item_metadata[str(items_by_name["Scale Mail"].local_key)]
    shield = item_metadata[str(items_by_name["Shield"].local_key)]
    longsword = item_metadata[str(items_by_name["Longsword"].local_key)]
    longbow = item_metadata[str(items_by_name["Longbow"].local_key)]
    assert scale_mail.armor_category == "medium" and scale_mail.base_ac == 14
    assert shield.armor_category == "shield" and shield.base_ac == 2
    assert longsword.attack_type == "melee" and longsword.damage
    assert longsword.properties and longsword.mastery_references
    assert longbow.attack_type == "ranged" and longbow.range_feet == [150, 600]
    assert longbow.properties and longbow.mastery_references and longbow.ammunition_reference

    artisan = next(row for row in catalog.backgrounds if row.name == "Artisan")
    artisan_choices = artisan.choices + artisan.starting_equipment.choices
    conditional = next(choice for choice in artisan_choices if choice.depends_on_choice is not None)
    parent = next(
        choice for choice in artisan_choices if choice.choice_key == conditional.depends_on_choice
    )
    assert (
        rules.get_choice_definition(
            "background", str(artisan.background_key), str(conditional.choice_key)
        )
        is conditional
    )
    assert (
        rules.get_choice_definition(
            "background", str(artisan.background_key), str(parent.choice_key)
        )
        is parent
    )

    # Make two production owners share a key to verify the public lookup contract.
    data = catalog.model_dump(mode="json")
    acolyte_data = next(
        row for row in data["backgrounds"] if row["background_key"] == str(acolyte.background_key)
    )
    shared_key = str(parent.choice_key)
    acolyte_data["choices"][0]["choice_key"] = shared_key
    shared_catalog = CharacterBuilderCatalog.model_validate(data)
    shared_rules = CharacterBuilderRules(shared_catalog)
    background_choice = shared_rules.get_choice_definition(
        "background", str(acolyte.background_key), shared_key
    )
    assert background_choice is not None and background_choice.kind is ChoiceKind.ABILITY_SCORE
    shared_artisan = next(
        row for row in shared_catalog.backgrounds if row.background_key == artisan.background_key
    )
    shared_artisan_choices = shared_artisan.choices + shared_artisan.starting_equipment.choices
    shared_conditional = next(
        choice for choice in shared_artisan_choices if choice.depends_on_choice is not None
    )
    shared_parent = next(
        choice
        for choice in shared_artisan_choices
        if choice.choice_key == shared_conditional.depends_on_choice
    )
    assert (
        shared_rules.get_choice_definition(
            "background", str(artisan.background_key), str(shared_conditional.choice_key)
        )
        is shared_conditional
    )
    assert (
        shared_rules.get_choice_definition(
            "background", str(artisan.background_key), str(shared_conditional.depends_on_choice)
        )
        is shared_parent
    )
    assert (
        shared_rules.get_choice_definition("background", str(acolyte.background_key), shared_key)
        is background_choice
    )

    bad_data = catalog.model_dump(mode="json")
    acolyte_data = next(
        row
        for row in bad_data["backgrounds"]
        if row["background_key"] == str(acolyte.background_key)
    )
    acolyte_data["choices"].append(json.loads(json.dumps(acolyte_data["choices"][0])))
    with pytest.raises(ValidationError, match="unique within background"):
        CharacterBuilderCatalog.model_validate(bad_data)


def test_production_import_is_idempotent_and_replacement_clears_builder_rows(
    tmp_path: Path,
) -> None:
    loaded = load_dataset(PRODUCTION)
    database = Database(tmp_path / "production.sqlite3")
    database.initialize()

    assert import_dataset(database, loaded).added == 3725
    assert _builder_counts(database) == BUILDER_TABLE_COUNTS
    _assert_resolved_references_exist(loaded, database)
    with database.connection() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT category, COUNT(*) FROM character_builder_equipment "
                "GROUP BY category ORDER BY category"
            )
        ] == [("armor", 430), ("weapon", 1252)]

    assert import_dataset(database, load_dataset(PRODUCTION)).is_noop
    assert _builder_counts(database) == BUILDER_TABLE_COUNTS

    without_catalog = tmp_path / "without-builder"
    shutil.copytree(PRODUCTION, without_catalog)
    (without_catalog / "character-builder.json").unlink()
    import_dataset(database, load_dataset(without_catalog))
    assert _builder_counts(database) == dict.fromkeys(BUILDER_TABLE_COUNTS, 0)

    import_dataset(database, loaded)
    assert _builder_counts(database) == BUILDER_TABLE_COUNTS


def test_migration_009_previous_bundled_pack_upgrades_automatically_and_preserves_user_data(
    tmp_path: Path,
) -> None:
    paths = ApplicationPaths.for_root(tmp_path / "application")
    old_migrations = tmp_path / "migrations-009"
    old_migrations.mkdir()
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(migration.name.split("_", 1)[0]) <= 9:
            shutil.copy2(migration, old_migrations / migration.name)

    old_pack_path = tmp_path / "official-before-builder"
    shutil.copytree(PRODUCTION, old_pack_path)
    (old_pack_path / "character-builder.json").unlink()
    old_pack = load_dataset(old_pack_path)
    current_pack = load_dataset(PRODUCTION)
    old_pack = replace(old_pack, content_hash=legacy_pre_character_builder_hash(current_pack))

    previous_database = Database(paths.database_path, old_migrations)
    assert previous_database.initialize() == tuple(range(1, 10))
    import_dataset(previous_database, old_pack)
    with previous_database.connection() as connection:
        assert connection.execute(
            "SELECT content_hash FROM datasets WHERE dataset_id=?",
            (PRODUCTION_DATASET_ID,),
        ).fetchone()[0] == legacy_pre_character_builder_hash(current_pack)
        reference_rows_before = [
            tuple(row)
            for row in connection.execute(
                "SELECT dataset_id, local_key, kind, name, content_hash FROM entries "
                "WHERE dataset_id=? ORDER BY local_key",
                (PRODUCTION_DATASET_ID,),
            )
        ]
    personal = PersonalDataService(previous_database)
    acid_splash = next(spell for spell in old_pack.pack.spells if spell.name == "Acid Splash")
    detail = SearchService(previous_database).get_entry_detail(
        f"{PRODUCTION_DATASET_ID}:{acid_splash.local_key}"
    )
    assert detail is not None
    personal.set_favorite(detail, True)
    collection_id = personal.create_collection("Milestone 21 collection")
    personal.set_collection_membership(collection_id, detail, True)
    personal.add_tag(detail.identity, "keep")
    personal.save_note(detail, "Milestone 21 note")
    personal.record_search("keep this search")

    # Ordinary startup applies migrations 010–012 and imports the changed bundled pack.
    initialize_application(paths, image_mode="off")
    upgraded_database = Database(paths.database_path)
    with upgraded_database.connection() as connection:
        assert [
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ] == list(range(1, 13))
        assert tuple(
            connection.execute(
                "SELECT content_hash, source_hash FROM datasets WHERE dataset_id=?",
                (PRODUCTION_DATASET_ID,),
            ).fetchone()
        ) == (current_pack.content_hash, current_pack.source_hash)
        reference_rows_after = [
            tuple(row)
            for row in connection.execute(
                "SELECT dataset_id, local_key, kind, name, content_hash FROM entries "
                "WHERE dataset_id=? ORDER BY local_key",
                (PRODUCTION_DATASET_ID,),
            )
        ]
        assert reference_rows_after == reference_rows_before
    assert _builder_counts(upgraded_database) == BUILDER_TABLE_COUNTS
    upgraded_personal = PersonalDataService(upgraded_database)
    assert upgraded_personal.is_favorite(detail.identity)
    assert upgraded_personal.collections_for(detail.identity) == ("Milestone 21 collection",)
    assert upgraded_personal.tags_for(detail.identity) == ("keep",)
    assert upgraded_personal.note_for(detail.identity) == "Milestone 21 note"
    assert upgraded_personal.recent_searches() == ("keep this search",)
    with upgraded_database.connection() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
