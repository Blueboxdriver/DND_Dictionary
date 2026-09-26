"""Direct SQL persistence for validated dataset packs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from ..search import normalize_name

if TYPE_CHECKING:
    from ..importer import LoadedDataset
    from ..search import (
        DatasetMetadata,
        EditionOption,
        EntryDetail,
        EntrySummary,
        SearchCategory,
        SearchPage,
        SearchQuery,
        SourceBrowseInfo,
        SourceIdentity,
        SourceOption,
    )


class RepositoryError(RuntimeError):
    """Raised when a validated dataset cannot be persisted."""


def _profile(profiler, name: str):
    return profiler.measure(name) if profiler is not None else nullcontext()


@dataclass(frozen=True)
class InstalledDataset:
    dataset_id: str
    title: str
    version: str
    content_hash: str
    entry_hashes: dict[str, str]
    source_hash: str | None = None


def _category_for_kind(kind: str):
    from ..search import SearchCategory

    return {
        "item": SearchCategory.ITEMS,
        "spell": SearchCategory.SPELLS,
        "feat": SearchCategory.FEATS,
        "class": SearchCategory.CLASSES,
        "monster": SearchCategory.MONSTERS,
        "subclass": SearchCategory.SUBCLASSES,
        "condition": SearchCategory.CONDITIONS,
        "rule": SearchCategory.RULES,
    }[kind]


def _search_text(*values: object) -> str:
    return " ".join(
        str(value).strip() for value in values if value is not None and str(value).strip()
    )


def _entry_search_body(connection: sqlite3.Connection, entry: sqlite3.Row) -> str:
    """Flatten source-faithful searchable content for one persisted entry."""

    entry_id = int(entry["id"])
    kind = str(entry["kind"])
    parts: list[str] = [str(entry["name"]), str(entry["description"])]
    if entry["kind"] == "rule" and entry["rule_section"]:
        parts.append(str(entry["rule_section"]))
    parts.extend(
        _search_text(row["heading"], row["body"])
        for row in connection.execute(
            "SELECT heading, body FROM entry_sections "
            "WHERE entry_id = ? ORDER BY display_order, section_key",
            (entry_id,),
        ).fetchall()
    )

    if kind == "spell":
        row = connection.execute(
            "SELECT school, casting_time, range, duration, material_description, "
            "higher_level_effects FROM spells WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is not None:
            parts.append(_search_text(*row))
    elif kind == "item":
        row = connection.execute(
            "SELECT item_type, subtype, rarity, attunement_prerequisite "
            "FROM items WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is not None:
            parts.append(_search_text(*row))
        parts.extend(
            _search_text(row["name"], row["description"], row["value"])
            for row in connection.execute(
                "SELECT ip.name, ip.description, ipl.value "
                "FROM item_property_links AS ipl "
                "JOIN item_properties AS ip ON ip.id = ipl.property_id "
                "WHERE ipl.item_id = ? ORDER BY ip.property_key",
                (entry_id,),
            ).fetchall()
        )
        row = connection.execute(
            "SELECT damage_expression, damage_type, range, versatile_damage, mastery "
            "FROM weapon_details WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is not None:
            parts.append(_search_text(*row))
        row = connection.execute(
            "SELECT armor_category, ac_expression FROM armor_details WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is not None:
            parts.append(_search_text(*row))
    elif kind == "feat":
        row = connection.execute(
            "SELECT category, prerequisite, ability_increase FROM feats WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is not None:
            parts.append(_search_text(*row))
    elif kind == "class":
        row = connection.execute(
            "SELECT primary_ability, saving_throw_proficiencies, skill_choices, "
            "weapon_proficiencies, armor_proficiencies, tool_proficiencies, "
            "starting_equipment, multiclassing, spellcasting_ability "
            "FROM classes WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is not None:
            parts.append(_search_text(*row))
        parts.extend(
            _search_text(row["title"], row["description"])
            for row in connection.execute(
                "SELECT title, description FROM class_features "
                "WHERE class_id = ? ORDER BY level, display_order, feature_key",
                (entry_id,),
            ).fetchall()
        )
        parts.extend(
            _search_text(row["name"], row["introduction"])
            for row in connection.execute(
                "SELECT name, introduction FROM subclasses "
                "WHERE class_id = ? ORDER BY subclass_key",
                (entry_id,),
            ).fetchall()
        )
        parts.extend(
            _search_text(row["title"], row["description"])
            for row in connection.execute(
                "SELECT sf.title, sf.description FROM subclass_features AS sf "
                "JOIN subclasses AS s ON s.id = sf.subclass_id "
                "WHERE s.class_id = ? ORDER BY s.subclass_key, sf.level, sf.display_order, "
                "sf.feature_key",
                (entry_id,),
            ).fetchall()
        )
        parts.extend(
            str(row["label"])
            for row in connection.execute(
                "SELECT label FROM class_progression_columns "
                "WHERE class_id = ? ORDER BY display_order, column_key",
                (entry_id,),
            ).fetchall()
        )
    elif kind == "monster":
        row = connection.execute(
            "SELECT creature_type, subtype, languages FROM monsters WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is not None:
            parts.append(_search_text(*row))
        parts.extend(
            _search_text(row["name"], row["description"])
            for row in connection.execute(
                "SELECT name, description FROM monster_abilities WHERE monster_id = ? "
                "ORDER BY section, display_order",
                (entry_id,),
            ).fetchall()
        )
    return _search_text(*parts)


def rebuild_search_index(connection: sqlite3.Connection, dataset_id: str | None = None) -> None:
    """Rebuild affected FTS rows inside the caller's transaction."""

    try:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entry_search'"
            ).fetchone()
            is None
        ):
            # This keeps the repository usable while constructing a pre-Milestone-5
            # database for an upgrade; the next normal initialization creates/backfills it.
            return
        if dataset_id is None:
            connection.execute("DELETE FROM entry_search")
            rows = connection.execute("SELECT * FROM entries ORDER BY id").fetchall()
        else:
            connection.execute("DELETE FROM entry_search WHERE dataset_id = ?", (dataset_id,))
            rows = connection.execute(
                "SELECT * FROM entries WHERE dataset_id = ? ORDER BY id", (dataset_id,)
            ).fetchall()
        connection.executemany(
            "INSERT INTO entry_search (entry_id, dataset_id, body) VALUES (?, ?, ?)",
            [
                (int(entry["id"]), str(entry["dataset_id"]), _entry_search_body(connection, entry))
                for entry in rows
            ],
        )
    except sqlite3.Error as exc:
        raise RepositoryError(f"cannot rebuild the FTS search index: {exc}") from exc


def normalize_stored_names(connection: sqlite3.Connection) -> None:
    """Bring names written by Milestone 4 up to the Milestone 5 normal form."""

    try:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entries'"
            ).fetchone()
            is None
        ):
            return
        rows = connection.execute("SELECT id, name, normalized_name FROM entries").fetchall()
        connection.executemany(
            "UPDATE entries SET normalized_name = ? WHERE id = ?",
            [
                (normalize_name(str(row["name"])), int(row["id"]))
                for row in rows
                if normalize_name(str(row["name"])) != str(row["normalized_name"])
            ],
        )
    except sqlite3.Error as exc:
        raise RepositoryError(f"cannot normalize stored entry names: {exc}") from exc


