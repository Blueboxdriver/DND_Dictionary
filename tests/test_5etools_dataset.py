from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dndref.importer import import_dataset, load_dataset
from dndref.models import validate_dataset
from dndref.search import SearchMode, SearchQuery, get_entry_detail, search
from dndref.storage.database import Database
from dndref.ui.app import BrowserApp, render_detail
from dndref.ui.class_detail import progression_table

SPEC = importlib.util.spec_from_file_location("build_5etools", "tools/build_5etools_dataset.py")
converter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(converter)
PACK = Path("src/dndref/datasets/official-5etools-2024")
SRD = Path("src/dndref/datasets/srd-5.2.1")
REVISION = "3a09c05a3a3be94423cd2b3c33936034eeae02f2"


def by_name(records, name, source=None):
    return next(r for r in records if r.name == name and (source is None or r.source == source))


@pytest.fixture(scope="module")
def loaded():
    return load_dataset(PACK)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def upstream(tmp_path):
    """Small invented rules fixture; exercises the same full pipeline offline."""
    root = tmp_path / "upstream"
    write_json(root / "package.json", {"version": "fixture"})
    files = {
        "books.json": {
            "book": [
                {"id": code, "name": name, "author": "Wizards RPG Team", "published": "2024-09-17"}
                for code, name in converter.BOOKS.items()
            ]
        },
        "book/book-xphb.json": {
            "data": [
                {
                    "type": "table",
                    "caption": "Character Advancement",
                    "rows": [[str(n), "0", "+2"] for n in range(1, 21)],
                },
                {"name": "Multiclassing", "entries": ["Fixture multiclass rule."]},
            ]
        },
        "items.json": {
            "item": [
                {
                    "name": "Test Charm",
                    "source": "XDMG",
                    "rarity": "rare",
                    "reqAttune": "by a Wizard",
                    "entries": ["A reference charm."],
                    "charges": 3,
                },
                {"name": "Homebrew", "source": "XPHB", "_isBrew": True},
                {"name": "Playtest", "source": "UATest"},
                {"name": "Legacy", "source": "PHB", "reprintedAs": ["Test Blade|XPHB"]},
            ]
        },
        "items-base.json": {
            "baseitem": [
                {
                    "name": "Test Blade",
                    "source": "XPHB",
                    "edition": "one",
                    "type": "M|XPHB",
                    "weapon": True,
                    "dmg1": "1d6",
                    "dmgType": "S",
                    "property": ["V|XPHB"],
                    "dmg2": "1d8",
                    "value": 100,
                    "weight": 2,
                },
                {
                    "name": "Test Mail",
                    "source": "XPHB",
                    "type": "MA|XPHB",
                    "ac": 14,
                    "strength": 13,
                    "stealth": True,
                },
            ],
            "itemType": [
                {"name": "Melee Weapon", "abbreviation": "M", "source": "XPHB"},
                {"name": "Medium Armor", "abbreviation": "MA", "source": "XPHB"},
            ],
            "itemProperty": [
                {
                    "abbreviation": "V",
                    "source": "XPHB",
                    "entries": [{"name": "Versatile", "entries": ["Fixture property definition."]}],
                }
            ],
        },
        "magicvariants.json": {
            "magicvariant": [
                {
                    "name": "+1 Test Weapon",
                    "requires": [{"weapon": True}],
                    "inherits": {
                        "source": "XDMG",
                        "rarity": "uncommon",
                        "namePrefix": "+1 ",
                        "bonusWeapon": "+1",
                        "entries": ["Bonus {=bonusWeapon}."],
                    },
                }
            ]
        },
        "feats.json": {
            "feat": [
                {
                    "name": "Test Feat",
                    "source": "XPHB",
                    "category": "G",
                    "ability": [{"choose": {"from": ["str", "dex"]}}],
                    "prerequisite": [{"level": 4, "ability": [{"str": 13}]}],
                    "entries": [
                        "Fixture feat.",
                        {"name": "First Benefit", "entries": ["Benefit rule."]},
                    ],
                }
            ]
        },
        "optionalfeatures.json": {},
        "fluff-items.json": {},
        "class/index.json": {"test": "class-test.json"},
        "class/class-test.json": {
            "class": [
                {
                    "name": "Wizard",
                    "source": "XPHB",
                    "edition": "one",
                    "hd": {"faces": 6},
                    "entries": ["Fixture class."],
                    "primaryAbility": [{"int": True}],
                    "proficiency": ["int", "wis"],
                    "startingProficiencies": {},
                    "startingEquipment": {"entries": ["A book."]},
                    "spellcastingAbility": "int",
                    "classTableGroups": [
                        {"colLabels": ["Resource"], "rows": [[i] for i in range(20)]}
                    ],
                    "classFeatures": ["Test Feature|Wizard|XPHB|1"],
                }
            ],
            "classFeature": [
                {
                    "name": "Test Feature",
                    "source": "XPHB",
                    "className": "Wizard",
                    "classSource": "XPHB",
                    "level": 1,
                    "entries": ["A class rule."],
                }
            ],
            "subclass": [
                {
                    "name": "Test School",
                    "shortName": "Test",
                    "source": "XPHB",
                    "className": "Wizard",
                    "classSource": "XPHB",
                    "subclassFeatures": ["Test School|Wizard|XPHB|Test|XPHB|3"],
                }
            ],
            "subclassFeature": [
                {
                    "name": "Test School",
                    "source": "XPHB",
                    "className": "Wizard",
                    "classSource": "XPHB",
                    "subclassSource": "XPHB",
                    "subclassShortName": "Test",
                    "level": 3,
                    "entries": [
                        "School introduction.",
                        {
                            "type": "refSubclassFeature",
                            "subclassFeature": "Child|Wizard|XPHB|Test|XPHB|3",
                        },
                    ],
                },
                {
                    "name": "Child",
                    "source": "XPHB",
                    "className": "Wizard",
                    "classSource": "XPHB",
                    "subclassSource": "XPHB",
                    "subclassShortName": "Test",
                    "level": 3,
                    "entries": ["Nested rule."],
                },
            ],
        },
        "spells/index.json": {"XPHB": "spells-xphb.json"},
        "spells/spells-xphb.json": {
            "spell": [
                {
                    "name": "Test Spell",
                    "source": "XPHB",
                    "level": 1,
                    "school": "V",
                    "time": [{"number": 1, "unit": "action"}],
                    "range": {"type": "point", "distance": {"type": "feet", "amount": 60}},
                    "components": {"v": True, "m": {"text": "a test crystal", "cost": 100}},
                    "duration": [
                        {
                            "type": "timed",
                            "duration": {"type": "minute", "amount": 1},
                            "concentration": True,
                        }
                    ],
                    "meta": {"ritual": True},
                    "entries": ["Deal {@damage 1d6} damage."],
                    "entriesHigherLevel": ["Add {@scaledamage 1d6|1-9|1d6} damage."],
                }
            ]
        },
        "generated/gendata-spell-source-lookup.json": {
            "xphb": {"test spell": {"class": {"XPHB": {"Wizard": True}}}}
        },
    }
    for name, value in files.items():
        write_json(root / "data" / name, value)
    write_json(root / "homebrew" / "items.json", {"item": [{"source": "XPHB"}]})
    return root


