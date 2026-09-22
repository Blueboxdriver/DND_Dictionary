"""Structured item, weapon, armor, and reusable property contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from .common import ContractModel, EntryBase, LocalKey, SourceKey


class ItemKind(StrEnum):
    MUNDANE = "mundane"
    WEAPON = "weapon"
    ARMOR = "armor"
    MAGIC_ITEM = "magic_item"


class ItemRarity(StrEnum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    VERY_RARE = "very_rare"
    LEGENDARY = "legendary"
    ARTIFACT = "artifact"


class ItemCost(ContractModel):
    """An exact source value retained alongside the display string."""

    amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=1)


class ItemProperty(ContractModel):
    """A reusable property definition declared once in an item dataset file."""

    key: LocalKey
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: SourceKey


class ItemPropertyReference(ContractModel):
    """An item-to-property link with optional item-specific display text."""

    property_key: LocalKey
    value: str | None = None


class WeaponDetails(ContractModel):
    damage_expression: str = Field(min_length=1)
    damage_type: str = Field(min_length=1)
    range: str = Field(min_length=1)
    versatile_damage: str | None = None
    mastery: str | None = None


class ArmorDetails(ContractModel):
    armor_category: str = Field(min_length=1)
    ac_expression: str = Field(min_length=1)
    strength_requirement: int | None = Field(default=None, ge=0)
    stealth_disadvantage: bool = False


class Item(EntryBase):
    """A mundane item, weapon, armor entry, or magic item."""

    kind: ItemKind = ItemKind.MUNDANE
    type: str = Field(min_length=1)
    subtype: str | None = None
    rarity: ItemRarity | None = None
    requires_attunement: bool = False
    attunement_prerequisite: str | None = None
    weight_display: str | None = None
    weight: Decimal | None = Field(default=None, ge=0)
    cost_display: str | None = None
    cost: ItemCost | None = None
    properties: list[ItemPropertyReference] = Field(default_factory=list)
    weapon: WeaponDetails | None = None
    armor: ArmorDetails | None = None

    @model_validator(mode="after")
    def validate_applicable_details(self) -> Item:
        if self.kind is ItemKind.WEAPON and self.weapon is None:
            raise ValueError("weapon details are required when kind is 'weapon'")
        if self.kind is ItemKind.ARMOR and self.armor is None:
            raise ValueError("armor details are required when kind is 'armor'")
        if (
            self.kind is not ItemKind.MAGIC_ITEM
            and self.kind is not ItemKind.WEAPON
            and self.weapon
        ):
            raise ValueError("weapon details are not valid for this item kind")
        if self.kind is not ItemKind.MAGIC_ITEM and self.kind is not ItemKind.ARMOR and self.armor:
            raise ValueError("armor details are not valid for this item kind")
        if self.attunement_prerequisite and not self.requires_attunement:
            raise ValueError("attunement prerequisite requires requires_attunement=true")
        return self


class ItemCatalog(ContractModel):
    """The JSON object stored in ``items.json``."""

    properties: list[ItemProperty] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_property_keys(self) -> ItemCatalog:
        keys = [property_definition.key for property_definition in self.properties]
        if len(keys) != len(set(keys)):
            raise ValueError("item properties must have unique keys")
        return self
