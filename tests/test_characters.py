from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from dndref.character_builder import CharacterBuilderRules
from dndref.characters import CharacterNotFoundError, CharacterService
from dndref.importer import import_dataset, load_dataset
from dndref.models import (
    Ability,
    AbilityChange,
    BackgroundBuilderRecord,
    CharacterBuilderCatalog,
    Choice,
    ChoiceKind,
    ChoiceOption,
    ClassBuilderRules,
    ClassProgression,
    DecisionProvenance,
    OptionCriteria,
    OptionCriteriaKind,
    PublishedReference,
    Requirement,
    RequirementOperator,
    SubclassBuilderRules,
)
from dndref.performance import PerformanceProfiler
from dndref.search import SearchService
from dndref.storage.database import MIGRATIONS_DIR, Database

FIXTURE = Path(__file__).parent / "fixtures" / "dataset"
DATASET_ID = "characters-2024"


def _dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset"
    shutil.copytree(FIXTURE, dataset)
    manifest = json.loads((dataset / "manifest.json").read_text())
    manifest["dataset_id"] = DATASET_ID
    manifest["sources"][0]["edition"] = "2024"
    (dataset / "manifest.json").write_text(json.dumps(manifest))

    classes_path = dataset / "classes.json"
    classes = json.loads(classes_path.read_text())
    wizard = classes[0]
    for class_key, class_name in (("class/warlock", "Warlock"), ("class/fighter", "Fighter")):
        row = copy.deepcopy(wizard)
        row["local_key"] = class_key
        row["name"] = class_name
        for subclass in row["subclasses"]:
            subclass["subclass_key"] = f"subclass/{class_key.split('/', 1)[1]}/star-sage"
            subclass["name"] = f"{class_name} Path"
        classes.append(row)
    classes_path.write_text(json.dumps(classes))
    return dataset


def _ready(tmp_path: Path) -> tuple[Database, CharacterService, Path]:
    dataset = _dataset(tmp_path)
    database = Database(tmp_path / "characters.sqlite3")
    import_dataset(database, load_dataset(dataset))
    return database, CharacterService(database), dataset


def _ref(kind: str, local_key: str, name: str) -> PublishedReference:
    if kind == "subclass":
        raise ValueError("subclasses use _subclass_ref")
    return PublishedReference(kind, f"{DATASET_ID}:{local_key}", name, "2024")


def _subclass_ref(class_name: str, local_class_key: str) -> PublishedReference:
    subclass_key = f"subclass/{local_class_key.split('/', 1)[1]}/star-sage"
    return PublishedReference(
        "subclass",
        f"{DATASET_ID}:subclass:{local_class_key}:{subclass_key}",
        f"{class_name} Path",
        "2024",
    )


def _seed_choice_rules(
    database: Database,
    *,
    owner_key: str,
    choices: list[Choice],
) -> CharacterBuilderRules:
    dataset_id = DATASET_ID
    background = BackgroundBuilderRecord(
        background_key=owner_key,
        name=owner_key,
        source="example-core",
        choices=choices,
    )
    catalog = CharacterBuilderCatalog(backgrounds=[background])
    rules = CharacterBuilderRules(catalog)
    with database.connection() as db, db:
        source_id = db.execute(
            "SELECT id FROM sources WHERE dataset_id=? LIMIT 1", (dataset_id,)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO character_builder_owners "
            "(dataset_id,owner_type,owner_key,source_id,edition,name) "
            "VALUES (?,'background',?,?,'2024',?)",
            (dataset_id, owner_key, source_id, owner_key),
        )
        for choice in choices:
            criteria = choice.criteria
            db.execute(
                "INSERT INTO character_rule_choices "
                "(dataset_id,owner_type,owner_key,choice_key,scope,choice_type,choice_count,"
                "criteria_kind,criteria_values_json,criteria_filters_json,depends_on_choice,"
                "depends_on_option,exclusions_json,source_rule) "
                "VALUES (?,'background',?,?,'background',?,?,?,?,?,?,?,?,?)",
                (
                    dataset_id,
                    owner_key,
                    str(choice.choice_key),
                    str(choice.kind),
                    choice.count,
                    str(criteria.kind) if criteria else None,
                    json.dumps(list(criteria.values), separators=(",", ":")) if criteria else None,
                    json.dumps(criteria.filters, sort_keys=True, separators=(",", ":"))
                    if criteria
                    else None,
                    str(choice.depends_on_choice) if choice.depends_on_choice else None,
                    str(choice.depends_on_option) if choice.depends_on_option else None,
                    json.dumps(
                        [item.model_dump(mode="json") for item in choice.exclusions],
                        separators=(",", ":"),
                    ),
                    choice.source_rule,
                ),
            )
            for option in choice.options:
                db.execute(
                    "INSERT INTO character_rule_choice_options "
                    "(dataset_id,owner_type,owner_key,choice_key,option_key,label,value,"
                    "reference_kind,reference_identity,resolved) "
                    "VALUES (?,'background',?,?,?,?,?,?,?,?)",
                    (
                        dataset_id,
                        owner_key,
                        str(choice.choice_key),
                        str(option.option_key),
                        option.label,
                        option.value,
                        str(option.reference.kind) if option.reference else None,
                        option.reference.identity if option.reference else None,
                        int(option.resolved),
                    ),
                )
        if any(
            choice.criteria is not None and choice.criteria.kind is OptionCriteriaKind.SKILL
            for choice in choices
        ):
            db.execute(
                "INSERT OR IGNORE INTO character_builder_skills"
                "(dataset_id,skill_key,name,ability_key) "
                "VALUES (?,'arcana','Arcana','int')",
                (dataset_id,),
            )
    return rules