def test_full_converter_fixture_is_deterministic_and_reconciles(upstream):
    a, source = converter.build(upstream, REVISION)
    b, again = converter.build(upstream, REVISION)
    assert converter.artifacts(a, source, REVISION) == converter.artifacts(b, again, REVISION)
    assert validate_dataset(a) is a
    assert len(a.items.items) == 4
    assert {source.key: source.edition for source in a.manifest.sources}["XPHB"] == "2024"
    assert {r["reason"] for r in source.excluded} >= {
        "homebrew-or-playtest",
        "source-not-allowlisted",
    }
    assert len(source.inventory["item"]) == len(a.items.items)
    assert a.classes[0].progression.levels[-1].values["resource"] == "19"
    sub = a.classes[0].subclasses[0]
    assert [f.title for f in sub.features] == ["Test School", "Child"]
    assert [f.display_order for f in sub.features] == [0, 1]
    assert "Nested rule" not in a.classes[0].features[0].description
    spell = a.spells[0]
    assert spell.components.material_description == "a test crystal"
    assert spell.ritual and spell.concentration
    assert spell.higher_level_effects == "Add 1d6 damage."
    assert spell.class_references == [a.classes[0].local_key]
    assert (
        by_name(a.items.items, "Test Mail").armor.ac_expression
        == "14 + Dexterity modifier (maximum 2)"
    )
    assert by_name(a.items.items, "Test Blade").properties[0].value == "1d8"
    assert by_name(a.items.items, "+1 Test Blade").description == "Bonus +1."
    assert by_name(a.items.items, "Test Charm").attunement_prerequisite == "by a Wizard"
    assert a.feats[0].minimum_level == 4
    assert [b.heading for b in a.feats[0].benefits] == ["Ability Score Increase", "First Benefit"]


