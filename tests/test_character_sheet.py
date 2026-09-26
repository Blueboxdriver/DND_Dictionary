from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from textual.widgets import Input, ListView, Static, TextArea

from dndref.characters import CharacterService
from dndref.crossrefs import CrossReferenceResolver
from dndref.derived_character import DerivedCharacterService
from dndref.importer import import_dataset, load_dataset
from dndref.models.character import CharacterLevel, PublishedReference, SubclassSelection
from dndref.models.derived_character import (
    ArmorClassResult,
    AttackSummary,
    DerivedValue,
    FeatureReference,
    PactMagicResult,
    SkillResult,
    SpellcastingProfile,
    SpellSelectionResult,
    SpellSlot,
)
from dndref.performance import PerformanceProfiler
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp
from dndref.ui.character_sheet import (
    CharacterSheetScreen,
    _armor_row,
    _attack_row,
    _derived_value,
)
from dndref.ui.launchers import CommandPaletteScreen
from dndref.ui.screens import TextEntryScreen

FIXTURE = Path("tests/fixtures/dataset")


@pytest.fixture(scope="module")
def sheet_database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    root = tmp_path_factory.mktemp("character-sheet")
    dataset = root / "dataset"
    shutil.copytree(FIXTURE, dataset)
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for source in manifest["sources"]:
        source["edition"] = "2024"
    manifest_path.write_text(json.dumps(manifest))
    database = Database(root / "sheet.sqlite3")
    import_dataset(database, load_dataset(dataset))
    return database


def _new_draft(database: Database, name: str = "Mira") -> tuple[CharacterService, str]:
    service = CharacterService(database)
    character = service.create_character(name)
    return service, character.character_id


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24), (60, 20)])
async def test_draft_sheet_sections_fit_supported_sizes(
    sheet_database: Database, size: tuple[int, int]
) -> None:
    characters, character_id = _new_draft(sheet_database, f"Mira {size[0]}")
    async with BrowserApp(sheet_database).run_test(size=size) as pilot:
        await pilot.pause(0.15)
        pilot.app._open_character_sheet(character_id)
        await pilot.pause(0.15)
        sheet = pilot.app.screen
        assert isinstance(sheet, CharacterSheetScreen)
        assert sheet.query_one("#sheet-name", Static).region.width > 0
        assert sheet.query_one("#sheet-rows", ListView).region.height > 0
        assert "Draft" in str(sheet.query_one("#sheet-name", Static).renderable)
        assert any(row.action == "resume" for row in sheet._visible_rows)
        footer = sheet.query_one("#sheet-footer", Static)
        assert all(len(line) <= size[0] - 4 for line in str(footer.renderable).splitlines())

        visited = [sheet.section]
        for _ in range(6):
            await pilot.press("right")
            await pilot.pause(0.03)
            visited.append(sheet.section)
            assert all(len(line) <= size[0] - 4 for line in str(footer.renderable).splitlines())
        assert visited == [
            "overview",
            "skills",
            "combat",
            "features",
            "spells",
            "equipment",
            "notes",
        ]
        for _ in range(6):
            await pilot.press("left")
            await pilot.pause(0.03)
        assert sheet.section == "overview"


