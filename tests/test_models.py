from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dndref.models import (
    ClassProgression,
    DatasetManifest,
    DatasetPack,
    DatasetValidationError,
    Feat,
    Item,
    ItemCatalog,
    Spell,
    validate_dataset,
)
from dndref.models.schema import generate_schemas

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "dataset"


def fixture_json(name: str) -> object:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def valid_pack() -> DatasetPack:
    return DatasetPack(
        manifest=fixture_json("manifest.json"),
        items=fixture_json("items.json"),
        spells=fixture_json("spells.json"),
        feats=fixture_json("feats.json"),
        classes=fixture_json("classes.json"),
    )


def test_manifest_fixture_and_defaults_validate() -> None:
    manifest = DatasetManifest.model_validate(fixture_json("manifest.json"))

    assert manifest.schema_version == "1.0"
    assert manifest.language == "en"
    assert manifest.dependencies == []


def test_manifest_rejects_unsupported_version_duplicate_source_and_dependency() -> None:
    manifest = fixture_json("manifest.json")
    assert isinstance(manifest, dict)

    with pytest.raises(ValidationError, match="schema_version"):
        DatasetManifest.model_validate({**manifest, "schema_version": "2.0"})

    duplicate_source = {**manifest, "sources": [manifest["sources"][0], manifest["sources"][0]]}
    with pytest.raises(ValidationError, match="unique keys"):
        DatasetManifest.model_validate(duplicate_source)

    dependency = {"dataset_id": "other-pack", "version": "1"}
    duplicate_dependency = {**manifest, "dependencies": [dependency, dependency]}
    with pytest.raises(ValidationError, match="unique dataset IDs"):
        DatasetManifest.model_validate(duplicate_dependency)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("local_key", "../unsafe", "local key"),
        ("name", "", "name"),
        ("image", "../art.png", "image reference"),
    ],
)
def test_common_identity_and_image_validation(field: str, value: str, message: str) -> None:
    item = {
        "local_key": "item/test",
        "name": "Test Item",
        "description": "A test item.",
        "source": "example-core",
        "type": "Gear",
    }
    item[field] = value

    with pytest.raises(ValidationError, match=message):
        Item.model_validate(item)


def test_item_fixture_covers_mundane_weapon_armor_magic_and_properties() -> None:
    catalog = ItemCatalog.model_validate(fixture_json("items.json"))
    by_key = {item.local_key: item for item in catalog.items}

    assert by_key["item/adventuring-pack"].kind.value == "mundane"
    assert by_key["item/rapier"].weapon is not None
    assert by_key["item/rapier"].properties[0].property_key == "property/finesse"
    assert by_key["item/chain-shirt"].armor is not None
    assert by_key["item/star-map"].requires_attunement is True


def test_item_rejects_inapplicable_or_incomplete_nested_details() -> None:
    base = {
        "local_key": "item/test",
        "name": "Test Item",
        "description": "A test item.",
        "source": "example-core",
        "type": "Weapon",
    }
    with pytest.raises(ValidationError, match="weapon details are required"):
        Item.model_validate({**base, "kind": "weapon"})
    with pytest.raises(ValidationError, match="armor details are not valid"):
        Item.model_validate({**base, "armor": {"armor_category": "Light", "ac_expression": "11"}})


def test_spell_fixture_covers_cantrip_leveled_components_and_class_references() -> None:
    spells = [Spell.model_validate(value) for value in fixture_json("spells.json")]

    assert spells[0].level == 0
    assert spells[0].components.material is False
    assert spells[1].level == 3
    assert spells[1].components.material_description == "a pinch of powdered glass"
    assert spells[1].class_references == ["class/wizard"]


def test_spell_rejects_invalid_level_and_material_representation() -> None:
    spell = fixture_json("spells.json")[0]
    with pytest.raises(ValidationError, match="less than or equal to 9"):
        Spell.model_validate({**spell, "level": 10})
    with pytest.raises(ValidationError, match="material_description"):
        Spell.model_validate({**spell, "components": {"material": True}})
    with pytest.raises(ValidationError, match="requires material=true"):
        Spell.model_validate(
            {**spell, "components": {"verbal": True, "material_description": "glass"}}
        )


