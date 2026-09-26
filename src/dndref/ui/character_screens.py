"""Characters management and guided level-one creation screens."""

from __future__ import annotations

from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from ..character_creation import (
    ABILITIES,
    POINT_BUY_BUDGET,
    BuilderChoice,
    BuilderChoiceOption,
    BuilderOption,
    CharacterCreationService,
    CreationStep,
    _spell_row_matches_group,
    point_buy_cost,
    validate_ability_scores,
)
from ..characters import CharacterService
from ..models.character import Character, CharacterSummary, PublishedReference
from .screens import TextEntryScreen


class ConfirmationScreen(ModalScreen[bool]):
    """A short, explicit confirmation for character deletion."""

    DEFAULT_CSS = """
    ConfirmationScreen { align: center middle; background: $background 82%; }
    #confirm-card { width: 60; max-width: 94%; height: auto; padding: 1 2;
        border: round $accent; background: $surface; }
    #confirm-copy { height: auto; margin: 1 0; }
    #confirm-actions { height: 3; align-horizontal: right; }
    #confirm-actions Button { margin-left: 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, message: str, *, confirm_label: str = "Delete") -> None:
        self.message = message
        self.confirm_label = confirm_label
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-card"):
            yield Label("Please confirm")
            yield Static(self.message, id="confirm-copy")
            with Horizontal(id="confirm-actions"):
                yield Button("Cancel", id="confirm-cancel")
                yield Button(self.confirm_label, variant="error", id="confirm-ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-ok":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class CharactersScreen(ModalScreen[tuple[str, str] | None]):
    """Summary-only manager for saved character drafts and level-one builds."""

    DEFAULT_CSS = """
    CharactersScreen { align: center middle; background: $background 82%; }
    #characters-card { width: 78; max-width: 96%; height: 94%; max-height: 96%;
        padding: 1 2; border: round $accent; background: $surface; }
    #characters-title { height: 1; text-style: bold; color: $accent; }
    #characters-empty { height: 1fr; content-align: center middle; color: $text-muted; }
    #characters-list { height: 1fr; border: none; background: transparent; margin-top: 1; }
    #characters-list > ListItem { height: 3; padding: 0 1; }
    #characters-list > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    #characters-actions { height: 3; }
    #characters-actions Button { width: 1fr; min-width: 8; margin-right: 1; }
    #characters-help { height: 1; color: $text-muted; }
    CharactersScreen.compact #characters-card { padding: 0 1; }
    CharactersScreen.compact #characters-actions {
        height: 6; layout: grid; grid-size: 3; grid-rows: 2; grid-gutter: 0 0;
    }
    CharactersScreen.compact #characters-actions Button {
        min-width: 0; margin: 0; padding: 0;
    }
    """

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, creation: CharacterCreationService) -> None:
        self.creation = creation
        self.service: CharacterService = creation.characters
        self.summaries = self.service.list_characters()
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="characters-card"):
            yield Label("Characters", id="characters-title")
            if self.summaries:
                with ListView(id="characters-list"):
                    for row in self.summaries:
                        yield ListItem(Label(_summary_label(row)))
                yield Static("", id="characters-empty")
            else:
                yield Static(
                    "No characters yet. Create one to start a level-1 character.",
                    id="characters-empty",
                )
                yield ListView(id="characters-list")
            with Horizontal(id="characters-actions"):
                yield Button("Create Character", variant="primary", id="character-create")
                yield Button("Open / Resume", id="character-open")
                yield Button("Rename", id="character-rename")
                yield Button("Duplicate", id="character-duplicate")
                yield Button("Delete", variant="error", id="character-delete")
            yield Static("↑↓ Move   Enter Open   Esc Close", id="characters-help")

    def on_mount(self) -> None:
        self._update_compact_class()
        view = self.query_one("#characters-list", ListView)
        view.index = 0 if self.summaries else None
        self.query_one("#characters-empty", Static).display = not self.summaries
        if self.summaries:
            view.focus()
        else:
            self.query_one("#character-create", Button).focus()

    def on_resize(self, _event: events.Resize) -> None:
        self._update_compact_class()

    def _update_compact_class(self) -> None:
        self.set_class(self.size.width < 80 or self.size.height < 24, "compact")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "characters-list":
            self._open_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "character-create":
            character = self.creation.create_draft()
            self.dismiss(("create", character.character_id))
        elif action == "character-open":
            self._open_selected()
        elif action == "character-rename":
            self._rename_selected()
        elif action == "character-duplicate":
            self._duplicate_selected()
        elif action == "character-delete":
            self._confirm_delete()

    def on_key(self, event: events.Key) -> None:
        if self.app.screen is not self or isinstance(self.focused, Input):
            return
        if event.key == "enter":
            event.stop()
            self._open_selected()
        elif event.character == "a":
            event.stop()
            character = self.creation.create_draft()
            self.dismiss(("create", character.character_id))
        elif event.character == "r":
            event.stop()
            self._rename_selected()
        elif event.character == "d":
            event.stop()
            self._duplicate_selected()
        elif event.character == "x":
            event.stop()
            self._confirm_delete()

    def _selected(self) -> CharacterSummary | None:
        view = self.query_one("#characters-list", ListView)
        if view.index is None or not 0 <= view.index < len(self.summaries):
            return None
        return self.summaries[view.index]

    def _open_selected(self) -> None:
        row = self._selected()
        if row is not None:
            self.dismiss(("open", row.character_id))

    def _rename_selected(self) -> None:
        row = self._selected()
        if row is None:
            self.notify("Select a character first.", timeout=2)
            return
        self.app.push_screen(
            TextEntryScreen("Rename character", row.name),
            lambda result: self._rename_result(row.character_id, result),
        )

    def _rename_result(self, character_id: str, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        try:
            self.service.rename_character(character_id, result[1])
        except ValueError as exc:
            self.notify(str(exc), timeout=3)
            return
        self._reload()

    def _duplicate_selected(self) -> None:
        row = self._selected()
        if row is None:
            self.notify("Select a character first.", timeout=2)
            return
        try:
            self.service.duplicate_character(row.character_id)
        except ValueError as exc:
            self.notify(str(exc), timeout=3)
            return
        self._reload()

    def _confirm_delete(self) -> None:
        row = self._selected()
        if row is None:
            self.notify("Select a character first.", timeout=2)
            return
        self.app.push_screen(
            ConfirmationScreen(f"Delete {row.name}? This removes its saved character data."),
            lambda confirmed: self._delete_result(row.character_id, confirmed),
        )

    def _delete_result(self, character_id: str, confirmed: bool) -> None:
        if confirmed:
            self.service.delete_character(character_id)
            self._reload()

    def _reload(self) -> None:
        self.summaries = self.service.list_characters()

        async def replace_rows() -> None:
            view = self.query_one("#characters-list", ListView)
            await view.clear()
            for row in self.summaries:
                await view.append(ListItem(Label(_summary_label(row))))
            view.index = 0 if self.summaries else None
            self.query_one("#characters-empty", Static).update(
                ""
                if self.summaries
                else "No characters yet. Create one to start a level-1 character."
            )
            self.query_one("#characters-empty", Static).display = not self.summaries

        self.run_worker(replace_rows(), exclusive=True)

    def action_close(self) -> None:
        self.dismiss(None)


class CharacterBuilderReferenceScreen(ModalScreen[None]):
    """Compact reference preview for Species and Background builder records."""

    DEFAULT_CSS = """
    CharacterBuilderReferenceScreen { align: center middle; background: $background 82%; }
    #builder-reference-card { width: 78; max-width: 94%; height: 86%; max-height: 94%;
        padding: 1 2; border: round $accent; background: $surface; }
    #builder-reference-title { height: 2; text-style: bold; color: $accent; }
    #builder-reference-copy { height: 1fr; overflow-y: auto; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, title: str, source: str, description: str, details: str) -> None:
        self.title = title
        self.source = source
        self.description = description
        self.details = details
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="builder-reference-card"):
            yield Label(self.title, id="builder-reference-title")
            yield Static(
                f"{self.source}\n\n{self.description}\n\n{self.details}\n\nEsc Back",
                id="builder-reference-copy",
            )

    def action_close(self) -> None:
        self.dismiss(None)