def test_source_revision_and_collision_policy(upstream):
    assert converter.exclusion({"source": "XPHB"}) is None
    for r in (
        {"source": "MyHomebrew"},
        {"source": "UA2024"},
        {"source": "PHB"},
        {"source": "XPHB", "edition": "classic"},
        {"source": "XPHB", "_isBrew": True},
        {"source": "XPHB", "isReprinted": True},
    ):
        assert converter.exclusion(r)
    first = {"name": "A-B", "source": "XPHB", "reprintedAs": ["A B|AU"]}
    second = {"name": "A B", "source": "AU"}
    s = converter.Snapshot(upstream)
    s.records["feat"] = [first, second]
    s.index.update({("feat", converter.uid(r)): r for r in [first, second]})
    assert s.select("feat") == [second]
    assert s.excluded[-1]["reason"] == "replaced-by:A B|AU"
    keys = [converter.local_key("feat", r) for r in [first, second, {**second, "source": "XPHB"}]]
    assert len(set(keys)) == 3
    assert converter.local_key("feat", first) == converter.local_key("feat", {**first, "page": 12})


def test_unknown_rules_fail_closed_and_templates_preserve_values(upstream):
    text = converter.Text(converter.Snapshot(upstream))
    assert text.render("{@b Nested {@spell Test|XPHB|spell name}}") == "**Nested spell name**"
    assert text.render("{@class Cleric|XPHB|War Domain|War|XPHB}") == "War Domain"
    assert (
        text.render("{@subclassFeature Test|Wizard|XPHB|Test|XPHB|3|XPHB|Test table}")
        == "Test table"
    )
    assert (
        text.render("{{getFullImmRes item.resist}}", {"resist": ["fire", "cold"]}) == "fire, cold"
    )
    with pytest.raises(ValueError, match="Unsupported user-visible"):
        text.render({"type": "newRuleKind", "rules": "Must not disappear"})
    with pytest.raises(ValueError, match="Missing template"):
        text.render("{{item.missing}}", {})
    with pytest.raises(ValueError, match="Unsupported inline"):
        text.render("{@unknownRule must not disappear}")


def test_copy_semantics_and_wrong_feature_owner(upstream):
    snapshot = converter.Snapshot(upstream)
    snapshot.index[("item", "original|xdmg")] = {
        "name": "Original",
        "source": "XDMG",
        "page": 5,
        "inherits": {"entries": ["First"], "rarity": "common"},
    }
    snapshot.index[("item", "revised|au")] = {
        "name": "Revised",
        "source": "AU",
        "_copy": {
            "name": "Original",
            "source": "XDMG",
            "_mod": {
                "inherits.entries": {"mode": "appendArr", "items": "Second"},
                "inherits.rarity": {"mode": "setProp", "value": "rare"},
            },
        },
    }
    revised = snapshot.resolve("item", "Revised|AU")
    assert revised["source"] == "AU" and "page" not in revised
    assert revised["inherits"] == {"entries": ["First", "Second"], "rarity": "rare"}
    with pytest.raises(ValueError, match="wrong class owner"):
        converter.convert_features(
            ["Test Feature|Wizard|XPHB|1"],
            "classFeature",
            "class/fighter",
            converter.Text(snapshot),
            {"name": "Fighter", "source": "XPHB"},
        )


def test_srd_content_files_remain_byte_identical():
    original = {
        "classes.json": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
        "feats.json": "f39d5d3252208dcb12cec4f85f8e3ebf92ad521b6447318ab3be3674f62688d9",
        "items.json": "2f13870d926ef7195639b88ffcace4a398a072205eda50e716609cc73d6f5595",
        "spells.json": "bbfbeadf66d3b75699e9d1000e2794d297fe220d0cdd1b2865c70eefe67b7146",
    }
    for name, expected in original.items():
        assert hashlib.sha256((SRD / name).read_bytes()).hexdigest() == expected
    manifest = json.loads((SRD / "manifest.json").read_text())
    assert manifest["sources"][0]["edition"] == "2024"