def _two_option_choice(choice_key: str = "skills", count: int = 2) -> Choice:
    return Choice(
        choice_key=choice_key,
        kind=ChoiceKind.PROFICIENCY,
        count=count,
        options=[
            ChoiceOption(option_key="arcana", label="Arcana", value="skill:arcana"),
            ChoiceOption(option_key="history", label="History", value="skill:history"),
            ChoiceOption(option_key="insight", label="Insight", value="skill:insight"),
        ],
        source_rule="Choose the listed proficiencies.",
    )


def test_migration_010_to_012_preserves_personal_and_reference_data(tmp_path: Path) -> None:
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for migration in MIGRATIONS_DIR.glob("0[0-1][0-9]_*.sql"):
        if int(migration.name[:3]) <= 10:
            shutil.copy(migration, old_migrations)
    dataset = _dataset(tmp_path)
    path = tmp_path / "upgrade.sqlite3"
    old_database = Database(path, old_migrations)
    import_dataset(old_database, load_dataset(dataset))
    with old_database.connection() as db, db:
        before = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        db.execute(
            "INSERT INTO user_favorites(entry_identity,kind,entry_name) "
            "VALUES ('characters-2024:spell/spark','spell','Spark')"
        )
    database = Database(path)

    assert database.initialize() == (11, 12)
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == before
        assert db.execute("SELECT COUNT(*) FROM user_favorites").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
            == 12
        )
        tables = {
            str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "user_characters",
            "user_character_levels",
            "user_character_subclasses",
            "user_character_ability_scores",
            "user_character_choices",
            "user_character_feats",
            "user_character_spells",
            "user_character_equipment",
            "user_character_hp_choices",
            "user_character_notes",
            "user_character_currency",
        } <= tables
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_character_crud_duplicate_and_delete_are_stable_and_transactional(tmp_path: Path) -> None:
    database, service, _dataset_path = _ready(tmp_path)
    wizard = _ref("class", "class/wizard", "Wizard")
    original = service.create_character("  New   Character  ")
    assert original.name == "New Character"
    assert original.state == "draft"
    assert original.total_level == 0
    assert original.starting_class is None
    assert service.list_characters()[0].display_summary == "New Character\nDraft\nLevel 0"
    with pytest.raises(ValueError, match="2024 edition"):
        service.create_character("Old Edition", edition="2014")

    service.add_class_level(original.character_id, wizard)
    service.update_note(original.character_id, "Private character notes.")
    before_rename = service.get_character(original.character_id)
    renamed = service.rename_character(original.character_id, "Rien")
    assert renamed.character_id == original.character_id
    assert renamed.updated_at > before_rename.updated_at
    assert renamed.created_at == before_rename.created_at

    duplicate = service.duplicate_character(original.character_id, "Rien Copy")
    assert duplicate.character_id != original.character_id
    assert duplicate.name == "Rien Copy"
    assert duplicate.notes == "Private character notes."
    assert duplicate.levels == service.get_character(original.character_id).levels
    assert duplicate.created_at != service.get_character(original.character_id).created_at

    reference_count = None
    with database.connection() as db:
        reference_count = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    assert service.delete_character(duplicate.character_id)
    assert not service.delete_character(duplicate.character_id)
    with pytest.raises(CharacterNotFoundError):
        service.get_character(duplicate.character_id)
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == reference_count
        assert (
            db.execute(
                "SELECT COUNT(*) FROM user_character_levels WHERE character_id=?",
                (duplicate.character_id,),
            ).fetchone()[0]
            == 0
        )