class CharacterWizardScreen(ModalScreen[tuple[str, ...] | None]):
    """One reusable, metadata-driven page for the entire creation workflow."""

    PAGE_SIZE = 20
    CHOICE_PAGE_SIZE = 50
    ROW_COUNT = 50
    DEFAULT_CSS = """
    CharacterWizardScreen { align: center middle; background: $background 86%; }
    #builder-card { width: 96%; max-width: 110; height: 96%; max-height: 100%;
        padding: 1 2; border: round $accent; background: $surface; }
    #builder-header { height: 3; color: $accent; text-style: bold; }
    #builder-search { height: 3; margin-bottom: 0; }
    #builder-name { height: 3; }
    #builder-preview { height: 3; color: $text-muted; overflow-y: auto; }
    #builder-options { height: 1fr; min-height: 4; border: none; background: transparent; }
    #builder-options > ListItem { height: 2; padding: 0 1; }
    #builder-options > ListItem.--highlight { background: #4a3a20; color: #eee7d5; }
    #builder-status { height: 1; color: $text-muted; }
    #builder-review { height: 1fr; overflow-y: auto; }
    #ability-methods { height: 3; }
    #ability-methods Button { width: 1fr; margin-right: 1; }
    .ability-line { height: 3; }
    .ability-label { width: 5; content-align: center middle; color: $accent; }
    .ability-input { width: 1fr; }
    #ability-preview { height: 3; color: $text-muted; }
    #builder-actions { height: 3; }
    #builder-actions Button { width: 1fr; min-width: 7; margin-right: 1; }
    #builder-footer { height: 1; color: $text-muted; }
    CharacterWizardScreen.compact #builder-card { padding: 0 1; height: 100%; }
    CharacterWizardScreen.compact #builder-header { height: 2; }
    CharacterWizardScreen.compact .ability-line { height: 2; }
    CharacterWizardScreen.compact #ability-methods { height: 2; }
    CharacterWizardScreen.compact #ability-preview { height: 2; }
    CharacterWizardScreen.compact #builder-actions {
        height: 4; layout: grid; grid-size: 4; grid-rows: 2; grid-gutter: 0 0;
    }
    CharacterWizardScreen.compact #builder-actions Button {
        height: 2; min-width: 0; margin: 0; padding: 0;
    }
    """

    BINDINGS = [("escape", "back", "Back"), ("f1", "help", "Help")]

    def __init__(
        self,
        creation: CharacterCreationService,
        character_id: str,
        *,
        step: str | None = None,
        query: str = "",
        selected_index: int = 0,
        page_offset: int = 0,
        choice_cursor: int = 0,
        spell_cursor: int = 0,
        spell_choice_cursor: int = 0,
    ) -> None:
        self.creation = creation
        self.characters: CharacterService = creation.characters
        self.character_id = character_id
        self.current_step = step or creation.resume_step(character_id)
        self.initial_query = query
        self.initial_selected_index = selected_index
        self.page_offset = page_offset
        self.selection_index = selected_index
        self.choice_cursor = choice_cursor
        self.spell_cursor = spell_cursor
        self.spell_choice_cursor = spell_choice_cursor
        self.ability_method = "standard_array"
        self._visible_options: tuple[Any, ...] = ()
        self._page_size = self.PAGE_SIZE
        self._rendering = False
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="builder-card"):
            yield Static("", id="builder-header")
            yield Input(self.initial_query, placeholder="Search this list", id="builder-search")
            yield Input(placeholder="Character name", id="builder-name")
            with Vertical(id="ability-fields"):
                with Horizontal(classes="ability-line"):
                    for ability in ABILITIES[:2]:
                        yield Label(ability.upper(), classes="ability-label")
                        yield Input(id=f"ability-{ability}", classes="ability-input")
                with Horizontal(classes="ability-line"):
                    for ability in ABILITIES[2:4]:
                        yield Label(ability.upper(), classes="ability-label")
                        yield Input(id=f"ability-{ability}", classes="ability-input")
                with Horizontal(classes="ability-line"):
                    for ability in ABILITIES[4:6]:
                        yield Label(ability.upper(), classes="ability-label")
                        yield Input(id=f"ability-{ability}", classes="ability-input")
            with Horizontal(id="ability-methods"):
                yield Button("Standard Array", id="method-standard_array")
                yield Button("Point Buy", id="method-point_buy")
                yield Button("Manual Entry", id="method-manual")
            yield Static("", id="ability-preview")
            yield Static("", id="builder-preview")
            with ListView(id="builder-options"):
                for index in range(self.ROW_COUNT):
                    yield ListItem(Label("", id=f"builder-option-label-{index}"))
            yield Static("", id="builder-review")
            yield Static("", id="builder-status")
            with Horizontal(id="builder-actions"):
                yield Button("Back", id="builder-back")
                yield Button("Reference", id="builder-reference")
                yield Button("‹ Page", id="builder-page-back")
                yield Button("Page ›", id="builder-page-next")
                yield Button("Continue", variant="primary", id="builder-continue")
                yield Button("Save & Close", id="builder-close")
                yield Button("Help", id="builder-help")
            yield Static("", id="builder-footer")

    def on_mount(self) -> None:
        self._update_compact_class()
        self.query_one("#builder-search", Input).focus()
        self._render_step()

    def on_resize(self, _event: events.Resize) -> None:
        self._update_compact_class()
        self._render_header()

    def _update_compact_class(self) -> None:
        self.set_class(self.size.width < 80 or self.size.height < 24, "compact")

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._rendering:
            return
        if event.input.id == "builder-search":
            self.page_offset = 0
            self.selection_index = 0
            self._load_options()
        elif event.input.id and event.input.id.startswith("ability-"):
            self._save_ability_inputs()
            self._render_ability_preview()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "builder-search":
            self.query_one("#builder-options", ListView).focus()
        elif event.input.id == "builder-name":
            self._commit_name()
            self._continue()
        elif event.input.id and event.input.id.startswith("ability-"):
            self._continue()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "builder-options":
            self.selection_index = event.list_view.index or 0
            self._render_preview()
            self._render_actions()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "builder-back":
            self.action_back()
        elif button_id == "builder-reference":
            self._open_reference()
        elif button_id == "builder-page-back":
            self._change_page(-1)
        elif button_id == "builder-page-next":
            self._change_page(1)
        elif button_id == "builder-continue":
            self._continue()
        elif button_id == "builder-close":
            self.dismiss(("close", self.character_id))
        elif button_id == "builder-help":
            self.action_help()
        elif button_id.startswith("method-"):
            self._set_ability_method(button_id.removeprefix("method-"))

    def on_key(self, event: events.Key) -> None:
        if self.app.screen is not self:
            return
        focus = self.focused
        if isinstance(focus, Button):
            return
        if isinstance(focus, Input):
            if event.key == "escape":
                event.stop()
                self.action_back()
            return
        if event.character == "/" and self._search_available():
            event.stop()
            self.query_one("#builder-search", Input).focus()
        elif event.character == "?":
            event.stop()
            self.action_help()
        elif event.character in {"j", "k"} or event.key in {"up", "down"}:
            event.stop()
            view = self.query_one("#builder-options", ListView)
            direction_down = event.character == "j" or event.key == "down"
            target = (view.index or 0) + (1 if direction_down else -1)
            if self._visible_options:
                view.index = max(0, min(target, len(self._visible_options) - 1))
        elif event.key == "space":
            if self.current_step in {
                "species_choices",
                "background_choices",
                "class_choices",
                "equipment",
                "spells",
            }:
                event.stop()
                self._toggle_highlighted()
        elif event.key == "enter":
            event.stop()
            self._enter_action()
        elif event.key == "escape":
            event.stop()
            self.action_back()

    def action_back(self) -> None:
        steps = self.creation.steps(self.character_id)
        keys = [step.key for step in steps]
        try:
            index = keys.index(self.current_step)
        except ValueError:
            index = len(keys) - 1
        if index > 0:
            self.current_step = keys[index - 1]
            self.page_offset = 0
            self.selection_index = 0
            self.choice_cursor = 0
            self.spell_cursor = 0
            self.spell_choice_cursor = 0
            self._render_step()
        else:
            self.dismiss(("close", self.character_id))

    def view_state(self) -> tuple[str, str, int, int, int, int, int]:
        return (
            self.current_step,
            self.query_one("#builder-search", Input).value,
            self.selection_index,
            self.page_offset,
            self.choice_cursor,
            self.spell_cursor,
            self.spell_choice_cursor,
        )

    def _render_step(self) -> None:
        self._rendering = True
        try:
            character = self.characters.get_character(self.character_id)
            if self.current_step == "name":
                self.query_one("#builder-name", Input).value = character.name
            scores = dict(character.base_ability_scores)
            self.ability_method = character.ability_score_method or self.ability_method
            for ability in ABILITIES:
                self.query_one(f"#ability-{ability}", Input).value = (
                    str(scores[ability]) if ability in scores else ""
                )
        finally:
            self._rendering = False
        self._render_header()
        self._load_options()
        self._render_ability_preview()
        self._render_review()
        self._render_actions()
        self._update_footer()

    def _render_header(self) -> None:
        steps = self.creation.steps(self.character_id)
        if self.current_step not in {step.key for step in steps}:
            self.current_step = self.creation.resume_step(self.character_id)
        index = next((i for i, step in enumerate(steps) if step.key == self.current_step), 0)
        title = steps[index].title if steps else "Character Creation"
        compact_names = " > ".join(_compact_step(step) for step in steps)
        progress = (
            f"{index + 1} / {len(steps)} — {title}"
            if self.has_class("compact")
            else f"{index + 1} / {len(steps)} — {title}\n{compact_names}"
        )
        self.query_one("#builder-header", Static).update(f"Character Creation  ·  {progress}")

    def _render_step_visibility(self) -> None:
        key = self.current_step
        is_name = key == "name"
        is_abilities = key == "abilities"
        is_review = key == "review"
        searchable = self._search_available()
        self.query_one("#builder-name", Input).display = is_name
        self.query_one("#ability-fields").display = is_abilities
        self.query_one("#ability-methods").display = is_abilities
        self.query_one("#ability-preview", Static).display = is_abilities
        self.query_one("#builder-search", Input).display = searchable
        self.query_one("#builder-options", ListView).display = (
            not is_review and not is_name and not is_abilities
        )
        self.query_one("#builder-review", Static).display = is_review
        self.query_one("#builder-preview", Static).display = (
            not is_review and not is_name and not is_abilities
        )

    def _search_available(self) -> bool:
        return self.current_step in {
            "species",
            "background",
            "class",
            "species_choices",
            "background_choices",
            "class_choices",
            "equipment",
            "spells",
        }

    def _load_options(self) -> None:
        self._render_step_visibility()
        if self.current_step in {"name", "abilities", "review"}:
            self._visible_options = ()
            self._write_option_rows(())
            self.query_one("#builder-status", Static).update("")
            self._render_preview()
            self._render_actions()
            return
        query = self.query_one("#builder-search", Input).value.strip()
        if self.current_step in {"species", "background", "class"}:
            owner_type = {"species": "species", "background": "background", "class": "class"}[
                self.current_step
            ]
            options = self.creation.list_options(
                owner_type, query=query, offset=self.page_offset, limit=self.PAGE_SIZE
            )
            self._page_size = self.PAGE_SIZE
            self._visible_options = options
        elif self.current_step == "spells":
            choices = self.creation.choices_for_step(self.character_id, "spells")
            active_choice = self._current_spell_choice(choices)
            if active_choice is not None:
                self._visible_options = self.creation.choice_options(
                    self.character_id,
                    active_choice,
                    query=query,
                    offset=self.page_offset,
                    limit=self.CHOICE_PAGE_SIZE,
                )
            else:
                groups = self.creation.spell_groups(self.character_id)
                self.spell_cursor = min(self.spell_cursor, max(0, len(groups) - 1))
                if not groups:
                    self._visible_options = ()
                else:
                    group = groups[self.spell_cursor]
                    self._visible_options = self.creation.spell_options(
                        self.character_id,
                        group,
                        query=query,
                        offset=self.page_offset,
                        limit=self.CHOICE_PAGE_SIZE,
                    )
            self._page_size = self.CHOICE_PAGE_SIZE
        else:
            choices = self.creation.choices_for_step(self.character_id, self.current_step)
            active = self._current_choice(choices)
            if active is None:
                self._visible_options = ()
            else:
                self._visible_options = self.creation.choice_options(
                    self.character_id,
                    active,
                    query=query,
                    offset=self.page_offset,
                    limit=self.CHOICE_PAGE_SIZE,
                )
            self._page_size = self.CHOICE_PAGE_SIZE
        self._write_option_rows(self._visible_options)
        self._render_preview()
        self._render_actions()

    def _write_option_rows(self, options: tuple[Any, ...]) -> None:
        view = self.query_one("#builder-options", ListView)
        character = self.characters.get_character(self.character_id)
        selected_owner = {
            "species": character.species,
            "background": character.background,
            "class": character.starting_class,
        }.get(self.current_step)
        selected_choice_keys: set[tuple[str, str | None, str | None]] = set()
        active_choice = None
        selected_spell_ids: set[str] = set()
        if self.current_step == "spells":
            active_choice = self._current_spell_choice(
                self.creation.choices_for_step(self.character_id, "spells")
            )
            if active_choice is not None:
                selected_choice_keys = {
                    (
                        row.selected_option_key,
                        row.selected_reference.identity if row.selected_reference else None,
                        row.selected_value,
                    )
                    for row in self.creation._selected_for_choice(character, active_choice)
                }
            else:
                groups = self.creation.spell_groups(self.character_id)
                if groups:
                    group = groups[min(self.spell_cursor, len(groups) - 1)]
                    selected_spell_ids = {
                        row.spell_reference.identity
                        for row in character.spells
                        if _spell_row_matches_group(row, group)
                    }
        elif self.current_step in {
            "species_choices",
            "background_choices",
            "class_choices",
            "equipment",
        }:
            active_choice = self._current_choice(
                self.creation.choices_for_step(self.character_id, self.current_step)
            )
            if active_choice is not None:
                selected_choice_keys = {
                    (
                        row.selected_option_key,
                        row.selected_reference.identity if row.selected_reference else None,
                        row.selected_value,
                    )
                    for row in self.creation._selected_for_choice(character, active_choice)
                }
        self._rendering = True
        try:
            for index, item in enumerate(view.children):
                label = item.query_one(Label)
                if index >= len(options):
                    item.display = False
                    label.update("")
                    continue
                item.display = True
                row = options[index]
                marker = "> " if view.index == index else "  "
                if isinstance(row, BuilderOption):
                    selected = bool(selected_owner and selected_owner.identity == row.identity)
                    content = (
                        f"{marker}{'[x]' if selected else '[ ]'} {row.name} · {row.source_name}"
                    )
                    if row.subtitle:
                        content += f"\n    {row.subtitle}"
                else:
                    selected = self._option_selected_from_keys(
                        row, selected_choice_keys, selected_spell_ids
                    )
                    content = f"{marker}{'[x]' if selected else '[ ]'} {row.label}"
                    if row.summary:
                        content += f"\n    {row.summary}"
                label.update(content)
            if options:
                if view.index is None or view.index >= len(options):
                    view.index = min(self.selection_index, len(options) - 1)
                self.selection_index = view.index or 0
                if not self.query_one("#builder-search", Input).has_focus:
                    view.focus()
        finally:
            self._rendering = False
        if len(options) >= self._page_size:
            page_count = "more results may be available"
        elif self.page_offset:
            page_count = "end of results"
        else:
            page_count = f"{len(options)} option{'s' if len(options) != 1 else ''}"
        self.query_one("#builder-status", Static).update(page_count)
        if self.current_step in {
            "species_choices",
            "background_choices",
            "class_choices",
            "equipment",
        }:
            choice = active_choice
            if choice is None:
                self.query_one("#builder-status", Static).update(
                    "No active structured choices are available on this step."
                )
            else:
                count = len(self.creation._selected_for_choice(character, choice))
                label = _choice_prompt(choice)
                self.query_one("#builder-status", Static).update(
                    f"{label} · {count} / {choice.definition.count} selected · {page_count}"
                )
        elif self.current_step == "spells":
            choice = active_choice
            if choice is not None:
                count = len(self.creation._selected_for_choice(character, choice))
                prompt = _choice_prompt(choice)
                if not options:
                    self.query_one("#builder-status", Static).update(
                        f"{prompt} is not available in the structured rules data."
                    )
                    return
                self.query_one("#builder-status", Static).update(
                    f"{prompt} · {count} / {choice.definition.count} selected · {page_count}"
                )
            else:
                groups = self.creation.spell_groups(self.character_id)
                if not groups:
                    unresolved = self.creation.unresolved_spell_choices(self.character_id)
                    self.query_one("#builder-status", Static).update(
                        unresolved[0] if unresolved else "No spell choices are required."
                    )
                    return
                group = groups[min(self.spell_cursor, len(groups) - 1)]
                count = self.creation._spell_group_count(character, group)
                self.query_one("#builder-status", Static).update(
                    f"{group.label} · {count} / {group.count} selected · {page_count}"
                )

    def _option_selected_from_keys(
        self,
        option: BuilderChoiceOption,
        selected_choice_keys: set[tuple[str, str | None, str | None]],
        selected_spell_ids: set[str],
    ) -> bool:
        if self.current_step == "spells":
            if (
                self._current_spell_choice(
                    self.creation.choices_for_step(self.character_id, "spells")
                )
                is not None
            ):
                return any(
                    (
                        option.option_key,
                        option.reference.identity if option.reference else None,
                        option.value,
                    )
                    == selection
                    for selection in selected_choice_keys
                )
            return bool(
                option.reference is not None and option.reference.identity in selected_spell_ids
            )
        return any(
            (
                option.option_key,
                option.reference.identity if option.reference else None,
                option.value,
            )
            == selection
            for selection in selected_choice_keys
        )

    def _render_preview(self) -> None:
        preview = self.query_one("#builder-preview", Static)
        if not self._visible_options:
            preview.update("")
            return
        index = self.query_one("#builder-options", ListView).index
        if index is None or not 0 <= index < len(self._visible_options):
            index = 0
        selected = self._visible_options[index]
        if isinstance(selected, BuilderOption):
            owner_type = {"species": "species", "background": "background", "class": "class"}.get(
                self.current_step
            )
            owner = self.creation.get_owner(owner_type, selected.identity) if owner_type else None
            detail = owner.description if owner and owner.description else selected.subtitle
            if owner and owner.metadata.get("origin_feat"):
                feat = owner.metadata["origin_feat"]
                if isinstance(feat, dict):
                    detail = (
                        f"{detail}\nOrigin feat: {str(feat.get('identity', '')).rsplit('/', 1)[-1]}"
                    )
            preview.update(
                f"{selected.name} · {selected.source_name}\n{detail or '2024 published option'}"
            )
        elif selected.summary:
            preview.update(f"{selected.label}\n{selected.summary}")
        else:
            preview.update(selected.label)

    def _render_ability_preview(self) -> None:
        preview = self.query_one("#ability-preview", Static)
        if self.current_step != "abilities":
            preview.update("")
            return
        character = self.characters.get_character(self.character_id)
        base = dict(character.base_ability_scores)
        adjustments: dict[str, int] = {}
        for row in character.ability_modifications:
            adjustments[row.ability] = adjustments.get(row.ability, 0) + row.amount
        lines = []
        for ability in ABILITIES:
            score = base.get(ability)
            if score is None:
                lines.append(f"{ability.upper()}  —")
            else:
                adjustment = adjustments.get(ability, 0)
                lines.append(f"{ability.upper()} {score:>2} {adjustment:+3} = {score + adjustment}")
        spent = ""
        if self.ability_method == "point_buy":
            try:
                cost = point_buy_cost(base)
                remaining = POINT_BUY_BUDGET - cost
                spent = f" · {cost} / {POINT_BUY_BUDGET} points spent · {remaining} remaining"
            except ValueError:
                spent = " · Point Buy values are incomplete or invalid"
        preview.update("   ".join(lines) + spent)

    def _render_review(self) -> None:
        if not self.is_mounted:
            return
        if self.current_step != "review":
            self.query_one("#builder-review", Static).update("")
            return
        character = self.characters.get_character(self.character_id)
        report = self.creation.validate_creation(self.character_id)
        lines = [
            f"Name: {character.name}",
            f"Species: {_reference_name(character.species)}",
            f"Background: {_reference_name(character.background)}",
            f"Starting Class: {_reference_name(character.starting_class)}",
            f"Level: {character.total_level}",
        ]
        if character.base_ability_scores:
            lines.append(
                "Ability Scores: "
                + ", ".join(_ability_review_value(character, ability) for ability in ABILITIES)
            )
        if character.feats:
            lines.append("Feats: " + ", ".join(row.feat_reference.name for row in character.feats))
        if character.choices:
            selected = []
            for row in character.choices:
                value = (
                    row.selected_reference.name if row.selected_reference else row.selected_value
                )
                if value:
                    selected.append(value.replace("skill:", "").replace("tool:", "").title())
            lines.append(
                "Choices: "
                + (
                    ", ".join(selected)
                    if selected
                    else f"{len(character.choices)} selections saved"
                )
            )
        if character.equipment:
            equipment = [
                f"{row.item_reference.name if row.item_reference else row.unresolved_selection} "
                f"×{row.quantity}"
                for row in character.equipment
            ]
            lines.append("Equipment: " + ", ".join(equipment))
        if character.currency:
            lines.append(
                "Starting currency: "
                + ", ".join(f"{row.amount} {row.currency}" for row in character.currency)
            )
        if character.spells:
            spells = [
                f"{row.spell_reference.name} ({row.acquisition.replace('_', ' ')})"
                for row in character.spells
            ]
            lines.append("Spells: " + ", ".join(spells))
        lines.append("")
        lines.extend(
            f"{'Warning' if issue.severity == 'warning' else 'Needs attention'}: {issue.message}"
            for issue in report.issues
        )
        if not report.issues:
            lines.append("All required level-1 creation choices are complete.")
        self.query_one("#builder-review", Static).update("\n".join(lines))

    def _update_footer(self) -> None:
        self._render_footer()

    def _enter_action(self) -> None:
        if self.current_step == "name":
            self._commit_name()
            self._continue()
        elif self.current_step == "abilities":
            self._continue()
        elif self.current_step == "review":
            self._continue()
        elif self.current_step in {"species", "background", "class"}:
            self._select_highlighted_owner()
        elif self.current_step == "spells":
            spell_choice = self._current_spell_choice(
                self.creation.choices_for_step(self.character_id, "spells")
            )
            if spell_choice is not None:
                if self._choice_complete(spell_choice):
                    self._continue_spell_choice()
                else:
                    self._toggle_highlighted()
            elif self._current_group_is_complete():
                self._continue_group()
            else:
                self._toggle_highlighted()
        elif self.current_step in {
            "species_choices",
            "background_choices",
            "class_choices",
            "equipment",
        }:
            if self._current_choice_is_complete():
                self._continue_choice()
            else:
                self._toggle_highlighted()

    def _toggle_highlighted(self) -> None:
        if not self._visible_options:
            return
        index = self.query_one("#builder-options", ListView).index
        if index is None or not 0 <= index < len(self._visible_options):
            return
        option = self._visible_options[index]
        try:
            if self.current_step == "spells":
                spell_choice = self._current_spell_choice(
                    self.creation.choices_for_step(self.character_id, "spells")
                )
                if spell_choice is not None:
                    self._toggle_choice(option, spell_choice)
                else:
                    self._toggle_spell(option)
            elif self.current_step in {
                "species_choices",
                "background_choices",
                "class_choices",
                "equipment",
            }:
                self._toggle_choice(option)
            elif self.current_step in {"species", "background", "class"}:
                self._select_highlighted_owner()
        except (ValueError, LookupError) as exc:
            self.notify(str(exc), timeout=3)
        self._mark_draft()
        self._load_options()
        self._render_review()

    def _select_highlighted_owner(self) -> None:
        if not self._visible_options:
            return
        index = self.query_one("#builder-options", ListView).index
        if index is None or index >= len(self._visible_options):
            return
        option = self._visible_options[index]
        if not isinstance(option, BuilderOption):
            return
        try:
            if self.current_step == "species":
                self.creation.choose_species(self.character_id, option.identity)
            elif self.current_step == "background":
                self.creation.choose_background(self.character_id, option.identity)
            else:
                self.creation.choose_starting_class(self.character_id, option.identity)
        except (ValueError, LookupError) as exc:
            self.notify(str(exc), timeout=3)
            return
        self._mark_draft()
        self.current_step = self.creation.resume_step(self.character_id)
        self.page_offset = 0
        self.selection_index = 0
        self.choice_cursor = 0
        self.spell_cursor = 0
        self.spell_choice_cursor = 0
        self._render_step()

    def _toggle_choice(
        self, option: BuilderChoiceOption, choice: BuilderChoice | None = None
    ) -> None:
        if choice is None:
            choices = self.creation.choices_for_step(self.character_id, self.current_step)
            choice = self._current_choice(choices)
        if choice is None:
            return
        self.creation.select_choice_option(self.character_id, choice, option)
        self._mark_draft()
        if choice.owner.owner_type and self.current_step == "spells":
            self._advance_spell_choice_cursor()
        elif self._current_choice_is_complete():
            self._advance_choice_cursor()

    def _toggle_spell(self, option: BuilderChoiceOption) -> None:
        if option.reference is None:
            return
        groups = self.creation.spell_groups(self.character_id)
        if not groups:
            return
        group = groups[min(self.spell_cursor, len(groups) - 1)]
        character = self.characters.get_character(self.character_id)
        selected = next(
            (
                row
                for row in character.spells
                if _spell_row_matches_group(row, group)
                and row.spell_reference.identity == option.reference.identity
            ),
            None,
        )
        if selected is not None:
            self.characters.remove_spell(self.character_id, selected.selection_id)
        else:
            self.creation.select_spell(self.character_id, group, option.reference)
        self._mark_draft()
        if self._current_group_is_complete():
            self._advance_spell_cursor()

    def _current_choice(self, choices: tuple[BuilderChoice, ...]) -> BuilderChoice | None:
        if not choices:
            return None
        if self.choice_cursor >= len(choices):
            incomplete = next(
                (i for i, choice in enumerate(choices) if not self._choice_complete(choice)),
                None,
            )
            if incomplete is None:
                self.choice_cursor = len(choices) - 1
            else:
                self.choice_cursor = incomplete
        return choices[self.choice_cursor]

    def _choice_complete(self, choice: BuilderChoice) -> bool:
        character = self.characters.get_character(self.character_id)
        return len(self.creation._selected_for_choice(character, choice)) >= choice.definition.count

    def _current_choice_is_complete(self) -> bool:
        choice = self._current_choice(
            self.creation.choices_for_step(self.character_id, self.current_step)
        )
        return choice is None or self._choice_complete(choice)

    def _advance_choice_cursor(self) -> None:
        choices = self.creation.choices_for_step(self.character_id, self.current_step)
        for index, choice in enumerate(choices):
            if not self._choice_complete(choice):
                self.choice_cursor = index
                break
        else:
            self.choice_cursor = len(choices)
        self.page_offset = 0
        self.selection_index = 0

    def _continue_choice(self) -> None:
        if not self._current_choice_is_complete():
            return
        choices = self.creation.choices_for_step(self.character_id, self.current_step)
        if self.choice_cursor < len(choices) - 1:
            self.choice_cursor += 1
            self.page_offset = 0
            self.selection_index = 0
            self._load_options()
        else:
            self._continue()

    def _current_group_is_complete(self) -> bool:
        groups = self.creation.spell_groups(self.character_id)
        if not groups:
            return True
        group = groups[min(self.spell_cursor, len(groups) - 1)]
        character = self.characters.get_character(self.character_id)
        return self.creation._spell_group_count(character, group) >= group.count

    def _advance_spell_cursor(self) -> None:
        groups = self.creation.spell_groups(self.character_id)
        character = self.characters.get_character(self.character_id)
        for index, group in enumerate(groups):
            if self.creation._spell_group_count(character, group) < group.count:
                self.spell_cursor = index
                break
        else:
            self.spell_cursor = len(groups)
        self.page_offset = 0
        self.selection_index = 0

    def _continue_group(self) -> None:
        if not self._current_group_is_complete():
            return
        groups = self.creation.spell_groups(self.character_id)
        if self.spell_cursor < len(groups) - 1:
            self.spell_cursor += 1
            self.page_offset = 0
            self.selection_index = 0
            self._load_options()
        else:
            self._continue()

    def _current_spell_choice(
        self, choices: tuple[BuilderChoice, ...] | None = None
    ) -> BuilderChoice | None:
        choices = choices or self.creation.choices_for_step(self.character_id, "spells")
        incomplete = next(
            (i for i, choice in enumerate(choices) if not self._choice_complete(choice)),
            None,
        )
        if incomplete is None:
            self.spell_choice_cursor = len(choices)
            return None
        if self.spell_choice_cursor >= len(choices) or self._choice_complete(
            choices[self.spell_choice_cursor]
        ):
            self.spell_choice_cursor = incomplete
        return choices[self.spell_choice_cursor]

    def _advance_spell_choice_cursor(self) -> None:
        choices = self.creation.choices_for_step(self.character_id, "spells")
        for index, choice in enumerate(choices):
            if not self._choice_complete(choice):
                self.spell_choice_cursor = index
                break
        else:
            self.spell_choice_cursor = len(choices)
        self.page_offset = 0
        self.selection_index = 0

    def _continue_spell_choice(self) -> None:
        if self._current_spell_choice() is not None and not self._choice_complete(
            self._current_spell_choice()
        ):
            return
        choices = self.creation.choices_for_step(self.character_id, "spells")
        self._advance_spell_choice_cursor()
        if self.spell_choice_cursor < len(choices):
            self._load_options()
        elif self.creation.spell_groups(self.character_id):
            self.spell_cursor = 0
            self._load_options()
        else:
            self._continue()

    def _continue(self) -> None:
        if self.current_step == "name":
            self._commit_name()
        elif self.current_step in {"species", "background", "class"}:
            character = self.characters.get_character(self.character_id)
            chosen = {
                "species": character.species,
                "background": character.background,
                "class": character.starting_class,
            }[self.current_step]
            if chosen is None or chosen.missing:
                self.notify(f"Choose a {self.current_step.title()} first.", timeout=3)
                return
        elif self.current_step == "abilities":
            try:
                values = self._ability_inputs()
                validate_ability_scores(self.ability_method, values, require_complete=True)
                self.creation.ability_scores(self.character_id, self.ability_method, values)
            except ValueError as exc:
                self.notify(str(exc), timeout=3)
                return
        elif self.current_step in {
            "species_choices",
            "background_choices",
            "class_choices",
            "equipment",
        }:
            if any(
                not self._choice_complete(choice)
                for choice in self.creation.choices_for_step(self.character_id, self.current_step)
            ):
                self.notify("Complete the required selections on this page first.", timeout=3)
                return
        elif self.current_step == "spells":
            required_spell_choices = self.creation.choices_for_step(self.character_id, "spells")
            if any(
                not self._choice_complete(choice) for choice in required_spell_choices
            ) or self.creation._spells_incomplete(self.character_id):
                self.notify("Complete the required spell selections first.", timeout=3)
                return
        elif self.current_step == "review":
            try:
                self.creation.complete_character(self.character_id)
            except ValueError as exc:
                self.notify(str(exc), timeout=3)
                self._render_review()
                return
            self.dismiss(("complete", self.character_id))
            return
        steps = self.creation.steps(self.character_id)
        try:
            index = next(i for i, step in enumerate(steps) if step.key == self.current_step)
        except StopIteration:
            index = -1
        if index + 1 < len(steps):
            self.current_step = steps[index + 1].key
        else:
            self.current_step = "review"
        self.page_offset = 0
        self.selection_index = 0
        self.choice_cursor = 0
        self.spell_cursor = 0
        self.spell_choice_cursor = 0
        self._render_step()

    def _commit_name(self) -> None:
        value = self.query_one("#builder-name", Input).value.strip()
        if not value:
            return
        try:
            self.characters.rename_character(self.character_id, value)
        except ValueError as exc:
            self.notify(str(exc), timeout=3)

    def _save_ability_inputs(self) -> None:
        values: dict[str, int] = {}
        for ability in ABILITIES:
            value = self.query_one(f"#ability-{ability}", Input).value.strip()
            if not value:
                continue
            try:
                values[ability] = int(value)
            except ValueError:
                return
        try:
            self.creation.save_ability_score_inputs(self.character_id, self.ability_method, values)
        except ValueError:
            return

    def _ability_inputs(self) -> dict[str, int]:
        result = {}
        for ability in ABILITIES:
            value = self.query_one(f"#ability-{ability}", Input).value.strip()
            if value:
                try:
                    result[ability] = int(value)
                except ValueError:
                    raise ValueError(f"Enter a whole number for {ability.upper()}.")
        return result

    def _set_ability_method(self, method: str) -> None:
        old = self.ability_method
        self.ability_method = method
        try:
            self.creation.save_ability_score_inputs(
                self.character_id, method, self._ability_inputs()
            )
        except ValueError:
            self.ability_method = old
            self.notify("Clear or change scores that do not fit this method first.", timeout=3)
        self._render_ability_preview()
        self._render_actions()

    def _reference_for_highlight(self) -> PublishedReference | None:
        if not self._visible_options:
            return None
        index = self.query_one("#builder-options", ListView).index
        if index is None or not 0 <= index < len(self._visible_options):
            return None
        option = self._visible_options[index]
        if isinstance(option, BuilderOption):
            kind = {"species": "species", "background": "background", "class": "class"}.get(
                self.current_step
            )
            return PublishedReference(kind or "class", option.identity, option.name, option.edition)
        return option.reference

    def _open_reference(self) -> None:
        reference = self._reference_for_highlight()
        if reference is not None:
            self._resume_view = self.view_state()
            self.dismiss(("reference", reference.kind, reference.identity))

    def _change_page(self, direction: int) -> None:
        if direction < 0 and self.page_offset > 0:
            self.page_offset = max(0, self.page_offset - self._page_size)
            self.selection_index = 0
            self._load_options()
        elif direction > 0 and len(self._visible_options) >= self._page_size:
            self.page_offset += self._page_size
            self.selection_index = 0
            self._load_options()

    def _render_actions(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#builder-reference", Button).display = (
            self._reference_for_highlight() is not None
        )
        self.query_one("#builder-page-back", Button).display = self.page_offset > 0
        self.query_one("#builder-page-next", Button).display = (
            len(self._visible_options) >= self._page_size
        )
        continue_button = self.query_one("#builder-continue", Button)
        if self.current_step == "review":
            report = self.creation.validate_creation(self.character_id)
            continue_button.label = "Mark Complete" if report.is_valid else "Fix Required Choices"
            continue_button.disabled = not report.is_valid
        else:
            continue_button.label = "Continue"
            continue_button.disabled = False
        if self.current_step == "name":
            self.query_one("#builder-name", Input).focus()
        elif self.current_step == "abilities" and not isinstance(self.focused, Input):
            self.query_one("#ability-str", Input).focus()
        elif self.current_step == "review":
            continue_button.focus()
        for method in ("standard_array", "point_buy", "manual"):
            button = self.query_one(f"#method-{method}", Button)
            button.variant = "primary" if self.ability_method == method else "default"
        self._render_footer()

    def _mark_draft(self) -> None:
        character = self.characters.get_character(self.character_id)
        if character.state == "complete":
            self.characters.set_character_state(self.character_id, "draft")

    def _render_footer(self) -> None:
        if not self.is_mounted:
            return
        if self.current_step in {
            "species_choices",
            "background_choices",
            "class_choices",
            "equipment",
            "spells",
        }:
            help_text = "↑↓ Move   Space Toggle   Enter Continue   / Search   Esc Back"
        elif self.current_step == "review":
            help_text = "Enter Mark Complete   Esc Back"
        elif self.current_step == "abilities":
            help_text = "Enter Continue   Esc Back   Scores save as you enter them"
        elif self.current_step == "name":
            help_text = "Enter Continue   Esc Back   Names are not required to be unique"
        else:
            help_text = "↑↓ Move   Enter Select   / Search   Esc Back   ? Help"
        self.query_one("#builder-footer", Static).update(help_text)

    def action_help(self) -> None:
        self.app.action_show_help()