def test_production_inventory_reference_integrity_and_hashes(loaded):
    p = validate_dataset(loaded.pack)
    categories = {
        "items": [(r.local_key, r.name, r.source, None) for r in p.items.items],
        "properties": [(r.key, r.name, r.source, None) for r in p.items.properties],
        "spells": [(r.local_key, r.name, r.source, None) for r in p.spells],
        "feats": [(r.local_key, r.name, r.source, None) for r in p.feats],
        "classes": [(r.local_key, r.name, r.source, None) for r in p.classes],
        "subclasses": [
            (s.subclass_key, s.name, s.source, c.local_key) for c in p.classes for s in c.subclasses
        ],
        "class-features": [
            (f.feature_key, f.title, f.source, c.local_key) for c in p.classes for f in c.features
        ],
        "subclass-features": [
            (f.feature_key, f.title, f.source, s.subclass_key)
            for c in p.classes
            for s in c.subclasses
            for f in s.features
        ],
    }
    for category, rows in categories.items():
        inventory = json.loads((PACK / "inventory" / f"{category}.json").read_text())
        assert len(rows) == len(inventory)
        assert len({r[0] for r in rows}) == len(rows)
        # Property inventories preserve upstream abbreviations as display identity.
        if category == "properties":
            assert {r[0] for r in rows} == {r["stable_key"] for r in inventory}
        else:
            assert set(rows) == {
                (r["stable_key"], r["name"], r["source"], r.get("owner")) for r in inventory
            }
        assert all(r["conversion_status"] == "converted" for r in inventory)
    report = json.loads((PACK / "inventory/reconciliation.json").read_text())
    for name, expected in report["output_sha256"].items():
        assert hashlib.sha256((PACK / name).read_bytes()).hexdigest() == expected
    assert report["revision"] == REVISION
    assert all(s.class_references for s in p.spells)
    assert {s.source for s in p.spells} <= converter.BOOKS.keys()
    assert len(p.spells) == 444 and len(p.feats) == 179 and len(p.classes) == 13
    assert len(categories["subclasses"]) == 76
    assert len(categories["class-features"]) == 302
    assert len(categories["subclass-features"]) == 508
    assert not any(
        marker in (PACK / f"{name}.json").read_text()
        for name in ("items", "spells", "feats", "classes")
        for marker in ("{@", "{#", "{{", "{=")
    )


def test_source_edition_mapping_is_explicit_and_unknowns_are_reported():
    editions, warnings = converter.source_editions(["UNKNOWN", "XPHB", "PHB", "XDMG"])
    assert editions == {"PHB": "2014", "XDMG": "2024", "XPHB": "2024"}
    assert warnings == [
        {
            "source_code": "UNKNOWN",
            "message": "No edition mapping; source edition left unset.",
        }
    ]


def test_production_source_editions_match_explicit_mapping(loaded):
    sources = {source.key: source.edition for source in loaded.pack.manifest.sources}
    assert sources == {
        code: converter.SOURCE_EDITIONS[code] for code in sorted(converter.BOOKS)
    }
    assert sources["XPHB"] == sources["XDMG"] == "2024"


def test_production_representative_rules(loaded):
    p = loaded.pack
    sword = by_name(p.items.items, "Longsword", "XPHB")
    assert sword.weapon.damage_expression == "1d8"
    assert sword.weapon.versatile_damage == "1d10" and sword.weapon.mastery == "Sap"
    assert by_name(p.items.items, "Dagger", "XPHB").weapon.damage_type == "Piercing"
    assert by_name(p.items.items, "Longbow", "XPHB").weapon.range == "150/600 ft."
    assert by_name(p.items.items, "Plate Armor", "XPHB").armor.strength_requirement == 15
    assert by_name(p.items.items, "Shield", "XPHB").armor.ac_expression == "+2"
    assert by_name(p.items.items, "Bag of Holding", "XDMG").rarity == "uncommon"
    assert by_name(p.items.items, "Cloak of Protection", "XDMG").requires_attunement
    assert (
        "item grants a +1 AC bonus" in by_name(p.items.items, "+1 Plate Armor").armor.ac_expression
    )
    assert any(
        "Vecna" in s.heading and len(s.body) > 4000
        for s in by_name(p.items.items, "Eye of Vecna").sections
    )
    assert any(
        "quantities of ten or twenty" in s.body
        for s in by_name(p.items.items, "+1 Arrows (20)").sections
    )
    assert by_name(p.spells, "Acid Splash").level == 0
    assert by_name(p.spells, "Alarm").ritual
    assert by_name(p.spells, "Misty Step").casting_time == "1 bonus action"
    assert by_name(p.spells, "Antimagic Field").concentration
    fireball = by_name(p.spells, "Fireball")
    assert "1d6" in fireball.higher_level_effects
    assert len(fireball.class_references) >= 2
    assert fireball.components.material_description == "a ball of bat guano and sulfur"
    assert by_name(p.feats, "Alert", "XPHB").category == "Origin"
    grappler = by_name(p.feats, "Grappler", "XPHB")
    assert grappler.minimum_level == 4 and "13" in grappler.prerequisite
    assert grappler.ability_increase and len(grappler.benefits) == 4
    wizard = by_name(p.classes, "Wizard")
    barbarian = by_name(p.classes, "Barbarian")
    assert wizard.spellcasting_ability == "Intelligence" and barbarian.spellcasting_ability is None
    assert len(wizard.progression.columns) > len(barbarian.progression.columns)
    assert all(len(c.progression.levels) == 20 for c in p.classes)
    assert wizard.progression.levels[-1].values["proficiency-bonus"] == "+6"
    assert "13" in wizard.multiclassing
    fighter = by_name(p.classes, "Fighter")
    assert len([f for f in fighter.features if f.level == 1]) == 3
    assert all(f.display_order == i for c in p.classes for i, f in enumerate(c.features))