def test_species_background_references_duplicate_and_resolve_exactly(tmp_path: Path) -> None:
    database, service, dataset = _ready(tmp_path)
    character = service.create_character("Origin")
    with database.connection() as db, db:
        source_id = db.execute(
            "SELECT id FROM sources WHERE dataset_id=? LIMIT 1", (DATASET_ID,)
        ).fetchone()[0]
        db.executemany(
            "INSERT INTO character_builder_owners "
            "(dataset_id,owner_type,owner_key,source_id,edition,name) VALUES (?,?,?,?,?,?)",
            [
                (DATASET_ID, "species", "species/elf", source_id, "2024", "Elf"),
                (DATASET_ID, "background", "background/scholar", source_id, "2024", "Scholar"),
            ],
        )
    species = _ref("species", "species/elf", "Elf")
    background = _ref("background", "background/scholar", "Scholar")
    service.set_species(character.character_id, species)
    selected = service.set_background(character.character_id, background)
    assert selected.species.identity == species.identity
    assert selected.background.identity == background.identity
    duplicate = service.duplicate_character(character.character_id)
    assert duplicate.species.identity == species.identity
    assert duplicate.background.identity == background.identity

    refreshed_spells = json.loads((dataset / "spells.json").read_text())
    refreshed_spells[0]["description"] = "A refreshed reference snapshot."
    (dataset / "spells.json").write_text(json.dumps(refreshed_spells))
    import_dataset(database, load_dataset(dataset))
    stale = service.get_character(character.character_id)
    assert stale.species.missing and stale.species.identity == species.identity
    assert stale.background.missing and stale.background.identity == background.identity
    with database.connection() as db, db:
        source_id = db.execute(
            "SELECT id FROM sources WHERE dataset_id=? LIMIT 1", (DATASET_ID,)
        ).fetchone()[0]
        db.executemany(
            "INSERT INTO character_builder_owners "
            "(dataset_id,owner_type,owner_key,source_id,edition,name) VALUES (?,?,?,?,?,?)",
            [
                (DATASET_ID, "species", "species/elf-renamed", source_id, "2024", "Elf"),
                (
                    DATASET_ID,
                    "background",
                    "background/scholar-renamed",
                    source_id,
                    "2024",
                    "Scholar",
                ),
            ],
        )
    assert service.get_character(character.character_id).species.missing
    assert service.get_character(character.character_id).background.missing
    with database.connection() as db, db:
        source_id = db.execute(
            "SELECT id FROM sources WHERE dataset_id=? LIMIT 1", (DATASET_ID,)
        ).fetchone()[0]
        db.executemany(
            "INSERT INTO character_builder_owners "
            "(dataset_id,owner_type,owner_key,source_id,edition,name) VALUES (?,?,?,?,?,?)",
            [
                (DATASET_ID, "species", "species/elf", source_id, "2024", "Elf Returned"),
                (
                    DATASET_ID,
                    "background",
                    "background/scholar",
                    source_id,
                    "2024",
                    "Scholar Returned",
                ),
            ],
        )
    restored = service.get_character(character.character_id)
    assert not restored.species.missing and restored.species.name == "Elf Returned"
    assert not restored.background.missing and restored.background.name == "Scholar Returned"


def test_duplicate_rolls_back_all_rows_when_copy_fails(tmp_path: Path, monkeypatch) -> None:
    _database, service, _dataset_path = _ready(tmp_path)
    original = service.create_character("Populated")
    service.add_class_level(original.character_id, _ref("class", "class/wizard", "Wizard"))
    service.update_note(original.character_id, "Copied only if the transaction commits.")
    copy_children = service._copy_child_rows

    def fail_after_copy(db, source_id: str, destination_id: str) -> None:
        copy_children(db, source_id, destination_id)
        raise RuntimeError("injected failure after child copy")

    monkeypatch.setattr(service, "_copy_child_rows", fail_after_copy)
    with pytest.raises(RuntimeError, match="injected failure"):
        service.duplicate_character(original.character_id)
    assert [item.character_id for item in service.list_characters()] == [original.character_id]


def test_ordered_multiclass_history_and_maximum_level(tmp_path: Path) -> None:
    _database, service, _dataset_path = _ready(tmp_path)
    character = service.create_character("Rien")
    warlock = _ref("class", "class/warlock", "Warlock")
    fighter = _ref("class", "class/fighter", "Fighter")
    wizard = _ref("class", "class/wizard", "Wizard")
    sequence = [warlock, warlock, fighter, fighter, fighter, warlock]
    for reference in sequence:
        service.add_class_level(character.character_id, reference)

    loaded = service.get_character(character.character_id)
    assert [row.total_level for row in loaded.levels] == [1, 2, 3, 4, 5, 6]
    assert [row.class_level for row in loaded.levels] == [1, 2, 1, 2, 3, 3]
    assert [row.class_reference.name for row in loaded.levels] == [
        "Warlock",
        "Warlock",
        "Fighter",
        "Fighter",
        "Fighter",
        "Warlock",
    ]
    assert loaded.total_level == 6
    assert loaded.class_levels == {warlock.identity: 3, fighter.identity: 3}
    assert (
        loaded.starting_class
        == service.get_character(character.character_id).levels[0].class_reference
    )
    assert loaded.class_summary == "Warlock 3 / Fighter 3"
    assert service.list_characters()[0].class_summary == "Warlock 3 / Fighter 3"

    three_track = service.create_character("Three Tracks")
    for reference in (wizard, warlock, fighter):
        service.add_class_level(three_track.character_id, reference)
    assert len(service.get_character(three_track.character_id).class_levels) == 3

    twenty = service.create_character("Level Twenty")
    for _ in range(20):
        service.add_class_level(twenty.character_id, wizard)
    assert service.get_character(twenty.character_id).total_level == 20
    with pytest.raises(ValueError, match="cannot exceed 20"):
        service.add_class_level(twenty.character_id, wizard)

    latest = service.remove_last_class_level(character.character_id)
    assert latest.total_level == 6
    assert service.get_character(character.character_id).class_summary == "Warlock 2 / Fighter 3"


