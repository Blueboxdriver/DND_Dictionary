from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Input, ListView, Static

from dndref.character_creation import (
    ABILITIES,
    POINT_BUY_BUDGET,
    POINT_BUY_COSTS,
    STANDARD_ARRAY,
    CharacterCreationService,
    point_buy_cost,
    validate_ability_scores,
)
from dndref.importer import import_dataset, load_dataset
from dndref.models.character import PublishedReference
from dndref.models.character_builder import OptionCriteriaKind
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp
from dndref.ui.character_screens import CharactersScreen, CharacterWizardScreen, ConfirmationScreen
from dndref.ui.character_sheet import CharacterSheetScreen
from dndref.ui.launchers import CommandPaletteScreen, LaunchRow
from dndref.ui.screens import TextEntryScreen

PRODUCTION = Path("src/dndref/datasets/official-5etools-2024")


@pytest.fixture
def production_creation(tmp_path: Path) -> CharacterCreationService:
    database = Database(tmp_path / "creation.sqlite3")
    import_dataset(database, load_dataset(PRODUCTION))
    return CharacterCreationService(database)


def _option(creation: CharacterCreationService, owner_type: str, name: str):
    matches = creation.list_options(owner_type, query=name, limit=20)
    return next(option for option in matches if option.name == name)


def _choose_all(creation: CharacterCreationService, character_id: str, step: str) -> None:
    """Resolve every currently active metadata choice, including conditional children."""

    for _ in range(12):
        changed = False
        for choice in creation.choices_for_step(character_id, step):
            character = creation.characters.get_character(character_id)
            selected = creation._selected_for_choice(character, choice)
            options = creation.choice_options(character_id, choice, limit=50)
            if (
                choice.definition.criteria is not None
                and choice.definition.criteria.kind is OptionCriteriaKind.FEAT_CATEGORY
                and choice.owner.name == "Fighter"
            ):
                archery = next((option for option in options if option.label == "Archery"), None)
                if archery is not None:
                    options = (archery,)
            for option in options:
                if len(selected) >= choice.definition.count:
                    break
                selection = (
                    option.option_key,
                    option.reference.identity if option.reference else None,
                    option.value,
                )
                if any(
                    (
                        row.selected_option_key,
                        row.selected_reference.identity if row.selected_reference else None,
                        row.selected_value,
                    )
                    == selection
                    for row in selected
                ):
                    continue
                creation.select_choice_option(character_id, choice, option)
                changed = True
                selected = creation._selected_for_choice(
                    creation.characters.get_character(character_id), choice
                )
        if not changed:
            return


def _start_character(
    creation: CharacterCreationService, class_name: str, *, background: str = "Soldier"
) -> str:
    character = creation.create_draft()
    character_id = character.character_id
    creation.characters.rename_character(character_id, "Éowyn Test")
    creation.choose_species(character_id, _option(creation, "species", "Dwarf").identity)
    creation.choose_background(character_id, _option(creation, "background", background).identity)
    _choose_all(creation, character_id, "background_choices")
    creation.choose_starting_class(character_id, _option(creation, "class", class_name).identity)
    creation.ability_scores(
        character_id,
        "standard_array",
        dict(zip(ABILITIES, STANDARD_ARRAY, strict=True)),
    )
    _choose_all(creation, character_id, "class_choices")
    _choose_all(creation, character_id, "equipment")
    return character_id


def _choose_spell_groups(creation: CharacterCreationService, character_id: str) -> None:
    for group in creation.spell_groups(character_id):
        options = creation.spell_options(character_id, group, limit=50)
        assert len(options) >= group.count, group.label
        for option in options[: group.count]:
            assert option.reference is not None
            creation.select_spell(character_id, group, option.reference)
    _choose_all(creation, character_id, "spells")