@pytest.mark.asyncio
async def test_equipment_reference_back_restores_sheet_and_notes_save(
    sheet_database: Database,
) -> None:
    characters, character_id = _new_draft(sheet_database, "Navigation Test")
    exact_item = PublishedReference(
        "item", "example-5e:item/adventuring-pack", "Adventuring Pack", "2024"
    )
    characters.add_equipment(
        character_id,
        item_reference=exact_item,
        quantity=2,
        equipped=True,
        provenance_label="Starting equipment",
    )
    characters.add_equipment(
        character_id,
        unresolved_selection="Arcane Focus",
        provenance_label="Generic choice",
        allow_unresolved=True,
    )

    async with BrowserApp(sheet_database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.15)
        pilot.app._open_character_sheet(character_id)
        await pilot.pause(0.15)
        sheet = pilot.app.screen
        assert isinstance(sheet, CharacterSheetScreen)
        for _ in range(5):
            await pilot.press("right")
            await pilot.pause(0.03)
        assert sheet.section == "equipment"
        exact_index = next(
            index
            for index, row in enumerate(sheet._visible_rows)
            if row.reference_identity == exact_item.identity
        )
        generic = next(row for row in sheet._visible_rows if "Arcane Focus" in row.label)
        assert generic.reference_identity is None
        assert "[generic]" in generic.label

        sheet.query_one("#sheet-rows", ListView).index = exact_index
        await pilot.press("enter")
        await pilot.pause(0.35)
        assert pilot.app._current_detail is not None
        assert pilot.app._current_detail.identity == exact_item.identity
        assert any(row.identity == exact_item.identity for row in pilot.app.recently_viewed.records)
        assert all(row.identity != character_id for row in pilot.app.recently_viewed.records)

        await pilot.press("alt+left")
        await pilot.pause(0.35)
        sheet = pilot.app.screen
        assert isinstance(sheet, CharacterSheetScreen)
        assert sheet.section == "equipment"
        assert sheet.query_one("#sheet-rows", ListView).index == exact_index

        previous_derived = sheet.derived
        await pilot.press("ctrl+p")
        await pilot.press(*"unequip")
        await pilot.pause(0.2)
        assert isinstance(pilot.app.screen, CommandPaletteScreen)
        await pilot.press("enter")
        await pilot.pause(0.15)
        assert pilot.app.screen is sheet
        assert sheet.derived is not previous_derived
        assert not next(
            item
            for item in characters.get_character(character_id).equipment
            if item.item_reference and item.item_reference.identity == exact_item.identity
        ).equipped

        await pilot.press("right")
        await pilot.pause(0.05)
        assert sheet.section == "notes"
        note_index = next(
            index for index, row in enumerate(sheet._visible_rows) if row.action == "edit_note"
        )
        sheet.query_one("#sheet-rows", ListView).index = note_index
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert isinstance(pilot.app.screen, TextEntryScreen)
        editor = pilot.app.screen.query_one("#note-input", TextArea)
        editor.text = "Saved character note"
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
        assert pilot.app.screen is sheet
        assert characters.get_character(character_id).notes == "Saved character note"
        assert "Saved character note" in "\n".join(row.label for row in sheet._all_rows)


@pytest.mark.asyncio
async def test_large_spell_section_pages_and_searches_bounded_rows(
    sheet_database: Database,
) -> None:
    characters, character_id = _new_draft(sheet_database, "Spell pagination")
    wizard = PublishedReference("class", "example-5e:class/wizard", "Wizard", "2024")
    spells = tuple(
        SpellSelectionResult(
            PublishedReference(
                "spell", f"example-5e:spell/spell-{index}", f"Spell {index:02}", "2024"
            ),
            "known",
            None,
            True,
            level=1,
            school="evocation",
        )
        for index in range(30)
    )
    profile = SpellcastingProfile(
        "class",
        wizard,
        wizard,
        5,
        "int",
        DerivedValue(15, "complete"),
        DerivedValue(7, "complete"),
        "known",
        4,
        None,
        8,
        (1, 2),
        (),
        spells,
    )
    async with BrowserApp(sheet_database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.15)
        pilot.app._open_character_sheet(character_id)
        await pilot.pause(0.15)
        sheet = pilot.app.screen
        assert isinstance(sheet, CharacterSheetScreen)
        sheet.derived = replace(sheet.derived, spellcasting_profiles=(profile,))
        sheet._switch_section("spells")
        await pilot.pause(0.1)
        view = sheet.query_one("#sheet-rows", ListView)
        assert len(sheet._all_rows) > sheet.PAGE_SIZE
        assert len(sheet._visible_rows) == sheet.PAGE_SIZE
        assert len(view.children) == sheet.PAGE_SIZE

        await pilot.press("ctrl+pagedown")
        await pilot.pause(0.1)
        assert sheet._section_state["spells"][2] == 1
        assert len(view.children) < sheet.PAGE_SIZE

        await pilot.press("/")
        search = sheet.query_one("#sheet-search", Input)
        search.value = "Spell 29"
        await pilot.pause(0.1)
        assert [row.label for row in sheet._visible_rows] == ["Spell 29 · 1st-level evocation"]