def test_level_history_gap_is_rejected_and_reported(tmp_path: Path) -> None:
    database, service, _dataset_path = _ready(tmp_path)
    character = service.create_character("Gap")
    wizard = _ref("class", "class/wizard", "Wizard")
    for _ in range(3):
        service.add_class_level(character.character_id, wizard)
    with database.connection() as db, db:
        db.execute(
            "DELETE FROM user_character_levels WHERE character_id=? AND total_level=2",
            (character.character_id,),
        )
    with pytest.raises(ValueError, match="not contiguous"):
        service.add_class_level(character.character_id, wizard)
    assert any(
        issue.code == "level_sequence_inconsistent" and issue.severity == "error"
        for issue in service.validate_character(character.character_id).issues
    )


def test_subclass_is_owned_by_exact_class_track_and_edition(tmp_path: Path) -> None:
    _database, service, _dataset_path = _ready(tmp_path)
    character = service.create_character("Two Traditions")
    wizard = _ref("class", "class/wizard", "Wizard")
    warlock = _ref("class", "class/warlock", "Warlock")
    fighter = _ref("class", "class/fighter", "Fighter")
    for reference in (wizard, wizard, warlock, warlock, fighter, fighter):
        service.add_class_level(character.character_id, reference)

    wizard_path = _subclass_ref("Wizard", "class/wizard")
    warlock_path = _subclass_ref("Warlock", "class/warlock")
    fighter_path = _subclass_ref("Fighter", "class/fighter")
    service.set_subclass(character.character_id, wizard.identity, wizard_path, 2)
    service.set_subclass(character.character_id, warlock.identity, warlock_path, 2)
    service.set_subclass(character.character_id, fighter.identity, fighter_path, 1)
    assert {
        row.class_reference.identity
        for row in service.get_character(character.character_id).subclasses
    } == {wizard.identity, warlock.identity, fighter.identity}
    with pytest.raises(ValueError, match="does not belong"):
        service.set_subclass(character.character_id, wizard.identity, warlock_path, 1)
    with pytest.raises(ValueError, match="2024 edition"):
        service.set_subclass(
            character.character_id,
            wizard.identity,
            PublishedReference("subclass", wizard_path.identity, wizard_path.name, "2014"),
            2,
        )


def test_subclass_validation_uses_builder_parent_and_selection_level(tmp_path: Path) -> None:
    _database, service, _dataset_path = _ready(tmp_path)
    wizard = _ref("class", "class/wizard", "Wizard")
    path = _subclass_ref("Wizard", "class/wizard")
    rules = CharacterBuilderRules(
        CharacterBuilderCatalog(
            classes=[
                ClassBuilderRules(
                    class_key="class/wizard",
                    name="Wizard",
                    source="example-core",
                    progression=ClassProgression(),
                    primary_ability_options=[[Ability.INT]],
                    multiclass_requirement=Requirement(
                        operator=RequirementOperator.ABILITY_SCORE,
                        ability=Ability.INT,
                        minimum=13,
                    ),
                )
            ],
            subclasses=[
                SubclassBuilderRules(
                    subclass_key="subclass/wizard/star-sage",
                    class_key="class/wizard",
                    name="Star Sage",
                    source="example-core",
                    selection_level=2,
                )
            ],
        )
    )
    character = service.create_character("Wizard")
    service.add_class_level(character.character_id, wizard)
    too_early = service.validate_subclass_selection(
        character.character_id, wizard.identity, path, rules, rules_dataset_id=DATASET_ID
    )
    assert too_early.status == "unsatisfied"
    service.add_class_level(character.character_id, wizard)
    wrong_edition = service.validate_subclass_selection(
        character.character_id,
        wizard.identity,
        PublishedReference("subclass", path.identity, path.name, "2014"),
        rules,
        rules_dataset_id=DATASET_ID,
    )
    assert wrong_edition.status == "unsatisfied"
    wrong_kind = service.validate_subclass_selection(
        character.character_id,
        wizard.identity,
        PublishedReference("feat", path.identity, path.name, "2024"),
        rules,
        rules_dataset_id=DATASET_ID,
    )
    assert wrong_kind.status == "unsatisfied"
    valid = service.validate_subclass_selection(
        character.character_id, wizard.identity, path, rules, rules_dataset_id=DATASET_ID
    )
    assert valid.status == "satisfied"


