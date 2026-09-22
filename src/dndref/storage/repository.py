"""Direct SQL persistence for validated dataset packs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from ..search import normalize_name

if TYPE_CHECKING:
    from ..importer import LoadedDataset
    from ..search import DatasetMetadata, EntryDetail, SearchPage, SearchQuery


class RepositoryError(RuntimeError):
    """Raised when a validated dataset cannot be persisted."""


@dataclass(frozen=True)
class InstalledDataset:
    dataset_id: str
    title: str
    version: str
    content_hash: str
    entry_hashes: dict[str, str]


def _category_for_kind(kind: str):
    from ..search import SearchCategory

    return {
        "item": SearchCategory.ITEMS,
        "spell": SearchCategory.SPELLS,
        "feat": SearchCategory.FEATS,
        "class": SearchCategory.CLASSES,
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
    return _search_text(*parts)


def rebuild_search_index(connection: sqlite3.Connection, dataset_id: str | None = None) -> None:
    """Rebuild affected FTS rows inside the caller's transaction."""

    try:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entry_search'"
        ).fetchone() is None:
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
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entries'"
        ).fetchone() is None:
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
        row = connection.execute(
            "SELECT dataset_id, title, version, content_hash FROM datasets WHERE dataset_id = ?",
            (dataset_id,),
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
    )


def list_installed_datasets(connection: sqlite3.Connection) -> tuple[InstalledDataset, ...]:
    """Return installed dataset metadata in stable order."""
    try:
        rows = connection.execute(
            "SELECT dataset_id, title, version, content_hash FROM datasets ORDER BY dataset_id"
        ).fetchall()
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
        connection.execute(
            "INSERT INTO datasets "
            "(dataset_id, title, version, ruleset, language, license_identifier, "
            "attribution, origin_url, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dataset_id,
                manifest.title,
                manifest.version,
                manifest.ruleset,
                manifest.language,
                manifest.license_identifier,
                manifest.attribution,
                str(manifest.origin_url) if manifest.origin_url is not None else None,
                loaded.content_hash,
            ),
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

        entries = [
            ("item", item) for item in pack.items.items
        ] + [
            ("spell", spell) for spell in pack.spells
        ] + [
            ("feat", feat) for feat in pack.feats
        ] + [
            ("class", character_class) for character_class in pack.classes
        ]
        for kind, entry in sorted(entries, key=lambda item: item[1].local_key):
            cursor = connection.execute(
                "INSERT INTO entries "
                "(dataset_id, local_key, kind, name, normalized_name, description, source_id, "
                "image_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    entry.local_key,
                    kind,
                    entry.name,
                    normalize_name(entry.name),
                    entry.description,
                    source_ids[entry.source],
                    image_ids.get(entry.image),
                    loaded.entry_hashes[entry.local_key],
                ),
            )
            entry_ids[str(entry.local_key)] = int(cursor.lastrowid)
            sections = list(entry.sections)
            if kind == "feat":
                sections.extend(entry.benefits)
            _insert_sections(connection, int(cursor.lastrowid), sections)

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
                    (dataset_id, spell_id, class_ids[str(reference.split(':', 1)[-1])])
                    for reference in spell.class_references
                ],
            )
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


def _name_match_sql(tokens: tuple[str, ...]) -> str:
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
"""

_SUMMARY_SELECT = """
SELECT
    e.dataset_id || ':' || e.local_key AS stable_id,
    e.dataset_id,
    e.local_key,
    e.kind,
    e.name,
    img.stored_path AS image_stored_path,
    img.media_type AS image_media_type,
    img.content_hash AS image_content_hash,
    src.title AS source_label,
    d.title AS dataset_title,
    CASE e.kind
        WHEN 'item' THEN COALESCE(NULLIF(i.subtype, ''), i.item_type, 'Item')
        WHEN 'spell' THEN
            CASE WHEN sp.level = 0 THEN 'Cantrip' ELSE 'Level ' || sp.level END
            || ' · ' || sp.school
        WHEN 'feat' THEN f.category
        WHEN 'class' THEN 'd' || c.hit_die || ' Hit Die'
    END AS subtitle
"""


def search_entries(connection: sqlite3.Connection, query: "SearchQuery") -> "SearchPage":
    """Run a paged, category-scoped search using the caller's connection."""

    from ..search import EntrySummary, SearchMode, SearchPage

    category_kind = query.category.storage_kind
    tokens = query.tokens
    fts_tokens = query.fts_tokens
    where = ["e.kind = ?"]
    where_params: list[object] = [category_kind]
    name_match = _name_match_sql(tokens)
    if tokens:
        where.append(f"({name_match})")
        where_params.extend(tokens)
        if query.mode is SearchMode.ALL_TEXT:
            where[-1] = (
                f"(({name_match}) OR e.id IN "
                "(SELECT entry_id FROM entry_search WHERE entry_search MATCH ?))"
            )
            where_params.append(_fts_query(fts_tokens))

    where_sql = " AND ".join(where)
    try:
        total_count = int(
            connection.execute(
                f"SELECT COUNT(*) {_SUMMARY_FROM} WHERE {where_sql}", where_params
            ).fetchone()[0]
        )
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
            order_params.extend(tokens)
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

    results = tuple(
        EntrySummary(
            stable_id=str(row["stable_id"]),
            category=_category_for_kind(str(row["kind"])),
            name=str(row["name"]),
            subtitle=str(row["subtitle"]),
            source_label=str(row["source_label"]),
            dataset_id=str(row["dataset_id"]),
            local_key=str(row["local_key"]),
            dataset_title=str(row["dataset_title"]),
        )
        for row in rows
    )
    return SearchPage(results, total_count, query.offset, query.limit, query.request_id)


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
    row = connection.execute(
        _SUMMARY_SELECT.replace(
            "SELECT\n", "SELECT\n    e.description,\n", 1
        )
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
    fields: dict[str, object] = {}
    table = {"item": "items", "spell": "spells", "feat": "feats", "class": "classes"}[kind]
    category_row = connection.execute(
        f"SELECT * FROM {table} WHERE entry_id = ?", (entry_id,)
    ).fetchone()
    if category_row is not None:
        fields.update({key: category_row[key] for key in category_row.keys() if key != "entry_id"})
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
