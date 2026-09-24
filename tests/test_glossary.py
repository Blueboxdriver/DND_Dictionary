from __future__ import annotations

import json
import shutil
from pathlib import Path

from dndref.cli import initialize_application
from dndref.config import ApplicationPaths
from dndref.crossrefs import CrossReferenceResolver
from dndref.importer import (
    import_dataset,
    legacy_glossaryless_hash,
    load_dataset,
)
from dndref.personal import PersonalDataService
from dndref.search import SearchCategory, SearchQuery, SearchService, parse_universal_query
from dndref.storage.database import MIGRATIONS_DIR, Database
from dndref.ui.app import BrowserApp, render_detail

PACK = Path("src/dndref/datasets/official-5etools-2024")
FIXTURE = Path("tests/fixtures/dataset")


def _installed(tmp_path: Path):
    database = Database(tmp_path / "dndref.sqlite3")
    database.initialize()
    loaded = load_dataset(PACK)
    report = import_dataset(database, loaded)
    assert import_dataset(database, loaded).is_noop
    return database, loaded, report


def test_condition_and_rule_records_search_detail_and_source_counts(tmp_path: Path) -> None:
    database, loaded, report = _installed(tmp_path)
    assert report.added == loaded.entry_count == 3725
    service = SearchService(database)
    conditions = service.search(SearchQuery("conditions", "prone", editions=("2024",)))
    rules = service.search(SearchQuery("rules", "cover", editions=("2024",)))
    assert [result.name for result in conditions.results] == ["Prone"]
    assert [result.name for result in rules.results] == ["Cover"]
    section_filtered = service.search(
        SearchQuery("rules", "cover", rule_sections=("Core Rules",))
    )
    assert [result.name for result in section_filtered.results] == ["Cover"]
    condition = service.get_entry_detail(conditions.results[0].identity)
    rule = service.get_entry_detail(rules.results[0].identity)
    assert condition is not None and "Condition · 2024" in render_detail(condition)
    assert rule is not None and "Rule · 2024" in render_detail(rule)
    assert "Core Rules" in render_detail(rule)
    assert "|" not in render_detail(condition)
    universal = service.search_all("condition: prone")
    assert [result.name for result in universal.results] == ["Prone"]
    assert [result.name for result in service.search_all("rule:cover").results] == ["Cover"]
    with database.connection() as connection:
        rows = connection.execute(
            "SELECT kind,COUNT(*) FROM entries WHERE source_id=(SELECT id FROM sources "
            "WHERE source_key='XPHB' AND dataset_id='official-5etools-2024') GROUP BY kind"
        ).fetchall()
    assert {str(row["kind"]): int(row[1]) for row in rows}["condition"] == 15
    assert {str(row["kind"]): int(row[1]) for row in rows}["rule"] == 30
    source = next(
        item for item in service.list_source_contents()
        if item.source.identity.dataset_id == "official-5etools-2024"
        and item.source.identity.source_key == "XPHB"
    )
    assert source.counts[SearchCategory.CONDITIONS] == 15
    assert source.counts[SearchCategory.RULES] == 30


def test_prefix_parser_and_literal_unknown_prefixes() -> None:
    assert parse_universal_query("condition:prone edition:2024").text == "prone"
    assert parse_universal_query("condition: prone").category is SearchCategory.CONDITIONS
    assert parse_universal_query("rule:concentration").category is SearchCategory.RULES
    assert parse_universal_query("rule: cover source:PHB").source == "PHB"
    parsed = parse_universal_query("unknown:prone")
    assert parsed.category is None and parsed.text == "unknown:prone"


def test_grouping_keeps_same_name_glossary_entries_in_separate_editions(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "dndref.sqlite3")
    database.initialize()
    for dataset_id, edition in (("glossary-2014", "2014"), ("glossary-2024", "2024")):
        path = tmp_path / dataset_id
        shutil.copytree(FIXTURE, path)
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["dataset_id"] = dataset_id
        manifest["sources"][0]["edition"] = edition
        manifest_path.write_text(json.dumps(manifest))
        (path / "conditions.json").write_text(json.dumps([
            {
                "local_key": "condition/prone", "name": "Prone",
                "description": f"Prone rules text for {edition}.",
                "source": "example-core",
            }
        ]))
        import_dataset(database, load_dataset(path))
    page = SearchService(database).search_grouped(
        SearchQuery("conditions", "prone", editions=("2014", "2024"))
    )
    assert page.total_count == 2
    assert {entry.source_edition for entry in page.results} == {"2014", "2024"}


