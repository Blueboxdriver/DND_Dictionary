"""Persistent, user-owned organization data, separate from imported records."""

from __future__ import annotations

import re
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass

from .search import EntryDetail
from .storage.database import Database


@dataclass(frozen=True)
class PersonalEntry:
    identity: str
    name: str
    category: str
    edition: str | None
    source: str | None
    tags: tuple[str, ...] = ()
    missing: bool = False
    note: str = ""


@dataclass(frozen=True)
class Collection:
    collection_id: int
    name: str
    description: str | None
    entry_count: int


class PersonalDataService:
    """One SQLite connection per public operation; reference IDs stay logical."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def record_search(self, query: str, limit: int = 25) -> None:
        cleaned = " ".join(query.split())
        if not cleaned:
            return
        with self.database.connection() as db:
            db.execute("DELETE FROM user_recent_searches WHERE lower(query)=lower(?)", (cleaned,))
            db.execute(
                "INSERT INTO user_recent_searches(query,used_at) VALUES (?,CURRENT_TIMESTAMP)",
                (cleaned,),
            )
            db.execute(
                "DELETE FROM user_recent_searches WHERE query NOT IN "
                "(SELECT query FROM user_recent_searches ORDER BY search_id DESC LIMIT ?)",
                (max(1, limit),),
            )
            db.commit()

    def recent_searches(self, limit: int = 25) -> tuple[str, ...]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT query FROM user_recent_searches ORDER BY search_id DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
            return tuple(str(row[0]) for row in rows)

    def clear_recent_searches(self) -> None:
        with self.database.connection() as db:
            db.execute("DELETE FROM user_recent_searches")
            db.commit()

    @staticmethod
    def _metadata(detail: EntryDetail) -> tuple[str, str | None, str, str | None, str]:
        return (
            detail.category.value,
            str(detail.fields.get("edition")) if detail.fields.get("edition") else None,
            detail.name,
            None,
            detail.source_label,
        )

    def is_favorite(self, identity: str) -> bool:
        with self.database.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM user_favorites WHERE entry_identity=?", (identity,)
                ).fetchone()
                is not None
            )

    def remove_favorite(self, identity: str) -> None:
        with self.database.connection() as db:
            db.execute("DELETE FROM user_favorites WHERE entry_identity=?", (identity,))
            db.commit()

    def collections_for(self, identity: str) -> tuple[str, ...]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT c.name FROM user_collections c "
                "JOIN user_collection_entries e USING(collection_id) "
                "WHERE e.entry_identity=? ORDER BY c.name COLLATE NOCASE",
                (identity,),
            ).fetchall()
            return tuple(str(r[0]) for r in rows)

    def collection_ids_for(self, identity: str) -> tuple[int, ...]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT collection_id FROM user_collection_entries "
                "WHERE entry_identity=? ORDER BY collection_id",
                (identity,),
            ).fetchall()
            return tuple(int(r[0]) for r in rows)

    def remove_collection_identity(self, collection_id: int, identity: str) -> None:
        with self.database.connection() as db:
            db.execute(
                "DELETE FROM user_collection_entries WHERE collection_id=? AND entry_identity=?",
                (collection_id, identity),
            )
            db.commit()

    def set_favorite(self, detail: EntryDetail, favorite: bool) -> None:
        with self.database.connection() as db:
            if favorite:
                db.execute(
                    "INSERT OR IGNORE INTO user_favorites "
                    "(entry_identity,kind,edition,entry_name,source_identity,source_label) "
                    "VALUES (?,?,?,?,?,?)",
                    (detail.identity, *self._metadata(detail)),
                )
            else:
                db.execute("DELETE FROM user_favorites WHERE entry_identity=?", (detail.identity,))
            db.commit()

    def list_favorites(
        self, query: str = "", category: str | None = None, edition: str | None = None
    ) -> tuple[PersonalEntry, ...]:
        profile = (
            self.database.profiler.operation("personal.favorites")
            if self.database.profiler is not None
            else nullcontext()
        )
        with profile, self.database.connection() as db:
            rows = db.execute(
                "SELECT entry_identity,kind,edition,entry_name,source_label FROM user_favorites "
                "ORDER BY entry_name COLLATE NOCASE,entry_identity"
            ).fetchall()
            return self._resolve_rows(db, rows, query, category, edition)

    def create_collection(self, name: str, description: str | None = None) -> int:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("collection name must not be empty")
        with self.database.connection() as db:
            cursor = db.execute(
                "INSERT INTO user_collections(name,description) VALUES (?,?)",
                (cleaned, description.strip() or None if description else None),
            )
            db.commit()
            return int(cursor.lastrowid)

    def rename_collection(
        self, collection_id: int, name: str, description: str | None = None
    ) -> None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("collection name must not be empty")
        with self.database.connection() as db:
            db.execute(
                "UPDATE user_collections SET name=?,description=? WHERE collection_id=?",
                (cleaned, description.strip() or None if description else None, collection_id),
            )
            db.commit()

    def delete_collection(self, collection_id: int) -> None:
        with self.database.connection() as db:
            db.execute("DELETE FROM user_collections WHERE collection_id=?", (collection_id,))
            db.commit()

    def list_collections(self) -> tuple[Collection, ...]:
        profile = (
            self.database.profiler.operation("personal.collections")
            if self.database.profiler is not None
            else nullcontext()
        )
        with profile, self.database.connection() as db:
            rows = db.execute(
                "SELECT c.collection_id,c.name,c.description,COUNT(e.entry_identity) AS n "
                "FROM user_collections c LEFT JOIN user_collection_entries e USING(collection_id) "
                "GROUP BY c.collection_id ORDER BY c.name COLLATE NOCASE,c.collection_id"
            ).fetchall()
            return tuple(Collection(int(r[0]), str(r[1]), r[2], int(r[3])) for r in rows)

    def collection_contains(self, collection_id: int, identity: str) -> bool:
        with self.database.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM user_collection_entries "
                    "WHERE collection_id=? AND entry_identity=?",
                    (collection_id, identity),
                ).fetchone()
                is not None
            )

    def set_collection_membership(
        self, collection_id: int, detail: EntryDetail, present: bool
    ) -> None:
        with self.database.connection() as db:
            if present:
                db.execute(
                    "INSERT OR IGNORE INTO user_collection_entries "
                    "(collection_id,entry_identity,kind,edition,entry_name,"
                    "source_identity,source_label) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (collection_id, detail.identity, *self._metadata(detail)),
                )
            else:
                db.execute(
                    "DELETE FROM user_collection_entries "
                    "WHERE collection_id=? AND entry_identity=?",
                    (collection_id, detail.identity),
                )
            db.commit()

    def list_collection_entries(
        self,
        collection_id: int,
        query: str = "",
        category: str | None = None,
        edition: str | None = None,
        tag: str | None = None,
    ) -> tuple[PersonalEntry, ...]:
        profile = (
            self.database.profiler.operation("personal.collection_entries")
            if self.database.profiler is not None
            else nullcontext()
        )
        with profile, self.database.connection() as db:
            rows = db.execute(
                "SELECT entry_identity,kind,edition,entry_name,source_label "
                "FROM user_collection_entries "
                "WHERE collection_id=? ORDER BY entry_name COLLATE NOCASE,entry_identity",
                (collection_id,),
            ).fetchall()
            records = self._resolve_rows(db, rows, query, category, edition)
            if tag:
                normalized = normalize_tag(tag)[0]
                records = tuple(r for r in records if normalized in r.tags)
            return records

    def tags_for(self, identity: str) -> tuple[str, ...]:
        with self.database.connection() as db:
            return tuple(self._tags_for(db, identity))

    @staticmethod
    def _tags_for(db: sqlite3.Connection, identity: str) -> list[str]:
        return [
            str(row[0])
            for row in db.execute(
                "SELECT t.display_name FROM user_tags t JOIN user_entry_tags et USING(tag_id) "
                "WHERE et.entry_identity=? ORDER BY t.normalized_name",
                (identity,),
            ).fetchall()
        ]

    def add_tag(self, identity: str, tag: str) -> None:
        normalized, display = normalize_tag(tag)
        with self.database.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO user_tags(normalized_name,display_name) VALUES (?,?)",
                (normalized, display),
            )
            db.execute(
                "INSERT OR IGNORE INTO user_entry_tags(entry_identity,tag_id) "
                "SELECT ?,tag_id FROM user_tags WHERE normalized_name=?",
                (identity, normalized),
            )
            db.commit()

    def remove_tag(self, identity: str, tag: str) -> None:
        normalized, _ = normalize_tag(tag)
        with self.database.connection() as db:
            db.execute(
                "DELETE FROM user_entry_tags WHERE entry_identity=? "
                "AND tag_id=(SELECT tag_id FROM user_tags WHERE normalized_name=?)",
                (identity, normalized),
            )
            db.execute(
                "DELETE FROM user_tags WHERE normalized_name=? "
                "AND NOT EXISTS (SELECT 1 FROM user_entry_tags "
                "WHERE tag_id=user_tags.tag_id)",
                (normalized,),
            )
            db.commit()

    def list_tags(self) -> tuple[tuple[str, int], ...]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT t.display_name,COUNT(et.entry_identity) FROM user_tags t "
                "LEFT JOIN user_entry_tags et USING(tag_id) "
                "GROUP BY t.tag_id ORDER BY t.normalized_name"
            ).fetchall()
            return tuple((str(r[0]), int(r[1])) for r in rows)

    def note_for(self, identity: str) -> str | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT note_text FROM user_notes WHERE entry_identity=?", (identity,)
            ).fetchone()
            return str(row[0]) if row else None

    def save_note(self, detail: EntryDetail, text: str) -> None:
        with self.database.connection() as db:
            if not text.strip():
                db.execute("DELETE FROM user_notes WHERE entry_identity=?", (detail.identity,))
            else:
                db.execute(
                    "INSERT INTO user_notes(entry_identity,kind,edition,entry_name,"
                    "source_identity,source_label,note_text) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(entry_identity) DO UPDATE SET "
                    "kind=excluded.kind,edition=excluded.edition,entry_name=excluded.entry_name,"
                    "source_identity=excluded.source_identity,source_label=excluded.source_label,"
                    "note_text=excluded.note_text,updated_at=CURRENT_TIMESTAMP",
                    (detail.identity, *self._metadata(detail), text),
                )
            db.commit()

    def _resolve_rows(
        self,
        db: sqlite3.Connection,
        rows: list[sqlite3.Row],
        query: str,
        category: str | None,
        edition: str | None,
    ) -> tuple[PersonalEntry, ...]:
        if not rows:
            return ()
        identities = [str(row[0]) for row in rows]
        tags_by_entry: dict[str, list[str]] = {}
        notes_by_entry: dict[str, str] = {}
        for batch in _chunks(identities, 400):
            placeholders = ",".join("?" for _ in batch)
            for tag_row in db.execute(
                "SELECT et.entry_identity,t.display_name FROM user_entry_tags et "
                "JOIN user_tags t USING(tag_id) "
                f"WHERE et.entry_identity IN ({placeholders}) "
                "ORDER BY t.normalized_name",
                batch,
            ):
                tags_by_entry.setdefault(str(tag_row[0]), []).append(str(tag_row[1]))
            note_rows = db.execute(
                "SELECT entry_identity,note_text FROM user_notes "
                f"WHERE entry_identity IN ({placeholders})",
                batch,
            ).fetchall()
            notes_by_entry.update({str(note[0]): str(note[1]) for note in note_rows})
        active = _active_entries(db, identities)
        results = []
        for row in rows:
            identity = str(row[0])
            live = active.get(identity)
            name = live[0] if live else str(row[3])
            kind = live[1] if live else str(row[1])
            record_edition = live[2] if live else (str(row[2]) if row[2] else None)
            source = live[3] if live else (str(row[4]) if row[4] else None)
            tags = tuple(tags_by_entry.get(identity, ()))
            note = notes_by_entry.get(identity, "")
            if category and kind != category:
                continue
            if edition and record_edition != edition:
                continue
            if query:
                haystack = " ".join(
                    (
                        name,
                        kind,
                        record_edition or "",
                        source or "",
                        " ".join(tags),
                        note,
                    )
                ).casefold()
                if query.casefold() not in haystack:
                    continue
            results.append(
                PersonalEntry(
                    identity,
                    name,
                    kind,
                    record_edition,
                    source,
                    tags,
                    live is None,
                    note,
                )
            )
        return tuple(results)


def _note_text(db: sqlite3.Connection, identity: str) -> str:
    row = db.execute(
        "SELECT note_text FROM user_notes WHERE entry_identity=?", (identity,)
    ).fetchone()
    return str(row[0]) if row else ""


def _chunks(values: list[str], size: int):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _active_entries(
    db: sqlite3.Connection, identities: list[str]
) -> dict[str, tuple[str, str, str | None, str | None]]:
    category_names = {
        "item": "items",
        "spell": "spells",
        "feat": "feats",
        "class": "classes",
        "monster": "monsters",
        "condition": "conditions",
        "rule": "rules",
    }
    direct: list[tuple[str, str]] = []
    subclasses: list[tuple[str, str, str]] = []
    for identity in identities:
        dataset_id, separator, local_key = identity.partition(":")
        if not separator:
            continue
        if ":subclass:" in identity:
            parent_key, found_separator, subclass_key = local_key.removeprefix(
                "subclass:"
            ).partition(":")
            if found_separator:
                subclasses.append((dataset_id, parent_key, subclass_key))
        else:
            direct.append((dataset_id, local_key))

    result: dict[str, tuple[str, str, str | None, str | None]] = {}
    for offset in range(0, len(direct), 200):
        pairs = direct[offset : offset + 200]
        terms = " OR ".join("(e.dataset_id=? AND e.local_key=?)" for _ in pairs)
        parameters = [part for pair in pairs for part in pair[:2]]
        rows = db.execute(
            "SELECT e.dataset_id||':'||e.local_key,e.name,e.kind,src.edition,src.title "
            "FROM entries e JOIN sources src ON src.id=e.source_id WHERE " + terms,
            parameters,
        ).fetchall()
        result.update(
            {
                str(row[0]): (
                    str(row[1]),
                    category_names.get(str(row[2]), str(row[2])),
                    str(row[3]) if row[3] else None,
                    str(row[4]),
                )
                for row in rows
            }
        )
    for offset in range(0, len(subclasses), 200):
        batch = subclasses[offset : offset + 200]
        terms = " OR ".join(
            "(s.dataset_id=? AND parent.local_key=? AND s.subclass_key=?)" for _ in batch
        )
        parameters = [part for item in batch for part in item]
        rows = db.execute(
            "SELECT s.dataset_id||':subclass:'||parent.local_key||':'||s.subclass_key,"
            "s.name,src.edition,src.title "
            "FROM subclasses s JOIN entries parent ON parent.id=s.class_id "
            "JOIN sources src ON src.id=s.source_id "
            "JOIN sources ps ON ps.id=parent.source_id "
            "WHERE src.edition=ps.edition AND (" + terms + ")",
            parameters,
        ).fetchall()
        result.update(
            {
                str(row[0]): (
                    str(row[1]), "subclasses", str(row[2]) if row[2] else None, str(row[3])
                )
                for row in rows
            }
        )
    return result


def normalize_tag(tag: str) -> tuple[str, str]:
    display = re.sub(r"\s+", " ", tag).strip().casefold()
    if not display:
        raise ValueError("tag must not be empty")
    return display, display
