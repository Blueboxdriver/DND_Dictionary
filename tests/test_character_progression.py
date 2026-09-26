from __future__ import annotations

from pathlib import Path

import pytest
from test_character_creation import PRODUCTION, _choose_spell_groups, _start_character
from textual.widgets import ListView, Static

from dndref.character_creation import CharacterCreationService
from dndref.character_progression import CharacterProgressionService
from dndref.importer import import_dataset, load_dataset
from dndref.models.character import PublishedReference
from dndref.models.character_builder import ChoiceKind
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp
from dndref.ui.character_progression import CharacterProgressionScreen


@pytest.fixture
def progression_services(tmp_path: Path):
    database = Database(tmp_path / "progression.sqlite3")
    import_dataset(database, load_dataset(PRODUCTION))
    creation = CharacterCreationService(database)
    progression = CharacterProgressionService(database, creation.characters, creation)
    return database, creation, progression


def _complete_character(
    creation: CharacterCreationService,
    class_name: str = "Fighter",
    *,
    scores: dict[str, int] | None = None,
    species_name: str | None = None,
) -> str:
    character_id = _start_character(creation, class_name)
    if species_name is not None:
        species = next(
            row for row in creation.list_options("species", query=species_name) 
            if row.name == species_name
        )
        creation.choose_species(character_id, species.identity)
    if scores is not None:
        creation.ability_scores(character_id, "point_buy", scores)
    _choose_spell_groups(creation, character_id)
    creation.complete_character(character_id)
    return character_id


def _class_ref(creation: CharacterCreationService, name: str) -> PublishedReference:
    option = next(row for row in creation.list_options("class", query=name) if row.name == name)
    return PublishedReference("class", option.identity, option.name, option.edition)


def _resolve_pending(
    service: CharacterProgressionService,
    draft_id: str,
    *,
    preferred: dict[ChoiceKind, str] | None = None,
) -> None:
    preferred = preferred or {}
    for _ in range(40):
        preview = service.get_preview(draft_id)
        changed = False
        for group in preview.spell_choices:
            while True:
                fresh = service.get_preview(draft_id)
                current = next(item for item in fresh.spell_choices if item.key == group.key)
                if len(current.selected) >= current.count:
                    break
                options = service.get_spell_options(draft_id, current.key, limit=50)
                assert options, current.label
                service.select_spell(draft_id, current.key, options[0])
                changed = True
        preview = service.get_preview(draft_id)
        pending = next(
            (item for item in preview.pending_choices if len(item.selected) < item.count),
            None,
        )
        if pending is None:
            if not changed:
                return
            continue
        query = preferred.get(pending.kind, "")
        options = service.get_choice_options(draft_id, pending.key, query=query, limit=50)
        if not options and query:
            options = service.get_choice_options(draft_id, pending.key, limit=50)
        selected_keys = set(pending.selected)
        selected = next(
            (
                row
                for row in options
                if row.status == "satisfied" and row.selection_key not in selected_keys
            ),
            None,
        )
        assert selected is not None, (pending, options)
        service.apply_pending_choice(draft_id, pending.key, selected.selection_key)
    raise AssertionError("progression choices did not settle")


def _commit(
    service: CharacterProgressionService,
    character_id: str,
    class_identity: str,
    *,
    hp: str = "fixed_average",
    roll: int | None = None,
    preferred: dict[ChoiceKind, str] | None = None,
):
    preview = service.start_level_preview(character_id, class_identity)
    _resolve_pending(service, preview.draft_id, preferred=preferred)
    service.set_hp_choice(preview.draft_id, hp, roll)
    ready = service.get_preview(preview.draft_id)
    assert ready.is_valid, ready.issues
    return service.commit_level(preview.draft_id)