def test_saved_draft_resumes_at_next_incomplete_step(production_creation) -> None:
    creation = production_creation
    draft = creation.create_draft()
    character_id = draft.character_id
    assert draft.state == "draft"
    assert creation.resume_step(character_id) == "name"

    creation.characters.rename_character(character_id, "  Zoë 雪  ")
    creation.choose_species(character_id, _option(creation, "species", "Dwarf").identity)
    creation.choose_background(character_id, _option(creation, "background", "Soldier").identity)
    assert creation.resume_step(character_id) == "background_choices"
    _choose_all(creation, character_id, "background_choices")

    resumed = CharacterCreationService(creation.database)
    saved = resumed.characters.get_character(character_id)
    assert saved.name == "Zoë 雪"
    assert saved.name_confirmed
    assert saved.species and saved.species.identity == _option(resumed, "species", "Dwarf").identity
    assert saved.background
    assert saved.background.identity == _option(resumed, "background", "Soldier").identity
    assert resumed.resume_step(character_id) == "class"
    assert resumed.characters.list_characters()[0].display_summary.startswith("Zoë 雪\nDraft")


def test_background_increases_keep_provenance_and_replace_only_background_state(
    production_creation,
) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Fighter")
    before = creation.characters.get_character(character_id)
    increased = creation.ability_scores(
        character_id,
        "standard_array",
        dict(zip(ABILITIES, STANDARD_ARRAY, strict=True)),
    )

    assert dict(increased.base_ability_scores) == dict(zip(ABILITIES, STANDARD_ARRAY, strict=True))
    assert increased.ability_modifications
    assert {row.source_kind for row in increased.ability_modifications} == {"background"}
    fighter_choices = [row for row in increased.choices if row.owner_type == "class"]
    assert fighter_choices

    creation.choose_background(character_id, _option(creation, "background", "Guide").identity)
    changed = creation.characters.get_character(character_id)
    assert changed.background and changed.background.name == "Guide"
    assert not changed.ability_modifications
    assert any(row.owner_type == "class" for row in changed.choices)
    assert before.starting_class == changed.starting_class


def test_fighter_level_one_creation_completes_with_exact_references_and_equipment(
    production_creation,
) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Fighter")
    character = creation.characters.get_character(character_id)

    assert character.total_level == 1
    assert character.levels[0].total_level == character.levels[0].class_level == 1
    assert character.starting_class and character.starting_class.name == "Fighter"
    assert character.species and character.species.kind == "species"
    assert character.background and character.background.kind == "background"
    assert dict(character.base_ability_scores) == dict(zip(ABILITIES, STANDARD_ARRAY, strict=True))
    assert character.equipment
    assert any(
        row.item_reference and row.item_reference.kind == "item" for row in character.equipment
    )
    assert character.spells == ()
    assert creation.validate_creation(character_id).is_valid

    completed = creation.complete_character(character_id)
    assert completed.state == "complete"
    assert completed.total_level == 1
    assert creation.resume_step(character_id) == "review"


def test_wizard_uses_level_one_spellbook_and_prepared_choices(production_creation) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Wizard")
    groups = creation.spell_groups(character_id)
    assert [(group.acquisition, group.count, group.spell_levels) for group in groups] == [
        ("cantrip", 3, (0,)),
        ("spellbook", 6, (1,)),
        ("prepared", 4, (1,)),
    ]

    cantrip_options = creation.spell_options(character_id, groups[0], limit=10)
    spellbook_options = creation.spell_options(character_id, groups[1], limit=10)
    assert len(cantrip_options) <= 10
    assert all(option.summary.startswith("Cantrip") for option in cantrip_options)
    assert all(option.summary.startswith("1st-level") for option in spellbook_options)
    _choose_spell_groups(creation, character_id)
    character = creation.characters.get_character(character_id)

    spellbook = {
        row.spell_reference.identity for row in character.spells if row.acquisition == "spellbook"
    }
    prepared = {
        row.spell_reference.identity for row in character.spells if row.acquisition == "prepared"
    }
    assert len(spellbook) == 6
    assert len(prepared) == 4
    assert prepared <= spellbook
    assert all(row.source_class_reference == character.starting_class for row in character.spells)
    assert creation.validate_creation(character_id).is_valid
    assert creation.complete_character(character_id).state == "complete"


