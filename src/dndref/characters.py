"""UI-independent persistence and validation for saved 2024 characters."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Iterable, Mapping

from .character_builder import CharacterBuilderRules, normalize_feat_category
from .models.character import (
    AbilityChange,
    AbilityModification,
    Character,
    CharacterLevel,
    CharacterSummary,
    CharacterValidationIssue,
    CharacterValidationReport,
    ChoiceResolution,
    CurrencyRecord,
    DecisionProvenance,
    EquipmentRecord,
    FeatSelection,
    HitPointChoice,
    PublishedReference,
    SpellSelection,
    SubclassSelection,
)
from .models.character_builder import CharacterRuleContext, RequirementEvaluation
from .storage.database import Database

_ABILITIES = frozenset({"str", "dex", "con", "int", "wis", "cha"})
_REFERENCE_KINDS = frozenset(
    {
        "class",
        "subclass",
        "species",
        "background",
        "feat",
        "spell",
        "item",
        "rule",
        "optional_feature",
    }
)
_OWNER_TYPES = frozenset({"class", "subclass", "species", "background", "feat"})
_SPELL_ACQUISITIONS = frozenset(
    {"cantrip", "known", "prepared", "always_prepared", "spellbook", "pact_magic", "innate"}
)


class CharacterNotFoundError(LookupError):
    """Raised when a requested character ID is not present."""


class CharacterService:
    """Persist character inputs without making any derived game statistic authoritative."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_character(
        self,
        name: str,
        edition: str = "2024",
        *,
        state: str = "draft",
        name_confirmed: bool = True,
    ) -> Character:
        cleaned = _clean_name(name)
        _require_edition(edition)
        _require_state(state)
        character_id = str(uuid.uuid4())
        now = _timestamp()
        with self.database.connection() as db, db:
            db.execute(
                "INSERT INTO user_characters "
                "(character_id,name,edition,state,schema_version,creation_name_confirmed,"
                "created_at,updated_at) VALUES (?,?,?, ?,1,?,?,?)",
                (character_id, cleaned, edition, state, int(name_confirmed), now, now),
            )
        return self.get_character(character_id)

    def get_character(self, character_id: str) -> Character:
        with self.database.connection() as db:
            return self._get_character(db, character_id)

    def run_with_rollback(self, character_id: str, operation):
        """Restore the exact saved aggregate if a coordinated creation change fails."""

        snapshot = self._capture_character_rows(character_id)
        try:
            return operation()
        except Exception:
            self._restore_character_rows(character_id, snapshot)
            raise

    def _capture_character_rows(
        self, character_id: str
    ) -> dict[str, tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]]:
        with self.database.connection() as db:
            parent = db.execute(
                "SELECT * FROM user_characters WHERE character_id=?", (character_id,)
            )
            parent_row = parent.fetchone()
            if parent_row is None:
                raise CharacterNotFoundError(f"character '{character_id}' does not exist")
            tables = [
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name LIKE 'user_character_%' ORDER BY name"
                ).fetchall()
            ]
            snapshot = {
                "user_characters": (
                    tuple(parent_row.keys()),
                    (tuple(parent_row),),
                )
            }
            for table in tables:
                columns = tuple(str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")'))
                if "character_id" not in columns:
                    continue
                rows = db.execute(
                    f'SELECT * FROM "{table}" WHERE character_id=?', (character_id,)
                ).fetchall()
                snapshot[table] = (columns, tuple(tuple(row) for row in rows))
        return snapshot

    def _restore_character_rows(
        self,
        character_id: str,
        snapshot: dict[str, tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]],
    ) -> None:
        with self.database.connection() as db, db:
            db.execute("DELETE FROM user_characters WHERE character_id=?", (character_id,))
            for table in (
                "user_characters",
                *sorted(name for name in snapshot if name != "user_characters"),
            ):
                columns, rows = snapshot[table]
                if not rows:
                    continue
                quoted_columns = ",".join(f'"{column}"' for column in columns)
                placeholders = ",".join("?" for _ in columns)
                db.executemany(
                    f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
                    rows,
                )

    def list_characters(self) -> tuple[CharacterSummary, ...]:
        """List names and ordered class summaries with one bounded summary query."""

        sql = """
            SELECT c.character_id, c.name, c.edition, c.state, c.updated_at,
                   COALESCE(levels.total_level, 0) AS total_level,
                   COALESCE(classes.class_summary, '') AS class_summary
            FROM user_characters AS c
            LEFT JOIN (
                SELECT character_id, COUNT(*) AS total_level
                FROM user_character_levels GROUP BY character_id
            ) AS levels USING (character_id)
            LEFT JOIN (
                SELECT character_id, group_concat(track_summary, ' / ') AS class_summary
                FROM (
                    SELECT character_id,
                           class_name || ' ' || MAX(resulting_class_level) AS track_summary,
                           MIN(total_level) AS first_taken
                    FROM user_character_levels
                    GROUP BY character_id, class_identity, class_name
                    ORDER BY character_id, first_taken
                ) GROUP BY character_id
            ) AS classes USING (character_id)
            ORDER BY c.name COLLATE NOCASE, c.character_id
        """
        profile = (
            self.database.profiler.operation("characters.list")
            if self.database.profiler is not None
            else nullcontext()
        )
        with profile, self.database.connection() as db:
            rows = db.execute(sql).fetchall()
        return tuple(
            CharacterSummary(
                character_id=str(row[0]),
                name=str(row[1]),
                edition=str(row[2]),
                state=str(row[3]),
                updated_at=str(row[4]),
                total_level=int(row[5]),
                class_summary=str(row[6]),
            )
            for row in rows
        )

    def rename_character(self, character_id: str, name: str) -> Character:
        cleaned = _clean_name(name)
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            db.execute(
                "UPDATE user_characters SET name=?,creation_name_confirmed=1 WHERE character_id=?",
                (cleaned, character_id),
            )
            _touch(db, character_id)
        return self.get_character(character_id)

    def set_character_state(self, character_id: str, state: str) -> Character:
        _require_state(state)
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            db.execute(
                "UPDATE user_characters SET state=? WHERE character_id=?", (state, character_id)
            )
            _touch(db, character_id)
        return self.get_character(character_id)

    def duplicate_character(self, character_id: str, name: str | None = None) -> Character:
        """Copy the complete character aggregate atomically to a new stable ID."""

        new_id = str(uuid.uuid4())
        now = _timestamp()
        with self.database.connection() as db, db:
            original = self._require_character(db, character_id)
            copy_name = _clean_name(name if name is not None else f"{original['name']} Copy")
            db.execute(
                "INSERT INTO user_characters "
                "(character_id,name,edition,state,schema_version,species_identity,species_name,"
                "species_edition,background_identity,background_name,background_edition,"
                "creation_name_confirmed,ability_score_method,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id,
                    copy_name,
                    original["edition"],
                    original["state"],
                    original["schema_version"],
                    original["species_identity"],
                    original["species_name"],
                    original["species_edition"],
                    original["background_identity"],
                    original["background_name"],
                    original["background_edition"],
                    original["creation_name_confirmed"],
                    original["ability_score_method"],
                    now,
                    now,
                ),
            )
            self._copy_child_rows(db, character_id, new_id)
        return self.get_character(new_id)

    @staticmethod
    def _copy_child_rows(db: sqlite3.Connection, original_id: str, new_id: str) -> None:
        """Copy all child state inside the caller's transaction."""

        columns_by_table = {
            "user_character_levels": (
                "total_level,class_identity,class_name,resulting_class_level,edition"
            ),
            "user_character_subclasses": (
                "class_identity,class_name,subclass_identity,subclass_name,"
                "selected_class_level,edition"
            ),
            "user_character_ability_scores": "ability_key,base_score",
            "user_character_ability_modifications": (
                "ability_key,amount,source_kind,source_label,"
                "source_owner_dataset_id,source_owner_type,source_owner_key,source_choice_key,"
                "source_option_key,character_level,class_identity,class_level,source_rule"
            ),
            "user_character_choices": (
                "owner_dataset_id,owner_type,owner_key,choice_key,selected_option_key,"
                "selected_reference_kind,selected_reference_identity,selected_reference_name,"
                "selected_reference_edition,selected_value,resolution_state,selection_fingerprint,"
                "context_key,character_level,class_identity,class_level,source_rule"
            ),
            "user_character_feats": (
                "feat_identity,feat_name,edition,provenance_kind,provenance_label,"
                "source_owner_dataset_id,source_owner_type,source_owner_key,source_choice_key,"
                "source_option_key,character_level,class_identity,class_level,source_rule,"
                "resolution_state"
            ),
            "user_character_spells": (
                "spell_identity,spell_name,edition,acquisition,source_class_identity,"
                "source_class_name,character_level,class_level,source_owner_dataset_id,"
                "source_owner_type,source_owner_key,source_choice_key,source_option_key,source_rule,"
                "resolution_state"
            ),
            "user_character_equipment": (
                "item_identity,item_name,unresolved_selection,edition,quantity,equipped,"
                "carried_state,provenance_kind,provenance_label,source_owner_dataset_id,"
                "source_owner_type,source_owner_key,source_choice_key,source_option_key,"
                "character_level,class_identity,class_level,source_rule,resolution_state"
            ),
            "user_character_currency": (
                "currency_key,amount,provenance_kind,provenance_label,source_owner_dataset_id,"
                "source_owner_type,source_owner_key,source_choice_key,source_option_key,source_rule"
            ),
            "user_character_hp_choices": (
                "total_level,class_identity,class_level,choice_kind,amount,edition"
            ),
            "user_character_notes": "note_text",
        }
        for table, columns in columns_by_table.items():
            names = columns.split(",")
            rows = db.execute(
                f"SELECT {columns} FROM {table} WHERE character_id=?", (original_id,)
            ).fetchall()
            if rows:
                placeholders = ",".join("?" for _ in range(len(names) + 1))
                db.executemany(
                    f"INSERT INTO {table} (character_id,{columns}) VALUES ({placeholders})",
                    [(new_id, *tuple(row)) for row in rows],
                )

    def delete_character(self, character_id: str) -> bool:
        with self.database.connection() as db, db:
            cursor = db.execute("DELETE FROM user_characters WHERE character_id=?", (character_id,))
            return cursor.rowcount > 0

    def set_species(self, character_id: str, reference: PublishedReference | None) -> Character:
        return self._set_top_level_reference(character_id, "species", reference)

    def set_background(self, character_id: str, reference: PublishedReference | None) -> Character:
        return self._set_top_level_reference(character_id, "background", reference)

    def replace_species(self, character_id: str, reference: PublishedReference) -> Character:
        """Replace Species and atomically remove only decisions owned by the prior Species."""

        return self._replace_creation_reference(character_id, "species", reference)

    def replace_background(self, character_id: str, reference: PublishedReference) -> Character:
        """Replace Background and atomically remove its exact owned decisions and outcomes."""

        return self._replace_creation_reference(character_id, "background", reference)

    def _replace_creation_reference(
        self, character_id: str, kind: str, reference: PublishedReference
    ) -> Character:
        if kind not in {"species", "background"}:
            raise ValueError("creation reference must be Species or Background")
        with self.database.connection() as db, db:
            character = self._require_character(db, character_id)
            actual = self._require_live_reference(db, reference, kind)
            previous_identity = character[f"{kind}_identity"]
            if previous_identity == actual.identity:
                return self._get_character(db, character_id)
            if previous_identity is not None:
                dataset_id, owner_key = _direct_parts(str(previous_identity))
                self._delete_builder_owner_state(db, character_id, dataset_id, kind, owner_key)
            db.execute(
                f"UPDATE user_characters SET {kind}_identity=?,{kind}_name=?,"
                f"{kind}_edition=?,state='draft' WHERE character_id=?",
                (actual.identity, actual.name, actual.edition, character_id),
            )
            _touch(db, character_id)
        return self.get_character(character_id)

    def replace_starting_class(self, character_id: str, reference: PublishedReference) -> Character:
        """Replace the one level-1 class and its exact class-owned state in one transaction."""

        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            actual = self._require_live_reference(db, reference, "class")
            levels = db.execute(
                "SELECT total_level,class_identity,class_name,resulting_class_level,edition "
                "FROM user_character_levels WHERE character_id=? ORDER BY total_level",
                (character_id,),
            ).fetchall()
            if len(levels) > 1:
                raise ValueError("the creation editor only supports a single level-1 class")
            if levels and str(levels[0][1]) == actual.identity:
                return self._get_character(db, character_id)
            if levels:
                old_identity = str(levels[0][1])
                old_dataset, old_key = _direct_parts(old_identity)
                self._delete_builder_owner_state(db, character_id, old_dataset, "class", old_key)
                db.execute(
                    "DELETE FROM user_character_subclasses WHERE character_id=? "
                    "AND class_identity=?",
                    (character_id, old_identity),
                )
                db.execute(
                    "DELETE FROM user_character_hp_choices WHERE character_id=? AND total_level=1",
                    (character_id,),
                )
                db.execute(
                    "DELETE FROM user_character_levels WHERE character_id=? AND total_level=1",
                    (character_id,),
                )
            db.execute(
                "INSERT INTO user_character_levels "
                "(character_id,total_level,class_identity,class_name,"
                "resulting_class_level,edition) "
                "VALUES (?,1,?,?,1,?)",
                (character_id, actual.identity, actual.name, actual.edition),
            )
            db.execute(
                "UPDATE user_characters SET state='draft' WHERE character_id=?", (character_id,)
            )
            _touch(db, character_id)
        return self.get_character(character_id)

    @staticmethod
    def _delete_builder_owner_state(
        db: sqlite3.Connection,
        character_id: str,
        dataset_id: str,
        owner_type: str,
        owner_key: str,
        _visited: set[tuple[str, str, str]] | None = None,
    ) -> None:
        visited = _visited if _visited is not None else set()
        identity = (dataset_id, owner_type, owner_key)
        if identity in visited:
            return
        visited.add(identity)
        granted_feats = [
            str(row[0])
            for row in db.execute(
                "SELECT feat_identity FROM user_character_feats WHERE character_id=? "
                "AND source_owner_dataset_id=? AND source_owner_type=? AND source_owner_key=?",
                (character_id, dataset_id, owner_type, owner_key),
            ).fetchall()
        ]
        owner_params = (character_id, dataset_id, owner_type, owner_key)
        for table, prefix in (
            ("user_character_choices", ""),
            ("user_character_ability_modifications", "source_"),
            ("user_character_feats", "source_"),
            ("user_character_spells", "source_"),
            ("user_character_equipment", "source_"),
            ("user_character_currency", "source_"),
        ):
            db.execute(
                f"DELETE FROM {table} WHERE character_id=? AND {prefix}owner_dataset_id=? "
                f"AND {prefix}owner_type=? AND {prefix}owner_key=?",
                owner_params,
            )
        CharacterService._delete_unreferenced_feat_owners(db, character_id, granted_feats, visited)

    @staticmethod
    def _delete_unreferenced_feat_owners(
        db: sqlite3.Connection,
        character_id: str,
        feat_identities: Iterable[str],
        visited: set[tuple[str, str, str]],
    ) -> None:
        for feat_identity in set(feat_identities):
            try:
                dataset_id, feat_key = _direct_parts(feat_identity)
            except ValueError:
                continue
            if db.execute(
                "SELECT 1 FROM user_character_feats WHERE character_id=? "
                "AND feat_identity=? LIMIT 1",
                (character_id, feat_identity),
            ).fetchone():
                continue
            CharacterService._delete_builder_owner_state(
                db, character_id, dataset_id, "feat", feat_key, visited
            )

    def _set_top_level_reference(
        self, character_id: str, kind: str, reference: PublishedReference | None
    ) -> Character:
        if kind not in {"species", "background"}:
            raise ValueError("only species or background can be set here")
        with self.database.connection() as db, db:
            character = self._require_character(db, character_id)
            if reference is None:
                db.execute(
                    f"UPDATE user_characters SET {kind}_identity=NULL,{kind}_name=NULL,"
                    f"{kind}_edition=NULL WHERE character_id=?",
                    (character_id,),
                )
            else:
                actual = self._require_live_reference(db, reference, kind)
                db.execute(
                    f"UPDATE user_characters SET {kind}_identity=?,{kind}_name=?,"
                    f"{kind}_edition=? WHERE character_id=?",
                    (actual.identity, actual.name, actual.edition, character_id),
                )
            if character:
                _touch(db, character_id)
        return self.get_character(character_id)

    def add_class_level(
        self, character_id: str, class_reference: PublishedReference
    ) -> CharacterLevel:
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            actual = self._require_live_reference(db, class_reference, "class")
            rows = db.execute(
                "SELECT total_level,class_identity,resulting_class_level FROM "
                "user_character_levels WHERE character_id=? ORDER BY total_level",
                (character_id,),
            ).fetchall()
            if len(rows) >= 20:
                raise ValueError("character level cannot exceed 20")
            if [int(row[0]) for row in rows] != list(range(1, len(rows) + 1)):
                raise ValueError("existing character level history is not contiguous")
            track = [row for row in rows if str(row[1]) == actual.identity]
            class_level = len(track) + 1
            if track and int(track[-1][2]) != len(track):
                raise ValueError("existing class track is not contiguous")
            total_level = len(rows) + 1
            db.execute(
                "INSERT INTO user_character_levels "
                "(character_id,total_level,class_identity,class_name,"
                "resulting_class_level,edition) "
                "VALUES (?,?,?,?,?,?)",
                (
                    character_id,
                    total_level,
                    actual.identity,
                    actual.name,
                    class_level,
                    actual.edition,
                ),
            )
            _touch(db, character_id)
        return CharacterLevel(total_level, actual, class_level)

    def remove_last_class_level(self, character_id: str) -> CharacterLevel:
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            row = db.execute(
                "SELECT total_level,class_identity,class_name,resulting_class_level,edition "
                "FROM user_character_levels WHERE character_id=? ORDER BY total_level DESC LIMIT 1",
                (character_id,),
            ).fetchone()
            if row is None:
                raise ValueError("character has no class levels")
            total_level = int(row[0])
            if total_level != int(
                db.execute(
                    "SELECT COUNT(*) FROM user_character_levels WHERE character_id=?",
                    (character_id,),
                ).fetchone()[0]
            ):
                raise ValueError("existing character level history is not contiguous")
            class_identity = str(row[1])
            class_level = int(row[3])
            snapshot = PublishedReference("class", class_identity, str(row[2]), str(row[4]))
            db.execute(
                "DELETE FROM user_character_levels WHERE character_id=? AND total_level=?",
                (character_id, total_level),
            )
            remaining_class_level = class_level - 1
            self._delete_level_bound_rows(
                db, character_id, total_level, class_identity, remaining_class_level
            )
            _touch(db, character_id)
        return CharacterLevel(total_level, snapshot, class_level)

    @staticmethod
    def _delete_level_bound_rows(
        db: sqlite3.Connection,
        character_id: str,
        removed_total_level: int,
        class_identity: str,
        remaining_class_level: int,
    ) -> None:
        db.execute(
            "DELETE FROM user_character_currency WHERE character_id=? AND EXISTS ("
            "SELECT 1 FROM user_character_choices c WHERE c.character_id=? "
            "AND c.owner_dataset_id=user_character_currency.source_owner_dataset_id "
            "AND c.owner_type=user_character_currency.source_owner_type "
            "AND c.owner_key=user_character_currency.source_owner_key "
            "AND c.choice_key=user_character_currency.source_choice_key "
            "AND (c.selected_option_key=user_character_currency.source_option_key "
            "OR (c.selected_option_key IS NULL AND "
            "user_character_currency.source_option_key IS NULL)) "
            "AND (c.character_level>=? OR (c.class_identity=? AND c.class_level>?)))",
            (
                character_id,
                character_id,
                removed_total_level,
                class_identity,
                remaining_class_level,
            ),
        )
        for table in (
            "user_character_choices",
            "user_character_feats",
            "user_character_ability_modifications",
            "user_character_equipment",
        ):
            db.execute(
                f"DELETE FROM {table} WHERE character_id=? AND "
                "(character_level>=? OR (class_identity=? AND class_level>?))",
                (character_id, removed_total_level, class_identity, remaining_class_level),
            )
        db.execute(
            "DELETE FROM user_character_spells WHERE character_id=? AND "
            "(character_level>=? OR (source_class_identity=? AND class_level>?))",
            (character_id, removed_total_level, class_identity, remaining_class_level),
        )
        db.execute(
            "DELETE FROM user_character_hp_choices WHERE character_id=? AND total_level>=?",
            (character_id, removed_total_level),
        )
        db.execute(
            "DELETE FROM user_character_subclasses WHERE character_id=? AND class_identity=? "
            "AND selected_class_level>?",
            (character_id, class_identity, remaining_class_level),
        )

    def set_subclass(
        self,
        character_id: str,
        class_identity: str,
        subclass_reference: PublishedReference,
        selected_class_level: int | None = None,
    ) -> SubclassSelection:
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            track = db.execute(
                "SELECT class_name,MAX(resulting_class_level) FROM user_character_levels "
                "WHERE character_id=? AND class_identity=? GROUP BY class_name",
                (character_id, class_identity),
            ).fetchone()
            if track is None:
                raise ValueError("subclass parent class is not in this character")
            class_name = str(track[0])
            current_class_level = int(track[1])
            actual = self._require_live_reference(db, subclass_reference, "subclass")
            parent_identity = _subclass_parent_identity(actual.identity)
            if parent_identity != class_identity:
                raise ValueError("subclass does not belong to the selected class identity")
            chosen_level = selected_class_level
            minimum_level = self._subclass_selection_level(db, actual.identity)
            if chosen_level is None:
                chosen_level = minimum_level if minimum_level is not None else current_class_level
            if chosen_level < 1 or chosen_level > current_class_level:
                raise ValueError("subclass selection level must exist in the class history")
            if minimum_level is not None and chosen_level < minimum_level:
                raise ValueError(f"subclass is not available before class level {minimum_level}")
            db.execute(
                "INSERT INTO user_character_subclasses "
                "(character_id,class_identity,class_name,subclass_identity,subclass_name,"
                "selected_class_level,edition) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(character_id,class_identity) DO UPDATE SET "
                "class_name=excluded.class_name,subclass_identity=excluded.subclass_identity,"
                "subclass_name=excluded.subclass_name,selected_class_level=excluded.selected_class_level,"
                "edition=excluded.edition",
                (
                    character_id,
                    class_identity,
                    class_name,
                    actual.identity,
                    actual.name,
                    chosen_level,
                    actual.edition,
                ),
            )
            _touch(db, character_id)
        return SubclassSelection(
            PublishedReference("class", class_identity, class_name, "2024"),
            actual,
            chosen_level,
        )

    def validate_subclass_selection(
        self,
        character_id: str,
        class_identity: str,
        subclass_reference: PublishedReference,
        rules: CharacterBuilderRules,
        *,
        rules_dataset_id: str,
    ) -> RequirementEvaluation:
        from .models.character_builder import RequirementStatus

        character = self.get_character(character_id)
        levels = character.class_levels.get(class_identity, 0)
        if levels == 0:
            return RequirementEvaluation(status="unsatisfied", reason="parent class is not present")
        if subclass_reference.kind != "subclass" or subclass_reference.edition != "2024":
            return RequirementEvaluation(
                status="unsatisfied", reason="reference is not a published 2024 subclass"
            )
        if _subclass_parent_identity(subclass_reference.identity) != class_identity:
            return RequirementEvaluation(
                status="unsatisfied", reason="subclass has the wrong parent class"
            )
        subclass_dataset, _parent_key, subclass_key = _subclass_parts(subclass_reference.identity)
        class_dataset, class_key = _direct_parts(class_identity)
        if subclass_dataset != rules_dataset_id or class_dataset != rules_dataset_id:
            return RequirementEvaluation(
                status="unresolved",
                reason="rules catalog does not match the exact published identities",
            )
        with self.database.connection() as db:
            live = _lookup_reference(db, "subclass", subclass_reference.identity)
            if live is None:
                return RequirementEvaluation(
                    status="unresolved", reason="subclass reference is missing"
                )
            if live[1] != "2024":
                return RequirementEvaluation(
                    status="unsatisfied", reason="subclass reference is not from 2024"
                )
        candidates = rules.get_available_subclasses(class_key, levels)
        match = next((row for row in candidates if str(row.subclass_key) == subclass_key), None)
        if match is None:
            known = next(
                (row for row in rules.catalog.subclasses if str(row.subclass_key) == subclass_key),
                None,
            )
            if known is None:
                return RequirementEvaluation(
                    status="unresolved", reason="subclass is absent from the rules catalog"
                )
            return RequirementEvaluation(
                status="unsatisfied",
                reason=f"subclass is available at class level {known.selection_level}",
            )
        return RequirementEvaluation(
            status=RequirementStatus.SATISFIED, reason="subclass parent and level are valid"
        )

    def set_ability_state(
        self,
        character_id: str,
        base_scores: Mapping[str, int],
        modifications: Iterable[AbilityChange] = (),
        *,
        method: str | None = None,
    ) -> Character:
        normalized_scores = _normalize_base_scores(base_scores)
        normalized_changes = tuple(modifications)
        if method is not None and method not in {"standard_array", "point_buy", "manual"}:
            raise ValueError("ability score method must be standard_array, point_buy, or manual")
        for change in normalized_changes:
            _validate_ability_change(change)
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            db.execute(
                "DELETE FROM user_character_ability_scores WHERE character_id=?", (character_id,)
            )
            db.executemany(
                "INSERT INTO user_character_ability_scores(character_id,ability_key,base_score) "
                "VALUES (?,?,?)",
                [(character_id, ability, score) for ability, score in normalized_scores.items()],
            )
            db.execute(
                "DELETE FROM user_character_ability_modifications WHERE character_id=?",
                (character_id,),
            )
            db.executemany(
                "INSERT INTO user_character_ability_modifications "
                "(character_id,ability_key,amount,source_kind,source_label,"
                "source_owner_dataset_id,source_owner_type,source_owner_key,source_choice_key,"
                "source_option_key,character_level,class_identity,class_level,source_rule) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        character_id,
                        change.ability.lower(),
                        change.amount,
                        change.source_kind,
                        change.source_label.strip(),
                        change.provenance.owner_dataset_id,
                        change.provenance.owner_type,
                        change.provenance.owner_key,
                        change.provenance.choice_key,
                        change.provenance.option_key,
                        change.provenance.character_level,
                        change.provenance.class_identity,
                        change.provenance.class_level,
                        change.provenance.source_rule,
                    )
                    for change in normalized_changes
                ],
            )
            if method is not None:
                db.execute(
                    "UPDATE user_characters SET ability_score_method=? WHERE character_id=?",
                    (method, character_id),
                )
            _touch(db, character_id)
        return self.get_character(character_id)

    def resolve_choice(
        self,
        character_id: str,
        *,
        rules: CharacterBuilderRules,
        rules_dataset_id: str,
        owner_type: str,
        owner_key: str,
        choice_key: str,
        selected_option_key: str | None = None,
        selected_reference: PublishedReference | None = None,
        selected_value: str | None = None,
        character_level: int | None = None,
        class_identity: str | None = None,
        class_level: int | None = None,
        allow_unresolved: bool = False,
        replace_existing: bool = False,
    ) -> ChoiceResolution:
        if owner_type not in _OWNER_TYPES:
            raise ValueError("unknown builder choice owner type")
        definition = rules.get_choice_definition(owner_type, owner_key, choice_key)
        if definition is None:
            raise ValueError("choice does not exist for this owner identity")
        if character_level is not None and not 1 <= character_level <= 20:
            raise ValueError("choice character level must be between 1 and 20")
        if class_level is not None and not 1 <= class_level <= 20:
            raise ValueError("choice class level must be between 1 and 20")
        if class_identity is not None and class_level is None:
            raise ValueError("class-level choice context requires class_level")
        if class_identity is None and class_level is not None:
            raise ValueError("class-level choice context requires class_identity")

        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            self._validate_character_context(
                db, character_id, character_level, class_identity, class_level
            )
            self._require_choice_owner(db, rules_dataset_id, owner_type, owner_key)
            persisted = self._require_persisted_choice(
                db, rules_dataset_id, owner_type, owner_key, choice_key, definition
            )
            context_key = _context_key(character_level, class_identity, class_level)
            if definition.depends_on_choice is not None:
                parent = db.execute(
                    "SELECT 1 FROM user_character_choices WHERE character_id=? "
                    "AND owner_dataset_id=? AND owner_type=? AND owner_key=? AND choice_key=? "
                    "AND selected_option_key=? AND context_key=? LIMIT 1",
                    (
                        character_id,
                        rules_dataset_id,
                        owner_type,
                        owner_key,
                        str(definition.depends_on_choice),
                        str(definition.depends_on_option),
                        context_key,
                    ),
                ).fetchone()
                if parent is None:
                    raise ValueError("conditional choice is not enabled by its parent selection")
            if replace_existing:
                self._delete_choice_tree(
                    db,
                    character_id,
                    rules_dataset_id,
                    owner_type,
                    owner_key,
                    choice_key,
                    context_key,
                )
            count = int(
                db.execute(
                    "SELECT COUNT(*) FROM user_character_choices WHERE character_id=? "
                    "AND owner_dataset_id=? AND owner_type=? AND owner_key=? AND choice_key=? "
                    "AND context_key=?",
                    (
                        character_id,
                        rules_dataset_id,
                        owner_type,
                        owner_key,
                        choice_key,
                        context_key,
                    ),
                ).fetchone()[0]
            )
            if count >= definition.count:
                raise ValueError("choice selection count would exceed the published limit")

            option = None
            selection_state = "resolved"
            canonical_reference: PublishedReference | None = None
            value = selected_value.strip() if selected_value and selected_value.strip() else None
            if selected_option_key is not None:
                option = next(
                    (
                        candidate
                        for candidate in definition.options
                        if str(candidate.option_key) == selected_option_key
                    ),
                    None,
                )
                if option is None:
                    raise ValueError("selected option is not valid for this choice")
                db_option = persisted["options"].get(selected_option_key)
                if db_option is None:
                    raise ValueError("selected option is not part of the persisted rules identity")
                expected_reference_kind = (
                    str(option.reference.kind) if option.reference is not None else None
                )
                expected_reference_identity = (
                    str(option.reference.identity) if option.reference is not None else None
                )
                expected_value = str(option.value) if option.value is not None else None
                if (
                    (str(db_option[1]) if db_option[1] is not None else None)
                    != expected_reference_kind
                    or (str(db_option[2]) if db_option[2] is not None else None)
                    != expected_reference_identity
                    or (str(db_option[3]) if db_option[3] is not None else None) != expected_value
                    or bool(db_option[4]) != bool(option.resolved)
                ):
                    raise ValueError("rules catalog option does not match its persisted identity")
                if option.reference is not None:
                    expected_identity = _canonical_reference(
                        rules_dataset_id, str(option.reference.identity)
                    )
                    if str(option.reference.kind) == "subclass":
                        expected_identity = _canonical_subclass_reference(
                            db, rules_dataset_id, expected_identity
                        )
                    _validate_published_identity(str(option.reference.kind), expected_identity)
                    if selected_reference is not None and (
                        selected_reference.kind != str(option.reference.kind)
                        or selected_reference.identity != expected_identity
                    ):
                        raise ValueError("selected reference does not match the published option")
                    live = _lookup_reference(db, str(option.reference.kind), expected_identity)
                    if live is None or live[1] != "2024":
                        if not allow_unresolved:
                            raise ValueError("selected published option reference is unavailable")
                        canonical_reference = _snapshot_reference(
                            selected_reference,
                            kind=str(option.reference.kind),
                            identity=expected_identity,
                        )
                        selection_state = "unresolved"
                    else:
                        canonical_reference = _reference_from_lookup(
                            str(option.reference.kind), expected_identity, live
                        )
                elif selected_reference is not None:
                    raise ValueError("this option does not select a published reference")
                option_value = str(option.value) if option.value is not None else None
                if value is not None and option_value is not None and value != option_value:
                    raise ValueError("selected value does not match the published option")
                value = option_value or value
                if not option.resolved or not int(db_option["resolved"]):
                    if not allow_unresolved:
                        raise ValueError("this published option is explicitly unresolved")
                    selection_state = "unresolved"
                if selected_value is not None and option.value is None and option.reference is None:
                    raise ValueError("selected value is not part of the published option")
            else:
                if selected_reference is None and value is None:
                    raise ValueError("select an option, exact published reference, or value")
                if definition.criteria is None:
                    raise ValueError("this choice requires a published option key")
                criteria_result = _criteria_match(
                    db, rules_dataset_id, definition.criteria, selected_reference, value
                )
                if criteria_result is False:
                    raise ValueError("selection does not match this choice's published criteria")
                if criteria_result is None:
                    if not allow_unresolved:
                        raise ValueError(
                            "criteria selection cannot be verified; mark it unresolved explicitly"
                        )
                    selection_state = "unresolved"
                if selected_reference is not None:
                    if selected_reference.edition != "2024":
                        raise ValueError("character choices only support 2024 references")
                    _validate_published_identity(
                        selected_reference.kind, selected_reference.identity
                    )
                    live = _lookup_reference(
                        db, selected_reference.kind, selected_reference.identity
                    )
                    if live is None or live[1] != "2024":
                        if not allow_unresolved:
                            raise ValueError("selected published reference is unavailable")
                        canonical_reference = _snapshot_reference(selected_reference)
                        selection_state = "unresolved"
                    else:
                        canonical_reference = _reference_from_lookup(
                            selected_reference.kind, selected_reference.identity, live
                        )
                if allow_unresolved and criteria_result is None:
                    selection_state = "unresolved"

            if (
                selection_state == "unresolved"
                and not allow_unresolved
                and (option is None or option.resolved)
            ):
                raise ValueError("unresolved selections must be explicitly allowed")
            fingerprint = _selection_fingerprint(
                selected_option_key,
                canonical_reference,
                value,
            )
            try:
                cursor = db.execute(
                    "INSERT INTO user_character_choices "
                    "(character_id,owner_dataset_id,owner_type,owner_key,choice_key,"
                    "selected_option_key,selected_reference_kind,selected_reference_identity,"
                    "selected_reference_name,selected_reference_edition,selected_value,"
                    "resolution_state,selection_fingerprint,context_key,character_level,"
                    "class_identity,class_level,source_rule) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        character_id,
                        rules_dataset_id,
                        owner_type,
                        owner_key,
                        choice_key,
                        selected_option_key,
                        canonical_reference.kind if canonical_reference else None,
                        canonical_reference.identity if canonical_reference else None,
                        canonical_reference.name if canonical_reference else None,
                        canonical_reference.edition if canonical_reference else None,
                        value,
                        selection_state,
                        fingerprint,
                        context_key,
                        character_level,
                        class_identity,
                        class_level,
                        str(definition.source_rule),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "this choice selection is already recorded in that context"
                ) from exc
            _touch(db, character_id)
            resolution_id = int(cursor.lastrowid)
        return self._get_choice_resolution(resolution_id)

    def resolve_spell_access_ability(
        self,
        character_id: str,
        *,
        owner_dataset_id: str,
        owner_type: str,
        owner_key: str,
        choice_key: str,
        selected_option_key: str,
        selected_value: str,
        character_level: int,
        class_identity: str | None,
        class_level: int | None,
        source_rule: str,
    ) -> ChoiceResolution:
        """Persist a spellcasting-ability pick against its exact published access metadata."""

        if owner_type not in _OWNER_TYPES:
            raise ValueError("unknown spellcasting choice owner type")
        if not choice_key.endswith("/ability"):
            raise ValueError("spellcasting ability choice identity is invalid")
        access_group = choice_key.removesuffix("/ability")
        selected = selected_value.casefold()
        if selected not in _ABILITIES or selected_option_key != f"option/ability/{selected}":
            raise ValueError("choose one of the published spellcasting abilities")
        _validate_level_number(character_level, "spell choice character level")
        _validate_level_number(class_level, "spell choice class level")
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            self._validate_character_context(
                db, character_id, character_level, class_identity, class_level
            )
            self._require_choice_owner(db, owner_dataset_id, owner_type, owner_key)
            metadata_rows = db.execute(
                "SELECT payload_json FROM character_builder_spell_access WHERE dataset_id=? "
                "AND owner_type=? AND owner_key=? AND class_level<=?",
                (owner_dataset_id, owner_type, owner_key, character_level),
            ).fetchall()
            allowed = False
            for row in metadata_rows:
                try:
                    payload = json.loads(str(row[0]))
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(payload, dict)
                    and payload.get("choice_group") == access_group
                    and payload.get("ability_selection") == "choice"
                    and source_rule == payload.get("source_rule")
                    and selected
                    in {str(value).casefold() for value in payload.get("ability_options", [])}
                ):
                    allowed = True
                    break
            if not allowed:
                raise ValueError("spellcasting ability is not allowed by the selected feature")

            context_key = _context_key(character_level, class_identity, class_level)
            old = db.execute(
                "SELECT resolution_id,selected_value FROM user_character_choices "
                "WHERE character_id=? "
                "AND owner_dataset_id=? AND owner_type=? AND owner_key=? AND choice_key=? "
                "AND context_key=?",
                (
                    character_id,
                    owner_dataset_id,
                    owner_type,
                    owner_key,
                    choice_key,
                    context_key,
                ),
            ).fetchone()
            if old is not None and str(old[1]) == selected:
                return self._get_choice_resolution(int(old[0]))
            if old is not None:
                self._delete_choice_tree(
                    db,
                    character_id,
                    owner_dataset_id,
                    owner_type,
                    owner_key,
                    choice_key,
                    context_key,
                )
            fingerprint = _selection_fingerprint(selected_option_key, None, selected)
            cursor = db.execute(
                "INSERT INTO user_character_choices "
                "(character_id,owner_dataset_id,owner_type,owner_key,choice_key,"
                "selected_option_key,selected_value,resolution_state,selection_fingerprint,"
                "context_key,character_level,class_identity,class_level,source_rule) "
                "VALUES (?,?,?,?,?,?,?,'resolved',?,?,?,?,?,?)",
                (
                    character_id,
                    owner_dataset_id,
                    owner_type,
                    owner_key,
                    choice_key,
                    selected_option_key,
                    selected,
                    fingerprint,
                    context_key,
                    character_level,
                    class_identity,
                    class_level,
                    source_rule,
                ),
            )
            _touch(db, character_id)
            resolution_id = int(cursor.lastrowid)
        return self._get_choice_resolution(resolution_id)

    def remove_spell_access_ability_choice(
        self,
        character_id: str,
        *,
        owner_dataset_id: str,
        owner_type: str,
        owner_key: str,
        choice_key: str,
        character_level: int,
        class_identity: str | None,
        class_level: int | None,
    ) -> bool:
        context_key = _context_key(character_level, class_identity, class_level)
        with self.database.connection() as db, db:
            exists = db.execute(
                "SELECT 1 FROM user_character_choices WHERE character_id=? "
                "AND owner_dataset_id=? AND owner_type=? AND owner_key=? AND choice_key=? "
                "AND context_key=? LIMIT 1",
                (
                    character_id,
                    owner_dataset_id,
                    owner_type,
                    owner_key,
                    choice_key,
                    context_key,
                ),
            ).fetchone()
            if exists is None:
                return False
            self._delete_choice_tree(
                db,
                character_id,
                owner_dataset_id,
                owner_type,
                owner_key,
                choice_key,
                context_key,
            )
            _touch(db, character_id)
            return True

    def remove_choice_resolution(self, resolution_id: int) -> bool:
        with self.database.connection() as db, db:
            row = db.execute(
                "SELECT character_id,owner_dataset_id,owner_type,owner_key,choice_key,"
                "selected_option_key,context_key FROM user_character_choices "
                "WHERE resolution_id=?",
                (resolution_id,),
            ).fetchone()
            if row is None:
                return False
            character_id = str(row[0])
            selected_option = str(row[5]) if row[5] is not None else None
            if selected_option is not None:
                child_rows = db.execute(
                    "SELECT choice_key FROM character_rule_choices WHERE dataset_id=? "
                    "AND owner_type=? AND owner_key=? AND depends_on_choice=? "
                    "AND depends_on_option=?",
                    (row[1], row[2], row[3], row[4], selected_option),
                ).fetchall()
                for child in child_rows:
                    self._delete_choice_tree(
                        db,
                        character_id,
                        str(row[1]),
                        str(row[2]),
                        str(row[3]),
                        str(child[0]),
                        str(row[6]),
                    )
                self._delete_choice_provenance(
                    db,
                    character_id,
                    str(row[1]),
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    selected_option,
                )
            db.execute("DELETE FROM user_character_choices WHERE resolution_id=?", (resolution_id,))
            _touch(db, character_id)
            return True

    @staticmethod
    def _delete_choice_tree(
        db: sqlite3.Connection,
        character_id: str,
        dataset_id: str,
        owner_type: str,
        owner_key: str,
        choice_key: str,
        context_key: str,
    ) -> None:
        """Remove a decision and dependent choices/outcomes within the same owner/context."""

        children = db.execute(
            "SELECT choice_key FROM character_rule_choices WHERE dataset_id=? AND owner_type=? "
            "AND owner_key=? AND depends_on_choice=? ORDER BY choice_key",
            (dataset_id, owner_type, owner_key, choice_key),
        ).fetchall()
        for child in children:
            CharacterService._delete_choice_tree(
                db,
                character_id,
                dataset_id,
                owner_type,
                owner_key,
                str(child[0]),
                context_key,
            )
        selections = db.execute(
            "SELECT selected_option_key FROM user_character_choices WHERE character_id=? "
            "AND owner_dataset_id=? AND owner_type=? AND owner_key=? AND choice_key=? "
            "AND context_key=?",
            (character_id, dataset_id, owner_type, owner_key, choice_key, context_key),
        ).fetchall()
        for selection in selections:
            CharacterService._delete_choice_provenance(
                db,
                character_id,
                dataset_id,
                owner_type,
                owner_key,
                choice_key,
                str(selection[0]) if selection[0] is not None else None,
            )
        db.execute(
            "DELETE FROM user_character_choices WHERE character_id=? AND owner_dataset_id=? "
            "AND owner_type=? AND owner_key=? AND choice_key=? AND context_key=?",
            (character_id, dataset_id, owner_type, owner_key, choice_key, context_key),
        )

    @staticmethod
    def _delete_choice_provenance(
        db: sqlite3.Connection,
        character_id: str,
        dataset_id: str,
        owner_type: str,
        owner_key: str,
        choice_key: str,
        option_key: str | None,
    ) -> None:
        option_filter = "" if option_key is None else " AND source_option_key=?"
        feat_params: list[object] = [character_id, dataset_id, owner_type, owner_key, choice_key]
        if option_key is not None:
            feat_params.append(option_key)
        feat_identities = [
            str(row[0])
            for row in db.execute(
                "SELECT feat_identity FROM user_character_feats WHERE character_id=? "
                "AND source_owner_dataset_id=? AND source_owner_type=? AND source_owner_key=? "
                "AND source_choice_key=?" + option_filter,
                feat_params,
            ).fetchall()
        ]
        for table in (
            "user_character_ability_modifications",
            "user_character_feats",
            "user_character_spells",
            "user_character_equipment",
            "user_character_currency",
        ):
            option_filter = "" if option_key is None else " AND source_option_key=?"
            params = [character_id, dataset_id, owner_type, owner_key, choice_key]
            if option_key is not None:
                params.append(option_key)
            db.execute(
                f"DELETE FROM {table} WHERE character_id=? AND source_owner_dataset_id=? "
                "AND source_owner_type=? AND source_owner_key=? AND source_choice_key=?"
                f"{option_filter}",
                params,
            )
        CharacterService._delete_unreferenced_feat_owners(db, character_id, feat_identities, set())

    def add_feat(
        self,
        character_id: str,
        feat_reference: PublishedReference,
        *,
        provenance_kind: str,
        provenance_label: str,
        provenance: DecisionProvenance = DecisionProvenance(),
        allow_unresolved: bool = False,
    ) -> FeatSelection:
        if provenance_kind not in {"background", "class_level", "feat", "other"}:
            raise ValueError("invalid feat provenance kind")
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            actual, resolution_state = self._reference_for_storage(
                db, feat_reference, "feat", allow_unresolved
            )
            _validate_provenance(provenance)
            self._validate_character_context(
                db,
                character_id,
                provenance.character_level,
                provenance.class_identity,
                provenance.class_level,
            )
            cursor = db.execute(
                "INSERT INTO user_character_feats "
                "(character_id,feat_identity,feat_name,edition,provenance_kind,provenance_label,"
                "source_owner_dataset_id,source_owner_type,source_owner_key,source_choice_key,"
                "source_option_key,character_level,class_identity,class_level,source_rule,"
                "resolution_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    character_id,
                    actual.identity,
                    actual.name,
                    actual.edition,
                    provenance_kind,
                    _clean_name(provenance_label),
                    provenance.owner_dataset_id,
                    provenance.owner_type,
                    provenance.owner_key,
                    provenance.choice_key,
                    provenance.option_key,
                    provenance.character_level,
                    provenance.class_identity,
                    provenance.class_level,
                    provenance.source_rule,
                    resolution_state,
                ),
            )
            _touch(db, character_id)
            selection_id = int(cursor.lastrowid)
        return next(
            row
            for row in self.get_character(character_id).feats
            if row.selection_id == selection_id
        )

    def remove_feat(self, character_id: str, selection_id: int) -> bool:
        with self.database.connection() as db, db:
            row = db.execute(
                "SELECT feat_identity FROM user_character_feats WHERE character_id=? "
                "AND feat_selection_id=?",
                (character_id, selection_id),
            ).fetchone()
            if row is None:
                return False
            db.execute(
                "DELETE FROM user_character_feats WHERE character_id=? AND feat_selection_id=?",
                (character_id, selection_id),
            )
            CharacterService._delete_unreferenced_feat_owners(
                db, character_id, [str(row[0])], set()
            )
            _touch(db, character_id)
            return True

    def add_spell(
        self,
        character_id: str,
        spell_reference: PublishedReference,
        *,
        acquisition: str,
        source_class: PublishedReference | None = None,
        character_level: int | None = None,
        class_level: int | None = None,
        provenance: DecisionProvenance = DecisionProvenance(),
        allow_unresolved: bool = False,
    ) -> SpellSelection:
        if acquisition not in _SPELL_ACQUISITIONS:
            raise ValueError("unsupported spell acquisition kind")
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            actual, resolution_state = self._reference_for_storage(
                db, spell_reference, "spell", allow_unresolved
            )
            source = (
                self._require_live_reference(db, source_class, "class")
                if source_class is not None
                else None
            )
            _validate_provenance(provenance)
            _validate_level_number(character_level, "spell character level")
            _validate_level_number(class_level, "spell class level")
            context_class_identity = provenance.class_identity or (
                source.identity if source is not None and class_level is not None else None
            )
            self._validate_character_context(
                db, character_id, character_level, context_class_identity, class_level
            )
            cursor = db.execute(
                "INSERT INTO user_character_spells "
                "(character_id,spell_identity,spell_name,edition,acquisition,source_class_identity,"
                "source_class_name,character_level,class_level,source_owner_dataset_id,source_owner_type,"
                "source_owner_key,source_choice_key,source_option_key,source_rule,"
                "resolution_state) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    character_id,
                    actual.identity,
                    actual.name,
                    actual.edition,
                    acquisition,
                    source.identity if source else None,
                    source.name if source else None,
                    character_level,
                    class_level,
                    provenance.owner_dataset_id,
                    provenance.owner_type,
                    provenance.owner_key,
                    provenance.choice_key,
                    provenance.option_key,
                    provenance.source_rule,
                    resolution_state,
                ),
            )
            _touch(db, character_id)
            selection_id = int(cursor.lastrowid)
        return next(
            row
            for row in self.get_character(character_id).spells
            if row.selection_id == selection_id
        )

    def remove_spell(self, character_id: str, selection_id: int) -> bool:
        return self._delete_owned_row(
            "user_character_spells", "spell_selection_id", character_id, selection_id
        )

    def add_equipment(
        self,
        character_id: str,
        *,
        item_reference: PublishedReference | None = None,
        unresolved_selection: str | None = None,
        quantity: int = 1,
        equipped: bool = False,
        carried_state: str = "carried",
        provenance_kind: str = "other",
        provenance_label: str = "Character equipment",
        provenance: DecisionProvenance = DecisionProvenance(),
        allow_unresolved: bool = False,
    ) -> EquipmentRecord:
        if (item_reference is None) == (unresolved_selection is None):
            raise ValueError(
                "supply one exact Item reference or one unresolved equipment selection"
            )
        if not isinstance(quantity, int) or quantity < 1:
            raise ValueError("equipment quantity must be a positive integer")
        if carried_state not in {"carried", "stowed"}:
            raise ValueError("carried_state must be 'carried' or 'stowed'")
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            _validate_provenance(provenance)
            self._validate_character_context(
                db,
                character_id,
                provenance.character_level,
                provenance.class_identity,
                provenance.class_level,
            )
            if item_reference is not None:
                actual, resolution_state = self._reference_for_storage(
                    db, item_reference, "item", allow_unresolved
                )
                generic_name = None
            else:
                actual = None
                generic_name = _clean_name(str(unresolved_selection))
                resolution_state = "unresolved"
            cursor = db.execute(
                "INSERT INTO user_character_equipment "
                "(character_id,item_identity,item_name,unresolved_selection,edition,quantity,equipped,"
                "carried_state,provenance_kind,provenance_label,source_owner_dataset_id,source_owner_type,"
                "source_owner_key,source_choice_key,source_option_key,character_level,class_identity,"
                "class_level,source_rule,resolution_state) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    character_id,
                    actual.identity if actual else None,
                    actual.name if actual else None,
                    generic_name,
                    actual.edition if actual else "2024",
                    quantity,
                    int(bool(equipped)),
                    carried_state,
                    provenance_kind,
                    _clean_name(provenance_label),
                    provenance.owner_dataset_id,
                    provenance.owner_type,
                    provenance.owner_key,
                    provenance.choice_key,
                    provenance.option_key,
                    provenance.character_level,
                    provenance.class_identity,
                    provenance.class_level,
                    provenance.source_rule,
                    resolution_state,
                ),
            )
            _touch(db, character_id)
            equipment_id = int(cursor.lastrowid)
        return next(
            row
            for row in self.get_character(character_id).equipment
            if row.equipment_id == equipment_id
        )

    def add_currency(
        self,
        character_id: str,
        currency: str,
        amount: int,
        *,
        provenance_kind: str = "other",
        provenance_label: str = "Character currency",
        provenance: DecisionProvenance = DecisionProvenance(),
        source_rule: str | None = None,
    ) -> CurrencyRecord:
        key = currency.casefold()
        if key not in {"cp", "sp", "ep", "gp", "pp"}:
            raise ValueError("currency must be cp, sp, ep, gp, or pp")
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("currency amount must be a positive integer")
        if provenance_kind not in {"background", "class_level", "feat", "other"}:
            raise ValueError("unknown currency provenance kind")
        if not provenance_label.strip():
            raise ValueError("currency provenance label must not be empty")
        _validate_provenance(provenance)
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            cursor = db.execute(
                "INSERT INTO user_character_currency "
                "(character_id,currency_key,amount,provenance_kind,provenance_label,"
                "source_owner_dataset_id,source_owner_type,source_owner_key,source_choice_key,"
                "source_option_key,source_rule) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    character_id,
                    key,
                    amount,
                    provenance_kind,
                    _clean_name(provenance_label),
                    provenance.owner_dataset_id,
                    provenance.owner_type,
                    provenance.owner_key,
                    provenance.choice_key,
                    provenance.option_key,
                    source_rule or provenance.source_rule,
                ),
            )
            _touch(db, character_id)
            currency_id = int(cursor.lastrowid)
        return next(
            row
            for row in self.get_character(character_id).currency
            if row.currency_id == currency_id
        )

    def update_equipment(
        self,
        character_id: str,
        equipment_id: int,
        *,
        quantity: int | None = None,
        equipped: bool | None = None,
        carried_state: str | None = None,
    ) -> EquipmentRecord:
        if quantity is not None and (not isinstance(quantity, int) or quantity < 1):
            raise ValueError("equipment quantity must be a positive integer")
        if carried_state is not None and carried_state not in {"carried", "stowed"}:
            raise ValueError("carried_state must be 'carried' or 'stowed'")
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            row = db.execute(
                "SELECT quantity,equipped,carried_state FROM user_character_equipment "
                "WHERE character_id=? AND equipment_id=?",
                (character_id, equipment_id),
            ).fetchone()
            if row is None:
                raise ValueError("equipment record does not exist for this character")
            db.execute(
                "UPDATE user_character_equipment SET quantity=?,equipped=?,carried_state=? "
                "WHERE character_id=? AND equipment_id=?",
                (
                    quantity if quantity is not None else row[0],
                    int(equipped) if equipped is not None else row[1],
                    carried_state if carried_state is not None else row[2],
                    character_id,
                    equipment_id,
                ),
            )
            _touch(db, character_id)
        return next(
            row
            for row in self.get_character(character_id).equipment
            if row.equipment_id == equipment_id
        )

    def remove_equipment(self, character_id: str, equipment_id: int) -> bool:
        return self._delete_owned_row(
            "user_character_equipment", "equipment_id", character_id, equipment_id
        )

    def set_hp_choice(
        self,
        character_id: str,
        total_level: int,
        choice_kind: str,
        amount: int,
    ) -> HitPointChoice:
        if choice_kind not in {"fixed_average", "rolled"}:
            raise ValueError("HP choice must be 'fixed_average' or 'rolled'")
        if not isinstance(amount, int) or not 1 <= amount <= 20:
            raise ValueError("HP choice amount must be between 1 and 20")
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            level = db.execute(
                "SELECT class_identity,class_name,resulting_class_level,edition "
                "FROM user_character_levels WHERE character_id=? AND total_level=?",
                (character_id, total_level),
            ).fetchone()
            if level is None:
                raise ValueError("HP choice must refer to an existing character level")
            db.execute(
                "INSERT INTO user_character_hp_choices "
                "(character_id,total_level,class_identity,class_level,choice_kind,amount,edition) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(character_id,total_level) DO UPDATE SET "
                "class_identity=excluded.class_identity,class_level=excluded.class_level,"
                "choice_kind=excluded.choice_kind,amount=excluded.amount,edition=excluded.edition",
                (character_id, total_level, level[0], level[2], choice_kind, amount, level[3]),
            )
            _touch(db, character_id)
        return HitPointChoice(
            total_level,
            PublishedReference("class", str(level[0]), str(level[1]), str(level[3])),
            int(level[2]),
            choice_kind,
            amount,
        )

    def update_note(self, character_id: str, note: str) -> Character:
        if not isinstance(note, str):
            raise TypeError("character note must be text")
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            db.execute(
                "INSERT INTO user_character_notes(character_id,note_text) VALUES (?,?) "
                "ON CONFLICT(character_id) DO UPDATE SET note_text=excluded.note_text",
                (character_id, note),
            )
            _touch(db, character_id)
        return self.get_character(character_id)

    def validate_add_class_level(
        self,
        character_id: str,
        class_reference: PublishedReference,
        rules: CharacterBuilderRules,
        *,
        rules_dataset_id: str,
        additional_context: CharacterRuleContext | None = None,
    ) -> RequirementEvaluation:
        from .models.character_builder import RequirementStatus

        character = self.get_character(character_id)
        dataset_id, class_key = _direct_parts(class_reference.identity)
        if dataset_id != rules_dataset_id:
            return RequirementEvaluation(
                status=RequirementStatus.UNRESOLVED,
                reason="rules catalog does not match the exact class reference dataset",
            )
        if class_reference.kind != "class" or class_reference.edition != "2024":
            return RequirementEvaluation(
                status=RequirementStatus.UNSATISFIED, reason="class reference is not a 2024 class"
            )
        if class_reference.missing:
            return RequirementEvaluation(
                status=RequirementStatus.UNRESOLVED, reason="class reference is missing"
            )
        with self.database.connection() as db:
            live = _lookup_reference(db, "class", class_reference.identity)
            if live is None:
                return RequirementEvaluation(
                    status=RequirementStatus.UNRESOLVED, reason="class reference is missing"
                )
            if live[1] != "2024":
                return RequirementEvaluation(
                    status=RequirementStatus.UNSATISFIED,
                    reason="class reference is not from 2024",
                )
        levels_by_key: dict[str, int] = {}
        for identity, level in character.class_levels.items():
            other_dataset, other_key = _direct_parts(identity)
            if other_dataset != rules_dataset_id:
                return RequirementEvaluation(
                    status=RequirementStatus.UNRESOLVED,
                    reason="existing class track belongs to a different rules dataset",
                )
            levels_by_key[other_key] = level
        if character.total_level == 0:
            if not any(str(row.class_key) == class_key for row in rules.catalog.classes):
                return RequirementEvaluation(
                    status=RequirementStatus.UNRESOLVED, reason="unknown starting class"
                )
            return RequirementEvaluation(
                status=RequirementStatus.SATISFIED, reason="valid starting class"
            )

        base_scores = {ability: score for ability, score in character.base_ability_scores}
        context = CharacterRuleContext(
            edition=character.edition,
            total_level=character.total_level,
            class_levels=levels_by_key,
            ability_scores=base_scores,
            subclasses={
                _direct_parts(row.class_reference.identity)[1]: _subclass_local_key(
                    row.subclass_reference.identity
                )
                for row in character.subclasses
                if _direct_parts(row.class_reference.identity)[0] == rules_dataset_id
            },
            feats={
                _local_part(row.feat_reference.identity)
                for row in character.feats
                if _dataset_part(row.feat_reference.identity) == rules_dataset_id
            },
        )
        if additional_context is not None:
            context = context.model_copy(
                update={
                    "ability_scores": {
                        **context.ability_scores,
                        **additional_context.ability_scores,
                    },
                    "proficiencies": set(additional_context.proficiencies),
                    "feats": context.feats | set(additional_context.feats),
                    "subclasses": {**context.subclasses, **additional_context.subclasses},
                    "spellcasting_classes": set(additional_context.spellcasting_classes),
                    "pact_magic_classes": set(additional_context.pact_magic_classes),
                    "class_primary_abilities": dict(additional_context.class_primary_abilities),
                }
            )
        return rules.can_enter_class(class_key, context)

    def validate_character(self, character_id: str) -> CharacterValidationReport:
        character = self.get_character(character_id)
        issues: list[CharacterValidationIssue] = []
        levels = character.levels
        if not levels:
            issues.append(
                CharacterValidationIssue(
                    "missing_starting_class",
                    "error" if character.state == "complete" else "warning",
                    "Character has no starting class level.",
                    "levels",
                )
            )
        if [row.total_level for row in levels] != list(range(1, len(levels) + 1)):
            issues.append(
                CharacterValidationIssue(
                    "level_sequence_inconsistent",
                    "error",
                    "Character levels are not contiguous.",
                    "levels",
                )
            )
        expected_by_class: dict[str, int] = {}
        for row in levels:
            expected = expected_by_class.get(row.class_reference.identity, 0) + 1
            if row.class_level != expected:
                issues.append(
                    CharacterValidationIssue(
                        "class_level_inconsistent",
                        "error",
                        f"Class level {row.class_level} should be {expected} at "
                        f"character level {row.total_level}.",
                        f"levels[{row.total_level}]",
                    )
                )
            expected_by_class[row.class_reference.identity] = row.class_level
        for ref in _character_references(character):
            if ref.missing:
                issues.append(
                    CharacterValidationIssue(
                        "published_reference_missing",
                        "unresolved",
                        f"Published {ref.kind} reference '{ref.name}' is unavailable.",
                        reference_identity=ref.identity,
                    )
                )
        for selection in character.subclasses:
            if selection.class_reference.identity not in character.class_levels:
                issues.append(
                    CharacterValidationIssue(
                        "invalid_subclass_parent",
                        "error",
                        "Subclass parent class is not present in the character's level history.",
                        reference_identity=selection.subclass_reference.identity,
                    )
                )
            elif (
                selection.selected_class_level
                > character.class_levels[selection.class_reference.identity]
            ):
                issues.append(
                    CharacterValidationIssue(
                        "invalid_subclass_level",
                        "error",
                        "Subclass was selected after the current class level.",
                        reference_identity=selection.subclass_reference.identity,
                    )
                )
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT resolution_id,owner_dataset_id,owner_type,owner_key,choice_key,"
                "selected_option_key,resolution_state,context_key FROM user_character_choices "
                "WHERE character_id=? ORDER BY resolution_id",
                (character_id,),
            ).fetchall()
            counts: Counter[tuple[str, str, str, str, str]] = Counter()
            for row in rows:
                identity = (
                    str(row[1]),
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[7]),
                )
                counts[identity] += 1
                choice_row = db.execute(
                    "SELECT choice_count FROM character_rule_choices WHERE dataset_id=? "
                    "AND owner_type=? AND owner_key=? AND choice_key=?",
                    (row[1], row[2], row[3], row[4]),
                ).fetchone()
                if choice_row is None:
                    issues.append(
                        CharacterValidationIssue(
                            "choice_owner_unavailable",
                            "unresolved",
                            "Choice rules for this saved decision are unavailable.",
                            f"choices[{row[0]}]",
                        )
                    )
                elif row[5] is not None:
                    option_row = db.execute(
                        "SELECT 1 FROM character_rule_choice_options WHERE dataset_id=? "
                        "AND owner_type=? AND owner_key=? AND choice_key=? AND option_key=?",
                        (row[1], row[2], row[3], row[4], row[5]),
                    ).fetchone()
                    if option_row is None:
                        issues.append(
                            CharacterValidationIssue(
                                "invalid_choice_option",
                                "error",
                                "Saved choice option is not defined for its exact owner identity.",
                                f"choices[{row[0]}]",
                            )
                        )
                if row[6] == "unresolved":
                    issues.append(
                        CharacterValidationIssue(
                            "choice_unresolved",
                            "warning",
                            "A player choice is retained but has not been fully resolved.",
                            f"choices[{row[0]}]",
                        )
                    )
            for identity, count in counts.items():
                choice_row = db.execute(
                    "SELECT choice_count FROM character_rule_choices WHERE dataset_id=? "
                    "AND owner_type=? AND owner_key=? AND choice_key=?",
                    identity[:4],
                ).fetchone()
                if choice_row is not None and count > int(choice_row[0]):
                    issues.append(
                        CharacterValidationIssue(
                            "too_many_choice_selections",
                            "error",
                            "Saved selections exceed the choice's published count.",
                            f"choices[{identity[1]}:{identity[2]}:{identity[3]}]",
                        )
                    )
            issues.extend(self._missing_choice_issues(db, character))
        return CharacterValidationReport(character_id, tuple(issues))

    def _missing_choice_issues(
        self, db: sqlite3.Connection, character: Character
    ) -> list[CharacterValidationIssue]:
        expected: list[tuple[str, str, str, str, str, int, int | None, str | None, int | None]] = []

        def add_owner(
            dataset_id: str,
            owner_type: str,
            owner_key: str,
            scopes: set[str],
            character_level: int | None,
            class_identity: str | None,
            class_level: int | None,
            event_key: str | None = None,
        ) -> None:
            marks = ",".join("?" for _ in scopes)
            scope_params = tuple(sorted(scopes))
            rows = db.execute(
                "SELECT choice_key,choice_count,depends_on_choice,depends_on_option,"
                "requirement_key "
                "FROM character_rule_choices WHERE dataset_id=? AND owner_type=? AND owner_key=? "
                f"AND scope IN ({marks}) AND ((event_key IS NULL AND ? IS NULL) OR event_key=?)",
                (dataset_id, owner_type, owner_key, *scope_params, event_key, event_key),
            ).fetchall()
            for row in rows:
                # Requirement trees are not evaluated here; those decisions remain a warning for a
                # later rules-aware builder instead of being treated as missing unconditionally.
                if row[4] is not None:
                    continue
                context = _context_key(character_level, class_identity, class_level)
                if row[2] is not None:
                    parent = db.execute(
                        "SELECT 1 FROM user_character_choices WHERE character_id=? "
                        "AND owner_dataset_id=? AND owner_type=? AND owner_key=? "
                        "AND choice_key=? AND selected_option_key=? "
                        "AND context_key=? LIMIT 1",
                        (
                            character.character_id,
                            dataset_id,
                            owner_type,
                            owner_key,
                            row[2],
                            row[3],
                            context,
                        ),
                    ).fetchone()
                    if parent is None:
                        continue
                expected.append(
                    (
                        dataset_id,
                        owner_type,
                        owner_key,
                        str(row[0]),
                        context,
                        int(row[1]),
                        character_level,
                        class_identity,
                        class_level,
                    )
                )

        if character.species is not None:
            parts = _direct_parts(character.species.identity)
            add_owner(parts[0], "species", parts[1], {"species"}, 1, None, None)
        if character.background is not None:
            parts = _direct_parts(character.background.identity)
            add_owner(
                parts[0],
                "background",
                parts[1],
                {"background", "starting_equipment"},
                1,
                None,
                None,
            )

        for level in character.levels:
            dataset_id, class_key = _direct_parts(level.class_reference.identity)
            first_in_track = level.class_level == 1
            if first_in_track:
                scopes = (
                    {"starting_class", "starting_equipment"}
                    if level.total_level == 1
                    else {"multiclass_entry"}
                )
                add_owner(
                    dataset_id,
                    "class",
                    class_key,
                    scopes,
                    level.total_level,
                    level.class_reference.identity,
                    level.class_level,
                )
            event_rows = db.execute(
                "SELECT event_key FROM class_progression_events WHERE dataset_id=? "
                "AND owner_type='class' AND owner_key=? AND class_level=?",
                (dataset_id, class_key, level.class_level),
            ).fetchall()
            for event in event_rows:
                add_owner(
                    dataset_id,
                    "class",
                    class_key,
                    {"progression_event"},
                    level.total_level,
                    level.class_reference.identity,
                    level.class_level,
                    str(event[0]),
                )

        for selection in character.subclasses:
            dataset_id, _parent_key, subclass_key = _subclass_parts(
                selection.subclass_reference.identity
            )
            track_level = character.class_levels.get(selection.class_reference.identity, 0)
            for class_level in range(selection.selected_class_level, track_level + 1):
                event_rows = db.execute(
                    "SELECT event_key FROM class_progression_events WHERE dataset_id=? "
                    "AND owner_type='subclass' AND owner_key=? AND class_level=?",
                    (dataset_id, subclass_key, class_level),
                ).fetchall()
                total_level = next(
                    (
                        row.total_level
                        for row in character.levels
                        if row.class_reference.identity == selection.class_reference.identity
                        and row.class_level == class_level
                    ),
                    None,
                )
                for event in event_rows:
                    add_owner(
                        dataset_id,
                        "subclass",
                        subclass_key,
                        {"progression_event"},
                        total_level,
                        selection.class_reference.identity,
                        class_level,
                        str(event[0]),
                    )

        for feat in character.feats:
            dataset_id, feat_key = _direct_parts(feat.feat_reference.identity)
            add_owner(
                dataset_id,
                "feat",
                feat_key,
                {"ability_score", "spellcasting"},
                feat.character_level,
                feat.class_identity,
                feat.class_level,
            )

        issues = []
        for (
            dataset_id,
            owner_type,
            owner_key,
            choice_key,
            context,
            required_count,
            character_level,
            class_identity,
            class_level,
        ) in expected:
            count = int(
                db.execute(
                    "SELECT COUNT(*) FROM user_character_choices WHERE character_id=? "
                    "AND owner_dataset_id=? AND owner_type=? AND owner_key=? AND choice_key=? "
                    "AND context_key=?",
                    (
                        character.character_id,
                        dataset_id,
                        owner_type,
                        owner_key,
                        choice_key,
                        context,
                    ),
                ).fetchone()[0]
            )
            if count < required_count:
                issues.append(
                    CharacterValidationIssue(
                        "missing_required_choice",
                        "error" if character.state == "complete" else "warning",
                        f"Choice '{choice_key}' has {count} of {required_count} "
                        "required selections.",
                        f"choices[{owner_type}:{owner_key}:{choice_key}]",
                    )
                )
        return issues

    def _delete_owned_row(self, table: str, id_column: str, character_id: str, row_id: int) -> bool:
        if table not in {
            "user_character_feats",
            "user_character_spells",
            "user_character_equipment",
        }:
            raise ValueError("unsupported character child table")
        with self.database.connection() as db, db:
            self._require_character(db, character_id)
            cursor = db.execute(
                f"DELETE FROM {table} WHERE character_id=? AND {id_column}=?",
                (character_id, row_id),
            )
            if cursor.rowcount:
                _touch(db, character_id)
            return cursor.rowcount > 0

    def _get_choice_resolution(self, resolution_id: int) -> ChoiceResolution:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT owner_dataset_id,owner_type,owner_key,choice_key,selected_option_key,"
                "selected_reference_kind,selected_reference_identity,selected_reference_name,"
                "selected_reference_edition,selected_value,resolution_state,character_level,"
                "class_identity,class_level,source_rule FROM user_character_choices "
                "WHERE resolution_id=?",
                (resolution_id,),
            ).fetchone()
            if row is None:
                raise LookupError("saved choice resolution disappeared")
            reference = None
            if row[6] is not None:
                reference = self._resolve_reference(
                    db,
                    str(row[5]),
                    str(row[6]),
                    str(row[7]),
                    str(row[8]),
                )
            return ChoiceResolution(
                resolution_id,
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]) if row[4] is not None else None,
                reference,
                str(row[9]) if row[9] is not None else None,
                str(row[10]),
                int(row[11]) if row[11] is not None else None,
                str(row[12]) if row[12] is not None else None,
                int(row[13]) if row[13] is not None else None,
                str(row[14]),
            )

    def _get_character(self, db: sqlite3.Connection, character_id: str) -> Character:
        row = self._require_character(db, character_id)
        live_references = _character_reference_map(db, character_id)

        def resolve(kind: str, identity: str, name: str, edition: str) -> PublishedReference:
            return _resolve_character_reference(live_references, kind, identity, name, edition)

        def top_ref(kind: str) -> PublishedReference | None:
            identity, name, edition = (
                row[f"{kind}_identity"],
                row[f"{kind}_name"],
                row[f"{kind}_edition"],
            )
            if identity is None:
                return None
            return resolve(kind, str(identity), str(name), str(edition))

        levels = tuple(
            CharacterLevel(
                int(item[0]),
                resolve("class", str(item[1]), str(item[2]), str(item[4])),
                int(item[3]),
            )
            for item in db.execute(
                "SELECT total_level,class_identity,class_name,resulting_class_level,edition "
                "FROM user_character_levels WHERE character_id=? ORDER BY total_level",
                (character_id,),
            ).fetchall()
        )
        subclasses = tuple(
            SubclassSelection(
                resolve("class", str(item[0]), str(item[1]), str(item[5])),
                resolve("subclass", str(item[2]), str(item[3]), str(item[5])),
                int(item[4]),
            )
            for item in db.execute(
                "SELECT class_identity,class_name,subclass_identity,subclass_name,"
                "selected_class_level,edition FROM user_character_subclasses WHERE character_id=? "
                "ORDER BY class_identity",
                (character_id,),
            ).fetchall()
        )
        base_scores = tuple(
            (str(item[0]), int(item[1]))
            for item in db.execute(
                "SELECT ability_key,base_score FROM user_character_ability_scores "
                "WHERE character_id=? ORDER BY CASE ability_key "
                "WHEN 'str' THEN 1 WHEN 'dex' THEN 2 "
                "WHEN 'con' THEN 3 WHEN 'int' THEN 4 WHEN 'wis' THEN 5 ELSE 6 END",
                (character_id,),
            ).fetchall()
        )
        modifications = tuple(
            AbilityModification(
                int(item[0]),
                str(item[1]),
                int(item[2]),
                str(item[3]),
                str(item[4]),
                str(item[5]) if item[5] is not None else None,
                str(item[6]) if item[6] is not None else None,
                str(item[7]) if item[7] is not None else None,
                str(item[8]) if item[8] is not None else None,
                str(item[9]) if item[9] is not None else None,
                int(item[10]) if item[10] is not None else None,
                str(item[11]) if item[11] is not None else None,
                int(item[12]) if item[12] is not None else None,
                str(item[13]) if item[13] is not None else None,
            )
            for item in db.execute(
                "SELECT modification_id,ability_key,amount,source_kind,source_label,"
                "source_owner_dataset_id,source_owner_type,source_owner_key,"
                "source_choice_key,source_option_key,character_level,class_identity,"
                "class_level,source_rule "
                "FROM user_character_ability_modifications WHERE character_id=? "
                "ORDER BY modification_id",
                (character_id,),
            ).fetchall()
        )
        choices = tuple(
            self._choice_from_row(db, item, live_references)
            for item in db.execute(
                "SELECT resolution_id,owner_dataset_id,owner_type,owner_key,choice_key,"
                "selected_option_key,selected_reference_kind,selected_reference_identity,"
                "selected_reference_name,selected_reference_edition,selected_value,resolution_state,"
                "character_level,class_identity,class_level,source_rule "
                "FROM user_character_choices "
                "WHERE character_id=? ORDER BY resolution_id",
                (character_id,),
            ).fetchall()
        )
        feats = tuple(
            FeatSelection(
                int(item[0]),
                resolve("feat", str(item[1]), str(item[2]), str(item[3])),
                str(item[4]),
                str(item[5]),
                str(item[6]) if item[6] is not None else None,
                str(item[7]) if item[7] is not None else None,
                str(item[8]) if item[8] is not None else None,
                str(item[9]) if item[9] is not None else None,
                str(item[10]) if item[10] is not None else None,
                int(item[11]) if item[11] is not None else None,
                str(item[12]) if item[12] is not None else None,
                int(item[13]) if item[13] is not None else None,
                str(item[14]) if item[14] is not None else None,
                str(item[15]),
            )
            for item in db.execute(
                "SELECT feat_selection_id,feat_identity,feat_name,edition,provenance_kind,"
                "provenance_label,source_owner_dataset_id,source_owner_type,source_owner_key,"
                "source_choice_key,source_option_key,character_level,class_identity,class_level,"
                "source_rule,resolution_state FROM user_character_feats WHERE character_id=? "
                "ORDER BY feat_selection_id",
                (character_id,),
            ).fetchall()
        )
        spells = tuple(
            SpellSelection(
                int(item[0]),
                resolve("spell", str(item[1]), str(item[2]), str(item[3])),
                str(item[4]),
                resolve("class", str(item[5]), str(item[6]), "2024")
                if item[5] is not None
                else None,
                int(item[7]) if item[7] is not None else None,
                int(item[8]) if item[8] is not None else None,
                str(item[9]) if item[9] is not None else None,
                str(item[10]) if item[10] is not None else None,
                str(item[11]) if item[11] is not None else None,
                str(item[12]) if item[12] is not None else None,
                str(item[13]) if item[13] is not None else None,
                str(item[14]) if item[14] is not None else None,
                str(item[15]),
            )
            for item in db.execute(
                "SELECT spell_selection_id,spell_identity,spell_name,edition,acquisition,"
                "source_class_identity,source_class_name,character_level,class_level,"
                "source_owner_dataset_id,source_owner_type,source_owner_key,source_choice_key,"
                "source_option_key,source_rule,resolution_state FROM user_character_spells "
                "WHERE character_id=? ORDER BY spell_selection_id",
                (character_id,),
            ).fetchall()
        )
        equipment = tuple(
            EquipmentRecord(
                int(item[0]),
                resolve("item", str(item[1]), str(item[2]), str(item[3]))
                if item[1] is not None
                else None,
                str(item[4]) if item[4] is not None else None,
                int(item[5]),
                bool(item[6]),
                str(item[7]),
                str(item[8]),
                str(item[9]),
                str(item[10]) if item[10] is not None else None,
                str(item[11]) if item[11] is not None else None,
                str(item[12]) if item[12] is not None else None,
                str(item[13]) if item[13] is not None else None,
                str(item[14]) if item[14] is not None else None,
                int(item[15]) if item[15] is not None else None,
                str(item[16]) if item[16] is not None else None,
                int(item[17]) if item[17] is not None else None,
                str(item[18]) if item[18] is not None else None,
                str(item[19]),
            )
            for item in db.execute(
                "SELECT equipment_id,item_identity,item_name,edition,unresolved_selection,quantity,"
                "equipped,carried_state,provenance_kind,provenance_label,source_owner_dataset_id,"
                "source_owner_type,source_owner_key,source_choice_key,source_option_key,character_level,"
                "class_identity,class_level,source_rule,resolution_state "
                "FROM user_character_equipment "
                "WHERE character_id=? ORDER BY equipment_id",
                (character_id,),
            ).fetchall()
        )
        currency = tuple(
            CurrencyRecord(
                int(item[0]),
                str(item[1]),
                int(item[2]),
                str(item[3]),
                str(item[4]),
                str(item[5]) if item[5] is not None else None,
                str(item[6]) if item[6] is not None else None,
                str(item[7]) if item[7] is not None else None,
                str(item[8]) if item[8] is not None else None,
                str(item[9]) if item[9] is not None else None,
                str(item[10]) if item[10] is not None else None,
            )
            for item in db.execute(
                "SELECT currency_id,currency_key,amount,provenance_kind,provenance_label,"
                "source_owner_dataset_id,source_owner_type,source_owner_key,source_choice_key,"
                "source_option_key,source_rule FROM user_character_currency "
                "WHERE character_id=? ORDER BY currency_id",
                (character_id,),
            ).fetchall()
        )
        hp_choices = tuple(
            HitPointChoice(
                int(item[0]),
                resolve("class", str(item[1]), str(item[2]), str(item[5])),
                int(item[3]),
                str(item[4]),
                int(item[6]),
            )
            for item in db.execute(
                "SELECT h.total_level,l.class_identity,l.class_name,l.resulting_class_level,"
                "h.choice_kind,h.edition,h.amount "
                "FROM user_character_hp_choices h "
                "JOIN user_character_levels l USING(character_id,total_level) "
                "WHERE h.character_id=? ORDER BY total_level",
                (character_id,),
            ).fetchall()
        )
        note_row = db.execute(
            "SELECT note_text FROM user_character_notes WHERE character_id=?", (character_id,)
        ).fetchone()
        return Character(
            character_id=character_id,
            name=str(row["name"]),
            edition=str(row["edition"]),
            state=str(row["state"]),
            schema_version=int(row["schema_version"]),
            name_confirmed=bool(row["creation_name_confirmed"]),
            ability_score_method=(
                str(row["ability_score_method"])
                if row["ability_score_method"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            species=top_ref("species"),
            background=top_ref("background"),
            levels=levels,
            subclasses=subclasses,
            base_ability_scores=base_scores,
            ability_modifications=modifications,
            choices=choices,
            feats=feats,
            spells=spells,
            equipment=equipment,
            currency=currency,
            hp_choices=hp_choices,
            notes=str(note_row[0]) if note_row else "",
        )

    def _choice_from_row(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        live_references: dict[tuple[str, str], tuple[str, str]] | None = None,
    ) -> ChoiceResolution:
        reference = None
        if row[7] is not None:
            reference = (
                _resolve_character_reference(
                    live_references or {}, str(row[6]), str(row[7]), str(row[8]), str(row[9])
                )
                if live_references is not None
                else self._resolve_reference(db, str(row[6]), str(row[7]), str(row[8]), str(row[9]))
            )
        return ChoiceResolution(
            int(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]) if row[5] is not None else None,
            reference,
            str(row[10]) if row[10] is not None else None,
            str(row[11]),
            int(row[12]) if row[12] is not None else None,
            str(row[13]) if row[13] is not None else None,
            int(row[14]) if row[14] is not None else None,
            str(row[15]),
        )

    def _resolve_reference(
        self,
        db: sqlite3.Connection,
        kind: str,
        identity: str,
        snapshot_name: str,
        snapshot_edition: str,
    ) -> PublishedReference:
        live = _lookup_reference(db, kind, identity)
        if live is None or live[1] != "2024":
            return PublishedReference(kind, identity, snapshot_name, snapshot_edition, True)
        return PublishedReference(kind, identity, live[0], live[1], False)

    def _is_missing(self, db: sqlite3.Connection, kind: str, identity: str) -> bool:
        live = _lookup_reference(db, kind, identity)
        return live is None or live[1] != "2024"

    def _require_live_reference(
        self,
        db: sqlite3.Connection,
        reference: PublishedReference,
        expected_kind: str,
    ) -> PublishedReference:
        if reference.kind != expected_kind:
            raise ValueError(f"expected a published {expected_kind} reference")
        if reference.edition != "2024":
            raise ValueError("character building currently supports the 2024 edition only")
        _validate_published_identity(expected_kind, reference.identity)
        live = _lookup_reference(db, expected_kind, reference.identity)
        if live is None:
            raise ValueError(f"exact published {expected_kind} reference is unavailable")
        if live[1] != "2024":
            raise ValueError(f"published {expected_kind} reference is not from the 2024 edition")
        return PublishedReference(expected_kind, reference.identity, live[0], live[1], False)

    def _reference_for_storage(
        self,
        db: sqlite3.Connection,
        reference: PublishedReference,
        expected_kind: str,
        allow_unresolved: bool,
    ) -> tuple[PublishedReference, str]:
        if reference.kind != expected_kind:
            raise ValueError(f"expected a published {expected_kind} reference")
        if reference.edition != "2024":
            raise ValueError("character building currently supports the 2024 edition only")
        _validate_published_identity(expected_kind, reference.identity)
        live = _lookup_reference(db, expected_kind, reference.identity)
        if live is None:
            if not allow_unresolved:
                raise ValueError(f"exact published {expected_kind} reference is unavailable")
            return _snapshot_reference(reference), "unresolved"
        if live[1] != "2024":
            raise ValueError(f"published {expected_kind} reference is not from the 2024 edition")
        return PublishedReference(expected_kind, reference.identity, live[0], live[1]), "resolved"

    def _require_character(self, db: sqlite3.Connection, character_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM user_characters WHERE character_id=?", (character_id,)
        ).fetchone()
        if row is None:
            raise CharacterNotFoundError(f"character '{character_id}' does not exist")
        return row

    def _validate_character_context(
        self,
        db: sqlite3.Connection,
        character_id: str,
        character_level: int | None,
        class_identity: str | None,
        class_level: int | None,
    ) -> None:
        total = int(
            db.execute(
                "SELECT COUNT(*) FROM user_character_levels WHERE character_id=?",
                (character_id,),
            ).fetchone()[0]
        )
        if character_level is not None:
            _validate_level_number(character_level, "character level")
            if character_level > total + 1:
                raise ValueError("decision context is after the next character level")
        if class_identity is None:
            if class_level is not None:
                raise ValueError("class-level context requires class_identity")
            return
        if class_level is None:
            raise ValueError("class-level context requires class_level")
        _validate_level_number(class_level, "class level")
        raw_level = db.execute(
            "SELECT MAX(resulting_class_level) FROM user_character_levels "
            "WHERE character_id=? AND class_identity=?",
            (character_id, class_identity),
        ).fetchone()[0]
        current = int(raw_level) if raw_level is not None else 0
        if class_level <= current:
            if character_level is not None:
                row = db.execute(
                    "SELECT 1 FROM user_character_levels WHERE character_id=? AND total_level=? "
                    "AND class_identity=? AND resulting_class_level=?",
                    (character_id, character_level, class_identity, class_level),
                ).fetchone()
                if row is None:
                    raise ValueError("class-level context does not match ordered level history")
            return
        if class_level != current + 1 or character_level not in (None, total + 1):
            raise ValueError("class-level context is not present in the level history")
        reference = _lookup_reference(db, "class", class_identity)
        if reference is None or reference[1] != "2024":
            raise ValueError("decision class identity is not an available 2024 class")

    def _require_choice_owner(
        self, db: sqlite3.Connection, dataset_id: str, owner_type: str, owner_key: str
    ) -> None:
        row = db.execute(
            "SELECT 1 FROM character_builder_owners WHERE dataset_id=? AND owner_type=? "
            "AND owner_key=? AND edition='2024'",
            (dataset_id, owner_type, owner_key),
        ).fetchone()
        if row is None:
            raise ValueError("choice owner identity is not present in published builder metadata")

    def _require_persisted_choice(
        self,
        db: sqlite3.Connection,
        dataset_id: str,
        owner_type: str,
        owner_key: str,
        choice_key: str,
        definition: object,
    ) -> dict[str, object]:
        row = db.execute(
            "SELECT choice_type,choice_count,criteria_kind,criteria_values_json,"
            "criteria_filters_json,"
            "depends_on_choice,depends_on_option,source_rule "
            "FROM character_rule_choices WHERE dataset_id=? AND owner_type=? AND owner_key=? "
            "AND choice_key=?",
            (dataset_id, owner_type, owner_key, choice_key),
        ).fetchone()
        if row is None:
            raise ValueError("choice is not present for the exact published owner identity")
        if str(row[0]) != str(definition.kind) or int(row[1]) != definition.count:
            raise ValueError("rules catalog does not match the persisted choice identity")
        if (
            (str(row[5]) if row[5] is not None else None)
            != (
                str(definition.depends_on_choice)
                if definition.depends_on_choice is not None
                else None
            )
            or (str(row[6]) if row[6] is not None else None)
            != (
                str(definition.depends_on_option)
                if definition.depends_on_option is not None
                else None
            )
            or str(row[7]) != definition.source_rule
        ):
            raise ValueError("rules catalog dependencies do not match persisted choice metadata")
        expected_criteria = definition.criteria
        if (str(row[2]) if row[2] is not None else None) != (
            str(expected_criteria.kind) if expected_criteria is not None else None
        ):
            raise ValueError("rules catalog criteria do not match persisted choice metadata")
        expected_values = (
            json.dumps(
                list(expected_criteria.values),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if expected_criteria is not None
            else None
        )
        expected_filters = (
            json.dumps(
                expected_criteria.filters, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if expected_criteria is not None
            else None
        )
        for found, expected, label in (
            (row[3], expected_values, "criteria values"),
            (row[4], expected_filters, "criteria filters"),
        ):
            if found != expected:
                raise ValueError(f"rules catalog {label} do not match persisted choice metadata")
        options = {
            str(option[0]): option
            for option in db.execute(
                "SELECT option_key,reference_kind,reference_identity,value,resolved "
                "FROM character_rule_choice_options WHERE dataset_id=? AND owner_type=? "
                "AND owner_key=? AND choice_key=?",
                (dataset_id, owner_type, owner_key, choice_key),
            ).fetchall()
        }
        if set(options) != {str(option.option_key) for option in definition.options}:
            raise ValueError("rules catalog options do not match persisted choice metadata")
        return {"row": row, "options": options}

    def _subclass_selection_level(self, db: sqlite3.Connection, identity: str) -> int | None:
        try:
            dataset_id, parent_key, subclass_key = _subclass_parts(identity)
        except ValueError:
            return None
        row = db.execute(
            "SELECT parent_class_key,metadata_json FROM character_builder_owners "
            "WHERE dataset_id=? AND owner_type='subclass' AND owner_key=? AND edition='2024'",
            (dataset_id, subclass_key),
        ).fetchone()
        if row is None or str(row[0]) != parent_key:
            return None
        try:
            value = json.loads(str(row[1])).get("selection_level")
            return int(value) if value is not None else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None


def _lookup_reference(db: sqlite3.Connection, kind: str, identity: str) -> tuple[str, str] | None:
    if kind not in _REFERENCE_KINDS:
        return None
    if kind == "subclass":
        try:
            dataset_id, parent_key, subclass_key = _subclass_parts(identity)
        except ValueError:
            return None
        row = db.execute(
            "SELECT s.name,src.edition FROM subclasses s "
            "JOIN entries parent ON parent.id=s.class_id "
            "JOIN sources src ON src.id=s.source_id "
            "JOIN sources parent_src ON parent_src.id=parent.source_id "
            "WHERE s.dataset_id=? AND parent.local_key=? AND s.subclass_key=? "
            "AND src.edition=parent_src.edition",
            (dataset_id, parent_key, subclass_key),
        ).fetchone()
    else:
        try:
            dataset_id, local_key = _direct_parts(identity)
        except ValueError:
            return None
    if kind != "subclass" and kind in {"species", "background", "optional_feature"}:
        row = db.execute(
            "SELECT name,edition FROM character_builder_owners WHERE dataset_id=? "
            "AND owner_type=? AND owner_key=?",
            (dataset_id, kind, local_key),
        ).fetchone()
    elif kind != "subclass":
        row = db.execute(
            "SELECT e.name,src.edition FROM entries e JOIN sources src ON src.id=e.source_id "
            "WHERE e.dataset_id=? AND e.local_key=? AND e.kind=?",
            (dataset_id, local_key, kind),
        ).fetchone()
    return (str(row[0]), str(row[1])) if row is not None and row[1] is not None else None


def _character_reference_map(
    db: sqlite3.Connection, character_id: str
) -> dict[tuple[str, str], tuple[str, str]]:
    """Resolve all references in one character snapshot with a bounded set of queries."""

    rows = db.execute(
        "SELECT 'species',species_identity,species_name,species_edition FROM user_characters "
        "WHERE character_id=? AND species_identity IS NOT NULL "
        "UNION ALL SELECT 'background',background_identity,background_name,background_edition "
        "FROM user_characters WHERE character_id=? AND background_identity IS NOT NULL "
        "UNION ALL SELECT 'class',class_identity,class_name,edition FROM user_character_levels "
        "WHERE character_id=? "
        "UNION ALL SELECT 'class',class_identity,class_name,edition FROM user_character_subclasses "
        "WHERE character_id=? "
        "UNION ALL SELECT 'subclass',subclass_identity,subclass_name,edition "
        "FROM user_character_subclasses WHERE character_id=? "
        "UNION ALL SELECT selected_reference_kind,selected_reference_identity,"
        "selected_reference_name,selected_reference_edition FROM user_character_choices "
        "WHERE character_id=? AND selected_reference_identity IS NOT NULL "
        "UNION ALL SELECT 'feat',feat_identity,feat_name,edition FROM user_character_feats "
        "WHERE character_id=? "
        "UNION ALL SELECT 'spell',spell_identity,spell_name,edition FROM user_character_spells "
        "WHERE character_id=? "
        "UNION ALL SELECT 'class',source_class_identity,source_class_name,'2024' "
        "FROM user_character_spells WHERE character_id=? AND source_class_identity IS NOT NULL "
        "UNION ALL SELECT 'item',item_identity,item_name,edition FROM user_character_equipment "
        "WHERE character_id=? AND item_identity IS NOT NULL",
        (character_id,) * 10,
    ).fetchall()
    requested: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        kind, identity = str(row[0]), str(row[1])
        if kind in _REFERENCE_KINDS and identity:
            requested[kind].add(identity)
    resolved: dict[tuple[str, str], tuple[str, str]] = {}
    for kind, identities in sorted(requested.items()):
        if kind == "subclass":
            parsed = []
            for identity in sorted(identities):
                try:
                    dataset_id, parent_key, subclass_key = _subclass_parts(identity)
                except ValueError:
                    continue
                parsed.append((identity, dataset_id, parent_key, subclass_key))
            for start in range(0, len(parsed), 250):
                chunk = parsed[start : start + 250]
                where = " OR ".join(
                    "(s.dataset_id=? AND parent.local_key=? AND s.subclass_key=?)" for _ in chunk
                )
                params = [
                    part
                    for _identity, dataset_id, parent_key, subclass_key in chunk
                    for part in (dataset_id, parent_key, subclass_key)
                ]
                live_rows = db.execute(
                    "SELECT s.dataset_id,parent.local_key,s.subclass_key,s.name,src.edition "
                    "FROM subclasses s JOIN entries parent ON parent.id=s.class_id "
                    "JOIN sources src ON src.id=s.source_id JOIN sources parent_src "
                    "ON parent_src.id=parent.source_id WHERE src.edition=parent_src.edition AND ("
                    + where
                    + ")",
                    params,
                ).fetchall()
                by_parts = {
                    (str(item[0]), str(item[1]), str(item[2])): (str(item[3]), str(item[4]))
                    for item in live_rows
                }
                for identity, dataset_id, parent_key, subclass_key in chunk:
                    value = by_parts.get((dataset_id, parent_key, subclass_key))
                    if value is not None:
                        resolved[(kind, identity)] = value
            continue
        parsed_direct = []
        for identity in sorted(identities):
            try:
                dataset_id, local_key = _direct_parts(identity)
            except ValueError:
                continue
            parsed_direct.append((identity, dataset_id, local_key))
        for start in range(0, len(parsed_direct), 300):
            chunk = parsed_direct[start : start + 300]
            where = " OR ".join("(dataset_id=? AND local_key=?)" for _ in chunk)
            params = [
                part
                for _identity, dataset_id, local_key in chunk
                for part in (dataset_id, local_key)
            ]
            if kind in {"species", "background", "optional_feature"}:
                live_rows = db.execute(
                    "SELECT dataset_id,owner_key,name,edition FROM character_builder_owners "
                    "WHERE owner_type=? AND ("
                    + where.replace("dataset_id", "dataset_id").replace("local_key", "owner_key")
                    + ")",
                    [kind, *params],
                ).fetchall()
                by_parts = {
                    (str(item[0]), str(item[1])): (str(item[2]), str(item[3])) for item in live_rows
                }
            else:
                live_rows = db.execute(
                    "SELECT e.dataset_id,e.local_key,e.name,src.edition FROM entries e "
                    "JOIN sources src ON src.id=e.source_id WHERE e.kind=? AND ("
                    + where.replace("dataset_id", "e.dataset_id").replace(
                        "local_key", "e.local_key"
                    )
                    + ")",
                    [kind, *params],
                ).fetchall()
                by_parts = {
                    (str(item[0]), str(item[1])): (str(item[2]), str(item[3])) for item in live_rows
                }
            for identity, dataset_id, local_key in chunk:
                value = by_parts.get((dataset_id, local_key))
                if value is not None:
                    resolved[(kind, identity)] = value
    return resolved


def _resolve_character_reference(
    live_references: dict[tuple[str, str], tuple[str, str]],
    kind: str,
    identity: str,
    snapshot_name: str,
    snapshot_edition: str,
) -> PublishedReference:
    live = live_references.get((kind, identity))
    if live is None or live[1] != "2024":
        return PublishedReference(kind, identity, snapshot_name, snapshot_edition, True)
    return PublishedReference(kind, identity, live[0], live[1], False)


def _criteria_match(
    db: sqlite3.Connection,
    owner_dataset_id: str,
    criteria: object,
    reference: PublishedReference | None,
    value: str | None,
) -> bool | None:
    """Verify criteria where normalized metadata supports it; otherwise return unknown."""

    kind = str(criteria.kind)
    values = {str(item).casefold() for item in criteria.values}
    if kind == "skill":
        if value is None:
            return False
        row = db.execute(
            "SELECT 1 FROM character_builder_skills WHERE dataset_id=? AND skill_key=?",
            (owner_dataset_id, value),
        ).fetchone()
        return row is not None
    if kind == "language":
        if value is None:
            return False
        candidate = value.casefold()
        if "all" in values:
            row = db.execute(
                "SELECT 1 FROM character_rule_grants WHERE dataset_id=? "
                "AND proficiency_kind='language' AND lower(value)=? LIMIT 1",
                (owner_dataset_id, candidate),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT 1 FROM character_rule_grants WHERE dataset_id=? "
                "AND proficiency_kind='language' AND lower(value)=? "
                "AND lower(value) IN (" + ",".join("?" for _ in values) + ") LIMIT 1",
                (owner_dataset_id, candidate, *sorted(values)),
            ).fetchone()
        return row is not None
    if reference is None:
        return None
    if kind == "feat_category" and reference.kind == "feat":
        try:
            dataset_id, local_key = _direct_parts(reference.identity)
        except ValueError:
            return False
        row = db.execute(
            "SELECT f.category FROM feats f JOIN entries e ON e.id=f.entry_id "
            "WHERE f.dataset_id=? AND e.local_key=?",
            (dataset_id, local_key),
        ).fetchone()
        if row is None:
            return None
        return not values or normalize_feat_category(str(row[0])) in {
            normalize_feat_category(item) for item in values
        }
    if kind == "spell_level" and reference.kind == "spell":
        dataset_id, local_key = _direct_parts(reference.identity)
        row = db.execute(
            "SELECT sp.level FROM spells sp JOIN entries e ON e.id=sp.entry_id "
            "WHERE sp.dataset_id=? AND e.local_key=?",
            (dataset_id, local_key),
        ).fetchone()
        if row is None:
            return None
        actual = _normalize_builder_value(str(row[0]))
        return actual in {_normalize_builder_value(item) for item in values}
    if kind == "spell_list" and reference.kind == "spell":
        try:
            spell_dataset, spell_key = _direct_parts(reference.identity)
        except ValueError:
            return False
        spell_exists = db.execute(
            "SELECT 1 FROM entries e JOIN spells sp ON sp.entry_id=e.id "
            "WHERE e.dataset_id=? AND e.local_key=? LIMIT 1",
            (spell_dataset, spell_key),
        ).fetchone()
        if spell_exists is None:
            return None
        class_keys: set[str] = set()
        for item in criteria.values:
            try:
                referenced_dataset, class_key = _direct_parts(str(item))
            except ValueError:
                referenced_dataset, class_key = owner_dataset_id, str(item)
            if referenced_dataset == owner_dataset_id:
                class_keys.add(class_key)
        if not class_keys:
            return False
        marks = ",".join("?" for _ in class_keys)
        filters = criteria.filters
        spell_levels = filters.get("spell_level") if isinstance(filters, dict) else None
        params: list[object] = [spell_dataset, spell_key, owner_dataset_id, *sorted(class_keys)]
        level_clause = ""
        if isinstance(spell_levels, list) and spell_levels:
            level_marks = ",".join("?" for _ in spell_levels)
            level_clause = f" AND CAST(sp.level AS TEXT) IN ({level_marks})"
            params.extend(str(level) for level in spell_levels)
        row = db.execute(
            "SELECT 1 FROM entries spell_entry JOIN spells sp ON sp.entry_id=spell_entry.id "
            "JOIN spell_classes sc ON sc.spell_id=sp.entry_id "
            "JOIN classes c ON c.entry_id=sc.class_id "
            "JOIN entries class_entry ON class_entry.id=c.entry_id "
            f"WHERE spell_entry.dataset_id=? AND spell_entry.local_key=? "
            f"AND class_entry.dataset_id=? AND class_entry.local_key IN ({marks})"
            f"{level_clause} LIMIT 1",
            params,
        ).fetchone()
        return row is not None
    if kind == "item_type" and reference.kind == "item":
        dataset_id, local_key = _direct_parts(reference.identity)
        row = db.execute(
            "SELECT i.item_type FROM items i JOIN entries e ON e.id=i.entry_id "
            "WHERE i.dataset_id=? AND e.local_key=?",
            (dataset_id, local_key),
        ).fetchone()
        if row is None:
            return None
        return _normalize_builder_value(str(row[0])) in {
            _normalize_builder_value(value) for value in values
        }
    if kind == "weapon_category" and reference.kind == "item":
        try:
            dataset_id, local_key = _direct_parts(reference.identity)
        except ValueError:
            return False
        row = db.execute(
            "SELECT weapon_category FROM character_builder_equipment "
            "WHERE dataset_id=? AND item_key=?",
            (dataset_id, local_key),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]).casefold() in values if row[0] is not None else False
    if kind == "optional_feature_type" and reference.kind == "optional_feature":
        dataset_id, local_key = _direct_parts(reference.identity)
        row = db.execute(
            "SELECT metadata_json FROM character_builder_owners WHERE dataset_id=? "
            "AND owner_type='optional_feature' AND owner_key=?",
            (dataset_id, local_key),
        ).fetchone()
        if row is None:
            return None
        try:
            option_type = str(json.loads(row[0]).get("option_type", "")).casefold()
        except (TypeError, json.JSONDecodeError):
            return None
        return option_type in values
    if kind == "weapon_mastery" and reference.kind == "rule":
        try:
            dataset_id, local_key = _direct_parts(reference.identity)
        except ValueError:
            return False
        if dataset_id != owner_dataset_id:
            return False
        return local_key.casefold() in values
    if kind == "weapon_mastery" and reference.kind == "item":
        try:
            dataset_id, local_key = _direct_parts(reference.identity)
        except ValueError:
            return False
        row = db.execute(
            "SELECT mastery_references_json FROM character_builder_equipment "
            "WHERE dataset_id=? AND item_key=?",
            (dataset_id, local_key),
        ).fetchone()
        if row is None:
            return None
        try:
            mastery_refs = json.loads(str(row[0]))
        except (TypeError, json.JSONDecodeError):
            return None
        identities = {
            str(item.get("identity", "")).casefold().split(":", 1)[-1]
            for item in mastery_refs
            if isinstance(item, dict)
        }
        normalized_values = {item.split(":", 1)[-1] for item in values}
        return bool(identities & normalized_values)
    if kind == "source_filter" and reference.kind in {"item", "spell", "feat", "class"}:
        try:
            dataset_id, local_key = _direct_parts(reference.identity)
        except ValueError:
            return False
        row = db.execute(
            "SELECT src.source_key FROM entries e JOIN sources src ON src.id=e.source_id "
            "WHERE e.dataset_id=? AND e.local_key=?",
            (dataset_id, local_key),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]).casefold() in values
    if kind == "tool_group" and reference.kind == "item":
        try:
            dataset_id, local_key = _direct_parts(reference.identity)
        except ValueError:
            return False
        row = db.execute(
            "SELECT i.item_type,e.name FROM items i JOIN entries e ON e.id=i.entry_id "
            "WHERE i.dataset_id=? AND e.local_key=?",
            (dataset_id, local_key),
        ).fetchone()
        if row is None:
            return None
        item_type = str(row[0]).casefold()
        item_name = str(row[1]).casefold()
        if "all" in values:
            return any(token in item_type for token in ("tool", "instrument", "set"))
        normalized = {_normalize_builder_value(item) for item in values}
        if "artisan" in normalized:
            return "artisan" in item_type or "artisan" in item_name
        return any(item in item_type or item in item_name for item in normalized)
    return False


def _normalize_builder_value(value: str) -> str:
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    return {
        "instrumentmusical": "musicalinstrument",
        "setgaming": "gamingset",
        "gamingset": "gamingset",
    }.get(normalized, normalized)


def _character_references(character: Character) -> tuple[PublishedReference, ...]:
    refs: list[PublishedReference] = []
    if character.species is not None:
        refs.append(character.species)
    if character.background is not None:
        refs.append(character.background)
    refs.extend(row.class_reference for row in character.levels)
    for row in character.subclasses:
        refs.extend((row.class_reference, row.subclass_reference))
    refs.extend(
        row.selected_reference for row in character.choices if row.selected_reference is not None
    )
    refs.extend(row.feat_reference for row in character.feats)
    for row in character.spells:
        refs.append(row.spell_reference)
        if row.source_class_reference is not None:
            refs.append(row.source_class_reference)
    refs.extend(row.item_reference for row in character.equipment if row.item_reference is not None)
    refs.extend(row.class_reference for row in character.hp_choices)
    return tuple(refs)


def _reference_from_lookup(kind: str, identity: str, lookup: tuple[str, str]) -> PublishedReference:
    return PublishedReference(kind, identity, lookup[0], lookup[1], lookup[1] != "2024")


def _snapshot_reference(
    reference: PublishedReference | None,
    *,
    kind: str | None = None,
    identity: str | None = None,
) -> PublishedReference:
    if reference is None:
        resolved_kind = str(kind)
        resolved_identity = str(identity)
        _validate_published_identity(resolved_kind, resolved_identity)
        return PublishedReference(resolved_kind, resolved_identity, resolved_identity, "2024", True)
    resolved_kind = kind or reference.kind
    resolved_identity = identity or reference.identity
    _validate_published_identity(resolved_kind, resolved_identity)
    if reference.edition != "2024":
        raise ValueError("character references only support the 2024 edition")
    return PublishedReference(resolved_kind, resolved_identity, reference.name, "2024", True)


def _normalize_base_scores(base_scores: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(base_scores, Mapping):
        raise TypeError("base ability scores must be a mapping")
    result: dict[str, int] = {}
    for ability, score in base_scores.items():
        key = str(ability).lower()
        if key not in _ABILITIES:
            raise ValueError(f"unknown ability score '{ability}'")
        if not isinstance(score, int) or not 1 <= score <= 30:
            raise ValueError(f"base {key.upper()} score must be between 1 and 30")
        result[key] = score
    return dict(
        sorted(result.items(), key=lambda item: "str dex con int wis cha".split().index(item[0]))
    )


def _validate_ability_change(change: AbilityChange) -> None:
    if change.ability.lower() not in _ABILITIES:
        raise ValueError(f"unknown ability '{change.ability}'")
    if not isinstance(change.amount, int) or not -30 <= change.amount <= 30 or change.amount == 0:
        raise ValueError("ability modification amount must be a nonzero integer from -30 to 30")
    if change.source_kind not in {"background", "asi", "feat", "other"}:
        raise ValueError("unknown ability modification source kind")
    if not change.source_label.strip():
        raise ValueError("ability modification needs provenance text")
    _validate_provenance(change.provenance)


def _validate_level_number(value: int | None, label: str) -> None:
    if value is not None and (not isinstance(value, int) or not 1 <= value <= 20):
        raise ValueError(f"{label} must be between 1 and 20")


def _validate_provenance(value: DecisionProvenance) -> None:
    owner_parts = (value.owner_dataset_id, value.owner_type, value.owner_key)
    if any(part is not None for part in owner_parts) and not all(part for part in owner_parts):
        raise ValueError("decision provenance needs a complete owner identity")
    if value.owner_type is not None and value.owner_type not in _OWNER_TYPES:
        raise ValueError("unknown decision provenance owner type")
    if value.choice_key is not None and value.owner_dataset_id is None:
        raise ValueError("choice provenance requires an owner identity")
    if value.option_key is not None and value.choice_key is None:
        raise ValueError("option provenance requires a choice identity")
    if value.character_level is not None and not 1 <= value.character_level <= 20:
        raise ValueError("provenance character level must be between 1 and 20")
    if value.class_level is not None and not 1 <= value.class_level <= 20:
        raise ValueError("provenance class level must be between 1 and 20")


def _context_key(
    character_level: int | None,
    class_identity: str | None,
    class_level: int | None,
) -> str:
    return json.dumps(
        {
            "character_level": character_level,
            "class_identity": class_identity,
            "class_level": class_level,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _selection_fingerprint(
    option_key: str | None,
    reference: PublishedReference | None,
    value: str | None,
) -> str:
    if option_key is not None:
        return f"option:{option_key}"
    if reference is not None:
        return f"reference:{reference.kind}:{reference.identity}"
    if value is not None:
        return f"value:{value}"
    raise ValueError("choice selection needs an option, reference, or value")


def _canonical_reference(owner_dataset_id: str, identity: str) -> str:
    return identity if ":" in identity else f"{owner_dataset_id}:{identity}"


def _canonical_subclass_reference(
    db: sqlite3.Connection, owner_dataset_id: str, identity: str
) -> str:
    """Resolve a builder subclass key to its stable parent-scoped character identity."""
    if ":subclass:" in identity:
        return identity
    dataset_id, separator, local_key = identity.partition(":")
    if not separator:
        dataset_id, local_key = owner_dataset_id, identity
    if dataset_id != owner_dataset_id and identity.startswith("subclass/"):
        dataset_id = owner_dataset_id
    if not local_key.startswith("subclass/"):
        return identity
    row = db.execute(
        "SELECT parent.local_key,s.subclass_key FROM subclasses s "
        "JOIN entries parent ON parent.id=s.class_id "
        "JOIN sources source ON source.id=s.source_id "
        "WHERE s.dataset_id=? AND s.subclass_key=? AND source.edition='2024'",
        (dataset_id, local_key),
    ).fetchone()
    if row is None:
        return identity
    return f"{dataset_id}:subclass:{row[0]}:{row[1]}"


def _direct_parts(identity: str) -> tuple[str, str]:
    dataset_id, separator, local_key = identity.partition(":")
    if not separator or not dataset_id or not local_key or ":" in local_key:
        raise ValueError("published identity must have the form dataset_id:local_key")
    _validate_dataset_and_local_key(dataset_id, local_key)
    return dataset_id, local_key


def _subclass_parts(identity: str) -> tuple[str, str, str]:
    dataset_id, separator, tail = identity.partition(":")
    if not separator or not dataset_id or not tail.startswith("subclass:"):
        raise ValueError("subclass identity must use the published stable identity format")
    parent_key, found, subclass_key = tail.removeprefix("subclass:").partition(":")
    if not found or not parent_key or not subclass_key or ":" in subclass_key:
        raise ValueError("subclass identity must contain a parent class and subclass key")
    _validate_dataset_id(dataset_id)
    _validate_local_key(parent_key)
    _validate_local_key(subclass_key)
    return dataset_id, parent_key, subclass_key


def _validate_published_identity(kind: str, identity: str) -> None:
    if kind not in _REFERENCE_KINDS:
        raise ValueError(f"unsupported published reference kind '{kind}'")
    if kind == "subclass":
        _subclass_parts(identity)
    else:
        _direct_parts(identity)


def _validate_dataset_and_local_key(dataset_id: str, local_key: str) -> None:
    _validate_dataset_id(dataset_id)
    _validate_local_key(local_key)


def _validate_dataset_id(dataset_id: str) -> None:
    if len(dataset_id) > 80 or re.fullmatch(r"[a-z0-9][a-z0-9._-]*", dataset_id) is None:
        raise ValueError("published identity contains an invalid dataset ID")


def _validate_local_key(local_key: str) -> None:
    if len(local_key) > 200 or local_key.startswith("/") or local_key.endswith("/"):
        raise ValueError("published identity contains an invalid local key")
    if (
        "\\" in local_key
        or ":" in local_key
        or any(character.isspace() for character in local_key)
        or any(ord(character) < 32 for character in local_key)
    ):
        raise ValueError("published identity contains an invalid local key")
    if any(segment in {"", ".", ".."} for segment in local_key.split("/")):
        raise ValueError("published identity contains an invalid local key")


def _subclass_parent_identity(identity: str) -> str:
    dataset_id, parent_key, _subclass_key = _subclass_parts(identity)
    return f"{dataset_id}:{parent_key}"


def _subclass_local_key(identity: str) -> str:
    return _subclass_parts(identity)[2]


def _dataset_part(identity: str) -> str:
    return _direct_parts(identity)[0]


def _local_part(identity: str) -> str:
    return _direct_parts(identity)[1]


def _clean_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("character name must be text")
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError("character name must not be empty")
    if len(cleaned) > 200:
        raise ValueError("character name must be at most 200 characters")
    return cleaned


def _require_edition(edition: str) -> None:
    if edition != "2024":
        raise ValueError("saved characters currently support the 2024 edition only")


def _require_state(state: str) -> None:
    if state not in {"draft", "complete"}:
        raise ValueError("character state must be 'draft' or 'complete'")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _touch(db: sqlite3.Connection, character_id: str) -> None:
    row = db.execute(
        "SELECT updated_at FROM user_characters WHERE character_id=?", (character_id,)
    ).fetchone()
    if row is None:
        raise CharacterNotFoundError(f"character '{character_id}' does not exist")
    now = datetime.now(UTC)
    current = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    if now <= current:
        now = current + timedelta(microseconds=1)
    updated = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    db.execute(
        "UPDATE user_characters SET updated_at=? WHERE character_id=?", (updated, character_id)
    )
