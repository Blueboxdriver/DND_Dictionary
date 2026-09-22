"""Dataset manifest and whole-pack validation."""

from __future__ import annotations

from typing import Literal

from pydantic import AnyHttpUrl, Field, model_validator

from .common import ContractModel, DatasetId, SourceMetadata, split_reference

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
    category_entries = {
        "items.json": pack.items.items,
        "spells.json": pack.spells,
        "feats.json": pack.feats,
        "classes.json": pack.classes,
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
            for feature_index, feature in enumerate(subclass.features):
                if feature.source is not None and feature.source not in source_keys:
                    errors.append(
                        f"classes.json[{index}].subclasses[{subclass_index}].features["
                        f"{feature_index}].source: "
                        f"unknown source '{feature.source}'"
                    )

    if errors:
        raise DatasetValidationError("dataset validation failed:\n" + "\n".join(errors))
    return pack


from .character_class import CharacterClass  # noqa: E402  (type-only cycle resolution)
from .feat import Feat  # noqa: E402
from .item import ItemCatalog  # noqa: E402
from .spell import Spell  # noqa: E402

DatasetPack.model_rebuild()