def test_condition_links_are_exact_edition_scoped_and_conservative(tmp_path: Path) -> None:
    database, _, _ = _installed(tmp_path)
    resolver = CrossReferenceResolver(database)
    service = SearchService(database)
    grease = service.search(SearchQuery("spells", "Grease", editions=("2024",))).results[0]
    assert {
        target.name for target in resolver.structured_references(grease.identity)
    } >= {"Prone"}
    animal_lord = service.search(
        SearchQuery("monsters", "Animal Lord", editions=("2024",))
    ).results[0]
    assert {
        target.name for target in resolver.structured_references(animal_lord.identity)
    } >= {"Frightened"}
    linked = resolver.explicit_condition_references(
        "The target has the prone condition, then falls.", "2024"
    )
    assert [target.name for target in linked] == ["Prone"]
    assert resolver.explicit_condition_references("A prone creature moves slowly.", "2024") == ()
    assert resolver.explicit_condition_references("notprone condition", "2024") == ()
    assert resolver.explicit_condition_references("the poisoned condition", "2014") == ()
    resolution = resolver.resolve_reference(
        content_type="condition", name="Prone", edition="2014"
    )
    assert resolution.candidates == ()
    assert resolver.resolve_reference(
        content_type="condition", name="Prone", edition="2024"
    ).resolved is not None
    rules = resolver.explicit_rule_references(
        "The target has Advantage while crossing Difficult Terrain; take an action, attack, "
        "move, and rest afterward.",
        "2024",
    )
    assert {target.name for target in rules} == {"Advantage", "Difficult Terrain"}
    assert resolver.explicit_rule_references("attack, move, action, rest", "2024") == ()


def test_status_and_weapon_mastery_records_are_rules_with_typed_links(tmp_path: Path) -> None:
    database, _, _ = _installed(tmp_path)
    service = SearchService(database)
    resolver = CrossReferenceResolver(database)
    concentration = service.search(
        SearchQuery("rules", "Concentration", editions=("2024",))
    ).results[0]
    assert service.get_entry_detail(concentration.identity).fields["section"] == "Statuses"
    concentration_spell_id = None
    mastery_item_id = None
    with database.connection() as connection:
        concentration_spell_id = connection.execute(
            "SELECT source.dataset_id || ':' || source.local_key FROM entries source "
            "JOIN spells ON spells.entry_id=source.id "
            "JOIN entry_references rel ON rel.source_entry_id=source.id "
            "JOIN entries target ON target.id=rel.target_entry_id "
            "WHERE target.normalized_name='concentration' LIMIT 1"
        ).fetchone()[0]
        mastery_item_id = connection.execute(
            "SELECT source.dataset_id || ':' || source.local_key FROM entries source "
            "JOIN items ON items.entry_id=source.id "
            "JOIN entry_references rel ON rel.source_entry_id=source.id "
            "JOIN entries target ON target.id=rel.target_entry_id "
            "WHERE target.rule_section='Weapon Masteries' LIMIT 1"
        ).fetchone()[0]
    assert any(
        target.name == "Concentration"
        for target in resolver.structured_references(str(concentration_spell_id))
    )
    assert any(
        target.category is SearchCategory.RULES
        and target.name in {"Cleave", "Graze", "Nick", "Push", "Sap", "Slow", "Topple", "Vex"}
        for target in resolver.structured_references(str(mastery_item_id))
    )


def test_conditions_and_rules_persist_in_personal_data_and_reimport(tmp_path: Path) -> None:
    database, loaded, _ = _installed(tmp_path)
    service = SearchService(database)
    condition = service.search(SearchQuery("conditions", "prone")).results[0]
    rule = service.search(SearchQuery("rules", "cover")).results[0]
    condition_detail = service.get_entry_detail(condition.identity)
    rule_detail = service.get_entry_detail(rule.identity)
    assert condition_detail and rule_detail
    personal = PersonalDataService(database)
    personal.set_favorite(condition_detail, True)
    collection_id = personal.create_collection("Combat reference")
    personal.set_collection_membership(collection_id, rule_detail, True)
    personal.add_tag(condition.identity, "combat")
    personal.save_note(rule_detail, "Quick terrain lookup")
    import_dataset(database, loaded)
    assert personal.is_favorite(condition.identity)
    assert personal.collections_for(rule.identity) == ("Combat reference",)
    assert personal.tags_for(condition.identity) == ("combat",)
    assert personal.note_for(rule.identity) == "Quick terrain lookup"