def test_same_class_history_subclass_hp_and_latest_level_undo(progression_services) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(creation, species_name="Halfling")
    fighter = _class_ref(creation, "Fighter")
    before = progression.characters.get_character(character_id)

    _commit(progression, character_id, fighter.identity)
    assert progression.characters.get_character(character_id).total_level == 2
    assert len(progression.characters.get_character(character_id).choices) == len(
        before.choices
    )
    _commit(progression, character_id, fighter.identity, hp="rolled", roll=5)

    character = progression.characters.get_character(character_id)
    assert [(row.total_level, row.class_level) for row in character.levels] == [
        (1, 1),
        (2, 2),
        (3, 3),
    ]
    assert character.hp_choices[-1].choice_kind == "rolled"
    assert character.hp_choices[-1].amount == 5
    assert len(character.subclasses) == 1

    removed = progression.undo_last_level(character_id)
    character = progression.characters.get_character(character_id)
    assert removed.total_level == 3
    assert character.total_level == 2
    assert [(row.total_level, row.amount) for row in character.hp_choices] == [(2, 6)]
    assert character.subclasses == ()


def test_subclass_candidates_use_exact_parent_and_same_edition_sources(
    progression_services,
) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(creation)
    fighter = _class_ref(creation, "Fighter")
    _commit(progression, character_id, fighter.identity)
    preview = progression.start_level_preview(character_id, fighter.identity)
    choice = next(item for item in preview.pending_choices if item.kind is ChoiceKind.SUBCLASS)
    all_options = progression.get_choice_options(preview.draft_id, choice.key, limit=50)

    assert all(item.reference and item.reference.kind == "subclass" for item in all_options)
    assert all(
        item.reference.identity.split(":subclass:", 1)[1].startswith("class/xphb/fighter-")
        for item in all_options
    )
    assert {item.reference.edition for item in all_options if item.reference} == {"2024"}
    assert {item.label for item in all_options} >= {"Arcane Archer", "Banneret"}

    with _database.connection() as db:
        wizard_row = db.execute(
            "SELECT parent.local_key,s.subclass_key,s.name,source.edition "
            "FROM subclasses s JOIN entries parent ON parent.id=s.class_id "
            "JOIN sources source ON source.id=s.source_id WHERE s.name='Evoker' "
            "AND source.edition='2024' LIMIT 1"
        ).fetchone()
    assert wizard_row is not None
    wizard_subclass = PublishedReference(
        "subclass",
        f"official-5etools-2024:subclass:{wizard_row[0]}:{wizard_row[1]}",
        str(wizard_row[2]),
        str(wizard_row[3]),
    )
    with pytest.raises(ValueError, match="does not belong"):
        progression.characters.set_subclass(
            character_id,
            fighter.identity,
            wizard_subclass,
            3,
        )
    progression.cancel(preview.draft_id)


def test_multiclass_prerequisite_failure_does_not_mutate_character(progression_services) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(creation)
    warlock = _class_ref(creation, "Warlock")
    before = progression.characters.get_character(character_id)
    option = next(
        row
        for row in progression.get_multiclass_options(character_id)
        if row.class_reference.identity == warlock.identity
    )

    assert option.status == "unsatisfied"
    assert "CHA" in option.reason
    with pytest.raises(ValueError, match="CHA"):
        progression.start_level_preview(character_id, warlock.identity)
    assert progression.characters.get_character(character_id) == before


