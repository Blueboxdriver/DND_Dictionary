"""Readable, width tolerant monster stat block rendering."""

from __future__ import annotations

from ..search import EntryDetail

SECTION_TITLES = {
    "traits": "Traits", "actions": "Actions", "bonus_actions": "Bonus Actions",
    "reactions": "Reactions", "legendary_actions": "Legendary Actions",
    "lair_actions": "Lair Actions", "regional_effects": "Regional Effects",
    "spellcasting": "Spellcasting", "innate_spellcasting": "Innate Spellcasting",
}


def render_monster_detail(detail: EntryDetail) -> str:
    fields = detail.fields
    creature = " ".join(filter(None, (
        str(fields.get("size") or ""), str(fields.get("creature_type") or ""),
    )))
    if fields.get("subtype"):
        creature += f" ({fields['subtype']})"
    if fields.get("alignment"):
        creature += f", {fields['alignment']}"
    lines = [f"# {detail.name}", f"*{creature}*", "",
             f"**Source:** {detail.source_label}" +
             (f", p. {fields['page']}" if fields.get("page") else ""),
             f"**Edition:** {fields.get('edition') or 'Unspecified'}", ""]
    for label, value in (
        ("Armor Class", fields.get("armor_class")),
        ("Hit Points", str(fields.get("hit_points_text") or "") +
         (f" ({fields['hit_dice']})" if fields.get("hit_dice") else "")),
        ("Speed", ", ".join(f"{kind} {speed}" if kind != "walk" else str(speed)
                            for kind, speed in (fields.get("speed") or {}).items())
         or fields.get("speed_text")),
    ):
        if value:
            # Markdown treats adjacent lines as one paragraph. Keep each
            # headline stat in its own paragraph so they remain easy to scan.
            lines.extend([f"**{label}:** {value}", ""])
    scores = fields.get("abilities") or {}
    if scores:
        lines.extend(["", "| STR | DEX | CON |", "| ---: | ---: | ---: |",
                      "| " + " | ".join(str(scores.get(key, "")) for key in
                                       ("str", "dex", "con")) + " |",
                      "| INT | WIS | CHA |", "| ---: | ---: | ---: |",
                      "| " + " | ".join(str(scores.get(key, "")) for key in
                                       ("int", "wis", "cha")) + " |", ""])
    for label, key in (
        ("Saving Throws", "saving_throws"), ("Skills", "skills"),
        ("Damage Vulnerabilities", "damage_vulnerabilities"),
        ("Damage Resistances", "damage_resistances"),
        ("Damage Immunities", "damage_immunities"),
        ("Condition Immunities", "condition_immunities"),
        ("Senses", "senses"), ("Passive Perception", "passive_perception"),
        ("Languages", "languages"), ("Telepathy", "telepathy"),
    ):
        value = fields.get(key)
        if isinstance(value, dict):
            value = ", ".join(f"{name.upper()} {bonus}" for name, bonus in value.items())
        if value is not None and value != "":
            lines.append(f"**{label}:** {value}")
    cr = fields.get("challenge_rating")
    lines.append(f"**Challenge:** {cr}" + (f" ({fields['xp']} XP)" if fields.get("xp") else ""))
    if fields.get("proficiency_bonus"):
        lines.append(f"**Proficiency Bonus:** {fields['proficiency_bonus']}")
    grouped: dict[str, list[dict[str, object]]] = {}
    for ability in fields.get("abilities_and_actions") or ():
        grouped.setdefault(str(ability["section"]), []).append(ability)
    for section, title in SECTION_TITLES.items():
        abilities = grouped.get(section, ())
        if not abilities:
            continue
        lines.extend(["", f"## {title}", ""])
        if section == "legendary_actions" and fields.get("legendary_intro"):
            lines.extend([str(fields["legendary_intro"]), ""])
        for ability in abilities:
            cost = ability.get("cost")
            suffix = f" — {cost} {'Action' if cost == 1 else 'Actions'}" if cost else ""
            lines.extend([f"### {ability['name']}{suffix}", "", str(ability["description"]), ""])
    for section in detail.sections:
        lines.extend(["", f"## {section.heading}", "", section.body])
    return "\n".join(lines)