def get_installed_dataset(
    connection: sqlite3.Connection, dataset_id: str
) -> InstalledDataset | None:
    """Return one installed dataset and its entry hashes, if its schema exists."""
    try:
        has_source_hash = any(
            str(column[1]) == "source_hash"
            for column in connection.execute("PRAGMA table_info(datasets)").fetchall()
        )
        columns = "dataset_id, title, version, content_hash"
        if has_source_hash:
            columns += ", source_hash"
        row = connection.execute(
            f"SELECT {columns} FROM datasets WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise RepositoryError(f"cannot read installed dataset '{dataset_id}': {exc}") from exc

    if row is None:
        return None
    rows = connection.execute(
        "SELECT local_key, content_hash FROM entries WHERE dataset_id = ? ORDER BY local_key",
        (dataset_id,),
    ).fetchall()
    return InstalledDataset(
        dataset_id=str(row[0]),
        title=str(row[1]),
        version=str(row[2]),
        content_hash=str(row[3]),
        entry_hashes={str(entry[0]): str(entry[1]) for entry in rows},
        source_hash=(str(row[4]) if row[4] is not None else None) if has_source_hash else None,
    )


def list_installed_datasets(connection: sqlite3.Connection) -> tuple[InstalledDataset, ...]:
    """Return installed dataset metadata in stable order."""
    try:
        has_source_hash = any(
            str(column[1]) == "source_hash"
            for column in connection.execute("PRAGMA table_info(datasets)").fetchall()
        )
        columns = "dataset_id, title, version, content_hash"
        if has_source_hash:
            columns += ", source_hash"
        rows = connection.execute(f"SELECT {columns} FROM datasets ORDER BY dataset_id").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return ()
        raise RepositoryError(f"cannot list installed datasets: {exc}") from exc

    result: list[InstalledDataset] = []
    for row in rows:
        result.append(
            InstalledDataset(
                dataset_id=str(row[0]),
                title=str(row[1]),
                version=str(row[2]),
                content_hash=str(row[3]),
                entry_hashes={
                    str(entry[0]): str(entry[1])
                    for entry in connection.execute(
                        "SELECT local_key, content_hash FROM entries "
                        "WHERE dataset_id = ? ORDER BY local_key",
                        (row[0],),
                    ).fetchall()
                },
                source_hash=(str(row[4]) if row[4] is not None else None)
                if has_source_hash
                else None,
            )
        )
    return tuple(result)


def list_dataset_metadata(connection: sqlite3.Connection) -> tuple["DatasetMetadata", ...]:
    """Return installed dataset metadata for the typed About/Data API."""

    from ..search import DatasetMetadata, SourceInfo

    try:
        datasets = connection.execute(
            "SELECT dataset_id, title, version, ruleset, language, license_identifier, "
            "attribution, origin_url FROM datasets ORDER BY dataset_id"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return ()
        raise RepositoryError(f"cannot list dataset metadata: {exc}") from exc

    result: list[DatasetMetadata] = []
    for dataset in datasets:
        sources = tuple(
            SourceInfo(
                key=str(source["source_key"]),
                title=str(source["title"]),
                edition=str(source["edition"]) if source["edition"] is not None else None,
                citation=str(source["citation"]) if source["citation"] is not None else None,
            )
            for source in connection.execute(
                "SELECT source_key, title, edition, citation FROM sources "
                "WHERE dataset_id = ? ORDER BY source_key",
                (dataset["dataset_id"],),
            ).fetchall()
        )
        result.append(
            DatasetMetadata(
                dataset_id=str(dataset["dataset_id"]),
                title=str(dataset["title"]),
                version=str(dataset["version"]),
                ruleset=str(dataset["ruleset"]),
                language=str(dataset["language"]),
                license_identifier=str(dataset["license_identifier"]),
                attribution=str(dataset["attribution"]),
                origin_url=(
                    str(dataset["origin_url"]) if dataset["origin_url"] is not None else None
                ),
                sources=sources,
            )
        )
    return tuple(result)


def _bool(value: bool) -> int:
    return int(value)


def _source_id(source_ids: Mapping[str, int], source_key: str | None) -> int | None:
    if source_key is None:
        return None
    return source_ids[source_key]


def _insert_sections(
    connection: sqlite3.Connection,
    entry_id: int,
    sections: list[object],
) -> None:
    connection.executemany(
        "INSERT INTO entry_sections "
        "(entry_id, section_key, heading, body, display_order) VALUES (?, ?, ?, ?, ?)",
        [
            (
                entry_id,
                section.key,
                section.heading,
                section.body,
                section.display_order,
            )
            for section in sections
        ],
    )


def _json_value(value: object) -> str:
    def normalize(item):
        if hasattr(item, "model_dump"):
            return normalize(item.model_dump(mode="json"))
        if isinstance(item, dict):
            return {str(key): normalize(child) for key, child in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [normalize(child) for child in item]
        return item

    value = normalize(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _persist_character_builder(
    connection: sqlite3.Connection, pack: object, dataset_id: str, source_ids: Mapping[str, int]
) -> None:
    """Persist the rules catalog as owner rows and normalized rule children."""
    catalog = getattr(pack, "character_builder", None)
    if catalog is None:
        return

    def add_owner(owner_type, owner_key, name, source, description="", parent=None, metadata=None):
        connection.execute(
            "INSERT INTO character_builder_owners "
            "(dataset_id, owner_type, owner_key, source_id, edition, name, parent_class_key, "
            "description, metadata_json) VALUES (?, ?, ?, ?, '2024', ?, ?, ?, ?)",
            (
                dataset_id,
                owner_type,
                str(owner_key),
                source_ids[str(source)],
                name,
                str(parent) if parent is not None else None,
                description or "",
                _json_value(metadata or {}),
            ),
        )

    def add_requirement(owner_type, owner_key, requirement_key, scope, requirement):
        if requirement is None:
            return None
        key = str(requirement_key)
        connection.execute(
            "INSERT INTO character_rule_requirements "
            "(dataset_id, owner_type, owner_key, requirement_key, scope, payload_json, "
            "source_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                dataset_id,
                owner_type,
                str(owner_key),
                key,
                scope,
                _json_value(requirement),
                str(requirement.source_text or scope),
            ),
        )
        return key

    def add_grant(
        owner_type, owner_key, grant, scope, *, event_key=None, choice_key=None, option_key=None
    ):
        requirement_key = add_requirement(
            owner_type,
            owner_key,
            f"grant:{grant.grant_key}",
            "grant_activation",
            grant.condition,
        )
        reference = grant.reference
        connection.execute(
            "INSERT INTO character_rule_grants "
            "(dataset_id, owner_type, owner_key, grant_key, scope, event_key, choice_key, "
            "option_key, grant_type, value, reference_kind, reference_identity, proficiency_kind, "
            "quantity, unit, activation_requirement_key, source_rule, unresolved) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dataset_id,
                owner_type,
                str(owner_key),
                str(grant.grant_key),
                scope,
                str(event_key) if event_key is not None else None,
                str(choice_key) if choice_key is not None else None,
                str(option_key) if option_key is not None else None,
                str(grant.kind),
                grant.value,
                str(reference.kind) if reference is not None else None,
                reference.identity if reference is not None else None,
                str(grant.proficiency_kind) if grant.proficiency_kind is not None else None,
                grant.quantity,
                grant.unit,
                requirement_key,
                grant.source_rule,
                int(grant.unresolved),
            ),
        )

    def add_choice(owner_type, owner_key, choice, scope, *, event_key=None):
        requirement_key = add_requirement(
            owner_type,
            owner_key,
            f"choice:{choice.choice_key}",
            "choice",
            choice.requirement,
        )
        criteria = choice.criteria
        connection.execute(
            "INSERT INTO character_rule_choices "
            "(dataset_id, owner_type, owner_key, choice_key, scope, event_key, choice_type, "
            "choice_count, criteria_kind, criteria_values_json, criteria_filters_json, "
            "requirement_key, depends_on_choice, depends_on_option, exclusions_json, source_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dataset_id,
                owner_type,
                str(owner_key),
                str(choice.choice_key),
                scope,
                str(event_key) if event_key is not None else None,
                str(choice.kind),
                choice.count,
                str(criteria.kind) if criteria is not None else None,
                _json_value(criteria.values) if criteria is not None else None,
                _json_value(criteria.filters) if criteria is not None else None,
                requirement_key,
                str(choice.depends_on_choice) if choice.depends_on_choice is not None else None,
                str(choice.depends_on_option) if choice.depends_on_option is not None else None,
                _json_value(choice.exclusions),
                choice.source_rule,
            ),
        )
        for option in choice.options:
            reference = option.reference
            connection.execute(
                "INSERT INTO character_rule_choice_options "
                "(dataset_id, owner_type, owner_key, choice_key, option_key, label, value, "
                "reference_kind, reference_identity, ability_increases_json, group_name, resolved) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    owner_type,
                    str(owner_key),
                    str(choice.choice_key),
                    str(option.option_key),
                    option.label,
                    option.value,
                    str(reference.kind) if reference is not None else None,
                    reference.identity if reference is not None else None,
                    _json_value(option.ability_increases),
                    option.group,
                    int(option.resolved),
                ),
            )
            for grant in option.grants:
                add_grant(
                    owner_type,
                    owner_key,
                    grant,
                    "choice_option",
                    choice_key=choice.choice_key,
                    option_key=option.option_key,
                )

    def add_choices(owner_type, owner_key, choices, scope, *, event_key=None):
        for choice in choices:
            add_choice(owner_type, owner_key, choice, scope, event_key=event_key)

    def add_events(owner_type, owner_key, events):
        for event in events:
            requirement_key = add_requirement(
                owner_type,
                owner_key,
                f"event:{event.event_key}",
                "progression_event",
                event.requirement,
            )
            connection.execute(
                "INSERT INTO class_progression_events "
                "(dataset_id, owner_type, owner_key, event_key, class_level, title, source_rule, "
                "requirement_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    owner_type,
                    str(owner_key),
                    str(event.event_key),
                    event.level,
                    event.title,
                    event.source_rule,
                    requirement_key,
                ),
            )
            for grant in event.grants:
                add_grant(
                    owner_type, owner_key, grant, "progression_event", event_key=event.event_key
                )
            add_choices(
                owner_type, owner_key, event.choices, "progression_event", event_key=event.event_key
            )

    def add_spellcasting(owner_type, owner_key, rules):
        if rules is None:
            return
        reference = rules.spell_list_reference
        connection.execute(
            "INSERT INTO class_spellcasting "
            "(dataset_id, owner_type, owner_key, spellcasting_ability, spellcasting_model, "
            "multiclass_contribution, acquisition, spell_list_reference_kind, "
            "spell_list_reference_identity, prepared_spells_change, source_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dataset_id,
                owner_type,
                str(owner_key),
                str(rules.ability),
                str(rules.model),
                str(rules.multiclass_contribution),
                str(rules.acquisition),
                str(reference.kind) if reference is not None else None,
                reference.identity if reference is not None else None,
                rules.prepared_spells_change,
                rules.source_rule,
            ),
        )
        by_level = {}
        for field, column in (
            (rules.cantrips, "cantrips_known"),
            (rules.prepared_spells, "prepared_spells"),
            (rules.known_spells, "known_spells"),
        ):
            for row in field:
                by_level.setdefault(row.class_level, {})[column] = row.count
        connection.executemany(
            "INSERT INTO class_spellcasting_levels "
            "(dataset_id, owner_type, owner_key, class_level, cantrips_known, prepared_spells, "
            "known_spells) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    dataset_id,
                    owner_type,
                    str(owner_key),
                    level,
                    values.get("cantrips_known"),
                    values.get("prepared_spells"),
                    values.get("known_spells"),
                )
                for level, values in sorted(by_level.items())
            ],
        )
        for progression_kind, rows in (
            ("standalone", rules.standalone_slots),
            ("pact", rules.pact_slots),
        ):
            for row in rows:
                connection.executemany(
                    "INSERT INTO class_spell_slots "
                    "(dataset_id, owner_type, owner_key, progression_kind, class_level, "
                    "spell_level, slot_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            dataset_id,
                            owner_type,
                            str(owner_key),
                            progression_kind,
                            row.class_level,
                            spell_level,
                            count,
                        )
                        for spell_level, count in enumerate(row.slots_by_spell_level, 1)
                    ],
                )
        for index, access in enumerate(rules.additional_spells):
            connection.execute(
                "INSERT INTO character_builder_spell_access "
                "(dataset_id, owner_type, owner_key, access_key, access_type, class_level, "
                "level_scope, source_rule, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    owner_type,
                    str(owner_key),
                    f"spell-access-{index:03d}",
                    str(access.access),
                    access.class_level,
                    str(access.level_scope),
                    access.source_rule,
                    _json_value(access),
                ),
            )
        add_choices(owner_type, owner_key, rules.spell_choices, "spellcasting")

    def add_traits(owner_type, owner_key, traits):
        connection.executemany(
            "INSERT INTO character_builder_traits "
            "(dataset_id, owner_type, owner_key, trait_key, name, description, source_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    dataset_id,
                    owner_type,
                    str(owner_key),
                    str(trait.trait_key),
                    trait.name,
                    trait.description,
                    trait.source_rule,
                )
                for trait in traits
            ],
        )

    for row in catalog.skills:
        connection.execute(
            "INSERT INTO character_builder_skills (dataset_id, skill_key, name, ability_key) "
            "VALUES (?, ?, ?, ?)",
            (dataset_id, str(row.key), row.name, str(row.ability)),
        )
    for row in catalog.classes:
        owner_key = str(row.class_key)
        add_owner(
            "class",
            owner_key,
            row.name,
            row.source,
            metadata={
                "primary_ability_options": row.primary_ability_options,
                "subclass_selection_level": row.subclass_selection_level,
            },
        )
        add_requirement(
            "class", owner_key, "multiclass", "multiclass_entry", row.multiclass_requirement
        )
        for grant in row.starting_grants:
            add_grant("class", owner_key, grant, "starting_class")
        for grant in row.multiclass_grants:
            add_grant("class", owner_key, grant, "multiclass_entry")
        add_choices("class", owner_key, row.starting_choices, "starting_class")
        add_choices("class", owner_key, row.multiclass_choices, "multiclass_entry")
        add_choices("class", owner_key, row.starting_equipment.choices, "starting_equipment")
        add_events("class", owner_key, row.progression_events)
        add_spellcasting("class", owner_key, row.spellcasting)
    for row in catalog.subclasses:
        owner_key = str(row.subclass_key)
        add_owner(
            "subclass",
            owner_key,
            row.name,
            row.source,
            parent=row.class_key,
            metadata={"selection_level": row.selection_level},
        )
        add_events("subclass", owner_key, row.progression_events)
        add_spellcasting("subclass", owner_key, row.spellcasting)
    for row in catalog.species:
        owner_key = str(row.species_key)
        add_owner(
            "species",
            owner_key,
            row.name,
            row.source,
            row.description,
            metadata={
                "creature_types": row.creature_types,
                "sizes": row.sizes,
                "movement": row.movement,
                "darkvision_feet": row.darkvision_feet,
            },
        )
        for grant in row.grants:
            add_grant("species", owner_key, grant, "species")
        add_choices("species", owner_key, row.choices, "species")
        add_traits("species", owner_key, row.traits)
        for index, access in enumerate(row.spell_access):
            connection.execute(
                "INSERT INTO character_builder_spell_access "
                "(dataset_id, owner_type, owner_key, access_key, access_type, class_level, "
                "level_scope, source_rule, payload_json) "
                "VALUES (?, 'species', ?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    owner_key,
                    f"spell-access-{index:03d}",
                    str(access.access),
                    access.class_level,
                    str(access.level_scope),
                    access.source_rule,
                    _json_value(access),
                ),
            )
    for row in catalog.backgrounds:
        owner_key = str(row.background_key)
        add_owner(
            "background",
            owner_key,
            row.name,
            row.source,
            row.description,
            metadata={
                "origin_feat": row.origin_feat,
                "origin_feat_variant": row.origin_feat_variant,
                "includes_background_equipment": (
                    row.starting_equipment.includes_background_equipment
                ),
            },
        )
        for grant in row.grants:
            add_grant("background", owner_key, grant, "background")
        add_choices("background", owner_key, row.choices, "background")
        add_choices("background", owner_key, row.starting_equipment.choices, "starting_equipment")
        add_traits("background", owner_key, [])
    feat_names = {str(row.local_key): row.name for row in pack.feats}
    for row in catalog.feats:
        owner_key = str(row.feat_key)
        add_owner(
            "feat",
            owner_key,
            feat_names.get(owner_key, owner_key),
            row.source,
            metadata={"category": row.category, "repeatable": row.repeatable},
        )
        add_requirement("feat", owner_key, "prerequisite", "feat_prerequisite", row.prerequisite)
        for grant in row.grants:
            add_grant("feat", owner_key, grant, "feat")
        add_choices("feat", owner_key, row.ability_choices, "ability_score")
        add_choices("feat", owner_key, row.spell_choices, "spellcasting")
        for index, access in enumerate(row.spell_access):
            connection.execute(
                "INSERT INTO character_builder_spell_access "
                "(dataset_id, owner_type, owner_key, access_key, access_type, class_level, "
                "level_scope, source_rule, payload_json) VALUES (?, 'feat', ?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    owner_key,
                    f"spell-access-{index:03d}",
                    str(access.access),
                    access.class_level,
                    str(access.level_scope),
                    access.source_rule,
                    _json_value(access),
                ),
            )
    for row in catalog.optional_features:
        owner_key = str(row.option_key)
        add_owner(
            "optional_feature",
            owner_key,
            row.name,
            row.source,
            row.description,
            metadata={
                "option_type": row.option_type,
                "feature_types": row.feature_types,
                "repeatable": row.repeatable,
            },
        )
        add_requirement(
            "optional_feature", owner_key, "prerequisite", "optional_feature", row.prerequisite
        )
    item_names = {str(row.local_key): row.name for row in pack.items.items}
    for row in catalog.equipment:
        owner_key = str(row.item_key)
        add_owner("item", owner_key, item_names.get(owner_key, owner_key), row.source)
        connection.execute(
            "INSERT INTO character_builder_equipment "
            "(dataset_id, item_key, category, weapon_category, attack_type, damage, damage_type, "
            "range_json, properties_json, mastery_references_json, ammunition_reference_json, "
            "versatile_damage, armor_category, base_ac, dexterity_rule, dexterity_cap, "
            "strength_requirement, stealth_disadvantage, unresolved_fields_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dataset_id,
                owner_key,
                str(row.category),
                row.weapon_category,
                row.attack_type,
                row.damage,
                row.damage_type,
                _json_value(row.range_feet),
                _json_value(row.properties),
                _json_value(row.mastery_references),
                _json_value(row.ammunition_reference) if row.ammunition_reference else None,
                row.versatile_damage,
                row.armor_category,
                row.base_ac,
                row.dexterity_rule,
                row.dexterity_cap,
                row.strength_requirement,
                int(row.stealth_disadvantage) if row.stealth_disadvantage is not None else None,
                _json_value(row.unresolved_fields),
            ),
        )


