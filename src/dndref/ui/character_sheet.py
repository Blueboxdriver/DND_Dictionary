"""Terminal-native presentation of persisted character and derived statistics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static

from ..character_creation import CharacterCreationService
from ..characters import CharacterService
from ..commands import Command
from ..crossrefs import CrossReferenceResolver
from ..derived_character import DerivedCharacterService
from ..models.character import Character, EquipmentRecord
from ..models.derived_character import (
    AttackSummary,
    DerivedCharacter,
    DerivedIssue,
    DerivedValue,
    FeatureReference,
    MovementSpeedResult,
    SavingThrowResult,
    SkillResult,
    SpellcastingProfile,
    SpellSelectionResult,
)
from .character_screens import ConfirmationScreen
from .launchers import CommandPaletteScreen
from .screens import TextEntryScreen

SHEET_SECTIONS = (
    "Overview",
    "Skills",
    "Combat",
    "Features",
    "Spells",
    "Equipment",
    "Notes",
)
_SECTION_KEYS = tuple(value.casefold() for value in SHEET_SECTIONS)
_ABILITY_LABELS = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}
_ACQUISITION_LABELS = {
    "cantrip": "Cantrips",
    "known": "Known",
    "prepared": "Prepared",
    "spellbook": "Spellbook",
    "always_prepared": "Always Prepared",
    "granted": "Granted",
    "pact_magic": "Pact Magic",
    "innate": "Innate",
}


@dataclass(frozen=True)
class SheetRow:
    label: str
    reference_kind: str | None = None
    reference_identity: str | None = None
    action: str | None = None
    details: tuple[str, ...] = ()
    equipment_id: int | None = None


class CharacterSheetScreen(ModalScreen[tuple[object, ...] | None]):
    """Lazy, section-based view over one persisted character snapshot."""

    PAGE_SIZE = 24
    DEFAULT_CSS = """
    CharacterSheetScreen { align: center middle; background: $background 84%; }
    #sheet-card { width: 94%; max-width: 112; height: 92%; padding: 1 2;
        border: round $accent; background: $surface; }
    #sheet-name { height: 1; text-style: bold; color: $accent; }
    #sheet-identity, #sheet-summary { height: auto; max-height: 2; color: $text; }
    #sheet-section { height: 1; text-style: bold; margin-top: 1; }
    #sheet-search { height: 3; display: none; }
    #sheet-rows { height: 1fr; border: none; background: transparent; margin-top: 1; }
    #sheet-rows > ListItem { height: auto; min-height: 1; padding: 0 1; }
    #sheet-rows > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    #sheet-footer { height: auto; max-height: 3; color: $text-muted; }
    CharacterSheetScreen.compact #sheet-card { width: 100%; height: 100%; padding: 0 1;
        border: none; }
    CharacterSheetScreen.compact #sheet-section { margin-top: 0; }
    CharacterSheetScreen.compact #sheet-rows { margin-top: 0; }
    """

    BINDINGS = [("escape", "close", "Back")]

    def __init__(
        self,
        characters: CharacterService,
        creation: CharacterCreationService,
        derived_service: DerivedCharacterService,
        references: CrossReferenceResolver,
        character_id: str,
        *,
        context: tuple[object, ...] | None = None,
    ) -> None:
        self.characters = characters
        self.creation = creation
        self.derived_service = derived_service
        self.references = references
        self.character_id = character_id
        self.character: Character = characters.get_character(character_id)
        self.derived: DerivedCharacter = derived_service.derive(self.character)
        self.section = str(context[0]) if context and context[0] in _SECTION_KEYS else "overview"
        self._section_state: dict[str, tuple[int, int, int, str]] = {
            key: (0, 0, 0, "") for key in _SECTION_KEYS
        }
        if context:
            try:
                self._section_state[self.section] = (
                    int(context[1]),
                    int(context[2]),
                    int(context[3]),
                    str(context[4]),
                )
            except (IndexError, TypeError, ValueError):
                pass
        self._all_rows: list[SheetRow] = []
        self._visible_rows: list[SheetRow] = []
        self._feature_targets: dict[str, Any] | None = None
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="sheet-card"):
            yield Static("", id="sheet-name", markup=False)
            yield Static("", id="sheet-identity", markup=False)
            yield Static("", id="sheet-summary", markup=False)
            yield Static("", id="sheet-section", markup=False)
            yield Input(placeholder="Filter this section", id="sheet-search")
            yield ListView(id="sheet-rows")
            yield Static("", id="sheet-footer", markup=False)

    async def on_mount(self) -> None:
        self._update_compact_class()
        self._render_header()
        await self._render_section()
        self.query_one("#sheet-rows", ListView).focus()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 80 or event.size.height < 24, "compact")
        self._render_header()
        self._render_footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "sheet-rows":
            self._activate_selected()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "sheet-search":
            return
        index, scroll, _page, _query = self._section_state[self.section]
        self._section_state[self.section] = (index, scroll, 0, event.value.casefold())
        self.run_worker(self._render_section(remember=False), exclusive=True)

    def on_key(self, event: events.Key) -> None:
        focused = self.focused
        if event.key == "escape":
            if self.query_one("#sheet-search", Input).display:
                event.stop()
                search = self.query_one("#sheet-search", Input)
                search.value = ""
                search.display = False
                self.query_one("#sheet-rows", ListView).focus()
                return
            event.stop()
            self.dismiss(("close", self.context()))
            return
        if event.key == "ctrl+p":
            event.stop()
            self._open_commands()
            return
        if event.key in {"alt+left", "alt+right"}:
            event.stop()
            self.dismiss(("history", event.key, self.context()))
            return
        if isinstance(focused, Input):
            return
        if event.key in {"left", "right"}:
            event.stop()
            delta = -1 if event.key == "left" else 1
            index = (_SECTION_KEYS.index(self.section) + delta) % len(_SECTION_KEYS)
            self._switch_section(_SECTION_KEYS[index])
        elif event.key == "ctrl+pageup" and self._page_count() > 1:
            event.stop()
            self._change_page(-1)
        elif event.key == "ctrl+pagedown" and self._page_count() > 1:
            event.stop()
            self._change_page(1)
        elif event.character == "/" and self.section in {
            "skills",
            "features",
            "spells",
            "equipment",
        }:
            event.stop()
            search = self.query_one("#sheet-search", Input)
            search.display = True
            search.focus()
        elif event.character == "?":
            event.stop()
            self.app.action_show_help()

    def action_close(self) -> None:
        self.dismiss(("close", self.context()))

    def context(self) -> tuple[object, ...]:
        self._remember_section_state()
        index, scroll, page, query = self._section_state[self.section]
        return (self.section, index, scroll, page, query)

    def refresh_character(self, *, derive: bool = True) -> None:
        self._remember_section_state()
        self.character = self.characters.get_character(self.character_id)
        if derive:
            self.derived = self.derived_service.derive(self.character)
        self._render_header()
        self.run_worker(self._render_section(remember=False), exclusive=True)

    def open_section(self, section: str) -> None:
        key = section.casefold()
        if key in _SECTION_KEYS:
            self._switch_section(key)

    def _update_compact_class(self) -> None:
        self.set_class(self.size.width < 80 or self.size.height < 24, "compact")

    def _render_header(self) -> None:
        status = "Draft" if self.character.state == "draft" else "Complete"
        self.query_one("#sheet-name", Static).update(f"{self.character.name} · {status}")
        classes = self.character.class_summary or "Class incomplete"
        level = (
            f"Level {self.character.total_level}"
            if self.character.levels
            else "Level 0 · Incomplete"
        )
        species = self.character.species.name if self.character.species else "Species incomplete"
        background = (
            self.character.background.name if self.character.background else "Background incomplete"
        )
        self.query_one("#sheet-identity", Static).update(
            f"{level} — {classes} · {species} · {background} · {self.character.edition}"
        )
        ac = _number(self.derived.armor_class.value, self.derived.armor_class.state)
        hp = _number(self.derived.hit_points.maximum, self.derived.hit_points.state)
        initiative = _derived_value(self.derived.initiative)
        speed = _speed_summary(self.derived.speed)
        pb = _derived_value(self.derived.proficiency_bonus)
        issue_count = len(self.derived.issues)
        warning = (
            f" · {issue_count} rules issue{'s' if issue_count != 1 else ''}" if issue_count else ""
        )
        self.query_one("#sheet-summary", Static).update(
            f"Max HP {hp}   AC {ac}   Init {initiative}   Speed {speed}   PB {pb}{warning}"
        )

    def _render_footer(self) -> None:
        has_search = self.section in {"skills", "features", "spells", "equipment"}
        if self.size.width < 100:
            footer = "←→ Sections  ↑↓ Move  Enter Open\n"
            footer += "/ Search  " if has_search else ""
            footer += "Ctrl+P Cmds  ? Help  Esc Back"
        else:
            footer = "←→ Sections   ↑↓ Move   Enter Open   Ctrl+P Commands   ? Help   Esc Back"
            if has_search:
                footer = (
                    "←→ Sections   ↑↓ Move   Enter Open   / Search   "
                    "Ctrl+P Commands   ? Help   Esc Back"
                )
        if self._page_count() > 1:
            footer += "\nCtrl+PgUp/PgDn Page" if self.size.width < 100 else "   Ctrl+PgUp/PgDn Page"
        self.query_one("#sheet-footer", Static).update(footer)

    async def _render_section(self, *, remember: bool = True) -> None:
        if remember:
            self._remember_section_state()
        key = self.section
        self._all_rows = self._rows_for_section(key)
        index, scroll, page, query = self._section_state[key]
        if query:
            self._all_rows = [row for row in self._all_rows if query in row.label.casefold()]
        count = max(1, (len(self._all_rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = min(max(page, 0), count - 1)
        self._section_state[key] = (index, scroll, page, query)
        start = page * self.PAGE_SIZE
        self._visible_rows = self._all_rows[start : start + self.PAGE_SIZE]
        page_text = f" · Page {page + 1}/{count}" if count > 1 else ""
        section_title = SHEET_SECTIONS[_SECTION_KEYS.index(key)]
        self.query_one("#sheet-section", Static).update(section_title + page_text)
        view = self.query_one("#sheet-rows", ListView)
        await view.clear()
        await view.extend(ListItem(Static(row.label, markup=False)) for row in self._visible_rows)
        local_index = min(max(index - start, 0), len(self._visible_rows) - 1)
        view.index = local_index if self._visible_rows else None
        view.scroll_y = scroll
        self.call_after_refresh(lambda saved=scroll: self._restore_scroll(saved))
        search = self.query_one("#sheet-search", Input)
        search.value = query
        search.display = bool(query)
        self._render_footer()

    def _restore_scroll(self, scroll: int) -> None:
        if self.is_mounted:
            self.query_one("#sheet-rows", ListView).scroll_y = scroll

    def _remember_section_state(self) -> None:
        if not self.is_mounted:
            return
        view = self.query_one("#sheet-rows", ListView)
        index, _old_scroll, page, query = self._section_state[self.section]
        page_start = page * self.PAGE_SIZE
        local_index = view.index or 0
        self._section_state[self.section] = (
            page_start + local_index,
            int(view.scroll_y),
            page,
            query,
        )

    def _switch_section(self, section: str) -> None:
        self._remember_section_state()
        self.section = section
        self.run_worker(self._render_section(remember=False), exclusive=True)

    def _page_count(self) -> int:
        rows = self._all_rows
        query = self._section_state[self.section][3]
        if query:
            rows = [row for row in rows if query in row.label.casefold()]
        return max(1, (len(rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _change_page(self, delta: int) -> None:
        index, _scroll, page, query = self._section_state[self.section]
        page = min(max(page + delta, 0), self._page_count() - 1)
        self._section_state[self.section] = (index, 0, page, query)
        self.run_worker(self._render_section(remember=False), exclusive=True)

    def _rows_for_section(self, section: str) -> list[SheetRow]:
        return {
            "overview": self._overview_rows,
            "skills": self._skills_rows,
            "combat": self._combat_rows,
            "features": self._feature_rows,
            "spells": self._spell_rows,
            "equipment": self._equipment_rows,
            "notes": self._note_rows,
        }[section]()

    def _overview_rows(self) -> list[SheetRow]:
        rows = [
            SheetRow(
                "Status: DRAFT · incomplete"
                if self.character.state == "draft"
                else "Status: COMPLETE"
            ),
            SheetRow("Character identity"),
            SheetRow(f"Name: {self.character.name}"),
            SheetRow(f"Edition: {self.character.edition}"),
            _reference_row("Species", self.character.species),
            _reference_row("Background", self.character.background),
            SheetRow(f"Class summary: {self.character.class_summary or 'Incomplete'}"),
        ]
        rows.insert(
            1,
            SheetRow(
                "Resume Character Creation"
                if self.character.state == "draft"
                else "Edit Character · reopen the level-1 builder",
                action="resume" if self.character.state == "draft" else "edit",
            ),
        )
        if self.character.state == "complete":
            if self.character.total_level < 20:
                rows.insert(2, SheetRow("Level Up", action="level_up"))
            else:
                rows.insert(2, SheetRow("Level 20 · Maximum character level"))
            if self.character.total_level > 1:
                rows.insert(3, SheetRow("Undo Last Level", action="undo_level"))
        for level in self.character.levels:
            rows.append(
                _reference_row(
                    f"{level.class_reference.name} · Class level {level.class_level}",
                    level.class_reference,
                )
            )
        for subclass in self.character.subclasses:
            rows.append(
                _reference_row(
                    f"{subclass.subclass_reference.name} · selected at class level "
                    f"{subclass.selected_class_level}",
                    subclass.subclass_reference,
                )
            )
        rows.append(SheetRow("Ability scores · score · modifier"))
        for score in self.derived.ability_scores:
            score_text = _number(score.final, score.state)
            modifier = _number(score.modifier, score.state, signed=True)
            details = tuple(
                f"{component.label}: {component.value:+d}" for component in score.adjustments
            )
            rows.append(
                SheetRow(
                    f"{score.ability.upper():3}  {score_text:>7}  {modifier:>7}",
                    action="provenance" if details else None,
                    details=details,
                )
            )
        rows.extend(
            (
                _armor_row(self.derived),
                _hit_points_row(self.derived),
                _derived_row("Initiative", self.derived.initiative, signed=True),
                _speed_row(self.derived.speed),
                _derived_row("Proficiency Bonus", self.derived.proficiency_bonus, signed=True),
                _derived_row("Passive Perception", self.derived.passive_perception),
            )
        )
        if self.derived.issues:
            rows.append(
                SheetRow(
                    f"⚠ {len(self.derived.issues)} derived issue(s) · Open issue details",
                    action="issues",
                )
            )
        return rows

    def _skills_rows(self) -> list[SheetRow]:
        rows = [
            SheetRow(f"Proficiency Bonus: {_derived_value(self.derived.proficiency_bonus)}"),
            SheetRow("Saving Throws"),
        ]
        rows.extend(_saving_throw_row(row) for row in self.derived.saving_throws)
        rows.append(SheetRow("Skills · name · ability · modifier · proficiency"))
        rows.extend(_skill_row(row) for row in self.derived.skills)
        rows.append(_derived_row("Passive Perception", self.derived.passive_perception))
        return rows

    def _combat_rows(self) -> list[SheetRow]:
        rows = [
            _armor_row(self.derived),
            _hit_points_row(self.derived),
            _derived_row("Initiative", self.derived.initiative, signed=True),
            _speed_row(self.derived.speed),
            SheetRow("Hit Dice"),
        ]
        rows.extend(
            SheetRow(
                f"{pool.count}d{pool.die_size} · "
                + ", ".join(reference.name for reference in pool.classes)
            )
            for pool in self.derived.hit_dice
        )
        if not self.derived.hit_dice:
            rows.append(SheetRow("Hit Dice: Incomplete"))
        rows.append(SheetRow("Equipped armor and shield"))
        equipped = [item for item in self.character.equipment if item.equipped]
        if equipped:
            rows.extend(_equipment_row(item) for item in equipped)
        else:
            rows.append(SheetRow("No equipment is marked equipped."))
        rows.append(SheetRow("Weapon attacks"))
        rows.extend(_attack_row(attack) for attack in self.derived.attacks)
        if not self.derived.attacks:
            rows.append(SheetRow("No weapon attack summaries are available."))
        combat_sources = {attack.weapon.identity for attack in self.derived.attacks} | {
            item.item_reference.identity for item in equipped if item.item_reference is not None
        }
        combat_issues = [
            issue
            for issue in self.derived.issues
            if _is_combat_issue(issue) or issue.source_identity in combat_sources
        ]
        if combat_issues:
            rows.append(SheetRow("Combat issues"))
            rows.extend(_issue_row(issue) for issue in combat_issues)
        return rows

    def _feature_rows(self) -> list[SheetRow]:
        features = self.derived.features
        if self._feature_targets is None:
            self._feature_targets = self.references.get_many_by_id(
                [
                    identity
                    for feature in features
                    for identity in (feature.reference.identity, feature.source_identity)
                ]
            )
        groups: dict[str, list[FeatureReference]] = {
            key: [] for key in ("Class", "Subclass", "Species", "Background", "Feats", "Other")
        }
        classes = {row.class_reference.identity for row in self.character.levels}
        subclasses = {row.subclass_reference.identity for row in self.character.subclasses}
        for feature in features:
            source = feature.source_identity
            if source in classes:
                group = "Class"
            elif source in subclasses:
                group = "Subclass"
            elif self.character.species and source == self.character.species.identity:
                group = "Species"
            elif self.character.background and source == self.character.background.identity:
                group = "Background"
            elif feature.reference.kind == "feat" or any(
                feat.feat_reference.identity == feature.reference.identity
                for feat in self.character.feats
            ):
                group = "Feats"
            else:
                group = "Other"
            groups[group].append(feature)
        rows: list[SheetRow] = []
        for group, values in groups.items():
            if not values:
                continue
            rows.append(SheetRow(group))
            for feature in sorted(
                values, key=lambda item: (item.display_name or item.reference.name).casefold()
            ):
                label = feature.display_name or feature.reference.name
                metadata = [feature.source_label]
                if feature.class_level is not None:
                    metadata.append(f"Class level {feature.class_level}")
                if feature.source_rule:
                    metadata.append(feature.source_rule)
                target = self._feature_targets.get(feature.reference.identity)
                if target is None:
                    target = self._feature_targets.get(feature.source_identity)
                identity = target.identity if target is not None else None
                if identity is None and feature.reference.kind in {"species", "background"}:
                    identity = feature.reference.identity
                rows.append(
                    SheetRow(
                        f"{label} · {' · '.join(metadata)}",
                        target.category.storage_kind
                        if target is not None
                        else feature.reference.kind,
                        identity,
                        "reference" if identity is not None else None,
                        (f"Exact feature identity: {feature.reference.identity}",),
                    )
                )
        if not rows:
            rows.append(SheetRow("No effective features are available."))
        return rows

    def _spell_rows(self) -> list[SheetRow]:
        rows: list[SheetRow] = []
        if self.derived.spell_slots_state == "complete":
            rows.append(SheetRow("Standard Spell Slots · shared multiclass pool"))
            if self.derived.spell_slots:
                rows.extend(
                    SheetRow(f"{_ordinal(slot.spell_level)}: {slot.count}")
                    for slot in self.derived.spell_slots
                )
            else:
                rows.append(SheetRow("No standard spell slots"))
        else:
            state = "Partial" if self.derived.spell_slots_state == "partial" else "?"
            rows.append(SheetRow(f"Standard Spell Slots: {state}"))
            rows.extend(
                SheetRow(f"{_ordinal(slot.spell_level)}: {slot.count}")
                for slot in self.derived.spell_slots
            )
        for pact in self.derived.pact_magic:
            source_profile = next(
                (
                    profile
                    for profile in self.derived.spellcasting_profiles
                    if profile.owner.identity == pact.source
                ),
                None,
            )
            source_reference = next(
                (
                    row.class_reference
                    for row in self.character.levels
                    if row.class_reference.identity == pact.source
                ),
                None,
            )
            source = (
                source_profile.class_reference.name
                if source_profile and source_profile.class_reference
                else source_profile.owner.name
                if source_profile
                else source_reference.name
                if source_reference
                else pact.source
            )
            rows.extend(
                (
                    SheetRow(f"Pact Magic · {source} level {pact.class_level}"),
                    SheetRow(f"{pact.slot_count} slots · {_ordinal(pact.slot_level)} level"),
                )
            )
        for profile in self.derived.spellcasting_profiles:
            rows.extend(self._profile_rows(profile))
        if not self.derived.spellcasting_profiles and not self.derived.pact_magic:
            rows.append(SheetRow("No spellcasting profiles."))
        return rows

    def _profile_rows(self, profile: SpellcastingProfile) -> list[SheetRow]:
        owner = profile.owner.name
        if profile.class_reference is not None:
            title = f"{owner} · {profile.class_reference.name} level {profile.class_level or '—'}"
        else:
            title = f"{profile.owner_type.title()} · {owner}"
        rows = [
            SheetRow(title, profile.owner.kind, profile.owner.identity, "reference"),
            SheetRow(f"Spellcasting ability: {(profile.spellcasting_ability or '?').upper()}"),
            _derived_row("Spell Save DC", profile.spell_save_dc),
            _derived_row("Spell Attack Bonus", profile.spell_attack_bonus, signed=True),
        ]
        accessible = tuple(_ordinal(level) for level in profile.accessible_spell_levels)
        rows.append(
            SheetRow(
                "Accessible spell levels: "
                + (", ".join(accessible) if accessible else "Cantrips only or unresolved")
            )
        )
        grouped: dict[str, list[SpellSelectionResult]] = {}
        for spell in profile.spells:
            group = _ACQUISITION_LABELS.get(
                spell.acquisition, spell.acquisition.replace("_", " ").title()
            )
            grouped.setdefault(group, []).append(spell)
        for group, spells in grouped.items():
            rows.append(SheetRow(group))
            for spell in sorted(spells, key=lambda item: item.spell.name.casefold()):
                if spell.level == 0:
                    metadata = "Cantrip"
                elif spell.level is not None:
                    metadata = f"{_ordinal(spell.level)}-level"
                else:
                    metadata = "Level unresolved"
                if spell.school:
                    metadata += f" {spell.school}"
                state = (
                    " · Unresolved"
                    if spell.valid is None
                    else " · Invalid"
                    if spell.valid is False
                    else ""
                )
                rows.append(
                    SheetRow(
                        f"{spell.spell.name} · {metadata}{state}",
                        "spell",
                        spell.spell.identity,
                        "reference",
                        (spell.issue,) if spell.issue else (),
                    )
                )
        return rows

    def _equipment_rows(self) -> list[SheetRow]:
        rows: list[SheetRow] = []
        groups = (
            ("Equipped", [item for item in self.character.equipment if item.equipped]),
            (
                "Carried",
                [
                    item
                    for item in self.character.equipment
                    if not item.equipped and item.carried_state == "carried"
                ],
            ),
            (
                "Stowed",
                [
                    item
                    for item in self.character.equipment
                    if not item.equipped and item.carried_state == "stowed"
                ],
            ),
        )
        for label, items in groups:
            if not items:
                continue
            rows.append(SheetRow(label))
            rows.extend(_equipment_row(item) for item in items)
        if not rows:
            rows.append(SheetRow("No equipment is saved."))
        return rows

    def _note_rows(self) -> list[SheetRow]:
        note = self.character.notes.strip()
        return [
            SheetRow("Edit Character Notes", action="edit_note"),
            SheetRow("No character note is saved." if not note else f"Character Notes\n{note}"),
        ]

    def _activate_selected(self) -> None:
        view = self.query_one("#sheet-rows", ListView)
        if view.index is None or not 0 <= view.index < len(self._visible_rows):
            return
        row = self._visible_rows[view.index]
        if row.action == "reference" and row.reference_identity:
            self.dismiss(("reference", row.reference_kind, row.reference_identity, self.context()))
        elif row.action == "provenance":
            self.app.push_screen(SheetInfoScreen(row.label, row.details))
        elif row.action == "issues":
            self.app.push_screen(DerivedIssuesScreen(self.derived.issues))
        elif row.action == "resume":
            self.dismiss(("resume", self.character_id, self.context()))
        elif row.action == "edit":
            self.dismiss(("edit", self.character_id, self.context()))
        elif row.action == "level_up":
            self.dismiss(("level_up", self.character_id, self.context()))
        elif row.action == "undo_level":
            self._confirm_undo_level()
        elif row.action == "edit_note":
            self.app.push_screen(
                TextEntryScreen("Character Notes", self.character.notes, multiline=True),
                self._note_editor_result,
            )

    def _note_editor_result(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        self.characters.update_note(self.character_id, result[1])
        self.refresh_character(derive=False)

    def _confirm_undo_level(self) -> None:
        self.app.push_screen(
            ConfirmationScreen(
                f"Remove the latest level from {self.character.name}? Its level-owned "
                "choices, HP decision, subclass, feats, and spells will be removed."
            ),
            self._undo_level_confirmed,
        )

    def _undo_level_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self.dismiss(("undo_level", self.character_id, self.context()))

    def _open_commands(self) -> None:
        commands = [
            Command(
                f"sheet-{key}", f"Open {SHEET_SECTIONS[index]}", (SHEET_SECTIONS[index].casefold(),)
            )
            for index, key in enumerate(_SECTION_KEYS)
        ]
        commands.append(
            Command(
                "sheet-resume" if self.character.state == "draft" else "sheet-edit",
                "Resume Character Creation"
                if self.character.state == "draft"
                else "Edit Character",
                ("builder", "creation"),
            )
        )
        if self.character.state == "complete" and self.character.total_level < 20:
            commands.append(Command("sheet-level-up", "Level Up", ("advance",)))
        if self.character.state == "complete" and self.character.total_level > 1:
            commands.append(Command("sheet-undo-level", "Undo Last Level", ("undo",)))
        if self.derived.issues:
            commands.append(
                Command("sheet-issues", "Review Derived Issues", ("issues", "unresolved"))
            )
        if self.section == "notes":
            commands.append(Command("sheet-note", "Edit Character Notes", ("note",)))
        equipment = self._selected_equipment()
        if equipment is not None:
            if equipment.equipped:
                commands.append(Command("sheet-unequip", "Unequip Item", ("unequip",)))
            else:
                commands.append(Command("sheet-equip", "Equip Item", ("equip",)))
        self.app.push_screen(
            CommandPaletteScreen(
                self.app.search_service,
                tuple(commands),
                default_command_ids=tuple(command.command_id for command in commands),
            ),
            self._command_result,
        )

    def _command_result(self, result: tuple[str, object] | None) -> None:
        if result is None:
            return
        kind, value = result
        if kind == "entry":
            target = self.references.get_by_id(str(value))
            if target is not None:
                self.dismiss(
                    ("reference", target.category.storage_kind, target.identity, self.context())
                )
            return
        command = str(value)
        if command.startswith("sheet-") and command.removeprefix("sheet-") in _SECTION_KEYS:
            self.open_section(command.removeprefix("sheet-"))
        elif command in {"sheet-resume", "sheet-edit"}:
            action = "resume" if command == "sheet-resume" else "edit"
            self.dismiss((action, self.character_id, self.context()))
        elif command == "sheet-level-up":
            self.dismiss(("level_up", self.character_id, self.context()))
        elif command == "sheet-undo-level":
            self._confirm_undo_level()
        elif command == "sheet-issues":
            self.app.push_screen(DerivedIssuesScreen(self.derived.issues))
        elif command == "sheet-note":
            self.app.push_screen(
                TextEntryScreen("Character Notes", self.character.notes, multiline=True),
                self._note_editor_result,
            )
        elif command in {"sheet-equip", "sheet-unequip"}:
            selected = self._selected_equipment()
            if selected is not None:
                self.characters.update_equipment(
                    self.character_id, selected.equipment_id, equipped=command == "sheet-equip"
                )
                self.refresh_character()

    def _selected_equipment(self) -> EquipmentRecord | None:
        view = self.query_one("#sheet-rows", ListView)
        if view.index is None or not 0 <= view.index < len(self._visible_rows):
            return None
        row = self._visible_rows[view.index]
        equipment_id = row.equipment_id
        return next(
            (item for item in self.character.equipment if item.equipment_id == equipment_id),
            None,
        )


class SheetInfoScreen(ModalScreen[None]):
    """Small secondary pane for provenance components."""

    DEFAULT_CSS = """
    SheetInfoScreen { align: center middle; background: $background 82%; }
    #sheet-info-card { width: 70; max-width: 94%; height: auto; max-height: 82%;
        padding: 1 2; border: round $accent; background: $surface; }
    #sheet-info-title { height: 1; text-style: bold; color: $accent; }
    #sheet-info-copy { height: auto; max-height: 1fr; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, title: str, details: tuple[str, ...]) -> None:
        self.title = title
        self.details = details
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="sheet-info-card"):
            yield Static(self.title, id="sheet-info-title", markup=False)
            yield Static("\n".join(self.details), id="sheet-info-copy", markup=False)
            yield Static("Esc Close", markup=False)

    def action_close(self) -> None:
        self.dismiss()


