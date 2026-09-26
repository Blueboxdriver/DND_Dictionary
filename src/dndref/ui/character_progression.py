"""Keyboard-first level-up review and choice flow."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..character_progression import CharacterProgressionService
from ..models.character import PublishedReference
from ..models.progression import AdvancementOption, LevelUpPreview


class ProgressionRow(ListItem):
    def __init__(self, label: str, kind: str, payload: object = None) -> None:
        self.kind = kind
        self.payload = payload
        super().__init__(Label(label))


class ProgressionHelpScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]
    DEFAULT_CSS = """
    ProgressionHelpScreen { align: center middle; background: $background 80%; }
    #progress-help { width: 78; max-width: 92%; height: auto; max-height: 88%;
        padding: 1 2; border: round $accent; background: $surface; }
    #progress-help-title { height: 1; text-style: bold; color: $accent; }
    #progress-help-copy { height: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="progress-help"):
            yield Static("Level Up Help", id="progress-help-title")
            yield Static(
                "Choose a class to advance or add. New classes must meet every published "
                "multiclass prerequisite; unresolved requirements stay blocked. Complete "
                "each required feature, feat, spell, or proficiency choice. For HP, use the "
                "fixed average or enter your own roll. Review the preview and choose Confirm "
                "and commit level to save atomically. Esc cancels and leaves the saved "
                "character unchanged.",
                id="progress-help-copy",
                markup=False,
            )
            yield Static("Esc Close", id="progress-help-footer")