def test_three_class_progression_keeps_entry_hp_saves_and_standard_slots(
    progression_services,
) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(
        creation,
        scores={"str": 13, "dex": 8, "con": 13, "int": 13, "wis": 13, "cha": 14},
    )
    fighter = _class_ref(creation, "Fighter")
    wizard = _class_ref(creation, "Wizard")
    cleric = _class_ref(creation, "Cleric")
    before_saves = {
        row.ability
        for row in progression.derived.derive(
            progression.characters.get_character(character_id)
        ).saving_throws
        if row.proficiency == "proficient"
    }

    wizard_preview = progression.start_level_preview(character_id, wizard.identity)
    assert wizard_preview.is_new_multiclass
    assert {group.acquisition for group in wizard_preview.spell_choices} >= {
        "cantrip",
        "spellbook",
    }
    _resolve_pending(progression, wizard_preview.draft_id)
    progression.set_hp_choice(wizard_preview.draft_id, "fixed_average")
    wizard_preview = progression.get_preview(wizard_preview.draft_id)
    assert wizard_preview.derived_after.spell_slots
    progression.commit_level(wizard_preview.draft_id)
    wizard_hp = progression.characters.get_character(character_id).hp_choices[-1]
    assert wizard_hp.class_reference.identity == wizard.identity
    assert wizard_hp.amount == 4

    _commit(progression, character_id, cleric.identity)
    saved = progression.characters.get_character(character_id)
    derived = progression.derived.derive(saved)
    after_saves = {row.ability for row in derived.saving_throws if row.proficiency == "proficient"}
    assert saved.total_level == 3
    assert saved.class_levels == {fighter.identity: 1, wizard.identity: 1, cleric.identity: 1}
    assert saved.class_summary == "Fighter 1 / Wizard 1 / Cleric 1"
    assert after_saves == before_saves
    assert derived.spell_slots


def test_multiclass_entry_grants_do_not_grant_starting_saving_throws(
    progression_services,
) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(
        creation,
        "Wizard",
        scores={"str": 13, "dex": 9, "con": 13, "int": 14, "wis": 13, "cha": 12},
    )
    wizard = _class_ref(creation, "Wizard")
    barbarian = _class_ref(creation, "Barbarian")
    before = progression.derived.derive(progression.characters.get_character(character_id))
    before_saves = {
        row.ability for row in before.saving_throws if row.proficiency == "proficient"
    }

    _commit(progression, character_id, barbarian.identity)
    after = progression.derived.derive(progression.characters.get_character(character_id))
    after_saves = {
        row.ability for row in after.saving_throws if row.proficiency == "proficient"
    }
    proficiencies = {(row.kind, row.key) for row in after.proficiencies}
    assert before_saves == after_saves == {"int", "wis"}
    assert ("weapon", "martial") in proficiencies
    assert ("armor", "shield") in proficiencies
    assert progression.characters.get_character(character_id).class_levels == {
        wizard.identity: 1,
        barbarian.identity: 1,
    }


def test_asi_choice_preserves_base_scores_and_feat_prerequisites_are_structured(
    progression_services,
) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(creation, species_name="Halfling")
    fighter = _class_ref(creation, "Fighter")
    for _ in range(2):
        _commit(progression, character_id, fighter.identity)

    preview = progression.start_level_preview(character_id, fighter.identity)
    feat_choice = next(item for item in preview.pending_choices if item.kind is ChoiceKind.FEAT)
    illegal_actor = progression.get_choice_options(
        preview.draft_id, feat_choice.key, query="Actor", limit=10
    )
    assert illegal_actor
    assert all(item.status == "unsatisfied" for item in illegal_actor)
    assert any("CHA" in item.reason for item in illegal_actor)

    asi = progression.get_choice_options(
        preview.draft_id,
        feat_choice.key,
        query="Ability Score Improvement",
        limit=10,
    )
    asi_option = next(item for item in asi if item.status == "satisfied")
    preview = progression.apply_pending_choice(
        preview.draft_id, feat_choice.key, asi_option.selection_key
    )
    ability_choice = next(
        item for item in preview.pending_choices if item.kind is ChoiceKind.ABILITY_SCORE
    )
    increase = next(
        item
        for item in progression.get_choice_options(
            preview.draft_id, ability_choice.key, query="+2 CON", limit=10
        )
        if item.status == "satisfied"
    )
    preview = progression.apply_pending_choice(
        preview.draft_id, ability_choice.key, increase.selection_key
    )
    _resolve_pending(progression, preview.draft_id)
    progression.set_hp_choice(preview.draft_id, "fixed_average")
    ready = progression.get_preview(preview.draft_id)
    before_scores = {row.ability: row.final for row in ready.derived_before.ability_scores}
    after_scores = {row.ability: row.final for row in ready.derived_after.ability_scores}
    assert after_scores["con"] == before_scores["con"] + 2
    assert ready.derived_after.hit_points.maximum is not None, ready.derived_after.hit_points
    assert ready.derived_before.hit_points.maximum is not None, ready.derived_before.hit_points
    assert ready.derived_after.hit_points.maximum > ready.derived_before.hit_points.maximum
    saved = progression.commit_level(preview.draft_id)
    assert dict(saved.base_ability_scores)["int"] == 12
    assert (
        progression.derived.derive(saved).hit_points.maximum
        == ready.derived_after.hit_points.maximum
    )
    modification = next(
        row
        for row in saved.ability_modifications
        if row.ability == "con" and row.character_level == 4
    )
    assert modification.owner_type == "feat"
    assert modification.character_level == 4
    progression.undo_last_level(character_id)
    rolled_back = progression.characters.get_character(character_id)
    assert rolled_back.total_level == 3
    assert rolled_back.base_ability_scores == saved.base_ability_scores
    assert rolled_back.ability_modifications == saved.ability_modifications[:-1]
    assert rolled_back.feats == saved.feats[:-1]