def apply_dataset(
    connection: sqlite3.Connection,
    loaded: LoadedDataset,
    staged_assets: Mapping[str, str],
) -> None:
    """Replace one dataset snapshot inside the caller-owned transaction."""
    pack = loaded.pack
    manifest = pack.manifest
    dataset_id = str(manifest.dataset_id)
    source_ids: dict[str, int] = {}
    image_ids: dict[str, int] = {}
    entry_ids: dict[str, int] = {}
    class_ids: dict[str, int] = {}
    property_ids: dict[str, int] = {}

    try:
        connection.execute("DELETE FROM datasets WHERE dataset_id = ?", (dataset_id,))
        dataset_columns = {
            str(column[1])
            for column in connection.execute("PRAGMA table_info(datasets)").fetchall()
        }
        source_hash_column = ", source_hash" if "source_hash" in dataset_columns else ""
        source_hash_value = ", ?" if source_hash_column else ""
        values = (
            dataset_id,
            manifest.title,
            manifest.version,
            manifest.ruleset,
            manifest.language,
            manifest.license_identifier,
            manifest.attribution,
            str(manifest.origin_url) if manifest.origin_url is not None else None,
            loaded.content_hash,
        )
        if source_hash_column:
            values += (loaded.source_hash,)
        connection.execute(
            "INSERT INTO datasets "
            "(dataset_id, title, version, ruleset, language, license_identifier, "
            "attribution, origin_url, content_hash" + source_hash_column + ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?" + source_hash_value + ")",
            values,
        )
        connection.executemany(
            "INSERT INTO dataset_dependencies "
            "(dataset_id, dependency_dataset_id, version) VALUES (?, ?, ?)",
            [
                (dataset_id, dependency.dataset_id, dependency.version)
                for dependency in sorted(manifest.dependencies, key=lambda item: item.dataset_id)
            ],
        )

        for source in sorted(manifest.sources, key=lambda item: item.key):
            cursor = connection.execute(
                "INSERT INTO sources "
                "(dataset_id, source_key, title, edition, citation) VALUES (?, ?, ?, ?, ?)",
                (dataset_id, source.key, source.title, source.edition, source.citation),
            )
            source_ids[str(source.key)] = int(cursor.lastrowid)

        for asset in sorted(loaded.assets, key=lambda item: item.relative_path):
            cursor = connection.execute(
                "INSERT INTO images "
                "(dataset_id, relative_path, content_hash, stored_path, media_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    asset.relative_path,
                    asset.content_hash,
                    staged_assets[asset.relative_path],
                    asset.media_type,
                ),
            )
            image_ids[asset.relative_path] = int(cursor.lastrowid)

        entries = (
            [("item", item) for item in pack.items.items]
            + [("spell", spell) for spell in pack.spells]
            + [("feat", feat) for feat in pack.feats]
            + [("class", character_class) for character_class in pack.classes]
            + [("monster", monster) for monster in pack.monsters]
            + [("condition", item) for item in pack.conditions]
            + [("rule", item) for item in pack.rules]
        )
        has_rule_section = any(
            str(column["name"]) == "rule_section"
            for column in connection.execute("PRAGMA table_info(entries)").fetchall()
        )
        for kind, entry in sorted(entries, key=lambda item: item[1].local_key):
            columns = (
                "dataset_id, local_key, kind, name, normalized_name, description, source_id, "
                "image_id, content_hash" + (", rule_section" if has_rule_section else "")
            )
            values = (
                dataset_id,
                entry.local_key,
                kind,
                entry.name,
                normalize_name(entry.name),
                entry.description,
                source_ids[entry.source],
                image_ids.get(entry.image),
                loaded.entry_hashes[entry.local_key],
            ) + ((entry.section if kind == "rule" else None,) if has_rule_section else ())
            cursor = connection.execute(
                f"INSERT INTO entries ({columns}) VALUES ({', '.join('?' for _ in values)})",
                values,
            )
            entry_ids[str(entry.local_key)] = int(cursor.lastrowid)
            sections = list(entry.sections)
            if kind == "feat":
                sections.extend(entry.benefits)
            _insert_sections(connection, int(cursor.lastrowid), sections)

        has_entry_references = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entry_references'"
        ).fetchone()
        if has_entry_references:
            connection.executemany(
                "INSERT INTO entry_references "
                "(source_entry_id, target_entry_id, content_type) VALUES (?, ?, ?)",
                [
                    (
                        entry_ids[entry.local_key],
                        entry_ids[reference.target_key],
                        reference.content_type,
                    )
                    for _, entry in entries
                    for reference in entry.references
                ],
            )

        for property_definition in sorted(pack.items.properties, key=lambda item: item.key):
            cursor = connection.execute(
                "INSERT INTO item_properties "
                "(dataset_id, property_key, name, description, source_id) VALUES (?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    property_definition.key,
                    property_definition.name,
                    property_definition.description,
                    source_ids[property_definition.source],
                ),
            )
            property_ids[str(property_definition.key)] = int(cursor.lastrowid)

        for item in sorted(pack.items.items, key=lambda record: record.local_key):
            item_id = entry_ids[str(item.local_key)]
            connection.execute(
                "INSERT INTO items "
                "(entry_id, dataset_id, kind, item_type, subtype, rarity, requires_attunement, "
                "attunement_prerequisite, weight_display, weight, cost_display, cost_amount, "
                "cost_currency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    dataset_id,
                    item.kind.value,
                    item.type,
                    item.subtype,
                    item.rarity.value if item.rarity is not None else None,
                    _bool(item.requires_attunement),
                    item.attunement_prerequisite,
                    item.weight_display,
                    str(item.weight) if item.weight is not None else None,
                    item.cost_display,
                    str(item.cost.amount) if item.cost is not None else None,
                    item.cost.currency if item.cost is not None else None,
                ),
            )
            connection.executemany(
                "INSERT INTO item_property_links "
                "(dataset_id, item_id, property_id, value) VALUES (?, ?, ?, ?)",
                [
                    (
                        dataset_id,
                        item_id,
                        property_ids[str(reference.property_key)],
                        reference.value,
                    )
                    for reference in item.properties
                ],
            )
            if item.weapon is not None:
                connection.execute(
                    "INSERT INTO weapon_details "
                    "(entry_id, damage_expression, damage_type, range, versatile_damage, mastery) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        item_id,
                        item.weapon.damage_expression,
                        item.weapon.damage_type,
                        item.weapon.range,
                        item.weapon.versatile_damage,
                        item.weapon.mastery,
                    ),
                )
            if item.armor is not None:
                connection.execute(
                    "INSERT INTO armor_details "
                    "(entry_id, armor_category, ac_expression, strength_requirement, "
                    "stealth_disadvantage) VALUES (?, ?, ?, ?, ?)",
                    (
                        item_id,
                        item.armor.armor_category,
                        item.armor.ac_expression,
                        item.armor.strength_requirement,
                        _bool(item.armor.stealth_disadvantage),
                    ),
                )

        for spell in sorted(pack.spells, key=lambda record: record.local_key):
            spell_id = entry_ids[str(spell.local_key)]
            connection.execute(
                "INSERT INTO spells "
                "(entry_id, dataset_id, level, school, casting_time, range, verbal, somatic, "
                "material, material_description, duration, concentration, ritual, "
                "higher_level_effects) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spell_id,
                    dataset_id,
                    spell.level,
                    spell.school.value,
                    spell.casting_time,
                    spell.range,
                    _bool(spell.components.verbal),
                    _bool(spell.components.somatic),
                    _bool(spell.components.material),
                    spell.components.material_description,
                    spell.duration,
                    _bool(spell.concentration),
                    _bool(spell.ritual),
                    spell.higher_level_effects,
                ),
            )

        from ..models.monster import cr_value

        for monster in sorted(pack.monsters, key=lambda record: record.local_key):
            values = monster.model_dump(mode="json")
            columns = (
                "page",
                "group",
                "variant",
                "size",
                "creature_type",
                "subtype",
                "alignment",
                "armor_class",
                "hit_points",
                "hit_points_text",
                "hit_dice",
                "speed",
                "speed_text",
                "abilities",
                "saving_throws",
                "skills",
                "proficiency_bonus",
                "damage_vulnerabilities",
                "damage_resistances",
                "damage_immunities",
                "condition_immunities",
                "senses",
                "passive_perception",
                "languages",
                "telepathy",
                "challenge_rating",
                "xp",
                "legendary_intro",
            )
            row_values = [
                json.dumps(values[key], ensure_ascii=False)
                if key in {"speed", "abilities", "saving_throws", "skills"}
                else values[key]
                for key in columns
            ]
            cr_eighths = int(cr_value(monster.challenge_rating) * 8)
            connection.execute(
                "INSERT INTO monsters (entry_id, dataset_id, page, group_name, variant, size, "
                "creature_type, subtype, alignment, armor_class, hit_points, hit_points_text, "
                "hit_dice, speed_json, speed_text, abilities_json, saving_throws_json, "
                "skills_json, proficiency_bonus, damage_vulnerabilities, damage_resistances, "
                "damage_immunities, condition_immunities, senses, passive_perception, "
                "languages, telepathy, challenge_rating, cr_eighths, xp, legendary_intro) "
                "VALUES (" + ",".join("?" for _ in range(31)) + ")",
                (
                    entry_ids[monster.local_key],
                    dataset_id,
                    *row_values[:-2],
                    cr_eighths,
                    *row_values[-2:],
                ),
            )
            connection.executemany(
                "INSERT INTO monster_abilities "
                "(monster_id, section, name, description, display_order, cost) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        entry_ids[monster.local_key],
                        ability.section,
                        ability.name,
                        ability.description,
                        ability.display_order,
                        ability.cost,
                    )
                    for ability in monster.abilities_and_actions
                ],
            )

        for feat in sorted(pack.feats, key=lambda record: record.local_key):
            connection.execute(
                "INSERT INTO feats "
                "(entry_id, dataset_id, category, prerequisite, minimum_level, repeatable, "
                "ability_increase) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_ids[str(feat.local_key)],
                    dataset_id,
                    feat.category,
                    feat.prerequisite,
                    feat.minimum_level,
                    _bool(feat.repeatable),
                    feat.ability_increase,
                ),
            )

        for character_class in sorted(pack.classes, key=lambda record: record.local_key):
            class_id = entry_ids[str(character_class.local_key)]
            class_ids[str(character_class.local_key)] = class_id
            connection.execute(
                "INSERT INTO classes "
                "(entry_id, dataset_id, hit_die, primary_ability, saving_throw_proficiencies, "
                "skill_choices, weapon_proficiencies, armor_proficiencies, tool_proficiencies, "
                "starting_equipment, multiclassing, spellcasting_ability) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    class_id,
                    dataset_id,
                    int(character_class.hit_die),
                    character_class.primary_ability,
                    character_class.saving_throw_proficiencies,
                    character_class.skill_choices,
                    character_class.weapon_proficiencies,
                    character_class.armor_proficiencies,
                    character_class.tool_proficiencies,
                    character_class.starting_equipment,
                    character_class.multiclassing,
                    character_class.spellcasting_ability,
                ),
            )
            connection.executemany(
                "INSERT INTO class_features "
                "(dataset_id, class_id, feature_key, level, title, description, source_id, "
                "display_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        dataset_id,
                        class_id,
                        feature.feature_key,
                        feature.level,
                        feature.title,
                        feature.description,
                        _source_id(source_ids, feature.source),
                        feature.display_order,
                    )
                    for feature in character_class.features
                ],
            )
            connection.executemany(
                "INSERT INTO class_levels (class_id, level) VALUES (?, ?)",
                [(class_id, row.level) for row in character_class.progression.levels],
            )
            connection.executemany(
                "INSERT INTO class_progression_columns "
                "(class_id, column_key, label, display_order) VALUES (?, ?, ?, ?)",
                [
                    (class_id, column.key, column.label, column.display_order)
                    for column in character_class.progression.columns
                ],
            )
            connection.executemany(
                "INSERT INTO class_progression_values "
                "(class_id, level, column_key, value) VALUES (?, ?, ?, ?)",
                [
                    (class_id, row.level, column_key, value)
                    for row in character_class.progression.levels
                    for column_key, value in sorted(row.values.items())
                ],
            )
            for subclass in sorted(
                character_class.subclasses, key=lambda record: record.subclass_key
            ):
                cursor = connection.execute(
                    "INSERT INTO subclasses "
                    "(dataset_id, class_id, subclass_key, name, introduction, source_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        dataset_id,
                        class_id,
                        subclass.subclass_key,
                        subclass.name,
                        subclass.introduction,
                        source_ids[subclass.source],
                    ),
                )
                subclass_id = int(cursor.lastrowid)
                connection.executemany(
                    "INSERT INTO subclass_features "
                    "(dataset_id, subclass_id, feature_key, level, title, description, source_id, "
                    "display_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            dataset_id,
                            subclass_id,
                            feature.feature_key,
                            feature.level,
                            feature.title,
                            feature.description,
                            _source_id(source_ids, feature.source),
                            feature.display_order,
                        )
                        for feature in subclass.features
                    ],
                )

        for spell in sorted(pack.spells, key=lambda record: record.local_key):
            spell_id = entry_ids[str(spell.local_key)]
            connection.executemany(
                "INSERT INTO spell_classes (dataset_id, spell_id, class_id) VALUES (?, ?, ?)",
                [
                    (dataset_id, spell_id, class_ids[str(reference.split(":", 1)[-1])])
                    for reference in spell.class_references
                ],
            )
        _persist_character_builder(connection, pack, dataset_id, source_ids)
        rebuild_search_index(connection, dataset_id)
    except (KeyError, sqlite3.Error) as exc:
        raise RepositoryError(f"cannot persist dataset '{dataset_id}': {exc}") from exc