def test_sheet_values_are_rendered_from_derived_results(sheet_database: Database) -> None:
    characters, character_id = _new_draft(sheet_database, "Derived display")
    result = DerivedCharacterService(sheet_database, characters).derive_character(character_id)
    assert len(result.saving_throws) == 6
    names = (
        ("acrobatics", "Acrobatics", "dex"),
        ("animal_handling", "Animal Handling", "wis"),
        ("arcana", "Arcana", "int"),
        ("athletics", "Athletics", "str"),
        ("deception", "Deception", "cha"),
        ("history", "History", "int"),
        ("insight", "Insight", "wis"),
        ("intimidation", "Intimidation", "cha"),
        ("investigation", "Investigation", "int"),
        ("medicine", "Medicine", "wis"),
        ("nature", "Nature", "int"),
        ("perception", "Perception", "wis"),
        ("performance", "Performance", "cha"),
        ("persuasion", "Persuasion", "cha"),
        ("religion", "Religion", "int"),
        ("sleight_of_hand", "Sleight of Hand", "dex"),
        ("stealth", "Stealth", "dex"),
        ("survival", "Survival", "wis"),
    )
    supplied_skills = tuple(
        SkillResult(key, name, ability, 2, "proficient", 3, DerivedValue(5, "complete"))
        for key, name, ability in names
    )
    screen = object.__new__(CharacterSheetScreen)
    screen.derived = replace(result, skills=supplied_skills)
    rendered = screen._skills_rows()
    rows = [row.label for row in rendered[9:27]]
    assert len(rows) == 18
    assert all(
        f"({skill.ability.upper()}) +5 · Proficient" in label
        for skill, label in zip(supplied_skills, rows)
    )

    assert _derived_value(DerivedValue(None, "unresolved")) == "?"
    assert _derived_value(DerivedValue(5, "partial")) == "5 (Partial)"
    assert _derived_value(DerivedValue(0, "complete")) == "0"


def test_spell_profiles_and_slot_pools_remain_separate(sheet_database: Database) -> None:
    characters, character_id = _new_draft(sheet_database, "Spell profile display")
    character = characters.get_character(character_id)
    derived = DerivedCharacterService(sheet_database, characters).derive_character(character_id)
    wizard = PublishedReference("class", "example-5e:class/wizard", "Wizard", "2024")
    cleric = PublishedReference("class", "example-5e:class/cleric", "Cleric", "2024")
    warlock = PublishedReference("class", "example-5e:class/warlock", "Warlock", "2024")
    spark = PublishedReference("spell", "example-5e:spell/spark", "Spark", "2024")
    fire = PublishedReference("spell", "example-5e:spell/comet-burst", "Comet Burst", "2024")

    wizard_profile = SpellcastingProfile(
        "class",
        wizard,
        wizard,
        5,
        "int",
        DerivedValue(15, "complete"),
        DerivedValue(7, "complete"),
        "spellbook",
        4,
        2,
        6,
        (1, 2),
        (),
        (
            SpellSelectionResult(spark, "cantrip", None, True, level=0, school="evocation"),
            SpellSelectionResult(fire, "spellbook", None, True, level=3, school="evocation"),
        ),
    )
    cleric_profile = SpellcastingProfile(
        "class",
        cleric,
        cleric,
        3,
        "wis",
        DerivedValue(14, "complete"),
        DerivedValue(6, "complete"),
        "prepared",
        3,
        4,
        None,
        (1, 2),
        (),
        (SpellSelectionResult(fire, "prepared", None, True, level=3, school="evocation"),),
    )
    pact_profile = SpellcastingProfile(
        "class",
        warlock,
        warlock,
        5,
        "cha",
        DerivedValue(15, "complete"),
        DerivedValue(7, "complete"),
        "pact_magic",
        3,
        None,
        6,
        (3,),
        (),
        (SpellSelectionResult(fire, "pact_magic", None, True, level=3, school="evocation"),),
    )
    supplied = replace(
        derived,
        spellcasting_profiles=(wizard_profile, cleric_profile, pact_profile),
        spell_slots=(SpellSlot(1, 4), SpellSlot(2, 3), SpellSlot(3, 3)),
        pact_magic=(PactMagicResult(2, 3, 5, warlock.identity),),
    )
    screen = object.__new__(CharacterSheetScreen)
    screen.character = character
    screen.derived = supplied

    spell_rows = screen._spell_rows()
    labels = [row.label for row in spell_rows]
    assert "Standard Spell Slots · shared multiclass pool" in labels
    assert "Pact Magic · Warlock level 5" in labels
    assert "2 slots · 3rd level" in labels
    assert "Wizard · Wizard level 5" in labels
    assert "Spell Save DC: 15" in labels
    assert "Spell Attack Bonus: +7" in labels
    assert "Accessible spell levels: 1st, 2nd" in labels
    assert "Comet Burst · 3rd-level evocation" in labels
    assert "Cantrips" in labels and "Spellbook" in labels and "Prepared" in labels
    assert "Cleric · Cleric level 3" in labels
    assert "Spell Save DC: 14" in labels
    assert "Accessible spell levels: 3rd" in labels


