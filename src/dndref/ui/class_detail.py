"""Presentation widgets for persisted class details."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label, ListItem, ListView, Markdown, Static

from ..models import display_edition
from ..search import EntryDetail


def _get(record: object, key: str, default: object = None) -> object:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return default


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _ordered_features(features: Iterable[object]) -> list[object]:
    return sorted(
        features,
        key=lambda feature: (
            int(_get(feature, "level", 0) or 0),
            int(_get(feature, "display_order", 0) or 0),
            _text(_get(feature, "title")),
            _text(_get(feature, "feature_key")),
        ),
    )


def _grouped_features(features: Iterable[object]) -> list[tuple[int, list[object]]]:
    grouped: dict[int, list[object]] = {}
    for feature in _ordered_features(features):
        level = int(_get(feature, "level", 0) or 0)
        grouped.setdefault(level, []).append(feature)
    return list(grouped.items())


def _metadata_markdown(detail: EntryDetail) -> str:
    fields = detail.fields
    edition = display_edition(_text(fields.get("edition")))
    origin = " · ".join(
        part for part in ("Class", edition, detail.dataset_title, detail.source_label) if part
    )
    output = [
        f"# {detail.name}",
        f"*{origin}*\n",
    ]
    if detail.description.strip():
        output.append(f"{detail.description}\n")

    metadata = (
        ("Hit Die", f"d{fields.get('hit_die')}" if fields.get("hit_die") else None),
        ("Primary Ability", fields.get("primary_ability")),
        ("Saving Throws", fields.get("saving_throw_proficiencies")),
        ("Skills", fields.get("skill_choices")),
        ("Weapons", fields.get("weapon_proficiencies")),
        ("Armor", fields.get("armor_proficiencies")),
        ("Tools", fields.get("tool_proficiencies")),
        ("Spellcasting Ability", fields.get("spellcasting_ability")),
    )
    lines = [f"**{label}:** {_text(value)}" for label, value in metadata if _text(value)]
    if lines:
        output.append("### Class Metadata\n\n" + "\n".join(lines) + "\n")

    for label, key in (
        ("Starting Equipment", "starting_equipment"),
        ("Multiclassing", "multiclassing"),
    ):
        value = _text(fields.get(key))
        if value:
            output.append(f"### {label}\n\n{value}\n")
    return "\n".join(output)


def _features_markdown(features: Iterable[object]) -> str:
    groups = _grouped_features(features)
    if not groups:
        return ""
    output = ["## Class Features\n"]
    for level, level_features in groups:
        output.append(f"### Level {level}\n")
        for feature in level_features:
            title = _text(_get(feature, "title")) or "Feature"
            description = _text(_get(feature, "description"))
            output.append(f"#### {title}\n\n{description}\n")
    return "\n".join(output)


def _subclasses_markdown(subclasses: Iterable[object]) -> str:
    subclasses = list(subclasses)
    if not subclasses:
        return ""
    output = ["## Subclasses\n"]
    for subclass in sorted(subclasses, key=lambda value: _text(_get(value, "subclass_key"))):
        output.append(f"### {_text(_get(subclass, 'name'))}\n")
        introduction = _text(_get(subclass, "introduction"))
        if introduction:
            output.append(f"{introduction}\n")
        source = _text(_get(subclass, "source_label"))
        if source:
            output.append(f"*Source: {source}*\n")
        for level, level_features in _grouped_features(_get(subclass, "features", ()) or ()):
            output.append(f"#### Level {level}\n")
            for feature in level_features:
                title = _text(_get(feature, "title")) or "Feature"
                description = _text(_get(feature, "description"))
                output.append(f"##### {title}\n\n{description}\n")
    return "\n".join(output)


def _has_proficiency_column(columns: Iterable[object]) -> bool:
    for column in columns:
        key = _text(_get(column, "column_key")).casefold().replace("_", " ").replace("-", " ")
        label = _text(_get(column, "label")).casefold()
        if key in {"pb", "proficiency bonus"} or label in {"pb", "proficiency bonus"}:
            return True
    return False


def progression_table(detail: EntryDetail) -> tuple[list[tuple[str, str]], list[list[str]]]:
    """Build deterministic table columns and rows from persisted class data."""

    fields = detail.fields
    raw_columns = sorted(
        fields.get("progression_columns") or (),
        key=lambda column: (
            int(_get(column, "display_order", 0) or 0),
            _text(_get(column, "column_key")),
        ),
    )
    raw_levels = sorted(
        fields.get("progression_levels") or (),
        key=lambda level: int(_get(level, "level", 0) or 0),
    )
    values: dict[tuple[int, str], str] = {}
    for value in fields.get("progression_values") or ():
        level = int(_get(value, "level", 0) or 0)
        key = _text(_get(value, "column_key"))
        if key:
            values.setdefault((level, key), _text(_get(value, "value")))

    features_by_level: dict[int, list[str]] = {}
    for feature in _ordered_features(fields.get("features") or ()):
        title = _text(_get(feature, "title"))
        if title:
            features_by_level.setdefault(int(_get(feature, "level", 0) or 0), []).append(title)

    columns: list[tuple[str, str]] = [("level", "Lvl")]
    if not _has_proficiency_column(raw_columns):
        columns.append(("proficiency_bonus", "PB"))
    columns.append(("features", "Features"))
    columns.extend(
        (_text(_get(column, "column_key")), _text(_get(column, "label")))
        for column in raw_columns
        if _text(_get(column, "column_key")) and _text(_get(column, "label"))
    )

    rows: list[list[str]] = []
    for level in raw_levels:
        level_number = int(_get(level, "level", 0) or 0)
        row = [str(level_number)]
        has_dynamic_proficiency = _has_proficiency_column(raw_columns)
        if not has_dynamic_proficiency:
            row.append(_text(_get(level, "proficiency_bonus")) or "—")
        row.append(", ".join(features_by_level.get(level_number, ())) or "—")
        row.extend(
            values.get((level_number, key), "—") or "—"
            for key, _label in columns
            if key not in {"level", "features"}
            and (has_dynamic_proficiency or key != "proficiency_bonus")
        )
        rows.append(row)
    return columns, rows


def render_class_detail(detail: EntryDetail) -> str:
    """Return a Markdown representation for tests and non-widget callers."""

    columns, rows = progression_table(detail)
    output = [_metadata_markdown(detail)]
    if rows:
        output.append("## Class Progression\n")
        output.append("| " + " | ".join(label for _key, label in columns) + " |")
        output.append("| " + " | ".join("---" for _key, _label in columns) + " |")
        output.extend("| " + " | ".join(row) + " |" for row in rows)
    output.extend(
        (
            _features_markdown(detail.fields.get("features") or ()),
            _subclasses_markdown(detail.fields.get("subclasses") or ()),
        )
    )
    for section in detail.sections:
        output.append(f"### {section.heading}\n\n{section.body}\n")
    output.append(f"**Source:** {detail.source_label} · {detail.dataset_title}")
    return "\n\n".join(part for part in output if part)


def render_subclass_detail(detail: EntryDetail) -> str:
    """Render the stored subclass progression at its actual feature levels."""

    parent = _text(detail.fields.get("parent_class"))
    edition = display_edition(_text(detail.fields.get("edition"))) or "Unknown edition"
    parts = [
        f"# {detail.name}",
        f"*{parent} Subclass*",
        f"**Edition:** {edition}  \n**Source:** {detail.source_label} · {detail.dataset_title}",
    ]
    if detail.description.strip():
        parts.append(detail.description)
    parts.append("Press `c` to open the matching edition parent class.")
    groups = _grouped_features(detail.fields.get("features") or ())
    if not groups:
        parts.append("## Features\n\nNo subclass features are recorded.")
    for level, features in groups:
        parts.append(f"## Level {level}")
        for feature in features:
            title = _text(_get(feature, "title")) or "Feature"
            description = _text(_get(feature, "description"))
            source = _text(_get(feature, "source_label"))
            parts.append(
                f"### {title}\n\n{description}"
                + (f"\n\n*Source: {source}*" if source else "")
            )
    return "\n\n".join(parts)


class ClassDetailView(Vertical):
    """Structured class detail with a separately focusable progression table."""

    DEFAULT_CSS = """
    ClassDetailView {
        width: 1fr;
        height: auto;
    }

    ClassDetailView .detail-heading {
        color: #d4aa58;
        text-style: bold;
        padding: 1 0 0 0;
    }

    #progression-table {
        width: 1fr;
        height: auto;
        min-height: 4;
        max-height: 12;
        margin: 1 0;
        border: round #6c5530;
        background: #211f1d;
    }

    #class-sections {
        width: 1fr;
        padding: 1 0;
    }

    #subclass-list {
        width: 1fr;
        height: 8;
        max-height: 8;
        border: round #6c5530;
        margin: 0 0 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Markdown("", id="class-meta")
        yield Static("Class Progression · ← / → or h / l to scroll", classes="detail-heading")
        yield DataTable(
            id="progression-table",
            cursor_type="cell",
            show_row_labels=False,
            zebra_stripes=True,
        )
        yield Static("Subclasses · Tab to list, Enter to open", classes="detail-heading")
        yield Label("No compatible subclasses installed.", id="subclass-empty")
        yield ListView(id="subclass-list")
        yield Markdown("", id="class-sections")

    async def update_detail(self, detail: EntryDetail) -> None:
        if not self.is_mounted:
            return
        self.query_one("#class-meta", Markdown).update(_metadata_markdown(detail))
        table = self.query_one("#progression-table", DataTable)
        table.clear(columns=True)
        columns, rows = progression_table(detail)
        for index, (_key, label) in enumerate(columns):
            width = 5 if index == 0 else 4 if label == "PB" else None
            table.add_column(label, width=width, key=_key)
        for row in rows:
            table.add_row(*row, height=None)
        subclass_list = self.query_one("#subclass-list", ListView)
        await subclass_list.clear()
        subclasses = detail.fields.get("compatible_subclasses") or ()
        self.query_one("#subclass-empty", Label).display = not bool(subclasses)
        subclass_list.display = bool(subclasses)
        for subclass in subclasses:
            name = _text(_get(subclass, "name"))
            source = _text(_get(subclass, "source_label"))
            edition = display_edition(_text(_get(subclass, "edition")))
            label = f"{name} · {source}" + (f" · {edition}" if edition else "")
            await subclass_list.mount(ListItem(Label(label)))
        self.subclass_ids = tuple(
            _text(_get(subclass, "stable_id")) for subclass in subclasses
        )
        sections = "\n\n".join(
            part
            for part in (
                _features_markdown(detail.fields.get("features") or ()),
                *(
                    f"### {section.heading}\n\n{section.body}\n"
                    for section in detail.sections
                ),
                f"**Source:** {detail.source_label} · {detail.dataset_title}",
            )
            if part
        )
        self.query_one("#class-sections", Markdown).update(sections)


__all__ = ["ClassDetailView", "progression_table", "render_class_detail", "render_subclass_detail"]