class CharacterProgressionScreen(ModalScreen[tuple[object, ...] | None]):
    """A small generic wizard over structured class, feat, proficiency, and spell choices."""

    PAGE_SIZE = 20
    DEFAULT_CSS = """
    CharacterProgressionScreen { align: center middle; background: $background 84%; }
    #progress-card { width: 94%; max-width: 112; height: 92%; padding: 1 2;
        border: round $accent; background: $surface; }
    #progress-title { height: 1; text-style: bold; color: $accent; }
    #progress-status { height: auto; max-height: 3; color: $text; }
    #progress-search { height: 3; display: none; }
    #progress-list { height: 1fr; border: none; background: transparent; margin-top: 1; }
    #progress-list > ListItem { height: auto; min-height: 1; padding: 0 1; }
    #progress-list > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    #progress-footer { height: auto; max-height: 3; color: $text-muted; }
    CharacterProgressionScreen.compact #progress-card { width: 100%; height: 100%;
        padding: 0 1; border: none; }
    """

    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+i", "inspect", "Inspect")]

    def __init__(
        self,
        service: CharacterProgressionService,
        character_id: str,
        *,
        context: tuple[object, ...] | None = None,
    ) -> None:
        self.service = service
        self.character_id = character_id
        self.draft_id = str(context[0]) if context and context[0] else None
        self.step_key = str(context[1]) if context and len(context) > 1 else "class"
        self.search_query = str(context[2]) if context and len(context) > 2 else ""
        self.selected_index = int(context[3]) if context and len(context) > 3 else 0
        self.page_offset = int(context[4]) if context and len(context) > 4 else 0
        self._rows: list[ProgressionRow] = []
        self._steps: list[tuple[str, str | None]] = []
        self._preview: LevelUpPreview | None = None
        self._hp_input = ""
        self._page_count = 1
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="progress-card"):
            yield Static("Level Up", id="progress-title", markup=False)
            yield Static("", id="progress-status", markup=False)
            yield Input(self.search_query, placeholder="Search choices", id="progress-search")
            yield ListView(id="progress-list")
            yield Static(
                "↑↓ Move   Enter Select   / Search   Ctrl+I Inspect\n"
                "Ctrl+PgUp/PgDn Page   ← Previous Step   Esc Cancel   ? Help",
                id="progress-footer",
                markup=False,
            )

    async def on_mount(self) -> None:
        self.set_class(self.size.width < 80 or self.size.height < 24, "compact")
        if self.draft_id:
            self._preview = self.service.get_preview(self.draft_id)
            self._sync_steps()
        await self._render_step()
        self.query_one("#progress-list", ListView).focus()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 80 or event.size.height < 24, "compact")

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "progress-search":
            self.search_query = event.value
            self.page_offset = 0
            await self._render_step()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "progress-search":
            self.search_query = event.value
            await self._render_step()
            self.query_one("#progress-list", ListView).focus()
        elif self.step_key == "hp" and self._hp_input == "rolled":
            try:
                amount = int(event.value)
                self._preview = self.service.set_hp_choice(self._require_draft(), "rolled", amount)
                self._hp_input = ""
                self._next_step()
            except ValueError as exc:
                self.query_one("#progress-status", Static).update(str(exc))

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "progress-list":
            await self._activate()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape" and self.query_one("#progress-search", Input).display:
            event.stop()
            search = self.query_one("#progress-search", Input)
            search.value = ""
            search.display = False
            self.query_one("#progress-list", ListView).focus()
            return
        if isinstance(self.focused, Input):
            return
        if event.key == "ctrl+i":
            event.stop()
            self.action_inspect()
            return
        if event.key in {"alt+left", "alt+right"}:
            event.stop()
            self.dismiss(("history", event.key, self.context()))
            return
        if event.character == "/" and not isinstance(self.focused, Input):
            event.stop()
            search = self.query_one("#progress-search", Input)
            search.display = True
            search.focus()
            return
        if event.character == "?":
            event.stop()
            self.app.push_screen(ProgressionHelpScreen())
            return
        if event.key == "left" and not isinstance(self.focused, Input):
            event.stop()
            self._previous_step()
        elif event.key == "ctrl+pageup" and self.page_offset > 0:
            event.stop()
            self.page_offset = max(0, self.page_offset - self.PAGE_SIZE)
            self.run_worker(self._render_step())
        elif (
            event.key == "ctrl+pagedown"
            and self.page_offset + self.PAGE_SIZE < self._page_count * self.PAGE_SIZE
        ):
            event.stop()
            self.page_offset += self.PAGE_SIZE
            self.run_worker(self._render_step())

    def action_cancel(self) -> None:
        if self.draft_id:
            self.service.cancel(self.draft_id)
        self.dismiss(("cancel", self.character_id))

    def action_inspect(self) -> None:
        view = self.query_one("#progress-list", ListView)
        if view.index is None or not 0 <= view.index < len(self._rows):
            return
        row = self._rows[view.index]
        reference = row.payload
        if isinstance(reference, tuple) and len(reference) > 1:
            reference = reference[1]
        reference = getattr(reference, "reference", reference)
        if isinstance(row.payload, AdvancementOption):
            reference = row.payload.class_reference
        if getattr(reference, "identity", None) and getattr(reference, "kind", None):
            self.dismiss(("reference", reference.kind, reference.identity, self.context()))

    def context(self) -> tuple[object, ...]:
        view = self.query_one("#progress-list", ListView) if self.is_mounted else None
        index = view.index if view and view.index is not None else self.selected_index
        return (self.draft_id, self.step_key, self.search_query, index, self.page_offset)

    def _require_draft(self) -> str:
        if self.draft_id is None:
            raise ValueError("Choose a class before making level-up decisions.")
        return self.draft_id

    def _sync_steps(self) -> None:
        assert self._preview is not None
        steps: list[tuple[str, str | None]] = []
        steps.extend(("choice", row.key) for row in self._preview.pending_choices)
        steps.extend(("spell", row.key) for row in self._preview.spell_choices)
        steps.extend((("hp", None), ("review", None)))
        self._steps = steps
        if self.step_key not in {
            "class",
            *(f"choice:{key}" for kind, key in steps if kind == "choice"),
            *(f"spell:{key}" for kind, key in steps if kind == "spell"),
            "hp",
            "review",
        }:
            self.step_key = steps[0][0] + (f":{steps[0][1]}" if steps[0][1] else "")

    def _current_step_index(self) -> int:
        if self.step_key == "class":
            return -1
        return next(
            (
                index
                for index, (kind, key) in enumerate(self._steps)
                if self.step_key == kind + (f":{key}" if key else "")
            ),
            0,
        )

    def _next_step(self) -> None:
        self._sync_steps()
        index = self._current_step_index()
        if index + 1 >= len(self._steps):
            self.step_key = "review"
        else:
            kind, key = self._steps[index + 1]
            self.step_key = kind + (f":{key}" if key else "")
        self.search_query = ""
        self.page_offset = 0
        self.selected_index = 0
        self.run_worker(self._render_step())

    def _previous_step(self) -> None:
        if self.step_key == "class":
            return
        index = self._current_step_index()
        if index <= 0:
            self.step_key = "class"
        else:
            kind, key = self._steps[index - 1]
            self.step_key = kind + (f":{key}" if key else "")
        self.search_query = ""
        self.page_offset = 0
        self.selected_index = 0
        self.run_worker(self._render_step())

    async def _activate(self) -> None:
        if not self._rows:
            return
        view = self.query_one("#progress-list", ListView)
        if view.index is None or not 0 <= view.index < len(self._rows):
            return
        row = self._rows[view.index]
        try:
            if row.kind == "class":
                option = row.payload
                if option.status != "satisfied":
                    self.query_one("#progress-status", Static).update(option.reason)
                    return
                self._preview = self.service.start_level_preview(
                    self.character_id, option.class_reference.identity
                )
                self.draft_id = self._preview.draft_id
                self._sync_steps()
                self._next_step()
            elif row.kind == "choice":
                choice_key, option = row.payload
                self._preview = self.service.apply_pending_choice(
                    self._require_draft(), choice_key, option.selection_key
                )
                self._sync_steps()
                choice = next(
                    item for item in self._preview.pending_choices if item.key == choice_key
                )
                if len(choice.selected) >= choice.count:
                    self._next_step()
                else:
                    await self._render_step()
            elif row.kind == "spell":
                choice_key, spell = row.payload
                self._preview = self.service.select_spell(self._require_draft(), choice_key, spell)
                self._sync_steps()
                pending = next(
                    item for item in self._preview.spell_choices if item.key == choice_key
                )
                if len(pending.selected) >= pending.count:
                    self._next_step()
                else:
                    await self._render_step()
            elif row.kind == "hp-fixed":
                self._hp_input = ""
                self._preview = self.service.set_hp_choice(self._require_draft(), "fixed_average")
                self._next_step()
            elif row.kind == "hp-roll":
                self._hp_input = "rolled"
                search = self.query_one("#progress-search", Input)
                search.placeholder = f"Enter a manual roll from 1 to {self._preview.hp_die}"
                search.value = ""
                search.display = True
                search.focus()
                self.query_one("#progress-status", Static).update(
                    "No die is rolled automatically. Enter your actual d"
                    + str(self._preview.hp_die)
                    + " result."
                )
            elif row.kind == "commit":
                self.service.commit_level(self._require_draft())
                self.dismiss(("complete", self.character_id))
        except (ValueError, LookupError) as exc:
            self.query_one("#progress-status", Static).update(str(exc))

    async def _render_step(self) -> None:
        view = self.query_one("#progress-list", ListView)
        search = self.query_one("#progress-search", Input)
        search.display = self.step_key.startswith(("choice:", "spell:")) or self.step_key == "class"
        if search.value != self.search_query and self._hp_input != "rolled":
            search.value = self.search_query
        query = self.search_query.strip()
        self._rows = []
        page_total = 1
        if self.step_key == "class":
            options = [
                row
                for row in self.service.get_advancement_options(self.character_id)
                if not query or query.casefold() in row.class_reference.name.casefold()
            ]
            page_total = max(1, (len(options) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
            page = options[self.page_offset : self.page_offset + self.PAGE_SIZE]
            self._rows = [
                ProgressionRow(
                    f"{'Advance' if not item.is_new_multiclass else 'Add'} "
                    f"{item.class_reference.name} {item.next_class_level} · "
                    f"{item.status}: {item.reason}",
                    "class",
                    item,
                )
                for item in page
            ]
            self.query_one("#progress-title", Static).update("Level Up · Choose class to advance")
            self.query_one("#progress-status", Static).update(
                "Level 20 is the maximum. New class tracks must meet the published "
                "multiclass requirements."
            )
        elif self.draft_id is None:
            return
        else:
            self._preview = self.service.get_preview(self.draft_id)
            self._sync_steps()
            parts = self.step_key.split(":", 1)
            kind = parts[0]
            key = parts[1] if len(parts) > 1 else None
            step_number = self._current_step_index() + 2
            self.query_one("#progress-title", Static).update(
                f"Level Up — {self._preview.selected_class.name} {self._preview.next_class_level}"
            )
            self.query_one("#progress-status", Static).update(
                f"{step_number} / {len(self._steps) + 1} · "
                f"Character Level {self._preview.current_total_level} → "
                f"{self._preview.next_total_level}"
            )
            if kind == "choice" and key is not None:
                choice = next(item for item in self._preview.pending_choices if item.key == key)
                options = self.service.get_choice_options(
                    self.draft_id,
                    key,
                    query=query,
                    offset=self.page_offset,
                    limit=self.PAGE_SIZE + 1,
                )
                self._rows = [
                    ProgressionRow(
                        f"{item.label} · {item.status} · {item.reason}"
                        + (f" · {item.summary}" if item.summary else ""),
                        "choice",
                        (key, item),
                    )
                    for item in options[: self.PAGE_SIZE]
                ]
                page_total = self.page_offset // self.PAGE_SIZE + 1 + int(
                    len(options) > self.PAGE_SIZE
                )
                self.query_one("#progress-status", Static).update(
                    f"{step_number} / {len(self._steps) + 1} · {choice.owner_name} · "
                    f"{len(choice.selected)} / {choice.count} selected · {choice.source_rule}"
                )
            elif kind == "spell" and key is not None:
                group = next(item for item in self._preview.spell_choices if item.key == key)
                options = self.service.get_spell_options(
                    self.draft_id,
                    key,
                    query=query,
                    offset=self.page_offset,
                    limit=self.PAGE_SIZE + 1,
                )
                self._rows = [
                    ProgressionRow(f"{spell.name} · {spell.identity}", "spell", (key, spell))
                    for spell in options[: self.PAGE_SIZE]
                ]
                page_total = self.page_offset // self.PAGE_SIZE + 1 + int(
                    len(options) > self.PAGE_SIZE
                )
                self.query_one("#progress-status", Static).update(
                    f"{step_number} / {len(self._steps) + 1} · {group.label} · "
                    f"{len(group.selected)} / {group.count} selected · Max spell level "
                    f"{group.max_spell_level}"
                )
            elif kind == "hp":
                self._rows = [
                    ProgressionRow(
                        f"Use fixed value: {self._preview.fixed_hp_gain} + CON modifier",
                        "hp-fixed",
                    ),
                    ProgressionRow(f"Enter manual roll (d{self._preview.hp_die})", "hp-roll"),
                ]
                self.query_one("#progress-status", Static).update(
                    f"HP for {self._preview.selected_class.name} {self._preview.next_class_level}. "
                    "Constitution is added by derived statistics."
                )
            elif kind == "review":
                self._rows = self._review_rows(self._preview)
                self.query_one("#progress-status", Static).update(
                    "Review every decision. Enter commits the complete level atomically; "
                    "Esc cancels it."
                )
            self.query_one("#progress-title", Static).update(
                f"Level Up — {self._preview.selected_class.name} {self._preview.next_class_level}"
            )
        self._page_count = page_total
        await view.clear()
        await view.extend(self._rows)
        view.index = min(self.selected_index, len(self._rows) - 1) if self._rows else None

    def _review_rows(self, preview: LevelUpPreview) -> list[ProgressionRow]:
        rows = [
            ProgressionRow(
                f"Character Level {preview.current_total_level} → {preview.next_total_level}",
                "summary",
            ),
            ProgressionRow(
                f"{'Add' if preview.is_new_multiclass else 'Advance'} "
                f"{preview.selected_class.name} "
                f"{preview.next_class_level}",
                "reference",
                preview.selected_class,
            ),
            ProgressionRow(
                f"HP: {preview.hp_choice_kind} · {preview.hp_amount} + CON modifier",
                "summary",
            ),
        ]
        if preview.subclass_choice:
            rows.append(
                ProgressionRow(
                    f"Subclass: {preview.subclass_choice.name}",
                    "reference",
                    preview.subclass_choice,
                )
            )
        for event in preview.automatic_grants:
            rows.append(
                ProgressionRow(
                    f"Progression: {event.title} · {event.source_rule}",
                    "reference",
                    PublishedReference(
                        event.owner_type,
                        event.owner_identity,
                        event.title,
                        "2024",
                    ),
                )
            )
        for choice in preview.pending_choices:
            selected = ", ".join(choice.selected_labels) or "none selected"
            rows.append(
                ProgressionRow(
                    f"{choice.owner_name}: {len(choice.selected)} / {choice.count} · "
                    f"{selected} · {choice.source_rule}",
                    "summary",
                )
            )
        for group in preview.spell_choices:
            rows.extend(
                ProgressionRow(
                    f"New {group.acquisition} spell: {spell.name} · {spell.identity}",
                    "reference",
                    spell,
                )
                for spell in group.selected
            )
        old_spell_keys = {
            (profile.owner.identity, spell.spell.identity, spell.acquisition)
            for profile in preview.derived_before.spellcasting_profiles
            for spell in profile.spells
        }
        selected_spell_ids = {
            spell.identity for group in preview.spell_choices for spell in group.selected
        }
        for profile in preview.derived_after.spellcasting_profiles:
            rows.extend(
                ProgressionRow(
                    f"New {spell.acquisition} spell: {spell.spell.name} · "
                    f"{spell.spell.identity}",
                    "reference",
                    spell.spell,
                )
                for spell in profile.spells
                if (
                    (
                        profile.owner.identity,
                        spell.spell.identity,
                        spell.acquisition,
                    )
                    not in old_spell_keys
                    and spell.spell.identity not in selected_spell_ids
                )
            )
        before = preview.derived_before
        after = preview.derived_after
        old_feature_keys = {
            (feature.reference.identity, feature.source_identity, feature.source_rule)
            for feature in before.features
        }
        for feature in after.features:
            feature_key = (
                feature.reference.identity,
                feature.source_identity,
                feature.source_rule,
            )
            if feature_key in old_feature_keys:
                continue
            owner_kind = "subclass" if ":subclass:" in feature.source_identity else "class"
            rows.append(
                ProgressionRow(
                    f"New feature: {feature.display_name or feature.reference.name} · "
                    f"{feature.source_label} · {feature.source_rule}",
                    "reference",
                    PublishedReference(
                        owner_kind,
                        feature.source_identity,
                        feature.source_label,
                        "2024",
                    ),
                )
            )
        old_proficiencies = {(item.kind, item.key) for item in before.proficiencies}
        rows.extend(
            ProgressionRow(
                f"New {item.kind}: {item.key} ({item.level})",
                "summary",
            )
            for item in after.proficiencies
            if (item.kind, item.key) not in old_proficiencies
        )
        for change in preview.spell_progression:
            rows.append(
                ProgressionRow(
                    f"{change.owner_name} spell progression · Cantrips "
                    f"{change.previous_cantrips or 0} → {change.cantrips or 0}; Known "
                    f"{change.previous_known or 0} → {change.known or 0}; Prepared capacity "
                    f"{change.previous_prepared or 0} → {change.prepared or 0}; Spell access "
                    f"{self._levels(change.accessible_spell_levels_before)} → "
                    f"{self._levels(change.accessible_spell_levels_after)}",
                    "summary",
                )
            )
        rows.extend(
            (
                ProgressionRow(
                    f"HP {before.hit_points.maximum} → {after.hit_points.maximum}", "summary"
                ),
                ProgressionRow(
                    f"Proficiency Bonus {before.proficiency_bonus.value} → "
                    f"{after.proficiency_bonus.value}",
                    "summary",
                ),
                ProgressionRow(
                    f"Standard Slots: {self._slots(before.spell_slots)} → "
                    f"{self._slots(after.spell_slots)}",
                    "summary",
                ),
            )
        )
        if before.pact_magic or after.pact_magic:
            rows.append(
                ProgressionRow(
                    f"Pact Magic: {self._pact(before.pact_magic)} → {self._pact(after.pact_magic)}",
                    "summary",
                )
            )
        for ability in before.ability_scores:
            current = next(
                (row for row in after.ability_scores if row.ability == ability.ability),
                None,
            )
            if current is not None and current.final != ability.final:
                rows.append(
                    ProgressionRow(
                        f"{ability.ability.upper()} {ability.final} → {current.final}",
                        "summary",
                    )
                )
        for profile in after.spellcasting_profiles:
            old = next(
                (
                    item
                    for item in before.spellcasting_profiles
                    if item.owner.identity == profile.owner.identity
                ),
                None,
            )
            if old and old.spell_save_dc.value != profile.spell_save_dc.value:
                rows.append(
                    ProgressionRow(
                        f"{profile.owner.name} Spell Save DC "
                        f"{old.spell_save_dc.value} → {profile.spell_save_dc.value}",
                        "summary",
                    )
                )
            elif old is None:
                rows.append(
                    ProgressionRow(
                        f"{profile.owner.name} Spell Save DC "
                        f"{profile.spell_save_dc.value}; Spell Attack "
                        f"{profile.spell_attack_bonus.value}",
                        "summary",
                    )
                )
        rows.extend(
            ProgressionRow(f"{issue.severity.upper()}: {issue.message}", "issue")
            for issue in preview.issues
        )
        rows.append(ProgressionRow("Confirm and commit level", "commit"))
        return rows

    @staticmethod
    def _slots(slots) -> str:
        return ", ".join(f"{slot.spell_level}:{slot.count}" for slot in slots) or "none"

    @staticmethod
    def _pact(pact_slots) -> str:
        return (
            ", ".join(f"{slot.slot_count} × level {slot.slot_level}" for slot in pact_slots)
            or "none"
        )

    @staticmethod
    def _levels(levels) -> str:
        return ", ".join(str(level) for level in levels) or "none"