def test_milestone_18_database_gets_glossary_from_startup_upgrade(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "migrations-007"
    legacy_dir.mkdir()
    for migration in MIGRATIONS_DIR.glob("00[1-7]_*.sql"):
        shutil.copy(migration, legacy_dir)
    dataset_dir = tmp_path / "old-bundle"
    shutil.copytree(PACK, dataset_dir)
    (dataset_dir / "conditions.json").unlink()
    (dataset_dir / "rules.json").unlink()
    for filename in ("items.json", "spells.json", "feats.json", "classes.json", "monsters.json"):
        path = dataset_dir / filename
        payload = json.loads(path.read_text())

        def strip_references(value):
            if isinstance(value, dict):
                return {
                    key: strip_references(item)
                    for key, item in value.items()
                    if key != "references"
                }
            if isinstance(value, list):
                return [strip_references(item) for item in value]
            return value

        path.write_text(json.dumps(strip_references(payload)))
    paths = ApplicationPaths.for_root(tmp_path / "app")
    database_path = paths.database_path
    old_database = Database(database_path, legacy_dir)
    old_database.initialize()
    import_dataset(old_database, load_dataset(dataset_dir))
    current_pack = load_dataset(PACK)
    with old_database.connection() as connection:
        connection.execute(
            "UPDATE datasets SET content_hash=? WHERE dataset_id=?",
            (legacy_glossaryless_hash(current_pack), "official-5etools-2024"),
        )
        connection.commit()

    initialize_application(paths, image_mode="off")
    counts = SearchService(Database(database_path))
    assert counts.search(SearchQuery("conditions")).total_count == 15
    assert counts.search(SearchQuery("rules")).total_count == 30
    with Database(database_path).connection() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 9


async def test_category_commands_and_glossary_tabs_fit_responsive_sizes(tmp_path: Path) -> None:
    database, _, _ = _installed(tmp_path)
    app = BrowserApp(database)
    command_names = {command.name for command in app.commands.commands}
    assert {"Open Conditions", "Open Rules"} <= command_names
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        for width, height in ((140, 40), (100, 30), (80, 24), (60, 20)):
            await pilot.resize_terminal(width, height)
            await pilot.pause(0.1)
            for category in (SearchCategory.CONDITIONS, SearchCategory.RULES):
                button = pilot.app.query_one(f"#tab-{category.value}")
                assert button.region.width > 0
                assert button.region.x >= 0
                assert button.region.right <= width
        await pilot.press("7")
        await pilot.pause(0.3)
        assert pilot.app.category is SearchCategory.CONDITIONS
        assert pilot.app._current_detail is not None
        assert pilot.app._current_detail.category is SearchCategory.CONDITIONS
        await pilot.press("8")
        await pilot.pause(0.3)
        assert pilot.app.category is SearchCategory.RULES
        assert pilot.app._current_detail is not None
        assert pilot.app._current_detail.category is SearchCategory.RULES


async def test_monster_condition_navigation_restores_exact_origin(tmp_path: Path) -> None:
    database, _, _ = _installed(tmp_path)
    service = SearchService(database)
    monster = service.search(
        SearchQuery("monsters", "Animal Lord", editions=("2024",))
    ).results[0]
    async with BrowserApp(database).run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        app = pilot.app
        app._navigate_to_identity(monster.identity)
        await pilot.pause(0.5)
        assert app._current_detail is not None
        assert app._current_detail.identity == monster.identity
        condition_index = next(
            index for index, target in enumerate(app._related_targets)
            if target.category is SearchCategory.CONDITIONS and target.name == "Frightened"
        )
        app.query_one("#related-list").focus()
        app.query_one("#related-list").index = condition_index
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert app._current_detail is not None
        assert app._current_detail.name == "Frightened"
        assert app._current_detail.category is SearchCategory.CONDITIONS
        await pilot.press("alt+left")
        await pilot.pause(0.4)
        assert app._current_detail is not None
        assert app._current_detail.identity == monster.identity
        await pilot.press("alt+right")
        await pilot.pause(0.4)
        assert app._current_detail is not None
        assert app._current_detail.category is SearchCategory.CONDITIONS
        assert app._current_detail.name == "Frightened"