def test_warlock_and_wizard_keep_pact_and_standard_slots_separate(progression_services) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(
        creation,
        scores={"str": 13, "dex": 8, "con": 13, "int": 13, "wis": 13, "cha": 14},
    )
    warlock = _class_ref(creation, "Warlock")
    wizard = _class_ref(creation, "Wizard")

    _commit(progression, character_id, warlock.identity)
    wizard_preview = progression.start_level_preview(character_id, wizard.identity)
    assert wizard_preview.derived_after.spell_slots
    assert wizard_preview.derived_after.pact_magic
    _resolve_pending(progression, wizard_preview.draft_id)
    progression.set_hp_choice(wizard_preview.draft_id, "fixed_average")
    ready = progression.get_preview(wizard_preview.draft_id)
    expected_standard = ready.derived_after.spell_slots
    expected_pact = ready.derived_after.pact_magic
    progression.commit_level(wizard_preview.draft_id)

    derived = progression.derived.derive(progression.characters.get_character(character_id))
    assert derived.spell_slots == expected_standard
    assert derived.pact_magic == expected_pact
    assert derived.spell_slots and derived.pact_magic
    progression.undo_last_level(character_id)
    undone = progression.characters.get_character(character_id)
    assert undone.total_level == 2
    assert undone.class_levels == {
        _class_ref(creation, "Fighter").identity: 1,
        warlock.identity: 1,
    }
    assert any(row.source_class_reference.identity == warlock.identity for row in undone.spells)
    assert all(row.source_class_reference.identity != wizard.identity for row in undone.spells)


def test_standard_spell_access_advances_with_its_class_level(progression_services) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(
        creation,
        "Wizard",
        scores={"str": 8, "dex": 10, "con": 12, "int": 14, "wis": 14, "cha": 14},
    )
    wizard = _class_ref(creation, "Wizard")
    for _ in range(3):
        _commit(progression, character_id, wizard.identity)

    preview = progression.start_level_preview(character_id, wizard.identity)
    change = next(item for item in preview.spell_progression if item.owner_name == "Wizard")
    assert change.accessible_spell_levels_before == (1, 2)
    assert change.accessible_spell_levels_after == (1, 2, 3)
    assert all(group.max_spell_level == 3 for group in preview.spell_choices)
    progression.cancel(preview.draft_id)