def test_warlock_pact_magic_remains_distinct_from_standard_spellcasting(
    production_creation,
) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Warlock")
    groups = creation.spell_groups(character_id)
    assert [(group.acquisition, group.count) for group in groups] == [
        ("cantrip", 2),
        ("pact_magic", 2),
    ]
    assert not {group.acquisition for group in groups} & {"prepared", "known", "spellbook"}
    _choose_spell_groups(creation, character_id)
    spells = creation.characters.get_character(character_id).spells
    assert sum(row.acquisition == "cantrip" for row in spells) == 2
    assert sum(row.acquisition == "pact_magic" for row in spells) == 2
    assert all(row.spell_reference.kind == "spell" for row in spells)
    assert creation.validate_creation(character_id).is_valid
    assert creation.complete_character(character_id).state == "complete"


def test_species_choices_are_owner_scoped_and_metadata_driven(production_creation) -> None:
    creation = production_creation
    character = creation.create_draft()
    character_id = character.character_id
    human = _option(creation, "species", "Human")
    creation.choose_species(character_id, human.identity)
    choices = creation.choices_for_step(character_id, "species_choices")

    assert {choice.definition.kind.value for choice in choices} >= {"proficiency", "feat", "size"}
    skill_choice = next(
        choice
        for choice in choices
        if choice.definition.criteria
        and choice.definition.criteria.kind is OptionCriteriaKind.SKILL
    )
    assert len(creation.choice_options(character_id, skill_choice, limit=50)) == 18
    _choose_all(creation, character_id, "species_choices")
    saved = creation.characters.get_character(character_id)
    assert saved.species and saved.species.identity == human.identity
    assert any(row.owner_type == "species" for row in saved.choices)
    assert creation.resume_step(character_id) == "name"

    elf_character = creation.create_draft()
    elf_id = elf_character.character_id
    elf = _option(creation, "species", "Elf")
    creation.choose_species(elf_id, elf.identity)
    elf_choices = creation.choices_for_step(elf_id, "spells")
    assert any(
        choice.owner.name == "Elf" and choice.definition.kind.value == "spell"
        for choice in elf_choices
    )
    _choose_all(creation, elf_id, "species_choices")
    _choose_all(creation, elf_id, "spells")
    elf_saved = creation.characters.get_character(elf_id)
    assert elf_saved.species and elf_saved.species.identity == elf.identity
    assert any(row.owner_type == "species" for row in elf_saved.spells)


@pytest.mark.parametrize(
    ("owner_type", "replacement"),
    [
        ("species", "Aasimar"),
        ("background", "Guide"),
        ("class", "Warlock"),
    ],
)
def test_upstream_replacement_rolls_back_all_saved_rows_on_failure(
    production_creation,
    monkeypatch,
    owner_type: str,
    replacement: str,
) -> None:
    creation = production_creation
    character = creation.create_draft()
    character_id = character.character_id
    creation.characters.rename_character(character_id, "Rollback")
    creation.choose_species(character_id, _option(creation, "species", "Dwarf").identity)
    creation.choose_background(character_id, _option(creation, "background", "Soldier").identity)
    creation.choose_starting_class(character_id, _option(creation, "class", "Wizard").identity)
    _choose_all(creation, character_id, "background_choices")
    _choose_all(creation, character_id, "class_choices")
    before = creation.characters.get_character(character_id)

    def fail_after_reference_change(*_args, **_kwargs):
        raise RuntimeError("forced grant failure")

    monkeypatch.setattr(creation, "_apply_owner_grants", fail_after_reference_change)
    selector = {
        "species": creation.choose_species,
        "background": creation.choose_background,
        "class": creation.choose_starting_class,
    }[owner_type]
    with pytest.raises(RuntimeError, match="forced grant failure"):
        selector(character_id, _option(creation, owner_type, replacement).identity)

    assert creation.characters.get_character(character_id) == before