def test_sheet_reference_names_resolve_in_bounded_batches(sheet_database: Database) -> None:
    profiler = PerformanceProfiler()
    sheet_database.profiler = profiler
    resolver = CrossReferenceResolver(sheet_database)
    item_id = "example-5e:item/adventuring-pack"
    subclass_id = "example-5e:subclass:class/wizard:subclass/wizard/star-sage"
    identities = [item_id, subclass_id]
    identities.extend(f"missing-{index}:item/removed" for index in range(260))

    targets = resolver.get_many_by_id(identities)

    assert targets[item_id].name == "Adventuring Pack"
    assert targets[subclass_id].name == "Star Sage"
    assert profiler.statement_count("reference.batch") == 4


def test_features_group_by_provenance_and_combat_shows_unresolved_values(
    sheet_database: Database,
) -> None:
    characters, character_id = _new_draft(sheet_database, "Feature grouping")
    character = characters.get_character(character_id)
    wizard = PublishedReference("class", "example-5e:class/wizard", "Wizard", "2024")
    star_sage = PublishedReference(
        "subclass",
        "example-5e:subclass:class/wizard:subclass/wizard/star-sage",
        "Star Sage",
        "2024",
    )
    species = PublishedReference("species", "example-5e:species/dwarf", "Dwarf", "2024")
    background = PublishedReference(
        "background", "example-5e:background/soldier", "Soldier", "2024"
    )
    feat = PublishedReference("feat", "example-5e:feat/quick-study", "Quick Study", "2024")
    class_feature = PublishedReference(
        "class_feature",
        "example-5e:class/wizard#arcane-recovery",
        "Arcane Recovery",
        "2024",
    )
    subclass_feature = PublishedReference(
        "subclass_feature",
        f"{star_sage.identity}#astral-reading",
        "Astral Reading",
        "2024",
    )
    character = replace(
        character,
        levels=(CharacterLevel(1, wizard, 1),),
        subclasses=(SubclassSelection(wizard, star_sage, 2),),
        species=species,
        background=background,
    )
    features = (
        FeatureReference(
            class_feature, wizard.identity, "Wizard", "class progression", 1, "Arcane Recovery"
        ),
        FeatureReference(
            subclass_feature,
            star_sage.identity,
            "Star Sage",
            "subclass progression",
            2,
            "Astral Reading",
        ),
        FeatureReference(
            species, species.identity, "Dwarf", "species trait", display_name="Stonecunning"
        ),
        FeatureReference(
            background,
            background.identity,
            "Soldier",
            "background trait",
            display_name="Military Training",
        ),
        FeatureReference(
            feat, feat.identity, "Background feat", "background choice", display_name="Quick Study"
        ),
    )
    derived = DerivedCharacterService(sheet_database, characters).derive_character(character_id)
    screen = object.__new__(CharacterSheetScreen)
    screen.character = character
    screen.derived = replace(derived, features=features)
    screen.references = CrossReferenceResolver(sheet_database)
    screen._feature_targets = None

    rows = screen._feature_rows()
    labels = [row.label for row in rows]
    assert all(group in labels for group in ("Class", "Subclass", "Species", "Background", "Feats"))
    assert any("Arcane Recovery" in label for label in labels)
    assert any("Astral Reading" in label for label in labels)
    class_row = next(row for row in rows if "Arcane Recovery" in row.label)
    assert class_row.action == "reference"
    assert class_row.reference_identity == wizard.identity

    armor = _armor_row(
        replace(
            derived,
            armor_class=ArmorClassResult(None, "unresolved", None, (), "armor data is missing"),
        )
    )
    assert "Armor Class: ?" == armor.label
    attack = AttackSummary(
        PublishedReference("item", "example-5e:item/longbow", "Longbow", "2024"),
        None,
        None,
        None,
        None,
        None,
        (),
        (),
        (),
        "unresolved",
        "Source data is missing attack type.",
    )
    attack_row = _attack_row(attack)
    assert "Longbow" in attack_row.label
    assert "Attack calculation unavailable" in attack_row.label
    assert "Source data is missing attack type." in attack_row.label