def test_multiclass_requirement_hook_is_separate_from_storage(tmp_path: Path) -> None:
    _database, service, _dataset_path = _ready(tmp_path)
    wizard = _ref("class", "class/wizard", "Wizard")
    warlock = _ref("class", "class/warlock", "Warlock")
    rules = CharacterBuilderRules(
        CharacterBuilderCatalog(
            classes=[
                ClassBuilderRules(
                    class_key="class/wizard",
                    name="Wizard",
                    source="example-core",
                    progression=ClassProgression(),
                    primary_ability_options=[[Ability.INT]],
                    multiclass_requirement=Requirement(
                        operator=RequirementOperator.ABILITY_SCORE,
                        ability=Ability.INT,
                        minimum=13,
                    ),
                ),
                ClassBuilderRules(
                    class_key="class/warlock",
                    name="Warlock",
                    source="example-core",
                    progression=ClassProgression(),
                    primary_ability_options=[[Ability.CHA]],
                    multiclass_requirement=Requirement(
                        operator=RequirementOperator.ABILITY_SCORE,
                        ability=Ability.CHA,
                        minimum=13,
                    ),
                ),
            ]
        )
    )
    character = service.create_character("Rule Check")
    assert (
        service.validate_add_class_level(
            character.character_id, wizard, rules, rules_dataset_id=DATASET_ID
        ).status
        == "satisfied"
    )
    service.add_class_level(character.character_id, wizard)
    assert (
        service.validate_add_class_level(
            character.character_id, warlock, rules, rules_dataset_id=DATASET_ID
        ).status
        == "unresolved"
    )
    service.set_ability_state(character.character_id, {"int": 14, "cha": 12})
    assert (
        service.validate_add_class_level(
            character.character_id, warlock, rules, rules_dataset_id=DATASET_ID
        ).status
        == "unsatisfied"
    )
    service.set_ability_state(character.character_id, {"int": 14, "cha": 13})
    assert (
        service.validate_add_class_level(
            character.character_id, warlock, rules, rules_dataset_id=DATASET_ID
        ).status
        == "satisfied"
    )
    # Structural storage remains available even when the optional hook reports ineligible.
    service.set_ability_state(character.character_id, {"int": 14, "cha": 12})
    service.add_class_level(character.character_id, warlock)
    assert service.get_character(character.character_id).class_summary == "Wizard 1 / Warlock 1"


def test_ability_choices_notes_hp_spells_feats_and_equipment_persist(tmp_path: Path) -> None:
    database, service, _dataset_path = _ready(tmp_path)
    character = service.create_character("Inputs")
    wizard = _ref("class", "class/wizard", "Wizard")
    service.add_class_level(character.character_id, wizard)
    service.set_ability_state(
        character.character_id,
        {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        [
            AbilityChange(
                "int",
                1,
                "background",
                "Scholar background increase",
                provenance=DecisionProvenance(
                    owner_dataset_id=DATASET_ID,
                    owner_type="background",
                    owner_key="background/scholar",
                ),
            ),
            AbilityChange(
                "dex", 1, "asi", "Level 4 ASI", provenance=DecisionProvenance(character_level=4)
            ),
            AbilityChange("int", 1, "feat", "Quick Study increase"),
        ],
    )
    service.add_feat(
        character.character_id,
        _ref("feat", "feat/quick-study", "Quick Study"),
        provenance_kind="background",
        provenance_label="Background origin feat",
        provenance=DecisionProvenance(
            owner_dataset_id=DATASET_ID,
            owner_type="background",
            owner_key="background/scholar",
            choice_key="origin-feat",
            option_key="quick-study",
            character_level=1,
        ),
    )
    service.add_spell(
        character.character_id,
        _ref("spell", "spell/spark", "Spark"),
        acquisition="cantrip",
        source_class=wizard,
        character_level=1,
        class_level=1,
        provenance=DecisionProvenance(
            owner_dataset_id=DATASET_ID,
            owner_type="class",
            owner_key="class/wizard",
            choice_key="cantrip-choice",
            character_level=1,
            class_identity=wizard.identity,
            class_level=1,
        ),
    )
    service.add_spell(
        character.character_id,
        _ref("spell", "spell/comet-burst", "Comet Burst"),
        acquisition="pact_magic",
        source_class=wizard,
        character_level=1,
        class_level=1,
    )
    exact_item = service.add_equipment(
        character.character_id,
        item_reference=_ref("item", "item/star-map", "Star Map"),
        quantity=1,
        equipped=True,
        provenance_kind="starting_equipment",
        provenance_label="Class equipment choice",
    )
    generic_item = service.add_equipment(
        character.character_id,
        unresolved_selection="Holy Symbol",
        quantity=2,
        carried_state="stowed",
        provenance_kind="starting_equipment",
        provenance_label="Generic starting equipment choice",
    )
    service.update_equipment(character.character_id, exact_item.equipment_id, equipped=False)
    service.set_hp_choice(character.character_id, 1, "fixed_average", 4)
    service.update_note(character.character_id, "A local note.\nSecond line.")
    loaded = service.get_character(character.character_id)

    assert dict(loaded.base_ability_scores)["int"] == 15
    assert [
        (item.ability, item.source_kind, item.amount) for item in loaded.ability_modifications
    ] == [
        ("int", "background", 1),
        ("dex", "asi", 1),
        ("int", "feat", 1),
    ]
    assert loaded.feats[0].provenance_kind == "background"
    assert loaded.feats[0].feat_reference.identity == f"{DATASET_ID}:feat/quick-study"
    assert [spell.acquisition for spell in loaded.spells] == ["cantrip", "pact_magic"]
    assert loaded.spells[0].source_class_reference.identity == wizard.identity
    assert loaded.equipment[0].item_reference.identity == f"{DATASET_ID}:item/star-map"
    assert loaded.equipment[0].equipped is False
    assert loaded.equipment[1].unresolved_selection == "Holy Symbol"
    assert loaded.equipment[1].quantity == 2
    assert loaded.equipment[1].carried_state == "stowed"
    assert loaded.hp_choices[0].choice_kind == "fixed_average"
    assert loaded.hp_choices[0].amount == 4
    assert loaded.notes == "A local note.\nSecond line."
    serialized = json.dumps(loaded.to_dict(), sort_keys=True)
    assert json.loads(serialized)["starting_class_identity"] == wizard.identity
    assert json.loads(serialized)["total_level"] == 1
    assert service.get_character(character.character_id).to_dict() == loaded.to_dict()
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] > 0
        assert generic_item.resolution_state == "unresolved"