def test_class_replacement_removes_only_old_class_owned_spells_and_choices(
    production_creation,
) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Wizard")
    _choose_spell_groups(creation, character_id)
    before = creation.characters.get_character(character_id)
    background_choices = [row for row in before.choices if row.owner_type == "background"]
    assert before.spells

    creation.choose_starting_class(character_id, _option(creation, "class", "Fighter").identity)
    changed = creation.characters.get_character(character_id)
    assert changed.starting_class and changed.starting_class.name == "Fighter"
    assert changed.total_level == 1
    assert not changed.spells
    assert not any(row.owner_type == "class" for row in changed.choices)
    assert [row.resolution_id for row in changed.choices if row.owner_type == "background"] == [
        row.resolution_id for row in background_choices
    ]


def test_ability_score_methods_enforce_their_own_legal_ranges() -> None:
    scores = dict(zip(ABILITIES, STANDARD_ARRAY, strict=True))
    validate_ability_scores("standard_array", scores, require_complete=True)
    with pytest.raises(ValueError, match="only once"):
        validate_ability_scores("standard_array", {"str": 15, "dex": 15}, require_complete=False)
    with pytest.raises(ValueError, match="all six"):
        validate_ability_scores("standard_array", {"str": 15}, require_complete=True)

    legal_point_buy = {"str": 15, "dex": 15, "con": 15, "int": 8, "wis": 8, "cha": 8}
    assert point_buy_cost(legal_point_buy) == POINT_BUY_BUDGET == 27
    assert point_buy_cost({key: 8 for key in ABILITIES}) == 0
    assert POINT_BUY_COSTS[15] == 9
    with pytest.raises(ValueError, match="27 points"):
        validate_ability_scores(
            "point_buy",
            {"str": 15, "dex": 15, "con": 15, "int": 15, "wis": 8, "cha": 8},
            require_complete=True,
        )
    with pytest.raises(ValueError, match="exactly 27"):
        validate_ability_scores("point_buy", {key: 8 for key in ABILITIES}, require_complete=True)

    validate_ability_scores("manual", {key: 3 for key in ABILITIES}, require_complete=True)
    validate_ability_scores("manual", {key: 18 for key in ABILITIES}, require_complete=True)
    with pytest.raises(ValueError, match="between 3 and 18"):
        validate_ability_scores("manual", {"str": 19}, require_complete=False)


def test_equipment_and_spell_option_queries_are_bounded_and_exact(production_creation) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Fighter")
    mastery = next(
        choice
        for choice in creation.choices_for_step(character_id, "class_choices")
        if choice.definition.criteria
        and choice.definition.criteria.kind is OptionCriteriaKind.WEAPON_MASTERY
    )
    mastery_options = creation.choice_options(character_id, mastery, limit=7)
    assert len(mastery_options) == 7
    assert all(option.reference and option.reference.kind == "item" for option in mastery_options)
    assert all("+1 " not in option.label for option in mastery_options)

    creation.choose_starting_class(character_id, _option(creation, "class", "Wizard").identity)
    wizard_group = creation.spell_groups(character_id)[1]
    spell_page = creation.spell_options(character_id, wizard_group, limit=9)
    assert len(spell_page) <= 9
    assert all(option.reference and option.reference.edition == "2024" for option in spell_page)
    assert all(option.summary.startswith("1st-level") for option in spell_page)


