"""Build the Milestone 8 SRD spells/feats pack from the official PDF.

This is a development-time converter, not an application runtime dependency.
It expects the English SRD 5.2.1 PDF downloaded from the official SRD page and
uses pdftotext to preserve the source text while repairing PDF line wrapping.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from pathlib import Path

SOURCE_KEY = "srd-5-2-1"
EXPECTED_SPELL_COUNT = 339
EXPECTED_FEAT_COUNT = 17
SCHOOLS = {
    "Abjuration",
    "Conjuration",
    "Divination",
    "Enchantment",
    "Evocation",
    "Illusion",
    "Necromancy",
    "Transmutation",
}
FEAT_NAMES = (
    "Alert",
    "Magic Initiate",
    "Savage Attacker",
    "Skilled",
    "Ability Score Improvement",
    "Grappler",
    "Archery",
    "Defense",
    "Great Weapon Fighting",
    "Two-Weapon Fighting",
    "Boon of Combat Prowess",
    "Boon of Dimensional Travel",
    "Boon of Fate",
    "Boon of Irresistible Offense",
    "Boon of Spell Recall",
    "Boon of the Night Spirit",
    "Boon of Truesight",
)
FEAT_CATEGORIES = {
    "Alert": "Origin",
    "Magic Initiate": "Origin",
    "Savage Attacker": "Origin",
    "Skilled": "Origin",
    "Ability Score Improvement": "General",
    "Grappler": "General",
    "Archery": "Fighting Style",
    "Defense": "Fighting Style",
    "Great Weapon Fighting": "Fighting Style",
    "Two-Weapon Fighting": "Fighting Style",
    **{name: "Epic Boon" for name in FEAT_NAMES[10:]},
}
FEAT_SECTION_HEADINGS = {
    "Origin Feats",
    "General Feats",
    "Fighting Style Feats",
    "Epic Boon Feats",
}
FEAT_BENEFITS = {
    "Alert": ("Initiative Proficiency", "Initiative Swap"),
    "Magic Initiate": ("Two Cantrips", "Level 1 Spell", "Spell Change", "Repeatable"),
    "Grappler": ("Ability Score Increase", "Punch and Grab", "Attack Advantage", "Fast Wrestler"),
    "Boon of Combat Prowess": ("Ability Score Increase", "Peerless Aim"),
    "Boon of Dimensional Travel": ("Ability Score Increase", "Blink Steps"),
    "Boon of Fate": ("Ability Score Increase", "Improve Fate"),
    "Boon of Irresistible Offense": (
        "Ability Score Increase",
        "Overcome Defenses",
        "Overwhelming Strike",
    ),
    "Boon of Spell Recall": ("Ability Score Increase", "Free Casting"),
    "Boon of the Night Spirit": ("Ability Score Increase", "Merge with Shadows", "Shadowy Form"),
    "Boon of Truesight": ("Ability Score Increase", "Truesight"),
}


def _slug(name: str) -> str:
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    value = value.casefold().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _clean_text(lines: list[str]) -> str:
    text = "\n".join(lines).replace("\u00ad", "")
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _source_pages(
    text: str, marker: str, end_marker: str, first_page: int
) -> list[tuple[str, int]]:
    start = text.rindex(marker)
    end = text.index(end_marker, start)
    section = text[start:end]
    result: list[tuple[str, int]] = []
    for offset, page in enumerate(section.split("\f")):
        page_number = first_page + offset
        for line in page.splitlines():
            line = line.strip()
            if not line or re.fullmatch(r"System Reference Document 5\.2\.1 \d+", line):
                continue
            result.append((line, page_number))
    return result


def _spell_header(line: str) -> re.Match[str] | None:
    return re.match(
        r"^(?:Level (?P<level>\d+) (?P<school>\w+)|(?P<cantrip_school>\w+) Cantrip) "
        r"\((?P<classes>.*)\)$",
        line,
    )


def _spell_records(text: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    source_lines = _source_pages(text, "Spell Descriptions", "Rules Glossary", 107)
    lines = [line for line, _page in source_lines]
    starts: list[tuple[int, int, int, re.Match[str]]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _spell_header(line)
        consumed = 1
        if not match and (line.startswith("Level ") or " Cantrip (" in line):
            joined = line
            next_index = index + 1
            while next_index < len(lines) and not joined.endswith(")"):
                joined += " " + lines[next_index]
                next_index += 1
            match = _spell_header(joined)
            if match:
                consumed = next_index - index
        if match:
            name_index = index - 1
            if name_index < 0:
                raise ValueError(f"missing spell name before line {index}")
            starts.append((name_index, index, consumed, match))
            index += consumed
        else:
            index += 1

    if len(starts) != EXPECTED_SPELL_COUNT:
        raise ValueError(f"expected {EXPECTED_SPELL_COUNT} spell headers, found {len(starts)}")

    labels = ("Casting Time:", "Range:", "Components:", "Component:", "Duration:")
    spells: list[dict[str, object]] = []
    inventory: list[dict[str, object]] = []
    for position, (name_index, header_index, consumed, header) in enumerate(starts):
        end_index = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        block = lines[header_index + consumed : end_index]
        positions = []
        for line_index, line in enumerate(block):
            label = next((candidate for candidate in labels if line.startswith(candidate)), None)
            if label is not None:
                positions.append((line_index, label))
        if len(positions) < 4:
            raise ValueError(f"missing spell fields for {lines[name_index]}")

        values: dict[str, str] = {}
        for value_index, (line_index, label) in enumerate(positions):
            next_index = (
                positions[value_index + 1][0]
                if value_index + 1 < len(positions)
                else len(block)
            )
            if label == "Duration:":
                value_lines = block[line_index : line_index + 1]
            else:
                value_lines = block[line_index:next_index]
            key = {
                "Casting Time:": "casting_time",
                "Range:": "range",
                "Components:": "components",
                "Component:": "components",
                "Duration:": "duration",
            }[label]
            values[key] = _clean_text([value_lines[0][len(label) :], *value_lines[1:]])

        description_start = positions[-1][0] + 1
        description = _clean_text(block[description_start:])
        higher_level_effects = None
        higher_match = re.search(
            r"\b(Using a Higher-Level Spell Slot|Cantrip Upgrade)\.\s*", description
        )
        if higher_match:
            higher_level_effects = description[higher_match.end() :].strip()
            description = description[: higher_match.start()].strip()

        component_text = values["components"]
        material_match = re.search(r"\bM\s*\((.*)\)$", component_text)
        material_description = material_match.group(1).strip() if material_match else None
        school = header.group("school") or header.group("cantrip_school")
        level = int(header.group("level") or 0)
        if school not in SCHOOLS:
            raise ValueError(f"unknown spell school for {lines[name_index]}: {school}")
        components = {
            "verbal": bool(re.search(r"\bV\b", component_text)),
            "somatic": bool(re.search(r"\bS\b", component_text)),
            "material": material_match is not None,
        }
        if material_description:
            components["material_description"] = material_description
        name = lines[name_index]
        page = source_lines[name_index][1]
        spell = {
            "local_key": f"spell/{_slug(name)}",
            "name": name,
            "description": description,
            "source": SOURCE_KEY,
            "level": level,
            "school": school.casefold(),
            "casting_time": values["casting_time"],
            "range": values["range"],
            "components": components,
            "duration": values["duration"],
            "concentration": values["duration"].startswith("Concentration"),
            "ritual": "Ritual" in values["casting_time"],
            "class_references": [],
        }
        if higher_level_effects:
            spell["higher_level_effects"] = higher_level_effects
        spells.append(spell)
        inventory.append(
            {
                "local_key": spell["local_key"],
                "name": name,
                "level": level,
                "school": school.casefold(),
                "source_locator": f"SRD 5.2.1 p. {page}",
                "source_classes": [item.strip() for item in header.group("classes").split(",")],
                "conversion_status": "reviewed",
            }
        )
    return spells, inventory


def _feat_records(text: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    source_lines = _source_pages(text, "Feat Descriptions", "Equipment", 87)
    lines = [line for line, _page in source_lines]
    starts: list[tuple[int, str]] = []
    for index, name in enumerate(lines):
        if name not in FEAT_NAMES or any(existing_name == name for _i, existing_name in starts):
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if " Feat" in next_line:
            starts.append((index, name))
    if len(starts) != EXPECTED_FEAT_COUNT:
        raise ValueError(f"expected {EXPECTED_FEAT_COUNT} feat headers, found {len(starts)}")

    feats: list[dict[str, object]] = []
    inventory: list[dict[str, object]] = []
    for position, (start, name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        block = lines[start + 1 : end]
        while block and block[-1] in FEAT_SECTION_HEADINGS:
            block.pop()
        header_lines: list[str] = []
        header_index = 0
        while header_index < len(block):
            header_lines.append(block[header_index])
            if block[header_index].endswith(")") or "Prerequisite:" not in " ".join(header_lines):
                if " Feat" in " ".join(header_lines):
                    break
            header_index += 1
        header = _clean_text(header_lines)
        category = FEAT_CATEGORIES[name]
        prerequisite = None
        prerequisite_match = re.search(r"\(Prerequisite:\s*(.*?)\)$", header)
        if prerequisite_match:
            prerequisite = prerequisite_match.group(1)
        minimum_level_match = re.search(r"Level (\d+)\+", prerequisite or "")
        body = _clean_text(block[header_index + 1 :])
        repeatable = "Repeatable." in body
        repeatable_index = body.find("Repeatable.")
        if repeatable_index >= 0:
            repeatable_body = body[repeatable_index + len("Repeatable.") :].strip()
            body = body[:repeatable_index].strip()
        else:
            repeatable_body = None

        headings = list(FEAT_BENEFITS.get(name, ()))
        markers = [(heading, body.find(f"{heading}.")) for heading in headings]
        markers = [(heading, index) for heading, index in markers if index >= 0]
        sections: list[dict[str, object]] = []
        description = body[: markers[0][1]].strip() if markers else body
        for marker_index, (heading, index) in enumerate(markers):
            content_start = index + len(heading) + 1
            content_end = (
                markers[marker_index + 1][1]
                if marker_index + 1 < len(markers)
                else len(body)
            )
            content = body[content_start:content_end].strip()
            sections.append(
                {
                    "key": f"feat/{_slug(name)}/{_slug(heading)}",
                    "heading": heading,
                    "body": content,
                    "display_order": marker_index + 1,
                }
            )
        if repeatable_body is not None:
            sections.append(
                {
                    "key": f"feat/{_slug(name)}/repeatable",
                    "heading": "Repeatable",
                    "body": repeatable_body,
                    "display_order": len(sections) + 1,
                }
            )
        ability_increase = next(
            (
                section["body"]
                for section in sections
                if section["heading"] == "Ability Score Increase"
            ),
            None,
        )
        feat = {
            "local_key": f"feat/{_slug(name)}",
            "name": name,
            "description": description,
            "source": SOURCE_KEY,
            "category": category,
            "prerequisite": prerequisite,
            "minimum_level": int(minimum_level_match.group(1)) if minimum_level_match else None,
            "repeatable": repeatable,
            "ability_increase": ability_increase,
            "benefits": sections,
        }
        feats.append(feat)
        inventory.append(
            {
                "local_key": feat["local_key"],
                "name": name,
                "category": category,
                "source_locator": f"SRD 5.2.1 p. {source_lines[start][1]}",
                "conversion_status": "reviewed",
            }
        )
    return feats, inventory


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "dataset_id": "srd-5-2-1",
        "title": "D&D System Reference Document 5.2.1 — Spells and Feats",
        "version": "5.2.1",
        "ruleset": "D&D 5.5e / 2024 rules",
        "language": "en",
        "license_identifier": "CC-BY-4.0",
        "attribution": (
            "This work includes material from the System Reference Document 5.2.1 (SRD 5.2.1) "
            "by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. "
            "The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International "
            "License, available at https://creativecommons.org/licenses/by/4.0/legalcode."
        ),
        "origin_url": "https://www.dndbeyond.com/srd",
        "sources": [
            {
                "key": SOURCE_KEY,
                "title": "System Reference Document 5.2.1",
                "edition": "2024 rules",
                "citation": "System Reference Document 5.2.1, 2025",
            }
        ],
        "dependencies": [],
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    text = subprocess.run(
        ["pdftotext", "-raw", str(args.pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    spells, spell_inventory = _spell_records(text)
    feats, feat_inventory = _feat_records(text)
    args.output.mkdir(parents=True, exist_ok=True)
    _write_json(args.output / "manifest.json", _manifest())
    _write_json(args.output / "items.json", {"properties": [], "items": []})
    _write_json(args.output / "spells.json", sorted(spells, key=lambda item: item["local_key"]))
    _write_json(args.output / "feats.json", sorted(feats, key=lambda item: item["local_key"]))
    _write_json(args.output / "classes.json", [])
    inventory = args.output / "inventory"
    inventory.mkdir(exist_ok=True)
    _write_json(
        inventory / "spells.json",
        sorted(spell_inventory, key=lambda item: item["local_key"]),
    )
    _write_json(
        inventory / "feats.json",
        sorted(feat_inventory, key=lambda item: item["local_key"]),
    )


if __name__ == "__main__":
    main()
