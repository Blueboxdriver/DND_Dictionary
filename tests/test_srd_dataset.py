from __future__ import annotations

import json
from pathlib import Path

import pytest

from dndref.importer import import_dataset, load_dataset
from dndref.search import SearchMode, SearchQuery, search
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp, render_detail

SRD_DATASET = Path("src/dndref/datasets/srd-5.2.1")
# Counts are the reviewed inventories extracted from the official SRD 5.2.1 PDF.
EXPECTED_SPELL_COUNT = 339
EXPECTED_FEAT_COUNT = 17


def loaded_srd():
    return load_dataset(SRD_DATASET)


def by_name(records, name: str):
    return next(record for record in records if record.name == name)


def test_srd_inventories_reconcile_exactly() -> None:
    loaded = loaded_srd()
    spell_inventory = json.loads(
        (SRD_DATASET / "inventory" / "spells.json").read_text(encoding="utf-8")
    )
    feat_inventory = json.loads(
        (SRD_DATASET / "inventory" / "feats.json").read_text(encoding="utf-8")
    )
    assert len(loaded.pack.spells) == len(spell_inventory) == EXPECTED_SPELL_COUNT
    assert len(loaded.pack.feats) == len(feat_inventory) == EXPECTED_FEAT_COUNT
    assert {record.local_key for record in loaded.pack.spells} == {
        record["local_key"] for record in spell_inventory
    }
    assert {record.local_key for record in loaded.pack.feats} == {
        record["local_key"] for record in feat_inventory
    }
    assert all(record["conversion_status"] == "reviewed" for record in spell_inventory)
    assert all(record["conversion_status"] == "reviewed" for record in feat_inventory)


def test_srd_spell_conversion_invariants_and_representative_fields() -> None:
    loaded = loaded_srd()
    assert all(spell.source == "srd-5-2-1" for spell in loaded.pack.spells)
    assert all(not spell.class_references for spell in loaded.pack.spells)
    assert all(0 <= spell.level <= 9 for spell in loaded.pack.spells)

    acid_splash = by_name(loaded.pack.spells, "Acid Splash")
    assert acid_splash.level == 0
    assert acid_splash.components.verbal is True
    assert acid_splash.components.somatic is True
    assert acid_splash.components.material is False

    alarm = by_name(loaded.pack.spells, "Alarm")
    assert alarm.ritual is True
    assert alarm.components.material_description == "a bell and silver wire"

    antimagic_field = by_name(loaded.pack.spells, "Antimagic Field")
    assert antimagic_field.concentration is True
    assert antimagic_field.components.material_description == "iron filings"

    acid_arrow = by_name(loaded.pack.spells, "Acid Arrow")
    assert acid_arrow.higher_level_effects is not None
    assert "increases by 1d4" in acid_arrow.higher_level_effects

    divine_smite = by_name(loaded.pack.spells, "Divine Smite")
    assert divine_smite.components.verbal is True
    assert divine_smite.components.somatic is False
    assert divine_smite.components.material is False


def test_srd_feat_conversion_preserves_prerequisites_and_benefit_order() -> None:
    loaded = loaded_srd()
    assert all(feat.source == "srd-5-2-1" for feat in loaded.pack.feats)
    assert all(feat.name and feat.category for feat in loaded.pack.feats)

    alert = by_name(loaded.pack.feats, "Alert")
    assert [benefit.heading for benefit in alert.benefits] == [
        "Initiative Proficiency",
        "Initiative Swap",
    ]

    grappler = by_name(loaded.pack.feats, "Grappler")
    assert grappler.prerequisite == "Level 4+, Strength or Dexterity 13+"
    assert grappler.minimum_level == 4
    assert [benefit.heading for benefit in grappler.benefits] == [
        "Ability Score Increase",
        "Punch and Grab",
        "Attack Advantage",
        "Fast Wrestler",
    ]

    magic_initiate = by_name(loaded.pack.feats, "Magic Initiate")
    assert magic_initiate.repeatable is True
    assert magic_initiate.benefits[-1].heading == "Repeatable"

    boon = by_name(loaded.pack.feats, "Boon of Spell Recall")
    assert boon.minimum_level == 19
    assert boon.prerequisite == "Level 19+, Spellcasting Feature"
    assert boon.ability_increase is not None


def test_srd_import_is_deterministic_and_reimport_is_noop(tmp_path: Path) -> None:
    loaded = loaded_srd()
    assert loaded.content_hash == load_dataset(SRD_DATASET).content_hash
    database = Database(tmp_path / "data" / "dndref.sqlite3")

    dry_run = import_dataset(database, loaded, dry_run=True)
    assert dry_run.added == 356
    assert dry_run.total == 356
    assert not database.path.exists()

    first = import_dataset(database, loaded)
    second = import_dataset(database, load_dataset(SRD_DATASET))
    assert first.added == 356
    assert second.is_noop
    with database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM entries WHERE dataset_id = ?", ("srd-5-2-1",)
        ).fetchone()[0] == 356


def test_srd_search_indexes_spell_and_feat_content(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    import_dataset(database, loaded_srd())

    assert search(database, SearchQuery("spells", "Acid Arrow")).results[0].name == "Acid Arrow"
    assert search(
        database,
        SearchQuery("spells", "powdered rhubarb", SearchMode.ALL_TEXT),
    ).results[0].name == "Acid Arrow"
    assert search(
        database,
        SearchQuery("spells", "increases by 1d4", SearchMode.ALL_TEXT),
    ).total_count >= 1
    assert search(database, SearchQuery("feats", "Alert")).results[0].name == "Alert"
    assert search(
        database,
        SearchQuery("feats", "Initiative Swap", SearchMode.ALL_TEXT),
    ).results[0].name == "Alert"
    assert search(
        database,
        SearchQuery("feats", "Spellcasting Feature", SearchMode.ALL_TEXT),
    ).results[0].name == "Boon of Spell Recall"


@pytest.mark.asyncio
async def test_srd_spell_and_feat_details_render_in_production_browser(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "dndref.sqlite3")
    import_dataset(database, loaded_srd())
    async with BrowserApp(database).run_test(size=(80, 24)) as pilot:
        async def wait_for_detail(identity: str) -> None:
            for _ in range(40):
                await pilot.pause(0.1)
                if (pilot.app.state.selected_id == identity
                        and pilot.app._detail_loaded_for == identity):
                    return

        await wait_for_detail("srd-5-2-1:spell/acid-arrow")
        assert pilot.app.state.selected_id == "srd-5-2-1:spell/acid-arrow"
        assert pilot.app._detail_loaded_for == pilot.app.state.selected_id
        await pilot.press("3")
        await wait_for_detail("srd-5-2-1:feat/ability-score-improvement")
        assert pilot.app.state.selected_id == "srd-5-2-1:feat/ability-score-improvement"
        assert pilot.app._detail_loaded_for == pilot.app.state.selected_id
        detail = pilot.app.search_service.get_entry_detail(pilot.app.state.selected_id)
        assert detail is not None
        assert "Increase one ability score" in render_detail(detail)