def _escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fts_query(tokens: tuple[str, ...]) -> str:
    """Build FTS syntax only from application-generated literal tokens."""

    quoted = [f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens]
    if quoted:
        quoted[-1] += "*"
    return " AND ".join(quoted)


def _name_match_sql(tokens: tuple[str, ...], *, monster: bool = False) -> str:
    if monster:
        return " AND ".join(
            "(instr(e.normalized_name, ?) > 0 OR instr(lower(m.creature_type), ?) > 0)"
            for _ in tokens
        )
    return " AND ".join("instr(e.normalized_name, ?) > 0" for _ in tokens)


_SUMMARY_FROM = """
FROM entries AS e
JOIN datasets AS d ON d.dataset_id = e.dataset_id
JOIN sources AS src ON src.id = e.source_id
LEFT JOIN images AS img ON img.id = e.image_id
LEFT JOIN items AS i ON i.entry_id = e.id
LEFT JOIN spells AS sp ON sp.entry_id = e.id
LEFT JOIN feats AS f ON f.entry_id = e.id
LEFT JOIN classes AS c ON c.entry_id = e.id
LEFT JOIN monsters AS m ON m.entry_id = e.id
"""

_SUMMARY_SELECT = """
SELECT
    e.dataset_id || ':' || e.local_key AS stable_id,
    e.dataset_id,
    e.local_key,
    e.kind,
    e.name,
    e.normalized_name,
    img.stored_path AS image_stored_path,
    img.media_type AS image_media_type,
    img.content_hash AS image_content_hash,
    src.title AS source_label,
    src.dataset_id AS source_dataset_id,
    src.source_key AS source_key,
    src.edition AS source_edition,
    d.title AS dataset_title,
    CASE e.kind
        WHEN 'item' THEN COALESCE(NULLIF(i.subtype, ''), i.item_type, 'Item')
        WHEN 'spell' THEN
            CASE WHEN sp.level = 0 THEN 'Cantrip' ELSE 'Level ' || sp.level END
            || ' · ' || sp.school
        WHEN 'feat' THEN f.category
        WHEN 'class' THEN 'd' || c.hit_die || ' Hit Die'
        WHEN 'monster' THEN 'CR ' || m.challenge_rating || ' · ' || m.size || ' ' || m.creature_type
        WHEN 'condition' THEN 'Condition'
        WHEN 'rule' THEN COALESCE(e.rule_section, 'Rule')
    END AS subtitle
"""