def test_prose_only_spell_choice_blocks_completion_without_guessing(production_creation) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Fighter")
    creation.characters.set_character_state(character_id, "draft")
    creation.characters.add_feat(
        character_id,
        PublishedReference(
            "feat",
            "official-5etools-2024:feat/xphb/fey-touched-8c809edd1d",
            "Fey Touched",
            "2024",
        ),
        provenance_kind="other",
        provenance_label="Rules metadata test",
    )

    assert "spells" in {step.key for step in creation.steps(character_id)}
    assert creation.resume_step(character_id) == "spells"
    report = creation.validate_creation(character_id)
    assert not report.is_valid
    assert any(
        "Fey-Touched has a required spell choice" in issue.message for issue in report.issues
    )
    with pytest.raises(ValueError, match="Fey-Touched has a required spell choice"):
        creation.complete_character(character_id)


@pytest.mark.asyncio
async def test_characters_command_creates_resumes_renames_duplicates_and_confirms_delete(
    production_creation,
) -> None:
    creation = production_creation
    async with BrowserApp(creation.database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.25)
        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        palette = pilot.app.screen
        assert isinstance(palette, CommandPaletteScreen)
        rows = palette.query_one("#palette-list", ListView).children
        open_index = next(
            index
            for index, row in enumerate(rows)
            if isinstance(row, LaunchRow) and row.option.payload == "open-characters"
        )
        palette.query_one("#palette-list", ListView).index = open_index
        await pilot.press("enter")
        await pilot.pause(0.1)

        assert isinstance(pilot.app.screen, CharactersScreen)
        assert "No characters yet" in str(
            pilot.app.screen.query_one("#characters-empty", Static).renderable
        )
        await pilot.click("#character-create")
        await pilot.pause(0.15)
        wizard = pilot.app.screen
        assert isinstance(wizard, CharacterWizardScreen)
        character_id = wizard.character_id
        assert creation.characters.get_character(character_id).state == "draft"

        name_input = wizard.query_one("#builder-name", Input)
        name_input.focus()
        name_input.value = "  Ångström  "
        await pilot.press("ctrl+p")
        assert pilot.app.screen is wizard
        await pilot.press("enter")
        assert wizard.current_step == "species"
        assert creation.characters.get_character(character_id).name == "Ångström"

        await pilot.press("escape", "escape")
        await pilot.pause(0.15)
        manager = pilot.app.screen
        assert isinstance(manager, CharactersScreen)
        assert manager.summaries[0].name == "Ångström"
        await pilot.press("enter")
        await pilot.pause(0.15)
        sheet = pilot.app.screen
        assert isinstance(sheet, CharacterSheetScreen)
        assert sheet.character_id == character_id
        resume_index = next(
            (index for index, row in enumerate(sheet._visible_rows) if row.action == "resume"),
            None,
        )
        assert resume_index is not None, (
            sheet.section,
            sheet.character.state,
            [row.label for row in sheet._all_rows],
        )
        sheet.query_one("#sheet-rows", ListView).index = resume_index
        await pilot.press("enter")
        await pilot.pause(0.15)
        assert isinstance(pilot.app.screen, CharacterWizardScreen)
        assert pilot.app.screen.character_id == character_id
        assert pilot.app.screen.current_step == "species"

        await pilot.press("escape", "escape")
        await pilot.pause(0.15)
        assert isinstance(pilot.app.screen, CharacterSheetScreen)
        await pilot.press("escape")
        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        palette = pilot.app.screen
        assert isinstance(palette, CommandPaletteScreen)
        rows = palette.query_one("#palette-list", ListView).children
        open_index = next(
            index
            for index, row in enumerate(rows)
            if isinstance(row, LaunchRow) and row.option.payload == "open-characters"
        )
        palette.query_one("#palette-list", ListView).index = open_index
        await pilot.press("enter")
        await pilot.pause(0.1)
        manager = pilot.app.screen
        assert isinstance(manager, CharactersScreen)
        await pilot.click("#character-rename")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, TextEntryScreen)
        rename_input = pilot.app.screen.query_one("#note-input", Input)
        rename_input.value = "  Rëhn  "
        await pilot.press("enter")
        await pilot.pause(0.15)
        assert isinstance(pilot.app.screen, CharactersScreen)
        assert pilot.app.screen.summaries[0].name == "Rëhn"

        await pilot.click("#character-duplicate")
        await pilot.pause(0.15)
        manager = pilot.app.screen
        assert isinstance(manager, CharactersScreen)
        assert len(manager.summaries) == 2
        manager.query_one("#characters-list", ListView).index = 1
        await pilot.click("#character-delete")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, ConfirmationScreen)
        await pilot.click("#confirm-cancel")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, CharactersScreen)
        assert len(pilot.app.screen.summaries) == 2
        await pilot.click("#character-delete")
        await pilot.pause(0.1)
        assert isinstance(pilot.app.screen, ConfirmationScreen)
        await pilot.click("#confirm-ok")
        await pilot.pause(0.2)
        assert isinstance(pilot.app.screen, CharactersScreen)
        assert len(pilot.app.screen.summaries) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24), (60, 20)])
