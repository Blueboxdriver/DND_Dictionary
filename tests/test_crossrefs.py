from __future__ import annotations

import json
import shutil
from pathlib import Path

from dndref.crossrefs import CrossReferenceResolver
from dndref.importer import import_dataset, load_dataset
from dndref.search import SearchCategory
from dndref.storage.database import Database

FIXTURE = Path("tests/fixtures/dataset")


def _database(tmp_path: Path, editions: tuple[str, ...] = ("2024",)) -> Database:
    database = Database(tmp_path / "reference.sqlite3")
    for index, edition in enumerate(editions):
        pack = tmp_path / f"pack-{index}"
        shutil.copytree(FIXTURE, pack)
        manifest_path = pack / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["dataset_id"] = f"pack-{index}"
        manifest["sources"][0].update(title=f"Book {index}", edition=edition)
        manifest_path.write_text(json.dumps(manifest))
        import_dataset(database, load_dataset(pack))
    return database


def test_resolver_requires_type_name_and_same_edition(tmp_path: Path) -> None:
    resolver = CrossReferenceResolver(_database(tmp_path, ("2024", "2024", "2014")))
    same = resolver.resolve_reference(content_type="spell", name="Spark", edition="2024")
    assert len(same.candidates) == 2
    assert same.ambiguous and same.resolved is None
    assert {candidate.edition for candidate in same.candidates} == {"2024"}
    old = resolver.resolve_reference(content_type="spell", name="Spark", edition="2014")
    assert len(old.candidates) == 1
    assert {candidate.edition for candidate in old.candidates} == {"2014"}


def test_explicit_identity_normalization_missing_and_type_guard(tmp_path: Path) -> None:
    resolver = CrossReferenceResolver(_database(tmp_path))
    exact = resolver.resolve_reference(
        content_type=SearchCategory.SPELLS, name=" spark ", edition="2024"
    )
    assert len(exact.candidates) == 1
    target = exact.candidates[0]
    assert resolver.resolve_reference(
        content_type="spell", name="wrong name", edition="2014", stable_id=target.identity
    ).resolved == target
    assert resolver.resolve_reference(
        content_type="item", name="Spark", edition="2024", stable_id=target.identity
    ).candidates == ()
    assert resolver.resolve_reference(
        content_type="spell", name="Spark", edition="2024", stable_id="removed:spell/spark"
    ).candidates == ()
    assert resolver.resolve_reference(
        content_type="spell", name="missing", edition="2024"
    ).candidates == ()
    assert resolver.get_by_id("removed:spell/missing") is None


def test_monster_spell_matcher_is_scoped_to_edition_and_spellcasting_text(tmp_path: Path) -> None:
    resolver = CrossReferenceResolver(_database(tmp_path, ("2024", "2014")))
    matches = resolver.monster_spell_references(
        ("At will: Spark; 1/day: Comet Burst", "1st level (2 slots): Spark."),
        "2024",
    )
    assert {row.name for row in matches} == {"Spark", "Comet Burst"}
    assert {row.edition for row in matches} == {"2024"}
    assert resolver.monster_spell_references(
        ("The monster knows Spark and Comet Burst but lists no spells.",), "2024"
    ) == ()
    assert resolver.monster_spell_references(("Spark",), None) == ()


def test_feat_prerequisite_parser_requires_explicit_feat_label(tmp_path: Path) -> None:
    resolver = CrossReferenceResolver(_database(tmp_path))
    resolved = resolver.explicit_feat_prerequisite_references(
        "Level 4+; Feat: Quick Study", "2024"
    )
    assert len(resolved) == 1 and resolved[0].name == "Quick Study"
    assert resolver.explicit_feat_prerequisite_references(
        "Quick Study is helpful", "2024"
    ) == ()