def _search_clauses(query: "SearchQuery") -> tuple[str, list[object], str, list[object]]:
    from ..search import SearchCategory, SearchMode

    category_kind = query.category.storage_kind
    tokens = query.tokens
    fts_tokens = query.fts_tokens
    where = ["e.kind = ?"]
    where_params: list[object] = [category_kind]
    if query.editions:
        where.append(f"src.edition IN ({', '.join('?' for _ in query.editions)})")
        where_params.extend(query.editions)
    if query.sources:
        source_terms = ["(src.dataset_id = ? AND src.source_key = ?)" for _ in query.sources]
        where.append("(" + " OR ".join(source_terms) + ")")
        for source in query.sources:
            where_params.extend((source.dataset_id, source.source_key))
    if query.challenge_ratings:
        from ..models.monster import cr_value

        where.append(f"m.cr_eighths IN ({', '.join('?' for _ in query.challenge_ratings)})")
        where_params.extend(int(cr_value(value) * 8) for value in query.challenge_ratings)
    if query.creature_types:
        where.append(f"m.creature_type IN ({', '.join('?' for _ in query.creature_types)})")
        where_params.extend(query.creature_types)
    if query.sizes:
        where.append(f"m.size IN ({', '.join('?' for _ in query.sizes)})")
        where_params.extend(query.sizes)
    if getattr(query, "rule_sections", ()):
        if query.category is not SearchCategory.RULES:
            raise ValueError("rule section filters require the Rules category")
        where.append(f"e.rule_section IN ({', '.join('?' for _ in query.rule_sections)})")
        where_params.extend(query.rule_sections)
    monster = query.category is SearchCategory.MONSTERS
    name_match = _name_match_sql(tokens, monster=monster)
    match_params = [part for token in tokens for part in ((token, token) if monster else (token,))]
    if query.category is SearchCategory.RULES and tokens:
        section_match = " AND ".join("lower(e.rule_section) LIKE ?" for _ in tokens)
        name_match = f"(({name_match}) OR ({section_match}))"
        match_params.extend(f"%{token}%" for token in tokens)
    if tokens:
        where.append(f"({name_match})")
        where_params.extend(match_params)
        if query.mode is SearchMode.ALL_TEXT:
            where[-1] = (
                f"(({name_match}) OR e.id IN "
                "(SELECT entry_id FROM entry_search WHERE entry_search MATCH ?))"
            )
            where_params.append(_fts_query(fts_tokens))

    order_sql = "e.normalized_name, e.dataset_id, e.local_key"
    order_params: list[object] = []
    if tokens:
        normalized_query = query.normalized_text
        order_sql = (
            "CASE WHEN e.normalized_name = ? THEN 0 "
            "WHEN e.normalized_name LIKE ? || '%' ESCAPE '\\' THEN 1 "
            f"WHEN {name_match} THEN 2 ELSE 3 END, "
            "e.normalized_name, e.dataset_id, e.local_key"
        )
        order_params.extend((normalized_query, _escaped_like(normalized_query)))
        order_params.extend(match_params)
    return " AND ".join(where), where_params, order_sql, order_params


def _summary_from_row(row: sqlite3.Row) -> "EntrySummary":
    from ..search import EntrySummary, SourceIdentity

    return EntrySummary(
        stable_id=str(row["stable_id"]),
        category=_category_for_kind(str(row["kind"])),
        name=str(row["name"]),
        subtitle=str(row["subtitle"]),
        source_label=str(row["source_label"]),
        dataset_id=str(row["dataset_id"]),
        local_key=str(row["local_key"]),
        dataset_title=str(row["dataset_title"]),
        source_identity=SourceIdentity(
            dataset_id=str(row["source_dataset_id"]), source_key=str(row["source_key"])
        ),
        source_edition=(str(row["source_edition"]) if row["source_edition"] is not None else None),
    )


_SUBCLASS_SELECT = """
SELECT s.dataset_id, s.subclass_key, s.name,
       parent.name AS parent_name, parent.normalized_name AS parent_key,
       parent.local_key AS parent_local_key,
       src.title AS source_label, src.dataset_id AS source_dataset_id,
       src.source_key, src.edition AS source_edition, d.title AS dataset_title
FROM subclasses AS s
JOIN entries AS parent ON parent.id = s.class_id
JOIN sources AS parent_src ON parent_src.id = parent.source_id
JOIN sources AS src ON src.id = s.source_id
JOIN datasets AS d ON d.dataset_id = s.dataset_id
WHERE src.edition IS NOT NULL AND src.edition = parent_src.edition
"""

_SUBCLASS_DETAIL_SELECT = _SUBCLASS_SELECT.replace(
    "SELECT s.dataset_id, s.subclass_key, s.name,\n",
    "SELECT s.dataset_id, s.subclass_key, s.name, s.introduction,\n",
    1,
)

_SUBCLASS_TEXT_SELECT = """
SELECT s.dataset_id, s.subclass_key, s.name, s.introduction,
       parent.name AS parent_name, parent.normalized_name AS parent_key,
       parent.local_key AS parent_local_key,
       src.title AS source_label, src.dataset_id AS source_dataset_id,
       src.source_key, src.edition AS source_edition, d.title AS dataset_title,
       COALESCE(feature_text.feature_text, '') AS feature_text
FROM subclasses AS s
JOIN entries AS parent ON parent.id = s.class_id
JOIN sources AS parent_src ON parent_src.id = parent.source_id
JOIN sources AS src ON src.id = s.source_id
JOIN datasets AS d ON d.dataset_id = s.dataset_id
LEFT JOIN (
    SELECT subclass_id, GROUP_CONCAT(title || ' ' || description, ' ') AS feature_text
    FROM subclass_features GROUP BY subclass_id
) AS feature_text ON feature_text.subclass_id = s.id
WHERE src.edition IS NOT NULL AND src.edition = parent_src.edition
"""


