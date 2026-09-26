"""Dataset manifest and whole-pack validation."""

from __future__ import annotations

from typing import Literal

from pydantic import AnyHttpUrl, Field, model_validator

from .character_builder import CharacterBuilderCatalog, ReferenceKind
from .common import ContractModel, DatasetId, SourceMetadata, split_reference
from .glossary import GlossaryEntry

DATASET_SCHEMA_VERSION = "1.0"


class DatasetDependency(ContractModel):
    """A dataset required by a pack, without resolving it."""

    dataset_id: DatasetId
    version: str | None = Field(default=None, min_length=1)


class DatasetManifest(ContractModel):
    """The metadata needed to identify and install one JSON dataset pack."""

    schema_version: Literal["1.0"]
    dataset_id: DatasetId
    title: str = Field(min_length=1)
    version: str = Field(min_length=1)
    ruleset: str = Field(min_length=1)
    language: str = Field(default="en", min_length=2, max_length=16)
    license_identifier: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    origin_url: AnyHttpUrl | None = None
    sources: list[SourceMetadata] = Field(min_length=1)
    dependencies: list[DatasetDependency] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_manifest_keys(self) -> DatasetManifest:
        source_keys = [source.key for source in self.sources]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("manifest sources must have unique keys")
        dependency_ids = [dependency.dataset_id for dependency in self.dependencies]
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("manifest dependencies must have unique dataset IDs")
        if self.dataset_id in dependency_ids:
            raise ValueError("manifest cannot depend on itself")
        return self


class DatasetPack(ContractModel):
    """Parsed representation of the five files in a dataset directory."""

    manifest: DatasetManifest
    items: "ItemCatalog"
    spells: list["Spell"] = Field(default_factory=list)
    feats: list["Feat"] = Field(default_factory=list)
    classes: list["CharacterClass"] = Field(default_factory=list)
    monsters: list["Monster"] = Field(default_factory=list)
    conditions: list[GlossaryEntry] = Field(default_factory=list)
    rules: list[GlossaryEntry] = Field(default_factory=list)
    character_builder: CharacterBuilderCatalog | None = None


class DatasetValidationError(ValueError):
    """Raised for errors that require the complete pack rather than one record."""


def _check_unique_keys(errors: list[str], filename: str, entries: list[object]) -> set[str]:
    keys = [getattr(entry, "local_key") for entry in entries]
    seen: set[str] = set()
    for index, key in enumerate(keys):
        if key in seen:
            errors.append(f"{filename}[{index}].local_key: duplicate key '{key}'")
        seen.add(key)
    return set(keys)