def test_shared_slots_do_not_unlock_higher_level_spells_for_level_one_tracks(
    progression_services,
) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(
        creation,
        "Wizard",
        scores={"str": 8, "dex": 10, "con": 12, "int": 14, "wis": 14, "cha": 14},
    )
    cleric = _class_ref(creation, "Cleric")
    druid = _class_ref(creation, "Druid")
    warlock = _class_ref(creation, "Warlock")
    _commit(progression, character_id, cleric.identity)
    _commit(progression, character_id, druid.identity)
    preview = progression.start_level_preview(character_id, warlock.identity)
    assert any(slot.spell_level == 2 for slot in preview.derived_after.spell_slots)
    for profile in preview.derived_after.spellcasting_profiles:
        if profile.class_reference:
            assert profile.class_level == 1
            assert profile.accessible_spell_levels == (1,)
    pact_group = next(group for group in preview.spell_choices if group.acquisition == "pact_magic")
    assert pact_group.spell_levels == (1,)
    assert pact_group.max_spell_level == 1
    progression.cancel(preview.draft_id)


def test_cancel_and_transaction_rollback_leave_no_partial_levelup(
    progression_services, monkeypatch
):
    _database, creation, progression = progression_services
    character_id = _complete_character(
        creation,
        scores={"str": 13, "dex": 8, "con": 13, "int": 13, "wis": 13, "cha": 14},
    )
    warlock = _class_ref(creation, "Warlock")
    before = progression.characters.get_character(character_id)

    draft = progression.start_level_preview(character_id, warlock.identity)
    _resolve_pending(progression, draft.draft_id)
    progression.set_hp_choice(draft.draft_id, "fixed_average")
    first = progression.get_preview(draft.draft_id)
    assert progression.get_preview(draft.draft_id) == first
    progression.cancel(draft.draft_id)
    assert progression.characters.get_character(character_id) == before

    draft = progression.start_level_preview(character_id, warlock.identity)
    _resolve_pending(progression, draft.draft_id)
    progression.set_hp_choice(draft.draft_id, "fixed_average")
    original_add_spell = progression.characters.add_spell
    calls = 0

    def fail_after_spell_write(*args, **kwargs):
        nonlocal calls
        result = original_add_spell(*args, **kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("forced progression commit failure")
        return result

    monkeypatch.setattr(progression.characters, "add_spell", fail_after_spell_write)
    with pytest.raises(RuntimeError, match="forced progression commit failure"):
        progression.commit_level(draft.draft_id)
    assert progression.characters.get_character(character_id) == before


def test_draft_and_level_cap_block_progression(progression_services) -> None:
    database, creation, progression = progression_services
    draft = creation.create_draft()
    with pytest.raises(ValueError, match="Draft character"):
        progression.start_level_preview(
            draft.character_id, "official-5etools-2024:class/xphb/fighter-5264e16e85"
        )

    character_id = _complete_character(creation)
    fighter = _class_ref(creation, "Fighter")
    with database.transaction() as connection:
        for total_level in range(2, 21):
            class_level = total_level
            connection.execute(
                "INSERT INTO user_character_levels "
                "(character_id,total_level,class_identity,class_name,resulting_class_level,"
                "edition) "
                "VALUES (?,?,?,?,?,?)",
                (character_id, total_level, fighter.identity, fighter.name, class_level, "2024"),
            )
            if total_level > 1:
                connection.execute(
                    "INSERT INTO user_character_hp_choices "
                    "(character_id,total_level,class_identity,class_level,choice_kind,amount,"
                    "edition) "
                    "VALUES (?,?,?,?,'fixed_average',6,'2024')",
                    (character_id, total_level, fighter.identity, class_level),
                )
    saved = progression.characters.get_character(character_id)
    assert saved.total_level == 20
    assert all(
        row.status == "unsatisfied" for row in progression.get_advancement_options(character_id)
    )
    with pytest.raises(ValueError, match="maximum"):
        progression.start_level_preview(character_id, fighter.identity)


def test_advancement_options_cache_derived_prerequisite_state_until_character_changes(
    progression_services, monkeypatch
) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(creation)
    original_derive = progression.derived.derive
    calls = 0

    def counted_derive(character):
        nonlocal calls
        calls += 1
        return original_derive(character)

    monkeypatch.setattr(progression.derived, "derive", counted_derive)
    first = progression.get_advancement_options(character_id)
    assert progression.get_advancement_options(character_id) == first
    assert calls == 1
    progression.characters.rename_character(character_id, "Updated Character")
    progression.get_advancement_options(character_id)
    assert calls == 2


def test_level_nineteen_can_commit_as_level_twenty(progression_services) -> None:
    _database, creation, progression = progression_services
    character_id = _complete_character(creation)
    fighter = _class_ref(creation, "Fighter")
    for _ in range(18):
        _commit(progression, character_id, fighter.identity)
    assert progression.characters.get_character(character_id).total_level == 19
    _commit(progression, character_id, fighter.identity)
    assert progression.characters.get_character(character_id).total_level == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24), (60, 20)])