def _subclass_summary(row: sqlite3.Row) -> "EntrySummary":
    from ..search import EntrySummary, SearchCategory, SourceIdentity

    return EntrySummary(
        stable_id=(f"{row['dataset_id']}:subclass:{row['parent_local_key']}:{row['subclass_key']}"),
        category=SearchCategory.SUBCLASSES,
        name=str(row["name"]),
        subtitle=f"{row['parent_name']} Subclass",
        source_label=str(row["source_label"]),
        dataset_id=str(row["dataset_id"]),
        local_key=str(row["subclass_key"]),
        dataset_title=str(row["dataset_title"]),
        source_identity=SourceIdentity(str(row["source_dataset_id"]), str(row["source_key"])),
        source_edition=str(row["source_edition"]),
        group_key=(
            f"group:subclasses:{row['parent_key']}:{row['source_edition']}:"
            f"{normalize_name(str(row['name']))}"
        ),
    )


def _matching_subclass_rows(
    connection: sqlite3.Connection, query: "SearchQuery"
) -> list[sqlite3.Row]:
    """Filter existing subclass records without duplicating them in entries."""

    from ..search import SearchMode

    clauses: list[str] = []
    parameters: list[object] = []
    if query.editions:
        clauses.append(f"src.edition IN ({', '.join('?' for _ in query.editions)})")
        parameters.extend(query.editions)
    if query.sources:
        clauses.append(
            "("
            + " OR ".join("(src.dataset_id = ? AND src.source_key = ?)" for _ in query.sources)
            + ")"
        )
        for source in query.sources:
            parameters.extend((source.dataset_id, source.source_key))
    if query.parent_class:
        clauses.append("parent.normalized_name = ?")
        parameters.append(normalize_name(query.parent_class))
    all_text = query.mode is SearchMode.ALL_TEXT and bool(query.tokens)
    base_sql = _SUBCLASS_TEXT_SELECT if all_text else _SUBCLASS_SELECT
    sql = base_sql + (" AND " + " AND ".join(clauses) if clauses else "")
    try:
        rows = connection.execute(sql, parameters).fetchall()
    except sqlite3.Error as exc:
        raise RepositoryError(f"cannot search subclasses: {exc}") from exc
    tokens = query.tokens
    if tokens:
        rows = [
            row
            for row in rows
            if all(
                token in normalize_name(str(row["name"]))
                or token in str(row["parent_key"])
                or (
                    query.mode is SearchMode.ALL_TEXT
                    and token
                    in normalize_name(f"{row['introduction']} {row['feature_text'] or ''}")
                )
                for token in tokens
            )
        ]
    rows.sort(
        key=lambda row: (
            0 if normalize_name(str(row["name"])) == query.normalized_text else 1,
            normalize_name(str(row["name"])),
            str(row["source_edition"]),
            str(row["dataset_id"]),
            str(row["subclass_key"]),
        )
    )
    return rows


def search_subclasses(
    connection: sqlite3.Connection,
    query: "SearchQuery",
    *,
    profiler=None,
) -> "SearchPage":
    from ..search import SearchPage

    with _profile(profiler, f"category_sql.{query.category.value}"):
        rows = _matching_subclass_rows(connection, query)
    with _profile(profiler, f"category_transform.{query.category.value}"):
        page = rows[query.offset : query.offset + query.limit]
        results = tuple(_subclass_summary(row) for row in page)
    return SearchPage(results, len(rows), query.offset, query.limit, query.request_id)


def search_grouped_subclasses(
    connection: sqlite3.Connection,
    query: "SearchQuery",
    preferred_sources: tuple["SourceIdentity", ...] = (),
    *,
    profiler=None,
) -> "SearchPage":
    from ..search import GroupedEntrySummary, SearchPage

    with _profile(profiler, f"category_sql.{query.category.value}"):
        rows = _matching_subclass_rows(connection, query)
    with _profile(profiler, f"category_group.{query.category.value}"):
        groups: dict[tuple[str, str, str], list["EntrySummary"]] = {}
        for row in rows:
            summary = _subclass_summary(row)
            key = (str(row["parent_key"]), str(row["source_edition"]), normalize_name(summary.name))
            groups.setdefault(key, []).append(summary)
        preference = {source: index for index, source in enumerate(preferred_sources)}
        results: list["EntrySummary | GroupedEntrySummary"] = []
        for members in groups.values():
            counts: dict["SourceIdentity", int] = {}
            for member in members:
                counts[member.source_identity] = counts.get(member.source_identity, 0) + 1
            if any(count > 1 for count in counts.values()):
                results.extend(members)
                continue
            members.sort(
                key=lambda member: (
                    preference.get(member.source_identity, len(preference)),
                    member.source_label.casefold(),
                    member.dataset_id,
                    member.local_key,
                )
            )
            results.append(
                GroupedEntrySummary(
                    query.category,
                    normalize_name(members[0].name),
                    members[0],
                    tuple(members[1:]),
                    group_key=members[0].group_key,
                )
                if len(members) > 1
                else members[0]
            )
    return SearchPage(
        tuple(results[query.offset : query.offset + query.limit]),
        len(results),
        query.offset,
        query.limit,
        query.request_id,
    )


def list_compatible_subclasses(
    connection: sqlite3.Connection, class_identity: str
) -> tuple["EntrySummary", ...]:
    if ":" not in class_identity:
        return ()
    dataset_id, local_key = class_identity.split(":", 1)
    parent = connection.execute(
        "SELECT e.normalized_name, src.edition FROM entries AS e "
        "JOIN sources AS src ON src.id = e.source_id "
        "WHERE e.dataset_id = ? AND e.local_key = ? AND e.kind = 'class'",
        (dataset_id, local_key),
    ).fetchone()
    if parent is None or parent["edition"] is None:
        return ()
    rows = connection.execute(
        _SUBCLASS_SELECT + " AND parent.normalized_name = ? AND src.edition = ? "
        "ORDER BY s.name COLLATE NOCASE, src.title COLLATE NOCASE, s.dataset_id, s.subclass_key",
        (parent["normalized_name"], parent["edition"]),
    ).fetchall()
    return tuple(_subclass_summary(row) for row in rows)


def list_subclass_parents(
    connection: sqlite3.Connection, editions: tuple[str, ...] = ()
) -> tuple[str, ...]:
    clauses = ""
    parameters: list[object] = []
    if editions:
        clauses = f" AND src.edition IN ({', '.join('?' for _ in editions)})"
        parameters.extend(editions)
    rows = connection.execute(
        "SELECT DISTINCT parent.name, parent.normalized_name FROM subclasses AS s "
        "JOIN entries AS parent ON parent.id = s.class_id "
        "JOIN sources AS src ON src.id = s.source_id "
        "JOIN sources AS parent_src ON parent_src.id = parent.source_id "
        "WHERE src.edition IS NOT NULL AND src.edition = parent_src.edition"
        + clauses
        + " ORDER BY parent.normalized_name, parent.name",
        parameters,
    ).fetchall()
    return tuple(dict.fromkeys(str(row["name"]) for row in rows))