def test_choices_are_owner_scoped_counted_and_explicitly_unresolved(tmp_path: Path) -> None:
    database, service, _dataset_path = _ready(tmp_path)
    first = _two_option_choice(count=2)
    second = _two_option_choice(count=1)
    criteria = Choice(
        choice_key="skill-filter",
        kind=ChoiceKind.PROFICIENCY,
        count=1,
        criteria=OptionCriteria(kind=OptionCriteriaKind.SKILL, values=["skill"]),
        source_rule="Choose a skill proficiency.",
    )
    unresolved = Choice(
        choice_key="spell-list-filter",
        kind=ChoiceKind.SPELL,
        count=1,
        criteria=OptionCriteria(kind=OptionCriteriaKind.SPELL_LIST, values=["class/wizard"]),
        source_rule="Choose a spell from the list.",
    )
    parent = Choice(
        choice_key="parent-choice",
        kind=ChoiceKind.OTHER,
        count=1,
        options=[
            ChoiceOption(option_key="enabled", label="Enabled"),
            ChoiceOption(option_key="disabled", label="Disabled"),
        ],
        source_rule="Choose whether the follow-up applies.",
    )
    dependent = Choice(
        choice_key="dependent-choice",
        kind=ChoiceKind.OTHER,
        count=1,
        options=[ChoiceOption(option_key="follow-up", label="Follow-up")],
        source_rule="Choose the enabled follow-up.",
        depends_on_choice="parent-choice",
        depends_on_option="enabled",
    )
    rules_one = _seed_choice_rules(
        database,
        owner_key="background/first",
        choices=[first, criteria, unresolved, parent, dependent],
    )
    rules_two = _seed_choice_rules(database, owner_key="background/second", choices=[second])
    character = service.create_character("Choices")

    a = service.resolve_choice(
        character.character_id,
        rules=rules_one,
        rules_dataset_id=DATASET_ID,
        owner_type="background",
        owner_key="background/first",
        choice_key="skills",
        selected_option_key="arcana",
    )
    duplicate_choice_character = service.duplicate_character(
        character.character_id, "Repeated decision"
    )
    with pytest.raises(ValueError, match="already recorded"):
        service.resolve_choice(
            duplicate_choice_character.character_id,
            rules=rules_one,
            rules_dataset_id=DATASET_ID,
            owner_type="background",
            owner_key="background/first",
            choice_key="skills",
            selected_option_key="arcana",
        )
    b = service.resolve_choice(
        character.character_id,
        rules=rules_one,
        rules_dataset_id=DATASET_ID,
        owner_type="background",
        owner_key="background/first",
        choice_key="skills",
        selected_option_key="history",
    )
    assert a.owner_key != "background/second"
    assert b.selected_value == "skill:history"
    with pytest.raises(ValueError, match="published limit"):
        service.resolve_choice(
            character.character_id,
            rules=rules_one,
            rules_dataset_id=DATASET_ID,
            owner_type="background",
            owner_key="background/first",
            choice_key="skills",
            selected_option_key="insight",
        )
    with pytest.raises(ValueError, match="not valid"):
        invalid_character = service.create_character("Invalid option")
        service.resolve_choice(
            invalid_character.character_id,
            rules=rules_one,
            rules_dataset_id=DATASET_ID,
            owner_type="background",
            owner_key="background/first",
            choice_key="skills",
            selected_option_key="invalid-option",
        )
    other_owner = service.resolve_choice(
        character.character_id,
        rules=rules_two,
        rules_dataset_id=DATASET_ID,
        owner_type="background",
        owner_key="background/second",
        choice_key="skills",
        selected_option_key="arcana",
    )
    assert other_owner.owner_key == "background/second"
    skill = service.resolve_choice(
        character.character_id,
        rules=rules_one,
        rules_dataset_id=DATASET_ID,
        owner_type="background",
        owner_key="background/first",
        choice_key="skill-filter",
        selected_value="arcana",
    )
    assert skill.selected_value == "arcana"
    unresolved_spell = service.resolve_choice(
        character.character_id,
        rules=rules_one,
        rules_dataset_id=DATASET_ID,
        owner_type="background",
        owner_key="background/first",
        choice_key="spell-list-filter",
        selected_reference=PublishedReference(
            "spell", "missing-pack:spell/unknown", "Unknown Spell", "2024", True
        ),
        allow_unresolved=True,
    )
    assert unresolved_spell.resolution_state == "unresolved"
    assert unresolved_spell.selected_reference.missing
    disabled_character = service.create_character("Disabled conditional choice")
    service.resolve_choice(
        disabled_character.character_id,
        rules=rules_one,
        rules_dataset_id=DATASET_ID,
        owner_type="background",
        owner_key="background/first",
        choice_key="parent-choice",
        selected_option_key="disabled",
    )
    with pytest.raises(ValueError, match="conditional choice"):
        service.resolve_choice(
            disabled_character.character_id,
            rules=rules_one,
            rules_dataset_id=DATASET_ID,
            owner_type="background",
            owner_key="background/first",
            choice_key="dependent-choice",
            selected_option_key="follow-up",
        )
    enabled_character = service.create_character("Enabled conditional choice")
    service.resolve_choice(
        enabled_character.character_id,
        rules=rules_one,
        rules_dataset_id=DATASET_ID,
        owner_type="background",
        owner_key="background/first",
        choice_key="parent-choice",
        selected_option_key="enabled",
    )
    service.resolve_choice(
        enabled_character.character_id,
        rules=rules_one,
        rules_dataset_id=DATASET_ID,
        owner_type="background",
        owner_key="background/first",
        choice_key="dependent-choice",
        selected_option_key="follow-up",
    )
    assert any(
        issue.code == "choice_unresolved"
        for issue in service.validate_character(character.character_id).issues
    )