async def test_progression_class_hp_and_review_fit_supported_sizes(
    progression_services, size: tuple[int, int]
) -> None:
    database, creation, progression = progression_services
    character_id = _complete_character(creation)
    fighter = _class_ref(creation, "Fighter")
    before = progression.characters.get_character(character_id)

    async with BrowserApp(database).run_test(size=size) as pilot:
        await pilot.pause(0.1)
        pilot.app._open_character_progression(character_id)
        await pilot.pause(0.1)
        screen = pilot.app.screen
        assert isinstance(screen, CharacterProgressionScreen)
        rows = screen.query_one("#progress-list", ListView)
        assert rows.region.width > 0 and rows.region.height > 0
        assert len(screen._rows) >= 3 and len(rows.children) >= 3
        fighter_index = next(
            index
            for index, option in enumerate(screen.service.get_advancement_options(character_id))
            if option.class_reference.identity == fighter.identity
        )
        rows.index = fighter_index
        assert rows.index == fighter_index
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert screen.step_key == "hp", (
            rows.index,
            screen.focused,
            screen.query_one("#progress-status", Static).renderable,
            [(row.kind, getattr(row.payload, "status", None)) for row in screen._rows],
        )
        assert screen.query_one("#progress-status", Static).region.width > 0
        rows = screen.query_one("#progress-list", ListView)
        assert len(rows.children) == 2
        rows.index = 0
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert screen.step_key == "review"
        assert any(row.kind == "commit" for row in screen._rows)
        await pilot.press("escape")
        await pilot.pause(0.1)

    assert progression.characters.get_character(character_id) == before


@pytest.mark.asyncio
async def test_subclass_reference_back_restores_levelup_choice_state(progression_services) -> None:
    database, creation, progression = progression_services
    character_id = _complete_character(creation)
    fighter = _class_ref(creation, "Fighter")
    _commit(progression, character_id, fighter.identity)
    before = progression.characters.get_character(character_id)

    async with BrowserApp(database).run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.1)
        app = pilot.app
        wizard = app.character_progression.start_level_preview(character_id, fighter.identity)
        choice = next(item for item in wizard.pending_choices if item.kind is ChoiceKind.SUBCLASS)
        option = next(
            item
            for item in app.character_progression.get_choice_options(
                wizard.draft_id, choice.key, query="Banneret", limit=10
            )
            if item.label == "Banneret" and item.status == "satisfied"
        )
        app._open_character_progression(
            character_id,
            context=(wizard.draft_id, f"choice:{choice.key}", "Banneret", 0, 0),
        )
        await pilot.pause(0.1)
        screen = app.screen
        assert isinstance(screen, CharacterProgressionScreen)
        await pilot.press("ctrl+i")
        await pilot.pause(0.15)
        assert app._current_detail is not None
        assert app._current_detail.identity == option.reference.identity
        await pilot.press("alt+left")
        await pilot.pause(0.2)
        resumed = app.screen
        assert isinstance(resumed, CharacterProgressionScreen)
        assert resumed.step_key == f"choice:{choice.key}"
        assert resumed.search_query == "Banneret"
        assert [row.payload[1].label for row in resumed._rows] == ["Banneret"]
        await pilot.press("escape")
        await pilot.pause(0.1)

    assert progression.characters.get_character(character_id) == before