def test_import_replacement_coexistence_search_and_rendering(loaded, tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    dry = import_dataset(db, loaded, dry_run=True)
    assert dry.added == loaded.entry_count and not db.path.exists()
    srd = load_dataset(SRD)
    import_dataset(db, srd)
    assert import_dataset(db, loaded).added == loaded.entry_count
    assert import_dataset(db, loaded).is_noop
    with db.connection() as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM spell_classes").fetchone()[0] == sum(
            len(s.class_references) for s in loaded.pack.spells
        )
    both = [
        r for r in search(db, SearchQuery("spells", "Fireball")).results if r.name == "Fireball"
    ]
    assert len(both) == 2 and len({r.source_label for r in both}) == 2
    samples = {
        "items": [
            "Backpack",
            "Dagger",
            "Longsword",
            "Longbow",
            "Plate Armor",
            "Shield",
            "Bag of Holding",
            "Cloak of Protection",
            "Apparatus of Kwalish",
        ],
        "spells": ["Fireball", "Alarm", "Antimagic Field"],
        "feats": ["Alert", "Grappler"],
        "classes": ["Wizard", "Barbarian", "Warlock"],
    }
    for category, names in samples.items():
        for name in names:
            matches = search(db, SearchQuery(category, name)).results
            result = next(r for r in matches if r.stable_id.startswith(converter.DATASET_ID + ":"))
            detail = get_entry_detail(db, result.stable_id)
            assert name in render_detail(detail)
            if category == "classes":
                columns, rows = progression_table(detail)
                assert len(rows) == 20 and len(rows[0]) == len(columns)
                assert "+2" in rows[0] and "+6" in rows[-1]
    assert search(db, SearchQuery("classes", "Combat Superiority", SearchMode.ALL_TEXT)).total_count
    assert search(db, SearchQuery("items", "requires two hands", SearchMode.ALL_TEXT)).total_count
    replacement = tmp_path / "replacement"
    shutil.copytree(PACK, replacement)
    items = json.loads((replacement / "items.json").read_text())
    items["items"].pop()
    write_json(replacement / "items.json", items)
    assert import_dataset(db, load_dataset(replacement)).removed == 1
    assert import_dataset(db, srd).is_noop
    assert import_dataset(db, loaded).added == 1
    assert import_dataset(db, loaded).is_noop
    assert len(srd.pack.spells) == 339 and len(srd.pack.feats) == 17


def test_production_cli(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    for kind in ("CONFIG", "DATA", "CACHE", "STATE"):
        env[f"XDG_{kind}_HOME"] = str(tmp_path / kind.lower())
    for args in (
        ["validate", str(PACK)],
        ["import", str(PACK), "--dry-run"],
        ["import", str(SRD)],
        ["import", str(PACK)],
        ["import", str(PACK)],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "dndref", *args], env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
    assert "no changes" in result.stdout


@pytest.mark.asyncio
async def test_real_content_tui_and_progression(loaded, tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    import_dataset(db, loaded)
    async with BrowserApp(db).run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.4)
        for key, category in (("1", "items"), ("2", "spells"), ("3", "feats"), ("4", "classes")):
            await pilot.press(key)
            for _ in range(40):
                await pilot.pause(0.1)
                if (
                    pilot.app.state.selected_id
                    and pilot.app._detail_loaded_for == pilot.app.state.selected_id
                    and get_entry_detail(db, pilot.app.state.selected_id).category == category
                ):
                    break
            assert pilot.app._detail_loaded_for == pilot.app.state.selected_id
            detail = get_entry_detail(db, pilot.app.state.selected_id)
            assert detail.category == category
            assert detail.name in render_detail(detail)
        table = pilot.app.query_one("#progression-table")
        assert table.row_count == 20
        table.focus()
        for _ in range(40):
            await pilot.pause(0.1)
            if table.allow_horizontal_scroll:
                break
        assert table.max_scroll_x > 0
        await pilot.press("l")
        await pilot.pause()
        assert table.scroll_x > 0
        await pilot.resize_terminal(60, 24)
        await pilot.press("escape", "enter")
        await pilot.pause(0.3)
        assert pilot.app.state.selected_id