def search_entries(
    connection: sqlite3.Connection,
    query: "SearchQuery",
    *,
    profiler=None,
) -> "SearchPage":
    """Run a paged, category-scoped search using the caller's connection."""

    from ..search import SearchPage

    with _profile(profiler, f"category_filter_plan.{query.category.value}"):
        where_sql, where_params, order_sql, order_params = _search_clauses(query)
    try:
        with _profile(profiler, f"category_sql.{query.category.value}"):
            total_count = int(
                connection.execute(
                    f"SELECT COUNT(*) {_SUMMARY_FROM} WHERE {where_sql}", where_params
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"{_SUMMARY_SELECT} {_SUMMARY_FROM} WHERE {where_sql} "
                f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
                [*where_params, *order_params, query.limit, query.offset],
            ).fetchall()
    except sqlite3.Error as exc:
        if "entry_search" in str(exc):
            raise RuntimeError(
                "search index is unavailable; initialize the database with SQLite FTS5 support"
            ) from exc
        raise RepositoryError(f"cannot execute search: {exc}") from exc

    with _profile(profiler, f"category_transform.{query.category.value}"):
        results = tuple(_summary_from_row(row) for row in rows)
    return SearchPage(results, total_count, query.offset, query.limit, query.request_id)


def search_grouped_entries(
    connection: sqlite3.Connection,
    query: "SearchQuery",
    preferred_sources: tuple["SourceIdentity", ...] = (),
    *,
    profiler=None,
) -> "SearchPage":
    """Page matching name groups before fetching their matching member records."""

    from ..search import GroupedEntrySummary, SearchCategory, SearchPage

    if query.category is SearchCategory.MONSTERS:
        # Equal names can denote different stat blocks even within one source.
        return search_entries(connection, query, profiler=profiler)

    with _profile(profiler, f"category_filter_plan.{query.category.value}"):
        where_sql, where_params, order_sql, order_params = _search_clauses(query)
    rank_sql = order_sql.split(", e.normalized_name", 1)[0] if order_params else "0"
    edition_scoped = query.category in {SearchCategory.CONDITIONS, SearchCategory.RULES}
    matched_edition = ", src.edition AS edition" if edition_scoped else ""
    collision_group = (
        "normalized_name, edition, source_id" if edition_scoped else "normalized_name, source_id"
    )
    collides = (
        "EXISTS (SELECT 1 FROM colliding c WHERE c.normalized_name=m.normalized_name "
        "AND c.edition IS m.edition)"
        if edition_scoped
        else "m.normalized_name IN (SELECT normalized_name FROM colliding)"
    )
    group_name = (
        "'name:' || m.normalized_name || ':' || COALESCE(m.edition, '')"
        if edition_scoped
        else "'name:' || m.normalized_name"
    )
    cte = (
        "WITH matched AS ("
        "SELECT e.id, e.normalized_name, e.dataset_id, e.local_key, "
        f"src.id AS source_id{matched_edition}, {rank_sql} AS rank "
        f"{_SUMMARY_FROM} WHERE {where_sql}"
        "), colliding AS ("
        f"SELECT {collision_group} FROM matched "
        f"GROUP BY {collision_group} HAVING COUNT(*) > 1"
        "), candidates AS ("
        "SELECT m.id, m.normalized_name, m.rank, "
        f"CASE WHEN {collides} "
        "THEN 'entry:' || m.dataset_id || ':' || m.local_key "
        f"ELSE {group_name} END AS group_key FROM matched m"
        ") "
    )
    cte_params = [*order_params, *where_params]
    try:
        with _profile(profiler, f"category_sql.{query.category.value}"):
            total_count = int(
                connection.execute(
                    cte + "SELECT COUNT(DISTINCT group_key) FROM candidates", cte_params
                ).fetchone()[0]
            )
            keys = [
                str(row[0])
                for row in connection.execute(
                    cte + "SELECT group_key FROM candidates GROUP BY group_key "
                    "ORDER BY MIN(rank), MIN(normalized_name), group_key LIMIT ? OFFSET ?",
                    [*cte_params, query.limit, query.offset],
                ).fetchall()
            ]
            if not keys:
                return SearchPage((), total_count, query.offset, query.limit, query.request_id)
            key_sql = ", ".join("?" for _ in keys)
            rows = connection.execute(
                cte
                + _SUMMARY_SELECT.rstrip()
                + ", candidates.group_key AS group_key "
                + _SUMMARY_FROM
                + " JOIN candidates ON candidates.id = e.id "
                + f"WHERE candidates.group_key IN ({key_sql}) "
                + "ORDER BY candidates.rank, e.normalized_name, e.dataset_id, e.local_key",
                [*cte_params, *keys],
            ).fetchall()
    except sqlite3.Error as exc:
        if "entry_search" in str(exc):
            raise RuntimeError(
                "search index is unavailable; initialize the database with SQLite FTS5 support"
            ) from exc
        raise RepositoryError(f"cannot execute grouped search: {exc}") from exc

    with _profile(profiler, f"category_transform.{query.category.value}"):
        by_key: dict[str, list["EntrySummary"]] = {key: [] for key in keys}
        for row in rows:
            by_key[str(row["group_key"])].append(_summary_from_row(row))
    with _profile(profiler, f"category_group.{query.category.value}"):
        preference = {identity: index for index, identity in enumerate(preferred_sources)}
        groups = []
        for key in keys:
            members = by_key[key]
            members.sort(
                key=lambda member: (
                    preference.get(member.source_identity, len(preference)),
                    member.source_edition or "",
                    member.source_label.casefold(),
                    member.dataset_id,
                    member.source_identity.source_key,
                    member.local_key,
                )
            )
            edition_key = f":{members[0].source_edition or ''}" if edition_scoped else ""
            group_identity = (
                f"group:{query.category.value}:{normalize_name(members[0].name)}{edition_key}"
            )
            groups.append(
                GroupedEntrySummary(
                    query.category,
                    normalize_name(members[0].name),
                    members[0],
                    tuple(members[1:]),
                    group_identity,
                )
                if len(members) > 1
                else members[0]
            )
    return SearchPage(tuple(groups), total_count, query.offset, query.limit, query.request_id)


def _edition_sort_key(value: str) -> tuple[object, ...]:
    if value.isdecimal():
        return (0, int(value), value)
    return (1, value.casefold(), value)


def list_monster_facets(connection: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """Available CR, type, and size choices from installed stat blocks."""
    cr_rows = connection.execute(
        "SELECT DISTINCT challenge_rating, cr_eighths FROM monsters ORDER BY cr_eighths"
    ).fetchall()
    return {
        "cr": tuple(str(row["challenge_rating"]) for row in cr_rows),
        "type": tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT creature_type FROM monsters ORDER BY creature_type COLLATE NOCASE"
            )
        ),
        "size": tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT size FROM monsters ORDER BY size COLLATE NOCASE"
            )
        ),
    }


def _entry_scope(category: "SearchCategory | None") -> tuple[str, list[object]]:
    if category is None:
        return "", []
    return " AND e.kind = ?", [category.storage_kind]


def list_available_editions(
    connection: sqlite3.Connection, category: "SearchCategory | None" = None
) -> tuple["EditionOption", ...]:
    """Return non-null editions used by entries in the requested scope."""

    from ..models import display_edition
    from ..search import EditionOption

    if category is not None and category.value == "subclasses":
        rows = connection.execute(
            "SELECT DISTINCT src.edition FROM subclasses AS s "
            "JOIN sources AS src ON src.id = s.source_id "
            "JOIN entries AS parent ON parent.id = s.class_id "
            "JOIN sources AS parent_src ON parent_src.id = parent.source_id "
            "WHERE src.edition IS NOT NULL AND src.edition = parent_src.edition"
        ).fetchall()
        values = sorted((str(row[0]) for row in rows), key=_edition_sort_key)
        return tuple(EditionOption(value, display_edition(value) or value) for value in values)
    category_sql, parameters = _entry_scope(category)
    rows = connection.execute(
        "SELECT DISTINCT src.edition FROM entries AS e "
        "JOIN sources AS src ON src.id = e.source_id "
        "WHERE src.edition IS NOT NULL" + category_sql,
        parameters,
    ).fetchall()
    values = sorted((str(row[0]) for row in rows), key=_edition_sort_key)
    return tuple(EditionOption(value, display_edition(value) or value) for value in values)


def list_available_sources(
    connection: sqlite3.Connection,
    category: "SearchCategory | None" = None,
    editions: tuple[str, ...] = (),
) -> tuple["SourceOption", ...]:
    """Return source identities that actually contribute entries to this scope."""

    if category is not None and category.value == "subclasses":
        where = ["src.edition IS NOT NULL", "src.edition = parent_src.edition"]
        values: list[object] = []
        if editions:
            where.append(f"src.edition IN ({', '.join('?' for _ in editions)})")
            values.extend(editions)
        rows = connection.execute(
            "SELECT DISTINCT src.dataset_id, src.source_key, src.title, src.edition "
            "FROM subclasses AS s JOIN sources AS src ON src.id = s.source_id "
            "JOIN entries AS parent ON parent.id = s.class_id "
            "JOIN sources AS parent_src ON parent_src.id = parent.source_id "
            "WHERE " + " AND ".join(where),
            values,
        ).fetchall()
        return _source_options(rows)
    category_sql, parameters = _entry_scope(category)
    where = ["1 = 1" + category_sql]
    values = list(parameters)
    if editions:
        where.append(f"src.edition IN ({', '.join('?' for _ in editions)})")
        values.extend(editions)
    rows = connection.execute(
        "SELECT DISTINCT src.dataset_id, src.source_key, src.title, src.edition "
        "FROM entries AS e JOIN sources AS src ON src.id = e.source_id "
        "WHERE " + " AND ".join(where),
        values,
    ).fetchall()
    return _source_options(rows)


def _source_options(rows: list[sqlite3.Row]) -> tuple["SourceOption", ...]:
    from ..search import SourceIdentity, SourceOption

    options = [
        SourceOption(
            SourceIdentity(str(row["dataset_id"]), str(row["source_key"])),
            str(row["title"]),
            str(row["edition"]) if row["edition"] is not None else None,
        )
        for row in rows
    ]
    options.sort(
        key=lambda option: (
            _edition_sort_key(option.edition) if option.edition is not None else (2, "", ""),
            option.title.casefold(),
            option.title,
            option.dataset_id,
            option.identity.source_key,
        )
    )
    return tuple(options)


def list_source_contents(connection: sqlite3.Connection) -> tuple["SourceBrowseInfo", ...]:
    """List installed searchable sources and their real per-category counts."""

    from ..search import SearchCategory, SourceBrowseInfo

    source_options = {option.identity: option for option in list_available_sources(connection)}
    for option in list_available_sources(connection, category=SearchCategory.SUBCLASSES):
        source_options[option.identity] = option
    options = tuple(source_options.values())
    rows = connection.execute(
        "SELECT src.dataset_id, src.source_key, e.kind, COUNT(*) AS entry_count "
        "FROM entries AS e JOIN sources AS src ON src.id = e.source_id "
        "GROUP BY src.dataset_id, src.source_key, e.kind"
    ).fetchall()
    counts: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row["dataset_id"]), str(row["source_key"]))
        counts.setdefault(key, {})[_category_for_kind(str(row["kind"]))] = int(row["entry_count"])
    subclass_rows = connection.execute(
        "SELECT src.dataset_id, src.source_key, COUNT(*) AS entry_count "
        "FROM subclasses AS s JOIN sources AS src ON src.id = s.source_id "
        "JOIN entries AS parent ON parent.id = s.class_id "
        "JOIN sources AS parent_src ON parent_src.id = parent.source_id "
        "WHERE src.edition IS NOT NULL AND src.edition = parent_src.edition "
        "GROUP BY src.dataset_id, src.source_key"
    ).fetchall()
    for row in subclass_rows:
        key = (str(row["dataset_id"]), str(row["source_key"]))
        counts.setdefault(key, {})[SearchCategory.SUBCLASSES] = int(row["entry_count"])
    return tuple(
        SourceBrowseInfo(option, counts.get((option.dataset_id, option.identity.source_key), {}))
        for option in options
    )