def test_required_choice_validation_is_warning_for_draft_and_error_for_complete(
    tmp_path: Path,
) -> None:
    _database, service, _dataset_path = _ready(tmp_path)
    rules = _seed_choice_rules(
        _database,
        owner_key="background/scholar",
        choices=[_two_option_choice(count=2)],
    )
    character = service.create_character("Incomplete Origin")
    background = _ref("background", "background/scholar", "Scholar")
    service.set_background(character.character_id, background)

    draft_report = service.validate_character(character.character_id)
    missing = [issue for issue in draft_report.issues if issue.code == "missing_required_choice"]
    assert len(missing) == 1
    assert missing[0].severity == "warning"

    service.set_character_state(character.character_id, "complete")
    complete_report = service.validate_character(character.character_id)
    missing = [issue for issue in complete_report.issues if issue.code == "missing_required_choice"]
    assert len(missing) == 1
    assert missing[0].severity == "error"

    for option_key in ("arcana", "history"):
        service.resolve_choice(
            character.character_id,
            rules=rules,
            rules_dataset_id=DATASET_ID,
            owner_type="background",
            owner_key="background/scholar",
            choice_key="skills",
            selected_option_key=option_key,
            character_level=1,
        )
    assert not any(
        issue.code == "missing_required_choice"
        for issue in service.validate_character(character.character_id).issues
    )


def test_invalid_stable_identities_cannot_be_saved_as_unresolved(tmp_path: Path) -> None:
    _database, service, _dataset_path = _ready(tmp_path)
    character = service.create_character("Malformed Reference")
    invalid_identities = [
        "bad dataset:feat/quick-study",
        f"{DATASET_ID}:../feat/quick-study",
        f"{DATASET_ID}:feat//quick-study",
        f"{DATASET_ID}:feat/quick study",
    ]
    for identity in invalid_identities:
        with pytest.raises(ValueError, match="invalid|form"):
            service.add_feat(
                character.character_id,
                PublishedReference("feat", identity, "Quick Study", "2024", True),
                provenance_kind="other",
                provenance_label="Unresolved choice",
                allow_unresolved=True,
            )


def test_class_level_removal_cleans_only_decisions_after_removed_level(tmp_path: Path) -> None:
    _database, service, _dataset_path = _ready(tmp_path)
    character = service.create_character("Level Rollback")
    wizard = _ref("class", "class/wizard", "Wizard")
    service.add_class_level(character.character_id, wizard)
    service.set_hp_choice(character.character_id, 1, "rolled", 5)
    service.add_class_level(character.character_id, wizard)
    service.add_spell(
        character.character_id,
        _ref("spell", "spell/spark", "Spark"),
        acquisition="known",
        source_class=wizard,
        character_level=2,
        class_level=2,
        provenance=DecisionProvenance(
            character_level=2, class_identity=wizard.identity, class_level=2
        ),
    )
    service.set_hp_choice(character.character_id, 2, "fixed_average", 4)

    service.remove_last_class_level(character.character_id)
    loaded = service.get_character(character.character_id)
    assert loaded.total_level == 1
    assert loaded.hp_choices == (loaded.hp_choices[0],)
    assert loaded.hp_choices[0].total_level == 1
    assert not loaded.spells