class DerivedIssuesScreen(ModalScreen[None]):
    """Read-only view of the derived engine's issues for this character."""

    DEFAULT_CSS = """
    DerivedIssuesScreen { align: center middle; background: $background 82%; }
    #sheet-issues-card { width: 88; max-width: 96%; height: 90%; max-height: 94%;
        padding: 1 2; border: round $accent; background: $surface; }
    #sheet-issues-title { height: 1; text-style: bold; color: $accent; }
    #sheet-issues-list { height: 1fr; border: none; }
    #sheet-issues-list > ListItem { height: auto; min-height: 1; padding: 0 1; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, issues: tuple[DerivedIssue, ...]) -> None:
        self.issues = issues
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="sheet-issues-card"):
            yield Static("Derived character issues", id="sheet-issues-title", markup=False)
            with ListView(id="sheet-issues-list"):
                for issue in self.issues:
                    yield ListItem(Static(_issue_text(issue), markup=False))
            yield Static("↑↓ Move   Esc Close", markup=False)

    def action_close(self) -> None:
        self.dismiss()


def _reference_row(label: str, reference: Any) -> SheetRow:
    if reference is None:
        return SheetRow(f"{label}: Incomplete")
    status = " · stale reference" if reference.missing else ""
    return SheetRow(
        f"{label}: {reference.name}{status}",
        reference.kind,
        reference.identity,
        "reference",
    )


def _derived_value(value: DerivedValue | None, *, signed: bool = False) -> str:
    if value is None:
        return "?"
    return _number(value.value, value.state, signed=signed)


def _number(value: int | None, state: str, *, signed: bool = False) -> str:
    if state == "unresolved":
        return "?"
    if value is None:
        return "Partial" if state == "partial" else "—"
    rendered = f"{value:+d}" if signed else str(value)
    return f"{rendered} (Partial)" if state == "partial" else rendered


def _derived_row(
    label: str,
    value: DerivedValue | int | None,
    state: str | None = None,
    reason: str | None = None,
    *,
    signed: bool = False,
) -> SheetRow:
    if isinstance(value, DerivedValue):
        state = value.state
        reason = value.reason
        display = _derived_value(value, signed=signed)
        components = tuple(f"{item.label}: {item.value:+d}" for item in value.components)
    else:
        display = _number(value, state or "unresolved", signed=signed)
        components = ()
    details = components + ((reason,) if reason else ())
    return SheetRow(
        f"{label}: {display}",
        action="provenance" if details else None,
        details=details,
    )


def _armor_row(derived: DerivedCharacter) -> SheetRow:
    armor = derived.armor_class
    display = _number(armor.value, armor.state)
    details = tuple(f"{component.label}: {component.value:+d}" for component in armor.components)
    if armor.formula:
        details += (f"Formula: {armor.formula}",)
    if armor.reason:
        details += (armor.reason,)
    return SheetRow(
        f"Armor Class: {display}",
        action="provenance" if details else None,
        details=details,
    )


def _hit_points_row(derived: DerivedCharacter) -> SheetRow:
    result = derived.hit_points
    details = tuple(_hit_point_level_detail(level) for level in result.levels)
    if result.reason:
        details += (result.reason,)
    return SheetRow(
        f"Maximum HP: {_number(result.maximum, result.state)}",
        action="provenance" if details else None,
        details=details,
    )


def _hit_point_level_detail(level: Any) -> str:
    hit_die = level.hit_die if level.hit_die is not None else "?"
    die_result = level.die_result if level.die_result is not None else "?"
    constitution = level.constitution_modifier if level.constitution_modifier is not None else "?"
    gained = level.hit_points_gained if level.hit_points_gained is not None else level.state
    return (
        f"Level {level.total_level} · {level.class_reference.name}: "
        f"d{hit_die} result {die_result} · CON modifier {constitution} · gained {gained}"
    )


def _speed_row(values: tuple[MovementSpeedResult, ...]) -> SheetRow:
    details = tuple(
        f"{value.kind.title()} speed · {source.label}"
        for value in values
        for source in value.sources
    )
    return SheetRow(
        f"Speed: {_speed_summary(values)}",
        action="provenance" if details else None,
        details=details,
    )


def _saving_throw_row(value: SavingThrowResult) -> SheetRow:
    modifier = _derived_value(value.result, signed=True)
    proficiency = _proficiency_label(value.proficiency)
    details = tuple(f"{item.label}: {item.value:+d}" for item in value.result.components)
    if value.result.reason:
        details += (value.result.reason,)
    suffix = f" · {proficiency}" if proficiency else ""
    return SheetRow(
        f"{_ABILITY_LABELS.get(value.ability, value.ability.title())}: {modifier}{suffix}",
        action="provenance" if details else None,
        details=details,
    )


def _skill_row(value: SkillResult) -> SheetRow:
    modifier = _derived_value(value.result, signed=True)
    proficiency = _proficiency_label(value.proficiency)
    details = tuple(f"{item.label}: {item.value:+d}" for item in value.result.components)
    if value.result.reason:
        details += (value.result.reason,)
    suffix = f" · {proficiency}" if proficiency else ""
    return SheetRow(
        f"{value.name} ({value.ability.upper()}) {modifier}{suffix}",
        action="provenance" if details else None,
        details=details,
    )


def _proficiency_label(level: str) -> str:
    return {"proficient": "Proficient", "expertise": "Expertise"}.get(level, "")


def _speed_summary(values: tuple[MovementSpeedResult, ...]) -> str:
    if not values:
        return "?"
    return ", ".join(
        f"{value.kind.title()} {_number(value.feet, value.state)} ft." for value in values
    )


def _attack_row(attack: AttackSummary) -> SheetRow:
    if attack.state == "unresolved":
        to_hit = "Attack calculation unavailable"
    else:
        to_hit = f"{_number(attack.attack_bonus, attack.state, signed=True)} to hit"
    parts = [attack.weapon.name, to_hit]
    if attack.damage:
        damage_type = f" {attack.damage_type}" if attack.damage_type else ""
        parts.append(f"{attack.damage}{damage_type}")
    if attack.range_feet:
        parts.append("Range " + "/".join(str(value) for value in attack.range_feet) + " ft.")
    if attack.proficient is not None:
        parts.append("Proficient" if attack.proficient else "Not proficient")
    labels = (*attack.properties, *attack.mastery)
    if labels:
        parts.append(", ".join(labels))
    if attack.reason:
        parts.append(attack.reason)
    return SheetRow(
        "\n".join(parts),
        "item",
        attack.weapon.identity,
        "reference",
        (attack.reason,) if attack.reason else (),
    )


def _equipment_row(item: EquipmentRecord) -> SheetRow:
    name = (
        item.item_reference.name
        if item.item_reference
        else item.unresolved_selection or "Unresolved item"
    )
    flag = "Equipped" if item.equipped else item.carried_state.title()
    generic = " [generic]" if item.item_reference is None else ""
    stale = " [stale reference]" if item.item_reference and item.item_reference.missing else ""
    return SheetRow(
        f"{name} × {item.quantity}{generic}{stale} · {flag} · {item.provenance_label}",
        "item" if item.item_reference else None,
        item.item_reference.identity if item.item_reference else None,
        "reference" if item.item_reference else None,
        equipment_id=item.equipment_id,
    )


def _issue_row(issue: DerivedIssue) -> SheetRow:
    return SheetRow(_issue_text(issue), action="issues")


def _issue_text(issue: DerivedIssue) -> str:
    unresolved = issue.category.startswith("unresolved") or issue.category == "stale_reference"
    status = f"Unresolved ({issue.severity.title()})" if unresolved else issue.severity.title()
    source = f" · {issue.source_identity}" if issue.source_identity else ""
    return f"{status} · {issue.category}: {issue.message}{source}"


def _is_combat_issue(issue: DerivedIssue) -> bool:
    return any(
        token in issue.category
        for token in ("armor", "attack", "weapon", "hit_point", "speed", "initiative")
    )


def _ordinal(level: int) -> str:
    suffix = "th" if 10 <= level % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(level % 10, "th")
    return f"{level}{suffix}"
