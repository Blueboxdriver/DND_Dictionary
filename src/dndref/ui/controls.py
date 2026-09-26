"""Shared control descriptions for the browser footer and Help screen."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpControl:
    keys: str
    action: str
    short_keys: str | None = None
    short_action: str | None = None


@dataclass(frozen=True)
class HelpSection:
    title: str
    controls: tuple[HelpControl, ...] = ()
    notes: tuple[str, ...] = ()
    aligned_keys: bool = False


CORE_CONTROLS = (
    HelpControl("↑↓ or j/k", "Move through lists", "↑↓", "Move"),
    HelpControl("Enter", "Open or select", "Enter", "Open"),
    HelpControl("Esc", "Go back or close", "Esc", "Back"),
    HelpControl("/", "Search this list", "/", "Search"),
    HelpControl("Ctrl+K", "Search all categories", "Ctrl+K", "Search All"),
    HelpControl("Ctrl+P", "Open Commands", "Ctrl+P", "Commands"),
    HelpControl("?", "Open Help", "?", "Help"),
    HelpControl("q", "Quit the application", "q", "Quit"),
)


HELP_SECTIONS = (
    HelpSection("Getting Started", CORE_CONTROLS, aligned_keys=True),
    HelpSection(
        "Browsing",
        (
            HelpControl("Category tabs", "Choose a category from the row at the top."),
            HelpControl("Commands → Change Category", "Choose a category by name."),
            HelpControl("Commands → Filters", "Filter by edition, source, or category details."),
            HelpControl("Enter", "Open a selected entry or highlighted link."),
        ),
    ),
    HelpSection(
        "Searching",
        (
            HelpControl("/ or Ctrl+F", "Search the current list."),
            HelpControl("Ctrl+K", "Search every category and source."),
            HelpControl("Search examples", "spell:fireball · monster:dragon · source:XMM dragon"),
        ),
        (
            "Search All also understands item, feat, class, subclass, condition, "
            "rule, and edition prefixes.",
        ),
    ),
    HelpSection(
        "Entry Actions",
        (
            HelpControl(
                "Ctrl+P on an entry", "Favorite, add to a collection, edit tags, or edit a note."
            ),
            HelpControl(
                "Enter on a link", "Open a class, subclass, condition, rule, or related entry."
            ),
            HelpControl("Commands", "Browse Sources, Recently Viewed, or toggle images."),
        ),
        ("Favorites, collections, tags, and notes are saved on this device.",),
    ),
    HelpSection(
        "Character Creation",
        (
            HelpControl(
                "Commands → Open Characters",
                "Resume, rename, duplicate, or delete a saved character.",
            ),
            HelpControl(
                "Commands → New Character",
                "Create a saved 2024 level-1 Draft and start the guided builder.",
            ),
            HelpControl("Space", "Toggle an option in multi-choice character steps."),
            HelpControl(
                "Reference",
                "Open the published entry, then use Back to return to the same builder step.",
            ),
            HelpControl("Review", "Shows missing required choices and warnings before completion."),
        ),
        (
            "Drafts save each decision as you make it. Closing the builder does not discard "
            "progress.",
            "The builder creates one level-1 class. It does not calculate combat statistics or "
            "support level-up yet.",
        ),
    ),
    HelpSection(
        "Character Sheet",
        (
            HelpControl(
                "← / →",
                "Switch between Overview, Skills, Combat, Features, Spells, Equipment, and Notes.",
            ),
            HelpControl(
                "↑ / ↓ and Enter", "Move through a section and open a published reference."
            ),
            HelpControl(
                "Ctrl+P", "Open sheet actions, including section changes and creation editing."
            ),
            HelpControl(
                "Draft", "Shows incomplete state and keeps Resume Character Creation available."
            ),
            HelpControl("? or issues row", "Explain unresolved values and derived warnings."),
        ),
        (
            "Published entries opened from a sheet use the dictionary detail and browser history. "
            "The character itself is not added to Recently Viewed.",
            "The sheet shows maximum HP and static character data. It does not track current HP, "
            "spent spell slots, rests, or other live-play resources.",
        ),
    ),
    HelpSection(
        "Navigation",
        (
            HelpControl(
                "Esc",
                "Close an overlay, leave search, or go back from detail to results "
                "or the previous view.",
            ),
            HelpControl("Alt+Left / Alt+Right", "Move backward or forward through visited views."),
            HelpControl("Tab", "Move between a class description and its subclass list."),
        ),
        ("Esc does nothing at the top-level browser. It never quits the application.",),
    ),
    HelpSection(
        "Advanced",
        (
            HelpControl("1–8", "Switch categories directly."),
            HelpControl("F2", "Switch between name search and searching all entry text."),
            HelpControl("e / s / p", "Edition, source, and saved filter choices."),
            HelpControl("f / c / t / z", "Subclass parent and monster filters."),
            HelpControl("g / v", "Group source versions or choose a version."),
            HelpControl("b / r / i", "Browse Sources, Recently Viewed, or toggle images."),
            HelpControl("F / C", "Open Favorites or Collections."),
            HelpControl("c in subclass details", "Open the matching parent class."),
            HelpControl(
                "* / m / T / n", "Favorite an entry, add it to a collection, edit tags or note."
            ),
            HelpControl("t / e / g", "Cycle type, edition, or tag filters in personal lists."),
            HelpControl("d", "Remove a selected record from Favorites or a collection."),
            HelpControl("F1 / F3", "Open Help or Image and Data Info."),
            HelpControl("Ctrl+Q", "Quit outside text fields and overlays."),
            HelpControl("Ctrl+L", "Clear recent searches inside Search All."),
            HelpControl("a / r / x", "Create, rename, or delete a collection."),
            HelpControl("Space", "Toggle a filter or collection choice."),
            HelpControl("Ctrl+S", "Save a note or tags. Esc cancels."),
            HelpControl("Tab / Shift+Tab", "Move between controls."),
            HelpControl("PageUp / PageDown", "Move through a page of rows or text."),
            HelpControl("Home / End", "Go to the start or end of a list or table."),
            HelpControl("h / l in class tables", "Scroll sideways through class progression."),
            HelpControl("DELETE", "Confirm deletion after starting it in Collections."),
        ),
        (
            "These older shortcuts remain available for experienced users. Text fields keep their "
            "text; they do not run these actions.",
            "Search All accepts prefixes such as item:, spell:, feat:, class:, subclass:, "
            "monster:, condition:, rule:, edition:, and source:.",
        ),
    ),
)


def render_help_markdown() -> str:
    """Render the small, structured control reference as Markdown."""
    sections: list[str] = []
    for section in HELP_SECTIONS:
        sections.extend((f"## {section.title}", ""))
        if section.controls and section.aligned_keys:
            sections.extend(("| Keys | Action |", "|:--|:--|"))
            sections.extend(
                f"| `{control.keys}` | {control.action} |" for control in section.controls
            )
        elif section.controls:
            sections.extend(
                f"- `{control.keys}` — {control.action}" for control in section.controls
            )
        if section.notes:
            if section.controls:
                sections.append("")
            sections.extend(f"- {note}" for note in section.notes)
        sections.append("")
    return "\n".join(sections).strip()


def footer_control(action: str) -> str:
    """Return a compact footer hint using the same descriptions as Help."""
    control = next(
        (
            item
            for item in CORE_CONTROLS
            if item.short_action is not None and item.short_action.casefold() == action.casefold()
        ),
        None,
    )
    if control is None or control.short_keys is None or control.short_action is None:
        raise KeyError(action)
    return f"{control.short_keys} {control.short_action}"