def _summary_label(summary: CharacterSummary) -> str:
    if summary.state == "draft":
        status = "Draft"
        detail = summary.class_summary or f"Level {summary.total_level}"
    else:
        status = summary.class_summary or f"Level {summary.total_level}"
        detail = f"Level {summary.total_level} · 2024"
    return f"{summary.name}  ·  {status}\n{detail} · {summary.edition}"


def _reference_name(reference: PublishedReference | None) -> str:
    if reference is None:
        return "Not selected"
    return reference.name + (" · missing from installed data" if reference.missing else "")


def _ability_review_value(character: Character, ability: str) -> str:
    base = dict(character.base_ability_scores).get(ability, "—")
    adjustment = sum(
        item.amount for item in character.ability_modifications if item.ability == ability
    )
    return f"{ability.upper()} {base} ({_signed(adjustment)})"


def _signed(value: int) -> str:
    return f"{value:+d}" if value else "+0"


def _compact_step(step: CreationStep) -> str:
    return {
        "Starting Class": "Class",
        "Ability Scores": "Abilities",
        "Character Choices": "Choices",
        "Species Choices": "Species+",
        "Background Choices": "Background+",
        "Starting Equipment": "Equipment",
    }.get(step.title, step.title)


def _choice_prompt(choice: BuilderChoice) -> str:
    definition = choice.definition
    count = definition.count
    kind = definition.kind.value
    criterion = definition.criteria.kind.value if definition.criteria is not None else ""
    if choice.scope == "spellcasting_ability":
        return "Choose a spellcasting ability"
    labels = {
        ("proficiency", "skill"): "skill proficiencies",
        ("proficiency", "tool_group"): "tool proficiencies",
        ("proficiency", "language"): "languages",
        ("weapon_mastery", ""): "weapon masteries",
        ("ability_score", ""): "background ability increases",
        ("size", ""): "size options",
        ("feat", ""): "feats",
        ("spell", ""): "spells",
        ("cantrip", ""): "cantrips",
        ("equipment_package", ""): "starting equipment packages",
        ("equipment", ""): "equipment choices",
        ("optional_feature", ""): "class features",
    }
    label = labels.get((kind, criterion)) or labels.get((kind, "")) or "options"
    return f"Choose {count} {label}"