def test_feat_fixture_preserves_ordered_benefits_and_validates_minimum_level() -> None:
    feat = Feat.model_validate(fixture_json("feats.json")[0])

    assert [benefit.display_order for benefit in feat.benefits] == [1, 2]
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        Feat.model_validate({**fixture_json("feats.json")[0], "minimum_level": 0})


def test_class_fixture_covers_features_progression_and_subclass() -> None:
    character_class = valid_pack().classes[0]

    assert character_class.hit_die == 6
    assert character_class.features[0].level == 1
    assert character_class.progression.levels[0].values["arcane_recovery"] == "1/day"
    assert character_class.subclasses[0].features[0].level == 2


def test_class_progression_rejects_duplicate_rows_and_undeclared_columns() -> None:
    column = {"key": "resource", "label": "Resource", "display_order": 1}
    with pytest.raises(ValidationError, match="unique keys"):
        ClassProgression.model_validate({"columns": [column, column]})
    with pytest.raises(ValidationError, match="duplicate rows"):
        ClassProgression.model_validate(
            {"levels": [{"level": 1, "values": {}}, {"level": 1, "values": {}}]}
        )
    with pytest.raises(ValidationError, match="undeclared columns"):
        ClassProgression.model_validate(
            {"columns": [column], "levels": [{"level": 1, "values": {"other": "+1"}}]}
        )


def test_class_feature_levels_and_duplicate_feature_keys_are_rejected() -> None:
    character_class = fixture_json("classes.json")[0]
    invalid_feature = {**character_class["features"][0], "level": 21}
    with pytest.raises(ValidationError, match="less than or equal to 20"):
        type(valid_pack().classes[0]).model_validate(
            {**character_class, "features": [invalid_feature]}
        )
    invalid_low_feature = {**character_class["features"][0], "level": 0}
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        type(valid_pack().classes[0]).model_validate(
            {**character_class, "features": [invalid_low_feature]}
        )

    duplicate = {**character_class, "features": [character_class["features"][0]] * 2}
    with pytest.raises(ValidationError, match="unique feature keys"):
        type(valid_pack().classes[0]).model_validate(duplicate)


def test_dataset_validation_checks_sources_duplicates_and_local_references() -> None:
    pack = valid_pack()
    validate_dataset(pack)

    broken_reference = pack.model_copy(deep=True)
    broken_reference.spells[0].class_references = ["class/missing"]
    with pytest.raises(DatasetValidationError, match=r"spells.json\[0\].class_references\[0\]"):
        validate_dataset(broken_reference)

    broken_source = pack.model_copy(deep=True)
    broken_source.spells[0].source = "missing-source"
    with pytest.raises(DatasetValidationError, match="unknown source"):
        validate_dataset(broken_source)


def test_dataset_validation_rejects_duplicate_entry_keys() -> None:
    pack = valid_pack()
    pack.items.items.append(pack.items.items[0].model_copy(update={"local_key": "spell/spark"}))
    with pytest.raises(DatasetValidationError, match="already used"):
        validate_dataset(pack)


def test_fixture_round_trip_is_json_compatible_and_stable() -> None:
    pack = validate_dataset(valid_pack())
    encoded = pack.model_dump(mode="json")
    decoded = DatasetPack.model_validate(encoded)

    assert decoded == pack
    assert json.loads(json.dumps(encoded)) == encoded


def test_json_schema_generation_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_paths = generate_schemas(first)
    second_paths = generate_schemas(second)

    assert [path.name for path in first_paths] == [
        "manifest.schema.json",
        "items.schema.json",
        "spells.schema.json",
        "feats.schema.json",
        "classes.schema.json",
    ]
    for first_path, second_path in zip(first_paths, second_paths, strict=True):
        assert first_path.read_bytes() == second_path.read_bytes()
        schema = json.loads(first_path.read_text(encoding="utf-8"))
        assert "properties" in schema or "$defs" in schema