def get_entry_detail(
    connection: sqlite3.Connection,
    identity: str,
    asset_root: Path | None = None,
) -> "EntryDetail | None":
    """Retrieve one entry and its persisted structured children by stable identity."""

    from ..search import DetailSection, EntryDetail

    if not identity or ":" not in identity:
        return None
    dataset_id, local_key = identity.split(":", 1)
    if local_key.startswith("subclass:"):
        parent_key, separator, subclass_key = local_key.removeprefix("subclass:").partition(":")
        if not separator:
            return None
        return _get_subclass_detail(connection, dataset_id, parent_key, subclass_key)
    row = connection.execute(
        _SUMMARY_SELECT.replace("SELECT\n", "SELECT\n    e.description,\n", 1)
        + f" {_SUMMARY_FROM} WHERE e.dataset_id = ? AND e.local_key = ?",
        (dataset_id, local_key),
    ).fetchone()
    if row is None:
        return None

    entry_id = connection.execute(
        "SELECT id FROM entries WHERE dataset_id = ? AND local_key = ?",
        (dataset_id, local_key),
    ).fetchone()[0]
    kind = str(row["kind"])
    fields: dict[str, object] = {"edition": row["source_edition"]}
    table = {
        "item": "items",
        "spell": "spells",
        "feat": "feats",
        "class": "classes",
        "monster": "monsters",
        "condition": None,
        "rule": None,
    }[kind]
    category_row = (
        connection.execute(f"SELECT * FROM {table} WHERE entry_id = ?", (entry_id,)).fetchone()
        if table is not None
        else None
    )
    if category_row is not None:
        fields.update({key: category_row[key] for key in category_row.keys() if key != "entry_id"})
    if kind in {"condition", "rule"}:
        fields["section"] = connection.execute(
            "SELECT rule_section FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()[0]
    if kind == "monster":
        for key in ("speed_json", "abilities_json", "saving_throws_json", "skills_json"):
            fields[key.removesuffix("_json")] = json.loads(str(fields.pop(key)))
        fields["edition"] = row["source_edition"]
        fields["source_key"] = row["source_key"]
        fields["abilities_and_actions"] = tuple(
            dict(ability)
            for ability in connection.execute(
                "SELECT section, name, description, display_order, cost FROM monster_abilities "
                "WHERE monster_id = ? ORDER BY section, display_order",
                (entry_id,),
            ).fetchall()
        )
    sections = tuple(
        DetailSection(
            key=str(section["section_key"]),
            heading=str(section["heading"]),
            body=str(section["body"]),
            display_order=int(section["display_order"]),
        )
        for section in connection.execute(
            "SELECT section_key, heading, body, display_order FROM entry_sections "
            "WHERE entry_id = ? ORDER BY display_order, section_key",
            (entry_id,),
        ).fetchall()
    )
    if kind == "class":
        fields["edition"] = row["source_edition"]
        fields["compatible_subclasses"] = tuple(
            {
                "stable_id": summary.identity,
                "name": summary.name,
                "source_label": summary.source_label,
                "edition": summary.source_edition,
            }
            for summary in list_compatible_subclasses(connection, identity)
        )
        fields["progression_columns"] = tuple(
            dict(column)
            for column in connection.execute(
                "SELECT column_key, label, display_order FROM class_progression_columns "
                "WHERE class_id = ? ORDER BY display_order, column_key",
                (entry_id,),
            ).fetchall()
        )
        fields["progression_levels"] = tuple(
            dict(level)
            for level in connection.execute(
                "SELECT level, proficiency_bonus FROM class_levels "
                "WHERE class_id = ? ORDER BY level",
                (entry_id,),
            ).fetchall()
        )
        progression_values = tuple(
            dict(value)
            for value in connection.execute(
                "SELECT level, column_key, value FROM class_progression_values "
                "WHERE class_id = ? ORDER BY level, column_key",
                (entry_id,),
            ).fetchall()
        )
        fields["progression_values"] = progression_values
        fields["features"] = tuple(
            dict(feature)
            for feature in connection.execute(
                "SELECT feature_key, level, title, description, source_id, display_order "
                "FROM class_features WHERE class_id = ? ORDER BY level, display_order, feature_key",
                (entry_id,),
            ).fetchall()
        )
        subclasses: list[dict[str, object]] = []
        for subclass in connection.execute(
            "SELECT s.id, s.subclass_key, s.name, s.introduction, s.source_id, "
            "src.title AS source_label FROM subclasses AS s "
            "JOIN sources AS src ON src.id = s.source_id "
            "WHERE s.class_id = ? ORDER BY s.subclass_key",
            (entry_id,),
        ).fetchall():
            value = dict(subclass)
            value["features"] = tuple(
                dict(feature)
                for feature in connection.execute(
                    "SELECT feature_key, level, title, description, source_id, display_order "
                    "FROM subclass_features WHERE subclass_id = ? "
                    "ORDER BY level, display_order, feature_key",
                    (subclass["id"],),
                ).fetchall()
            )
            subclasses.append(value)
        fields["subclasses"] = tuple(subclasses)
    elif kind == "item":
        weapon = connection.execute(
            "SELECT damage_expression, damage_type, range, versatile_damage, mastery "
            "FROM weapon_details WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        armor = connection.execute(
            "SELECT armor_category, ac_expression, strength_requirement, stealth_disadvantage "
            "FROM armor_details WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if weapon is not None:
            fields["weapon"] = dict(weapon)
        if armor is not None:
            fields["armor"] = dict(armor)
        fields["properties"] = tuple(
            dict(property_row)
            for property_row in connection.execute(
                "SELECT ip.property_key, ip.name, ip.description, ipl.value "
                "FROM item_property_links AS ipl JOIN item_properties AS ip "
                "ON ip.id = ipl.property_id WHERE ipl.item_id = ? ORDER BY ip.property_key",
                (entry_id,),
            ).fetchall()
        )
    elif kind == "spell":
        fields["class_entry_ids"] = tuple(
            int(class_row["class_id"])
            for class_row in connection.execute(
                "SELECT class_id FROM spell_classes WHERE spell_id = ? ORDER BY class_id",
                (entry_id,),
            ).fetchall()
        )
        fields["class_names"] = tuple(
            str(class_row["name"])
            for class_row in connection.execute(
                "SELECT e.name FROM spell_classes AS sc "
                "JOIN entries AS e ON e.id = sc.class_id "
                "WHERE sc.spell_id = ? ORDER BY e.name, e.id",
                (entry_id,),
            ).fetchall()
        )
    image = None
    if row["image_stored_path"] is not None and asset_root is not None:
        from ..search import ImageAsset

        image = ImageAsset(
            path=Path(asset_root) / str(row["image_stored_path"]),
            media_type=str(row["image_media_type"]),
            content_hash=str(row["image_content_hash"]),
        )
    return EntryDetail(
        stable_id=str(row["stable_id"]),
        category=_category_for_kind(kind),
        name=str(row["name"]),
        description=str(row["description"]),
        source_label=str(row["source_label"]),
        dataset_id=dataset_id,
        local_key=local_key,
        dataset_title=str(row["dataset_title"]),
        fields=fields,
        sections=sections,
        image=image,
    )


def _get_subclass_detail(
    connection: sqlite3.Connection,
    dataset_id: str,
    parent_local_key: str,
    subclass_key: str,
) -> "EntryDetail | None":
    from ..search import EntryDetail, SearchCategory

    row = connection.execute(
        _SUBCLASS_DETAIL_SELECT
        + " AND s.dataset_id = ? AND parent.local_key = ? AND s.subclass_key = ?",
        (dataset_id, parent_local_key, subclass_key),
    ).fetchone()
    if row is None:
        return None
    feature_rows = connection.execute(
        "SELECT sf.feature_key, sf.level, sf.title, sf.description, sf.display_order, "
        "feature_src.title AS source_label FROM subclass_features AS sf "
        "LEFT JOIN sources AS feature_src ON feature_src.id = sf.source_id "
        "JOIN subclasses AS s ON s.id = sf.subclass_id "
        "JOIN entries AS parent ON parent.id = s.class_id "
        "WHERE s.dataset_id = ? AND parent.local_key = ? AND s.subclass_key = ? "
        "ORDER BY sf.level, sf.display_order, sf.feature_key",
        (dataset_id, parent_local_key, subclass_key),
    ).fetchall()
    parent = connection.execute(
        "SELECT e.dataset_id, e.local_key FROM entries AS e "
        "JOIN sources AS src ON src.id = e.source_id "
        "WHERE e.kind = 'class' AND e.normalized_name = ? AND src.edition = ? "
        "ORDER BY CASE WHEN e.dataset_id = ? THEN 0 ELSE 1 END, "
        "e.dataset_id, e.local_key LIMIT 1",
        (row["parent_key"], row["source_edition"], dataset_id),
    ).fetchone()
    fields: dict[str, object] = {
        "parent_class": str(row["parent_name"]),
        "parent_class_key": str(row["parent_key"]),
        "edition": str(row["source_edition"]),
        "parent_class_identity": (
            f"{parent['dataset_id']}:{parent['local_key']}" if parent is not None else None
        ),
        "features": tuple(dict(feature) for feature in feature_rows),
    }
    return EntryDetail(
        stable_id=f"{dataset_id}:subclass:{parent_local_key}:{subclass_key}",
        category=SearchCategory.SUBCLASSES,
        name=str(row["name"]),
        description=str(row["introduction"]),
        source_label=str(row["source_label"]),
        dataset_id=dataset_id,
        local_key=subclass_key,
        dataset_title=str(row["dataset_title"]),
        fields=fields,
        sections=(),
    )
