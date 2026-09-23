"""Edition-aware resolution for explicit references between imported entries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .search import SearchCategory, normalize_name

if TYPE_CHECKING:
    from .storage.database import Database


@dataclass(frozen=True)
class ReferenceTarget:
    identity: str
    category: SearchCategory
    name: str
    edition: str | None
    source_label: str
    dataset_id: str


@dataclass(frozen=True)
class ReferenceResolution:
    candidates: tuple[ReferenceTarget, ...]

    @property
    def resolved(self) -> ReferenceTarget | None:
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


class CrossReferenceResolver:
    """Resolve names only after content type and canonical edition are fixed."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def resolve_reference(
        self,
        *,
        content_type: SearchCategory | str,
        name: str,
        edition: str | None,
        stable_id: str | None = None,
    ) -> ReferenceResolution:
        aliases = {
            "item": SearchCategory.ITEMS, "spell": SearchCategory.SPELLS,
            "feat": SearchCategory.FEATS, "class": SearchCategory.CLASSES,
            "subclass": SearchCategory.SUBCLASSES, "monster": SearchCategory.MONSTERS,
        }
        category = aliases.get(str(content_type), None)
        if category is None:
            category = SearchCategory(content_type)
        normalized = normalize_name(name)
        if stable_id:
            target = self.get_by_id(stable_id)
            return ReferenceResolution(
                (target,) if target is not None and target.category is category else ()
            )
        if not normalized or not edition:
            return ReferenceResolution(())

        kind = {
            SearchCategory.ITEMS: "item",
            SearchCategory.SPELLS: "spell",
            SearchCategory.FEATS: "feat",
            SearchCategory.CLASSES: "class",
            SearchCategory.SUBCLASSES: "subclass",
            SearchCategory.MONSTERS: "monster",
        }[category]
        with self.database.connection() as connection:
            if kind == "subclass":
                rows = connection.execute(
                    "SELECT s.dataset_id || ':subclass:' || parent.local_key || ':' || "
                    "s.subclass_key AS identity, s.name, src.edition, src.title AS source_label, "
                    "s.dataset_id FROM subclasses AS s "
                    "JOIN entries AS parent ON parent.id=s.class_id "
                    "JOIN sources AS src ON src.id=s.source_id "
                    "WHERE src.edition=? "
                    "ORDER BY s.dataset_id, parent.local_key, s.subclass_key",
                    (edition,),
                ).fetchall()
                rows = [row for row in rows if normalize_name(str(row["name"])) == normalized]
            else:
                rows = connection.execute(
                    "SELECT e.dataset_id || ':' || e.local_key AS identity, e.name, "
                    "src.edition, src.title AS source_label, e.dataset_id "
                    "FROM entries AS e JOIN sources AS src ON src.id=e.source_id "
                    "WHERE e.kind=? AND e.normalized_name=? AND src.edition=? "
                    "ORDER BY e.dataset_id, e.local_key",
                    (kind, normalized, edition),
                ).fetchall()
        return ReferenceResolution(tuple(
            ReferenceTarget(
                identity=str(row["identity"]), category=category, name=str(row["name"]),
                edition=str(row["edition"]) if row["edition"] is not None else None,
                source_label=str(row["source_label"]), dataset_id=str(row["dataset_id"]),
            )
            for row in rows
        ))

    def get_by_id(self, stable_id: str) -> ReferenceTarget | None:
        with self.database.connection() as connection:
            if ":subclass:" in stable_id:
                dataset_id, _tag, parent_key, subclass_key = stable_id.split(":", 3)
                row = connection.execute(
                    "SELECT s.dataset_id || ':subclass:' || parent.local_key || ':' || "
                    "s.subclass_key AS identity, s.name, src.edition, src.title AS source_label, "
                    "s.dataset_id FROM subclasses AS s "
                    "JOIN entries AS parent ON parent.id=s.class_id "
                    "JOIN sources AS src ON src.id=s.source_id "
                    "WHERE s.dataset_id=? AND parent.local_key=? AND s.subclass_key=?",
                    (dataset_id, parent_key, subclass_key),
                ).fetchone()
                category = SearchCategory.SUBCLASSES
            else:
                dataset_id, separator, local_key = stable_id.partition(":")
                if not separator:
                    return None
                row = connection.execute(
                    "SELECT e.dataset_id || ':' || e.local_key AS identity, e.kind, e.name, "
                    "src.edition, src.title AS source_label, e.dataset_id FROM entries AS e "
                    "JOIN sources AS src ON src.id=e.source_id "
                    "WHERE e.dataset_id=? AND e.local_key=?",
                    (dataset_id, local_key),
                ).fetchone()
                if row is None:
                    return None
                category = {
                    "item": SearchCategory.ITEMS, "spell": SearchCategory.SPELLS,
                    "feat": SearchCategory.FEATS, "class": SearchCategory.CLASSES,
                    "monster": SearchCategory.MONSTERS,
                }.get(str(row["kind"]))
                if category is None:
                    return None
        if row is None:
            return None
        return ReferenceTarget(
            identity=str(row["identity"]), category=category, name=str(row["name"]),
            edition=str(row["edition"]) if row["edition"] is not None else None,
            source_label=str(row["source_label"]), dataset_id=str(row["dataset_id"]),
        )

    def monster_spell_references(
        self, descriptions: tuple[str, ...], edition: str | None
    ) -> tuple[ReferenceTarget, ...]:
        """Find exact spell-name mentions inside structured spellcasting fields.

        This intentionally does no prose-wide or fuzzy matching. Callers pass
        only spellcasting/innate-spellcasting ability descriptions.
        """
        if not edition or not descriptions:
            return ()
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT e.dataset_id || ':' || e.local_key AS identity, e.name, "
                "src.edition, src.title AS source_label, e.dataset_id "
                "FROM entries AS e JOIN sources AS src ON src.id=e.source_id "
                "WHERE e.kind='spell' AND src.edition=? ORDER BY length(e.name) DESC, e.name",
                (edition,),
            ).fetchall()
        list_header = re.compile(
            r"(?:^|\n|;\s*)(?:at\s+will|\d+\s*/\s*(?:day|week)(?:\s+each)?|"
            r"cantrips?\s*\([^)]*\)|\d+(?:st|nd|rd|th)(?:\s*[-–]\s*\d+(?:st|nd|rd|th))?"
            r"\s+levels?(?:\s*\([^)]*\))?)\s*:\s*",
            re.IGNORECASE,
        )
        chunks: list[str] = []
        for description in descriptions:
            headers = list(list_header.finditer(description))
            for index, header in enumerate(headers):
                start = header.end()
                next_header = (
                    headers[index + 1].start()
                    if index + 1 < len(headers) else len(description)
                )
                body = description[start:next_header]
                boundary = min(
                    (position for position in (body.find("\n"), body.find(".")) if position >= 0),
                    default=len(body),
                )
                chunks.append(body[:boundary])
        combined = "\n".join(chunks)
        if not combined:
            return ()
        found: dict[str, ReferenceTarget] = {}
        for row in rows:
            name = str(row["name"])
            pattern = rf"(?<![\w]){re.escape(name)}(?![\w])"
            if re.search(pattern, combined, flags=re.IGNORECASE):
                found[str(row["identity"])] = ReferenceTarget(
                    identity=str(row["identity"]), category=SearchCategory.SPELLS,
                    name=name, edition=str(row["edition"]),
                    source_label=str(row["source_label"]), dataset_id=str(row["dataset_id"]),
                )
        return tuple(sorted(
            found.values(), key=lambda target: (target.name.casefold(), target.identity)
        ))

    def explicit_feat_prerequisite_references(
        self, prerequisite: str | None, edition: str | None
    ) -> tuple[ReferenceTarget, ...]:
        """Resolve only prerequisite clauses explicitly labeled ``Feat:``."""
        if not prerequisite or not edition:
            return ()
        names = re.findall(
            r"(?:^|[;,])\s*feat\s*:\s*([^;,]+)", prerequisite, flags=re.IGNORECASE
        )
        targets: dict[str, ReferenceTarget] = {}
        for name in names:
            resolution = self.resolve_reference(
                content_type="feat", name=name.strip(), edition=edition
            )
            targets.update((target.identity, target) for target in resolution.candidates)
        return tuple(sorted(
            targets.values(), key=lambda target: (target.name.casefold(), target.identity)
        ))