async def test_creation_steps_keep_controls_accessible_at_supported_sizes(
    production_creation, size: tuple[int, int]
) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Wizard")
    width, height = size

    async with BrowserApp(creation.database).run_test(size=size) as pilot:
        await pilot.pause(0.25)
        app = pilot.app
        app.action_open_characters()
        await pilot.pause(0.1)
        manager = app.screen
        assert isinstance(manager, CharactersScreen)
        for selector in ("#characters-title", "#characters-list", "#characters-actions"):
            region = manager.query_one(selector).region
            assert region.width > 0 and region.height > 0
            assert region.x >= 0 and region.y >= 0
            assert region.right <= width and region.bottom <= height

        app._open_character_wizard(character_id, step="abilities")
        await pilot.pause(0.1)
        wizard = app.screen
        assert isinstance(wizard, CharacterWizardScreen)
        for step in ("abilities", "class_choices", "equipment", "spells", "review"):
            wizard.current_step = step
            wizard.page_offset = 0
            wizard.selection_index = 0
            wizard._render_step()
            await pilot.pause(0.05)
            for selector in ("#builder-header", "#builder-actions", "#builder-footer"):
                region = wizard.query_one(selector).region
                assert region.width > 0 and region.height > 0, (size, step, selector)
                assert region.x >= 0 and region.y >= 0, (size, step, selector, region)
                assert region.right <= width and region.bottom <= height, (
                    size,
                    step,
                    selector,
                    region,
                )
            if step == "abilities":
                for ability in ABILITIES:
                    region = wizard.query_one(f"#ability-{ability}", Input).region
                    assert region.width > 0 and region.height > 0
                    assert region.bottom <= height
                for method in ("standard_array", "point_buy", "manual"):
                    region = wizard.query_one(f"#method-{method}", Button).region
                    assert region.width > 0 and region.height > 0
                    assert region.bottom <= height
            elif step == "review":
                review = wizard.query_one("#builder-review", Static).region
                assert review.height > 0 and review.bottom <= height
            else:
                options = wizard.query_one("#builder-options", ListView).region
                assert options.height > 0 and options.bottom <= height


@pytest.mark.asyncio
async def test_spell_reference_navigation_returns_to_the_same_builder_query_and_selection(
    production_creation,
) -> None:
    creation = production_creation
    character_id = _start_character(creation, "Wizard")
    async with BrowserApp(creation.database).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.25)
        pilot.app._open_character_wizard(
            character_id, step="spells", query="Fire Bolt", selected_index=0
        )
        await pilot.pause(0.15)
        wizard = pilot.app.screen
        assert isinstance(wizard, CharacterWizardScreen)
        reference = wizard._reference_for_highlight()
        assert reference and reference.name == "Fire Bolt"
        before = wizard.view_state()
        wizard._open_reference()
        await pilot.pause(0.35)

        assert pilot.app._current_detail is not None
        assert pilot.app._current_detail.identity == reference.identity
        await pilot.press("escape")
        await pilot.pause(0.35)
        resumed = pilot.app.screen
        assert isinstance(resumed, CharacterWizardScreen)
        assert resumed.view_state() == before