def test_stale_references_survive_replacement_without_name_relinking(tmp_path: Path) -> None:
    _database, service, dataset = _ready(tmp_path)
    character = service.create_character("Stale")
    wizard = _ref("class", "class/wizard", "Wizard")
    service.add_class_level(character.character_id, wizard)
    service.add_feat(
        character.character_id,
        _ref("feat", "feat/quick-study", "Quick Study"),
        provenance_kind="background",
        provenance_label="Origin feat",
    )
    service.add_spell(
        character.character_id,
        _ref("spell", "spell/spark", "Spark"),
        acquisition="known",
    )
    service.add_equipment(
        character.character_id,
        item_reference=_ref("item", "item/star-map", "Star Map"),
    )
    original_files = {
        name: (dataset / name).read_text()
        for name in ("classes.json", "feats.json", "spells.json", "items.json")
    }
    classes = json.loads(original_files["classes.json"])
    replacement_class = copy.deepcopy(classes[0])
    replacement_class["local_key"] = "class/wizard-renamed"
    replacement_class["subclasses"] = []
    (dataset / "classes.json").write_text(json.dumps([replacement_class]))
    feats = json.loads(original_files["feats.json"])
    feats[0]["local_key"] = "feat/quick-study-renamed"
    (dataset / "feats.json").write_text(json.dumps(feats))
    spells = json.loads(original_files["spells.json"])
    spells[0]["local_key"] = "spell/spark-renamed"
    spells[0]["class_references"] = ["class/wizard-renamed"]
    spells[1]["class_references"] = ["class/wizard-renamed"]
    (dataset / "spells.json").write_text(json.dumps(spells))
    items = json.loads(original_files["items.json"])
    for item in items["items"]:
        if item["local_key"] == "item/star-map":
            item["local_key"] = "item/star-map-renamed"
    (dataset / "items.json").write_text(json.dumps(items))
    import_dataset(_database, load_dataset(dataset))

    stale = service.get_character(character.character_id)
    assert stale.total_level == 1
    assert stale.levels[0].class_reference.missing
    assert stale.feats[0].feat_reference.missing
    assert stale.spells[0].spell_reference.missing
    assert stale.equipment[0].item_reference.missing
    assert stale.levels[0].class_reference.identity == wizard.identity
    assert stale.feats[0].feat_reference.identity == f"{DATASET_ID}:feat/quick-study"
    assert {
        issue.reference_identity
        for issue in service.validate_character(character.character_id).issues
        if issue.code == "published_reference_missing"
    } >= {
        wizard.identity,
        f"{DATASET_ID}:feat/quick-study",
        f"{DATASET_ID}:spell/spark",
        f"{DATASET_ID}:item/star-map",
    }

    for name, content in original_files.items():
        (dataset / name).write_text(content)
    import_dataset(_database, load_dataset(dataset))
    restored = service.get_character(character.character_id)
    assert not restored.levels[0].class_reference.missing
    assert not restored.feats[0].feat_reference.missing
    assert not restored.spells[0].spell_reference.missing
    assert not restored.equipment[0].item_reference.missing


def test_dataset_reimport_preserves_character_and_personal_data(tmp_path: Path) -> None:
    database, service, dataset = _ready(tmp_path)
    character = service.create_character("Preserved")
    search = SearchService(database)
    detail = search.get_entry_detail(f"{DATASET_ID}:spell/spark")
    assert detail is not None
    from dndref.personal import PersonalDataService

    personal = PersonalDataService(database)
    personal.set_favorite(detail, True)
    import_dataset(database, load_dataset(dataset))
    assert service.get_character(character.character_id).name == "Preserved"
    assert personal.is_favorite(detail.identity)
    assert service.list_characters()[0].name == "Preserved"


def test_character_listing_uses_one_query_and_search_does_not_read_character_tables(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    profiler = PerformanceProfiler()
    database = Database(tmp_path / "performance.sqlite3", profiler=profiler)
    import_dataset(database, load_dataset(dataset))
    service = CharacterService(database)
    first = service.create_character("A")
    second = service.create_character("B")
    wizard = _ref("class", "class/wizard", "Wizard")
    for _ in range(3):
        service.add_class_level(first.character_id, wizard)
        service.add_spell(
            first.character_id,
            _ref("spell", "spell/spark", "Spark"),
            acquisition="known",
            source_class=wizard,
        )
        service.add_equipment(
            first.character_id,
            item_reference=_ref("item", "item/star-map", "Star Map"),
        )
    with profiler.operation("characters.list"):
        summaries = service.list_characters()
    assert len(summaries) == 2
    assert any(summary.character_id == second.character_id for summary in summaries)
    assert profiler.statement_count("characters.list") == 1

    character_reads: list[str] = []

    class GuardedDatabase(Database):
        def connect(self):
            connection = super().connect()

            def authorizer(action, table, _column, _database, _trigger):
                if action == sqlite3.SQLITE_READ and str(table).startswith("user_character"):
                    character_reads.append(str(table))
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorizer)
            return connection

    search = SearchService(GuardedDatabase(database.path))
    assert search.search_all("spark").results
    assert character_reads == []