def validate_dataset(pack: DatasetPack) -> DatasetPack:
    """Validate source ownership, entry identity uniqueness, and local references.

    Cross-dataset references are intentionally left unresolved. Milestone 4 owns
    dependency loading and resolution.
    """

    errors: list[str] = []
    source_keys = {source.key for source in pack.manifest.sources}
    source_editions = {source.key: source.edition for source in pack.manifest.sources}
    category_entries = {
        "items.json": pack.items.items,
        "spells.json": pack.spells,
        "feats.json": pack.feats,
        "classes.json": pack.classes,
        "monsters.json": pack.monsters,
        "conditions.json": pack.conditions,
        "rules.json": pack.rules,
    }
    all_keys: dict[str, str] = {}
    category_keys: dict[str, set[str]] = {}

    for filename, entries in category_entries.items():
        category_keys[filename] = _check_unique_keys(errors, filename, entries)
        for index, entry in enumerate(entries):
            key = getattr(entry, "local_key")
            previous = all_keys.get(key)
            if previous is not None:
                errors.append(
                    f"{filename}[{index}].local_key: key '{key}' is already used by {previous}"
                )
            else:
                all_keys[key] = f"{filename}[{index}]"
            source = getattr(entry, "source", None)
            if source not in source_keys:
                errors.append(f"{filename}[{index}].source: unknown source '{source}'")

    for index, property_definition in enumerate(pack.items.properties):
        if property_definition.source not in source_keys:
            errors.append(
                f"items.json.properties[{index}].source: unknown source "
                f"'{property_definition.source}'"
            )

    for index, monster in enumerate(pack.monsters):
        edition = source_editions.get(monster.source)
        if edition is None or edition in {"2014 rules", "2024 rules"}:
            errors.append(
                f"monsters.json[{index}].source: monster source requires a canonical edition"
            )

    for filename, entries in (("conditions.json", pack.conditions), ("rules.json", pack.rules)):
        for index, entry in enumerate(entries):
            edition = source_editions.get(entry.source)
            if edition not in {"2014", "2024"}:
                errors.append(
                    f"{filename}[{index}].source: "
                    f"{filename[:-5]} source requires a canonical edition"
                )

    glossary_keys = {
        "condition": {entry.local_key: entry for entry in pack.conditions},
        "rule": {entry.local_key: entry for entry in pack.rules},
    }
    for filename, entries in (
        ("items.json", pack.items.items),
        ("spells.json", pack.spells),
        ("feats.json", pack.feats),
        ("classes.json", pack.classes),
        ("monsters.json", pack.monsters),
        ("conditions.json", pack.conditions),
        ("rules.json", pack.rules),
    ):
        for index, entry in enumerate(entries):
            seen_references: set[tuple[str, str]] = set()
            for reference_index, reference in enumerate(entry.references):
                token = (reference.content_type, reference.target_key)
                if token in seen_references:
                    errors.append(
                        f"{filename}[{index}].references[{reference_index}]: duplicate reference"
                    )
                seen_references.add(token)
                target = glossary_keys[reference.content_type].get(reference.target_key)
                if target is None:
                    errors.append(
                        f"{filename}[{index}].references[{reference_index}]: "
                        f"unknown {reference.content_type} target '{reference.target_key}'"
                    )
                elif source_editions.get(entry.source) != source_editions.get(target.source):
                    errors.append(
                        f"{filename}[{index}].references[{reference_index}]: "
                        "target must have the same canonical edition"
                    )

    property_keys = {property_definition.key for property_definition in pack.items.properties}
    for index, item in enumerate(pack.items.items):
        for property_index, property_reference in enumerate(item.properties):
            if property_reference.property_key not in property_keys:
                errors.append(
                    "items.json[{}].properties[{}].property_key: unknown property '{}'".format(
                        index, property_index, property_reference.property_key
                    )
                )

    class_keys = category_keys["classes.json"]
    for index, spell in enumerate(pack.spells):
        for reference_index, reference in enumerate(spell.class_references):
            dataset_id, local_key = split_reference(reference, pack.manifest.dataset_id)
            if dataset_id == pack.manifest.dataset_id and local_key not in class_keys:
                errors.append(
                    f"spells.json[{index}].class_references[{reference_index}]: "
                    f"unknown class reference '{reference}'"
                )

    for index, character_class in enumerate(pack.classes):
        for feature_index, feature in enumerate(character_class.features):
            if feature.source is not None and feature.source not in source_keys:
                errors.append(
                    f"classes.json[{index}].features[{feature_index}].source: "
                    f"unknown source '{feature.source}'"
                )
        for subclass_index, subclass in enumerate(character_class.subclasses):
            if subclass.source not in source_keys:
                errors.append(
                    f"classes.json[{index}].subclasses[{subclass_index}].source: "
                    f"unknown source '{subclass.source}'"
                )
            elif source_editions.get(character_class.source) != source_editions.get(
                subclass.source
            ):
                errors.append(
                    f"classes.json[{index}].subclasses[{subclass_index}].source: "
                    "subclass and parent class must have the same edition"
                )
            for feature_index, feature in enumerate(subclass.features):
                if feature.source is not None and feature.source not in source_keys:
                    errors.append(
                        f"classes.json[{index}].subclasses[{subclass_index}].features["
                        f"{feature_index}].source: "
                        f"unknown source '{feature.source}'"
                    )

    catalog = pack.character_builder
    if catalog is not None:
        builder_classes = {str(row.class_key): row for row in catalog.classes}
        builder_subclasses = {str(row.subclass_key): row for row in catalog.subclasses}
        classes_by_key = {str(row.local_key): row for row in pack.classes}
        feats_by_key = {str(row.local_key): row for row in pack.feats}
        items_by_key = {str(row.local_key): row for row in pack.items.items}
        spells_by_key = {str(row.local_key): row for row in pack.spells}
        rules_by_key = {str(row.local_key): row for row in pack.rules}
        expected_builder_classes = {
            key for key, row in classes_by_key.items() if source_editions.get(row.source) == "2024"
        }
        if set(builder_classes) != expected_builder_classes:
            errors.append(
                "character-builder.json: class builder coverage does not match the 2024 classes"
            )
        for class_key, builder_class in builder_classes.items():
            source_class = classes_by_key.get(class_key)
            if source_class is None:
                errors.append(f"character-builder.json: unknown class '{class_key}'")
                continue
            if source_class.source != builder_class.source:
                errors.append(f"character-builder.json: class source mismatch for '{class_key}'")
            if source_editions.get(builder_class.source) != "2024":
                errors.append(
                    f"character-builder.json: class '{class_key}' is not from the 2024 edition"
                )
            source_subclasses = {
                str(subclass.subclass_key): subclass for subclass in source_class.subclasses
            }
            for subclass_key, builder_subclass in builder_subclasses.items():
                if str(builder_subclass.class_key) != class_key:
                    continue
                source_subclass = source_subclasses.get(subclass_key)
                if source_subclass is None:
                    errors.append(
                        f"character-builder.json: subclass '{subclass_key}' has the wrong parent"
                    )
                elif source_subclass.source != builder_subclass.source:
                    errors.append(
                        f"character-builder.json: subclass source mismatch for '{subclass_key}'"
                    )

        expected_subclasses = {
            str(subclass.subclass_key)
            for character_class in pack.classes
            if source_editions.get(character_class.source) == "2024"
            for subclass in character_class.subclasses
        }
        if set(builder_subclasses) != expected_subclasses:
            errors.append(
                "character-builder.json: subclass builder coverage does not match 2024 subclasses"
            )

        owner_sources = (
            [("species", str(row.species_key), row.source) for row in catalog.species]
            + [("background", str(row.background_key), row.source) for row in catalog.backgrounds]
            + [("feat", str(row.feat_key), row.source) for row in catalog.feats]
            + [
                ("optional feature", str(row.option_key), row.source)
                for row in catalog.optional_features
            ]
            + [("item", str(row.item_key), row.source) for row in catalog.equipment]
        )
        for kind, key, source in owner_sources:
            if source not in source_keys:
                errors.append(
                    f"character-builder.json: {kind} '{key}' has unknown source '{source}'"
                )
            elif source_editions.get(source) != "2024":
                errors.append(f"character-builder.json: {kind} '{key}' is not 2024 edition")
        if {str(row.feat_key) for row in catalog.feats} != {
            str(row.local_key) for row in pack.feats if source_editions.get(row.source) == "2024"
        }:
            errors.append("character-builder.json: feat builder coverage does not match 2024 feats")
        if {str(row.item_key) for row in catalog.equipment} - set(items_by_key):
            errors.append("character-builder.json: equipment metadata references an unknown item")

        reference_keys = {
            ReferenceKind.ITEM: set(items_by_key),
            ReferenceKind.SPELL: set(spells_by_key),
            ReferenceKind.FEAT: set(feats_by_key),
            ReferenceKind.CLASS: set(classes_by_key),
            ReferenceKind.RULE: set(rules_by_key),
            ReferenceKind.SUBCLASS: expected_subclasses,
            ReferenceKind.CLASS_FEATURE: {
                f"{class_key}#{feature.feature_key}"
                for class_key, character_class in classes_by_key.items()
                for feature in character_class.features
            },
            ReferenceKind.SUBCLASS_FEATURE: {
                f"{subclass.subclass_key}#{feature.feature_key}"
                for character_class in pack.classes
                for subclass in character_class.subclasses
                for feature in subclass.features
            },
            ReferenceKind.OPTIONAL_FEATURE: {
                str(row.option_key) for row in catalog.optional_features
            },
        }
        reference_fields = []
        for builder_class in catalog.classes:
            reference_fields.append(builder_class.model_dump(mode="python"))
        reference_fields.extend(row.model_dump(mode="python") for row in catalog.subclasses)
        reference_fields.extend(row.model_dump(mode="python") for row in catalog.species)
        reference_fields.extend(row.model_dump(mode="python") for row in catalog.backgrounds)
        reference_fields.extend(row.model_dump(mode="python") for row in catalog.feats)
        reference_fields.extend(row.model_dump(mode="python") for row in catalog.optional_features)
        reference_fields.extend(row.model_dump(mode="python") for row in catalog.equipment)

        def visit_builder_value(value: object, path: str) -> None:
            if isinstance(value, dict):
                if {"kind", "identity", "resolved"} <= set(value):
                    try:
                        kind = ReferenceKind(value["kind"])
                    except ValueError:
                        errors.append(f"character-builder.json:{path}: invalid reference kind")
                        return
                    if value["resolved"]:
                        identity = str(value["identity"])
                        dataset_id, separator, local_key = identity.partition(":")
                        if dataset_id != str(pack.manifest.dataset_id) or not separator:
                            errors.append(
                                f"character-builder.json:{path}: "
                                "cross-dataset or malformed reference"
                            )
                        elif local_key not in reference_keys[kind]:
                            errors.append(
                                f"character-builder.json:{path}: unresolved required "
                                f"{kind.value} reference '{identity}'"
                            )
                    return
                for child_key, child in value.items():
                    visit_builder_value(child, f"{path}.{child_key}")
            elif isinstance(value, list):
                for child_index, child in enumerate(value):
                    visit_builder_value(child, f"{path}[{child_index}]")

        for record_index, record in enumerate(reference_fields):
            visit_builder_value(record, f"record[{record_index}]")

    if errors:
        raise DatasetValidationError("dataset validation failed:\n" + "\n".join(errors))
    return pack


from .character_class import CharacterClass  # noqa: E402  (type-only cycle resolution)
from .feat import Feat  # noqa: E402
from .item import ItemCatalog  # noqa: E402
from .monster import Monster  # noqa: E402
from .spell import Spell  # noqa: E402

DatasetPack.model_rebuild()
