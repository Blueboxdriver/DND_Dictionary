"""Offline, fail-closed 5etools snapshot converter. See docs/milestone-9-10.md."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from dndref.importer import load_dataset
from dndref.models import DatasetPack, validate_dataset
from dndref.models.character_builder import (
    CharacterBuilderCatalog,
)

DATASET_ID = "official-5etools-2024"
UPSTREAM = "https://github.com/5etools-mirror-3/5etools-src"
# Reviewed publisher/source identities, NOT a publication-year heuristic.
BOOKS = {
    "XPHB": "Player's Handbook (2024)",
    "XDMG": "Dungeon Master's Guide (2024)",
    "XMM": "Monster Manual (2025)",
    "FRHoF": "Forgotten Realms: Heroes of Faerûn",
    "FRAiF": "Forgotten Realms: Adventures in Faerûn",
    "EFA": "Eberron: Forge of the Artificer",
    "RHW": "Ravenloft: The Horrors Within",
    "AU": "Arcana Unleashed",
}
# Source codes are the upstream identities verified against data/books.json.
# This table assigns rule generation explicitly; publication dates and titles
# do not determine edition.
SOURCE_EDITIONS = {
    "PHB": "2014",
    "DMG": "2014",
    "MM": "2014",
    "XPHB": "2024",
    "XDMG": "2024",
    "XMM": "2024",
    "FRHoF": "2024",
    "FRAiF": "2024",
    "EFA": "2024",
    "RHW": "2024",
    "AU": "2024",
}
ABILITIES = dict(
    zip(
        ("str", "dex", "con", "int", "wis", "cha"),
        ("Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"),
    )
)
SCHOOLS = dict(
    zip(
        "ACDEVIN T".replace(" ", ""),
        (
            "abjuration",
            "conjuration",
            "divination",
            "enchantment",
            "evocation",
            "illusion",
            "necromancy",
            "transmutation",
        ),
    )
)
DAMAGE = {
    "B": "Bludgeoning",
    "P": "Piercing",
    "S": "Slashing",
    "A": "Acid",
    "F": "Fire",
    "C": "Cold",
    "L": "Lightning",
    "N": "Necrotic",
    "I": "Poison",
    "R": "Radiant",
    "T": "Thunder",
    "Y": "Psychic",
    "O": "Force",
}
FEAT_CATEGORIES = {
    "O": "Origin",
    "G": "General",
    "FS": "Fighting Style",
    "EB": "Epic Boon",
    "D": "Dragonmark",
    "DG": "Dark Gift",
    "FS:P": "Fighting Style Replacement (Paladin)",
    "FS:R": "Fighting Style Replacement (Ranger)",
}
OUT_OF_SCOPE_ITEM_TYPES = {"AIR", "SHP", "VEH", "MNT"}
# These two XPHB foci still use a pre-migration sourceless property UID.
# Explicit, reviewed repairs; never globally reinterpret legacy property sources.
PROPERTY_OVERRIDES = {("staff|xphb", "V"): "V|XPHB", ("wooden staff|xphb", "V"): "V|XPHB"}
RULE_GLOSSARY_NAMES = {
    "Ability Check",
    "Advantage",
    "Attack Roll",
    "Cover",
    "D20 Test",
    "Death Saving Throw",
    "Difficult Terrain",
    "Disadvantage",
    "Grappling",
    "Long Rest",
    "Saving Throw",
    "Short Rest",
    "Spell",
    "Spellcasting Focus",
    "Dash",
    "Dodge",
    "Help",
    "Hide",
    "Opportunity Attack",
    "Ready",
    "Shove",
}
STATUS_GLOSSARY_NAMES = {"Bloodied", "Concentration"}
MASTERY_GLOSSARY_NAMES = {
    "Cleave",
    "Graze",
    "Nick",
    "Push",
    "Sap",
    "Slow",
    "Topple",
    "Vex",
}
RULE_TYPE_LABELS = {
    "C": "Core Rules",
    "V": "Variant Rules",
    "O": "Optional Rules",
    "VO": "Optional Variant Rules",
}


def source_editions(codes):
    """Return explicit edition assignments and deterministic unmapped warnings."""

    editions = {}
    warnings = []
    for code in sorted(codes):
        edition = SOURCE_EDITIONS.get(code)
        if edition is None:
            warnings.append(
                {
                    "source_code": code,
                    "message": "No edition mapping; source edition left unset.",
                }
            )
        else:
            editions[code] = edition
    return editions, warnings


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def slug(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower().replace("'", "")).strip("-")


def uid(record, category=""):
    if category in {"classFeature", "subclassFeature"}:
        parts = [record["name"], record["className"], record.get("classSource", "PHB")]
        if category == "subclassFeature":
            parts += [record["subclassShortName"], record.get("subclassSource", "PHB")]
        parts += [str(record["level"]), record["source"]]
        return "|".join(parts).lower()
    if category == "subclass":
        return "|".join(
            [
                record["name"],
                record["className"],
                record.get("classSource", "PHB"),
                record["source"],
            ]
        ).lower()
    return f"{record.get('abbreviation', record.get('name'))}|{record['source']}".lower()


def local_key(category, record):
    identity = uid(record, category)
    # A hash suffix avoids punctuation collisions without changing old keys when a
    # new colliding record arrives. It hashes identity, never text or array position.
    return (
        f"{category.lower()}/{slug(record['source'])}/"
        f"{slug(record.get('name', record.get('abbreviation')))[:100]}-"
        f"{hashlib.sha256(identity.encode()).hexdigest()[:10]}"
    )


def exclusion(record, allowed=BOOKS):
    source = record.get("source", "")
    if record.get("_isBrew") or record.get("isHomebrew") or source.startswith("UA"):
        return "homebrew-or-playtest"
    if source not in allowed:
        return "source-not-allowlisted"
    if record.get("edition") not in (None, "one") or record.get("isLegacy"):
        return "legacy-edition"
    if record.get("isReprinted"):
        return "upstream-marked-reprinted"
    return None


def normalized_ref(value, category):
    parts = value.lower().split("|")
    if category == "classFeature":
        parts += [""] * (5 - len(parts))
        parts[2] = parts[2] or "phb"
        parts[4] = parts[4] or parts[2]
    elif category == "subclassFeature":
        parts += [""] * (7 - len(parts))
        parts[2] = parts[2] or "phb"
        parts[4] = parts[4] or "phb"
        parts[6] = parts[6] or parts[4]
    elif len(parts) == 1:
        parts += ["phb"]
    return "|".join(parts)


class Snapshot:
    def __init__(self, root):
        self.root = Path(root)
        self.hashes = {}
        self.records = defaultdict(list)
        self.index = {}
        self.excluded = []
        self.inventory = defaultdict(list)
        self.used = set()
        self.embedded = set()
        self.stable_keys = {}
        self.version = self.read("package.json")["version"]
        self.books = {b["id"]: b for b in self.read("data/books.json")["book"]}
        for code, title in BOOKS.items():
            book = self.books[code]
            if book["name"] != title or book.get("author") != "Wizards RPG Team":
                raise ValueError(f"Source definition changed: review {code}")
        self.source_editions, self.edition_warnings = source_editions(BOOKS)
        paths = [
            "data/items.json",
            "data/items-base.json",
            "data/magicvariants.json",
            "data/feats.json",
            "data/optionalfeatures.json",
            "data/races.json",
            "data/backgrounds.json",
            "data/fluff-races.json",
            "data/fluff-backgrounds.json",
            "data/conditionsdiseases.json",
            "data/variantrules.json",
            "data/actions.json",
        ]
        for directory in ("class", "spells"):
            index = self.read(f"data/{directory}/index.json")
            paths += [f"data/{directory}/{name}" for name in sorted(set(index.values()))]
        paths += [
            f"data/class/{p.name}"
            for p in sorted((self.root / "data/class").glob("fluff-class-*.json"))
        ]
        for path in sorted(set(paths)):
            if (
                path
                in {
                    "data/conditionsdiseases.json",
                    "data/variantrules.json",
                    "data/actions.json",
                    "data/races.json",
                    "data/backgrounds.json",
                    "data/fluff-races.json",
                    "data/fluff-backgrounds.json",
                }
                and not (self.root / path).is_file()
            ):
                continue
            for category, records in self.read(path).items():
                if not isinstance(records, list):
                    continue
                if path == "data/races.json" and category != "race":
                    continue
                if path == "data/backgrounds.json" and category != "background":
                    continue
                for original in records:
                    if not isinstance(original, dict):
                        continue
                    r = copy.deepcopy(original)
                    if category == "magicvariant" and "source" not in r:
                        r["source"] = r.get("inherits", {}).get("source")
                    if not r.get("source"):
                        continue
                    r["_file"] = path
                    self.records[category].append(r)
                    identity = (category, uid(r, category))
                    if identity in self.index:
                        raise ValueError(f"Duplicate upstream identity: {identity}")
                    self.index[identity] = r
        self.spell_lookup = self.read("data/generated/gendata-spell-source-lookup.json")
        book = self.read("data/book/book-xphb.json")

        def find(value, key, target):
            found = []
            if isinstance(value, list):
                for v in value:
                    found += find(v, key, target)
            elif isinstance(value, dict):
                if value.get(key) == target:
                    found.append(value)
                for v in value.values():
                    found += find(v, key, target)
            return found

        (advancement,) = find(book, "caption", "Character Advancement")
        self.proficiency = {int(row[0]): row[2] for row in advancement["rows"]}
        (self.multiclass_rules,) = find(book, "name", "Multiclassing")

    def read(self, path):
        raw = (self.root / path).read_bytes()
        self.hashes[path] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    def resolve(self, category, identity, stack=()):
        identity = normalized_ref(identity, category)
        token = (category, identity)
        if token in stack:
            raise ValueError(f"Cyclic upstream copy/reference: {token}")
        r = copy.deepcopy(self.index[token])
        if "_copy" not in r:
            return r
        specification = r.pop("_copy")
        base = self.resolve(category, uid(specification), (*stack, token))
        # Upstream copy metadata is not inherited by default.
        for key in (
            "page",
            "srd",
            "srd52",
            "basicRules",
            "basicRules2024",
            "reprintedAs",
            "isReprinted",
            "otherSources",
            "hasFluff",
            "hasFluffImages",
            "referenceSources",
            "lootTables",
            "tier",
            "_versions",
        ):
            preserve = specification.get("_preserve", {})
            if not (preserve.get(key) or preserve.get("*")):
                base.pop(key, None)
        base.update(r)
        for field, mods in specification.get("_mod", {}).items():
            target = base
            path = field.split(".")
            for segment in path[:-1]:
                target = target.setdefault(segment, {})
            field = path[-1]
            for mod in mods if isinstance(mods, list) else [mods]:
                if mod == "remove":
                    target.pop(field, None)
                    continue
                mode = mod["mode"]
                values = mod.get("items", [])
                values = values if isinstance(values, list) else [values]
                if mode in {"appendArr", "appendIfNotExistsArr", "prependArr"}:
                    old = target.setdefault(field, [])
                    if mode == "appendIfNotExistsArr":
                        values = [v for v in values if v not in old]
                    target[field] = values + old if mode == "prependArr" else old + values
                elif mode == "setProp":
                    target[field] = mod["value"]
                elif mode == "replaceTxt":
                    flags = re.I if "i" in mod.get("flags", "") else 0

                    def replace(value):
                        if isinstance(value, str):
                            return re.sub(mod["replace"], mod["with"], value, flags=flags)
                        if isinstance(value, list):
                            return [replace(v) for v in value]
                        if isinstance(value, dict):
                            return {k: replace(v) for k, v in value.items()}
                        return value

                    target[field] = replace(target[field])
                else:
                    raise ValueError(f"Unsupported copy modification {mode}: {token}")
        return base

    def select(self, category):
        result = []
        for original in sorted(self.records[category], key=lambda r: uid(r, category)):
            r = (
                self.resolve(category, uid(original, category))
                if not exclusion(original)
                else original
            )
            reason = exclusion(r)
            if not reason:
                for replacement in r.get("reprintedAs", []):
                    target = replacement if isinstance(replacement, str) else replacement["uid"]
                    target_record = self.index.get((category, normalized_ref(target, category)))
                    if target_record and not exclusion(target_record):
                        reason = f"replaced-by:{target}"
                        break
            if reason:
                self.reject(category, r, reason)
            else:
                result.append(r)
        return result

    def reject(self, category, r, reason):
        self.excluded.append(
            {
                "category": category,
                "upstream_identity": uid(r, category),
                "name": r.get("name", r.get("abbreviation")),
                "source": r["source"],
                "conversion_status": "excluded",
                "reason": reason,
            }
        )

    def track(self, category, r, key, owner=None, status="converted"):
        self.stable_keys[(category, uid(r, category))] = key
        token = (category, uid(r, category), owner)
        if token in self.used:
            return
        self.used.add(token)
        row = {
            "stable_key": key,
            "name": r.get("name", r.get("abbreviation")),
            "source": r["source"],
            "upstream_identity": uid(r, category),
            "upstream_file": r["_file"],
            "upstream_page": r.get("page"),
            "normalized_sha256": digest({k: v for k, v in r.items() if not k.startswith("_")}),
            "conversion_status": status,
        }
        if owner:
            row["owner"] = owner
        self.inventory[category].append(row)


def inline(value):
    """Decode display tags, including nested tags, without HTML rendering."""
    pattern = r"\{@(\w+) ([^{}]*)}"

    def replace(match):
        tag, payload = match.groups()
        parts = payload.split("|")
        if tag in {"scaledice", "scaledamage"}:
            return parts[2]
        if tag == "dc":
            return "DC " + parts[0]
        if tag == "hit":
            return ("+" if not parts[0].startswith("-") else "") + parts[0]
        if tag == "chance":
            return parts[1] if len(parts) > 1 else parts[0] + " percent"
        if tag in {"b", "bold"}:
            return "**" + parts[0] + "**"
        if tag in {"i", "italic"}:
            return "*" + parts[0] + "*"
        if tag in {"filter", "book", "5etools", "link", "note", "area", "tip"}:
            return parts[0]
        if tag in {"dice", "damage", "d20"}:
            return parts[1] if len(parts) > 1 and parts[1] else parts[0]
        if tag in {"classFeature", "subclassFeature", "class", "subclass"}:
            display_index = {"classFeature": 5, "subclassFeature": 7, "class": 2, "subclass": 4}[
                tag
            ]
            return (
                parts[display_index]
                if len(parts) > display_index and parts[display_index]
                else parts[0]
            )
        if tag in {
            "spell",
            "condition",
            "variantrule",
            "item",
            "action",
            "creature",
            "skill",
            "feat",
            "status",
            "sense",
            "table",
            "hazard",
            "deity",
            "race",
            "object",
            "itemProperty",
            "itemMastery",
            "optfeature",
            "reward",
            "background",
        }:
            return parts[2] if len(parts) > 2 and parts[2] else parts[0]
        raise ValueError(f"Unsupported inline tag: {tag}")

    while re.search(pattern, value):
        value = re.sub(pattern, replace, value)
    if "{@" in value:
        raise ValueError(f"Unparsed inline tag: {value[:160]}")
    return value


class Text:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.reference_targets = {}

    def references(self, record):
        """Retain exact 5etools condition/rule/action tags as stable local links."""
        values = []

        def collect(value):
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
            elif isinstance(value, dict):
                for child in value.values():
                    collect(child)

        collect(record)
        found = {}
        pattern = re.compile(r"\{@(condition|variantrule|action|status|itemMastery) ([^{}]+)}")
        for value in values:
            for tag, payload in pattern.findall(value):
                parts = payload.split("|")
                source = parts[1] if len(parts) > 1 else ""
                target = self.reference_targets.get((tag, source.casefold(), parts[0].casefold()))
                if target is not None:
                    content_type, target_key = target
                    if tag in {"status", "itemMastery"}:
                        content_type = "rule"
                    found[(content_type, target_key)] = {
                        "content_type": content_type,
                        "target_key": target_key,
                    }
        return [found[key] for key in sorted(found)]

    def render(self, value, context=None, stack=()):
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):

            def item_entry(match):
                r = self.snapshot.resolve("itemEntry", match[1])
                if exclusion(r):
                    raise ValueError(f"Unapproved item entry {match[1]}")
                return self.render(r["entriesTemplate"], context, stack)

            value = re.sub(r"\{#itemEntry ([^}]+)}", item_entry, value)

            def template(match):
                expression = match[1]
                field = expression.removeprefix("getFullImmRes ").removeprefix("item.")
                if context is None or field not in context:
                    raise ValueError(f"Missing template input: {expression}")
                v = context[field]
                return ", ".join(str(s) for s in v) if isinstance(v, list) else str(v)

            value = re.sub(r"\{\{([^{}]+)}}", template, value)
            value = re.sub(r"\{=([^{}]+)}", template, value)
            return inline(value)
        if isinstance(value, list):
            return "\n\n".join(filter(None, (self.render(v, context, stack) for v in value)))
        kind = value.get("type", "entries")
        if kind.startswith("ref"):
            category = kind[3:4].lower() + kind[4:]
            field = category if category != "optionalfeature" else "optionalfeature"
            identity = normalized_ref(value[field], category)
            token = (category, identity)
            if token in stack:
                raise ValueError(f"Reference cycle: {token}")
            r = self.snapshot.resolve(category, identity)
            if exclusion(r):
                raise ValueError(f"Reference to excluded rules: {token}")
            self.snapshot.embedded.add(token)
            body = self.render(r.get("entries", []), r, (*stack, token))
            prereq = prerequisite(r.get("prerequisite", []))
            if prereq:
                body = f"Prerequisite: {prereq}\n\n{body}"
            return f"### {r['name']}\n\n{body}"
        if kind in {"entries", "section", "inset", "insetReadaloud", "options", "item"}:
            body = self.render(value.get("entries", value.get("entry", [])), context, stack)
            if value.get("name"):
                return f"**{inline(value['name'])}.** {body}"
            return body
        if kind == "list":
            return "\n".join("- " + self.render(v, context, stack) for v in value.get("items", []))
        if kind == "table":
            labels = [self.render(x, context, stack) for x in value.get("colLabels", [])]
            rows = value.get("rows", [])
            if not labels and rows:
                labels = [""] * len(rows[0])

            def cell(v):
                if isinstance(v, dict) and "roll" in v:
                    roll = v["roll"]
                    return (
                        str(roll["exact"])
                        if "exact" in roll
                        else (f"{roll.get('min', '')}–{roll.get('max', '')}")
                    )
                return self.render(v, context, stack).replace("\n", " ").replace("|", "\\|")

            lines = [
                "| " + " | ".join(labels) + " |",
                "| " + " | ".join("---" for _ in labels) + " |",
            ]
            for row in rows:
                cells = row.get("row", []) if isinstance(row, dict) else row
                lines.append("| " + " | ".join(cell(v) for v in cells) + " |")
            body = "\n".join(lines)
            if value.get("caption"):
                body = f"**{inline(value['caption'])}**\n\n{body}"
            return body + (
                "\n\n" + self.render(value["footnotes"], context, stack)
                if value.get("footnotes")
                else ""
            )
        if kind == "dice":
            return "+".join(f"{d['number']}d{d['faces']}" for d in value["toRoll"])
        if kind == "bonus":
            return f"{value['value']:+}"
        if kind == "bonusSpeed":
            return f"{value['value']:+} ft."
        if kind == "statblock":
            # Creature/statblock categories are explicitly out of scope.
            return f"Referenced {value.get('tag', 'statblock')}: {value.get('name', '')}"
        raise ValueError(f"Unsupported user-visible entry type: {kind}: {value}")


def readable(value):
    if isinstance(value, str):
        return inline(value.split("|")[0])
    if isinstance(value, list):
        return ", ".join(readable(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{ABILITIES.get(k, k)}: {readable(v)}" for k, v in value.items())
    return str(value)


def prerequisite(values):
    alternatives = []
    for obj in values:
        parts = []
        for key, value in obj.items():
            if key == "level":
                if isinstance(value, dict):
                    parts.append(
                        f"Level {value['level']}+ {value.get('class', {}).get('name', '')}"
                    )
                else:
                    parts.append(f"Level {value}+")
            elif key == "ability":
                parts.append(
                    " or ".join(
                        " and ".join(f"{ABILITIES[a]} {n}+" for a, n in option.items())
                        for option in value
                    )
                )
            elif key == "spellcasting2020":
                parts.append("Spellcasting or Pact Magic feature")
            elif key in {"spellcasting", "spellcastingFeature"}:
                parts.append("Spellcasting feature")
            elif key == "otherSummary":
                parts.append(inline(value["entry"]))
            elif key == "spell":
                parts.append(
                    "Spell: "
                    + " or ".join(
                        inline(v["entry"]) if isinstance(v, dict) else readable(v) for v in value
                    )
                )
            elif key in {"featCategory", "exclusiveFeatCategory"}:
                names = ", ".join(FEAT_CATEGORIES[v] for v in value)
                parts.append(
                    ("No other " if key == "exclusiveFeatCategory" else "Has a ") + names + " feat"
                )
            elif key in {
                "feat",
                "feature",
                "campaign",
                "proficiency",
                "other",
                "optionalfeature",
                "pact",
            }:
                display = (
                    " or ".join(readable(v) for v in value)
                    if isinstance(value, list)
                    else readable(value)
                )
                parts.append(f"{key.capitalize()}: {display}")
            else:
                raise ValueError(f"Unsupported prerequisite: {key}={value}")
        alternatives.append("; ".join(parts))
    return " OR ".join(f"({v})" if len(alternatives) > 1 else v for v in alternatives)


def ability_increase(choices):
    options = []
    for obj in choices:
        if obj.get("hidden"):
            continue  # Text is already supplied verbatim in entries.
        maximum = obj.get("max", 20)
        if "choose" in obj:
            choice = obj["choose"]
            if "entry" in choice:
                options.append(inline(choice["entry"]))
            else:
                names = ", ".join(ABILITIES[a] for a in choice["from"])
                options.append(
                    f"Increase {choice.get('count', 1)} of {names} by "
                    f"{choice.get('amount', 1)}, to a maximum of {maximum}."
                )
        else:
            options.append(
                " ".join(
                    f"Increase {ABILITIES[a]} by {v}, to a maximum of {maximum}."
                    for a, v in obj.items()
                    if a in ABILITIES
                )
            )
    return " OR ".join(options) or None


def base_entry(category, r, text):
    return {
        "local_key": local_key(category, r),
        "name": r["name"],
        "source": r["source"],
        "description": text.render(r.get("entries", []), r),
        "references": text.references(r),
    }


def quantity_unit(number, unit):
    unit = {"bonus": "bonus action"}.get(unit, unit)
    return f"{number} {unit}{'s' if number != 1 else ''}"


def convert_spell(r, text, class_keys):
    entry = base_entry("spell", r, text)
    components = r["components"]
    material = components.get("m")
    entry.update(
        level=r["level"],
        school=SCHOOLS[r["school"]],
        components={
            "verbal": bool(components.get("v")),
            "somatic": bool(components.get("s")),
            "material": bool(material),
            "material_description": (
                inline(material["text"] if isinstance(material, dict) else material)
                if material
                else None
            ),
        },
        casting_time=" or ".join(
            quantity_unit(t["number"], t["unit"])
            + (f", {inline(t['condition'])}" if t.get("condition") else "")
            for t in r["time"]
        ),
        concentration=any(d.get("concentration") for d in r["duration"]),
        ritual=bool(r.get("meta", {}).get("ritual")),
        higher_level_effects=text.render(r.get("entriesHigherLevel")) or None,
    )
    distance = r["range"].get("distance", {})
    distance_text = (
        f"{distance['amount']} {distance['type']}"
        if "amount" in distance
        else distance.get("type", r["range"]["type"]).capitalize()
    )
    entry["range"] = (
        distance_text
        if r["range"]["type"] in {"point", "special"}
        else f"Self ({distance_text} {r['range']['type']})"
    )
    durations = []
    for d in r["duration"]:
        if d["type"] == "timed":
            duration = d["duration"]
            durations.append(
                ("Up to " if d.get("concentration") or duration.get("upTo") else "")
                + quantity_unit(duration["amount"], duration["type"])
            )
        elif d["type"] == "permanent":
            durations.append(
                "Until "
                + " or ".join(
                    {"dispel": "dispelled", "trigger": "triggered", "discharge": "discharged"}[v]
                    for v in d["ends"]
                )
            )
        else:
            durations.append({"instant": "Instantaneous", "special": "Special"}[d["type"]])
    entry["duration"] = " or ".join(durations)
    lookup = text.snapshot.spell_lookup[r["source"].lower()][r["name"].lower()]
    entry["class_references"] = sorted(
        {
            class_keys[(source.lower(), name.lower())]
            for source, names in lookup.get("class", {}).items()
            for name in names
            if (source.lower(), name.lower()) in class_keys
        }
    )
    return entry


def convert_feat(r, text):
    entry = base_entry("feat", r, text)
    increase = ability_increase(r.get("ability", []))
    if not increase and r.get("ability"):
        increase = next(
            (
                inline(v)
                for v in r.get("entries", [])
                if isinstance(v, str) and "ability score" in v.lower()
            ),
            None,
        )
    levels = [v["level"] for v in r.get("prerequisite", []) if isinstance(v.get("level"), int)]
    entry.update(
        category=FEAT_CATEGORIES[r["category"]],
        prerequisite=prerequisite(r.get("prerequisite", [])) or None,
        minimum_level=min(levels) if levels else None,
        repeatable=bool(r.get("repeatable")),
        ability_increase=increase,
    )
    benefits = []
    if increase:
        benefits.append(
            {
                "key": "ability-score-increase",
                "heading": "Ability Score Increase",
                "body": increase,
                "display_order": 0,
            }
        )
    for v in r.get("entries", []):
        if isinstance(v, dict) and v.get("name"):
            benefits.append(
                {
                    "key": slug(v["name"]),
                    "heading": inline(v["name"]),
                    "body": text.render(v.get("entries", v.get("entry")), r),
                    "display_order": len(benefits),
                }
            )
    entry["benefits"] = benefits
    return entry


def progression(r, text):
    columns = [{"key": "proficiency-bonus", "label": "Proficiency Bonus", "display_order": 0}]
    rows = {i: {"proficiency-bonus": text.snapshot.proficiency[i]} for i in range(1, 21)}
    for group in r.get("classTableGroups", []):
        labels = group["colLabels"]
        values = group.get("rows", group.get("rowsSpellProgression"))
        if len(values) != 20 or any(len(row) != len(labels) for row in values):
            raise ValueError(f"Invalid progression shape: {uid(r)}")
        for i, label in enumerate(labels):
            label = text.render(label)
            title = text.render(group.get("title"))
            key = slug(f"{title}-{label}")
            columns.append(
                {
                    "key": key,
                    "label": f"{title}: {label}" if title else label,
                    "display_order": len(columns),
                }
            )
            for level, row in enumerate(values, 1):
                rows[level][key] = text.render(row[i])
    return {
        "columns": columns,
        "levels": [{"level": level, "values": values} for level, values in rows.items()],
    }


def proficiencies(r, text):
    def skills(options):
        result = []
        for option in options:
            if "choose" in option:
                choice = option["choose"]
                result.append(f"Choose {choice.get('count', 1)}: " + ", ".join(choice["from"]))
            elif "any" in option:
                result.append(f"Choose any {option['any']} skills")
            else:
                result.append(readable(option))
        return "; ".join(result) or "None"

    return {
        "skill_choices": skills(r.get("skills", [])),
        "weapon_proficiencies": text.render(r.get("weapons", [])) or "None",
        "armor_proficiencies": text.render(r.get("armor", [])) or "None",
        "tool_proficiencies": text.render(r.get("tools", [])) or "None",
    }


def convert_features(refs, category, owner, text, owner_record):
    features = []
    visited = set()

    def add(reference):
        reference = reference[category] if isinstance(reference, dict) else reference
        identity = normalized_ref(reference, category)
        if identity in visited:
            return
        visited.add(identity)
        r = text.snapshot.resolve(category, identity)
        if exclusion(r):
            raise ValueError(f"Excluded feature required by selected owner: {identity}")
        expected_class = (
            owner_record["name"] if category == "classFeature" else owner_record["className"]
        )
        expected_source = (
            owner_record["source"] if category == "classFeature" else owner_record["classSource"]
        )
        if (r["className"].lower(), r.get("classSource", "PHB").lower()) != (
            expected_class.lower(),
            expected_source.lower(),
        ):
            raise ValueError(f"Feature has wrong class owner: {identity}")
        if category == "subclassFeature" and (
            r["subclassShortName"].lower(),
            r.get("subclassSource", "PHB").lower(),
        ) != (
            owner_record.get("shortName", owner_record["name"]).lower(),
            owner_record["source"].lower(),
        ):
            raise ValueError(f"Feature has wrong subclass owner: {identity}")
        children = []

        def extract(value):
            if isinstance(value, list):
                return [v for item in value if (v := extract(item)) is not None]
            if isinstance(value, dict):
                if value.get("type") == "ref" + category[0].upper() + category[1:]:
                    children.append(value[category])
                    return None
                return {k: extract(v) for k, v in value.items()}
            return value

        body = text.render(extract(r.get("entries", [])), r)
        # Level-only pointers are meaningful headings; do not invent rules.
        body = body or r["name"]
        key = local_key(category, r)
        features.append(
            {
                "feature_key": key,
                "level": r["level"],
                "title": r["name"],
                "description": body,
                "source": r["source"],
                "display_order": len(features),
            }
        )
        text.snapshot.track(category, r, key, owner)
        for child in children:
            add(child)

    for reference in refs:
        add(reference)
    return features


def optional_sections(r, text):
    sections = []
    for group in r.get("optionalfeatureProgression", []):
        types = set(group["featureType"])
        options = [
            v
            for v in text.snapshot.records["optionalfeature"]
            if not exclusion(v)
            and v["source"] == r["source"]
            and types.intersection(v.get("featureType", []))
        ]
        bodies = []
        for option in sorted(options, key=uid):
            text.snapshot.embedded.add(("optionalfeature", uid(option)))
            body = text.render(option.get("entries"), option)
            prereq = prerequisite(option.get("prerequisite", []))
            bodies.append(
                f"### {option['name']}\n\n"
                + (f"Prerequisite: {prereq}\n\n" if prereq else "")
                + body
            )
        if bodies:
            sections.append(
                {
                    "key": "options/" + slug(group["name"]),
                    "heading": group["name"],
                    "body": "\n\n".join(bodies),
                    "display_order": len(sections),
                }
            )
    return sections


def convert_class(r, subclasses, text):
    entry = base_entry("class", r, text)
    fluff = text.snapshot.index.get(("classFluff", uid(r)))
    entry["description"] = text.render(fluff.get("entries"), r) if fluff else entry["description"]
    entry.update(
        hit_die=r["hd"]["faces"],
        primary_ability=" or ".join(
            " and ".join(ABILITIES[a] for a in option) for option in r["primaryAbility"]
        ),
        saving_throw_proficiencies=", ".join(ABILITIES[a] for a in r["proficiency"]),
        spellcasting_ability=ABILITIES.get(r.get("spellcastingAbility")),
        starting_equipment=text.render(
            r["startingEquipment"].get("entries", r["startingEquipment"].get("default", []))
        ),
        progression=progression(r, text),
        features=convert_features(r["classFeatures"], "classFeature", entry["local_key"], text, r),
        subclasses=[],
        sections=optional_sections(r, text),
    )
    entry.update(proficiencies(r["startingProficiencies"], text))
    multi = r.get("multiclassing", {})
    parts = []
    if multi.get("requirements"):
        parts.append("Requirements: " + readable(multi["requirements"]))
    if multi.get("entries"):
        parts.append(text.render(multi["entries"]))
    gained = multi.get("proficienciesGained", {})
    for key, value in proficiencies(gained, text).items():
        if value != "None":
            parts.append(f"{key.replace('_', ' ').capitalize()}: {value}")
    # Prefer exact multiclass prose if a future snapshot supplies it in class fluff.
    if fluff:

        def find_multi(value):
            found = []
            if isinstance(value, list):
                for v in value:
                    found += find_multi(v)
            elif isinstance(value, dict):
                if "multiclass" in value.get("name", "").lower():
                    found.append(text.render(value))
                else:
                    found += find_multi(value.get("entries", []))
            return found

        parts = find_multi(fluff.get("entries", [])) or parts
    entry["multiclassing"] = "\n\n".join(parts) or "No additional proficiencies."
    entry["multiclassing"] += (
        "\n\n### Multiclass Rules (Player's Handbook 2024, p. 44)\n\n"
        + text.render(text.snapshot.multiclass_rules)
    )
    for sub in subclasses:
        key = local_key("subclass", sub)
        features = convert_features(sub["subclassFeatures"], "subclassFeature", key, text, sub)
        intro = text.render(sub.get("entries", []), sub)
        if not intro and features:
            intro = features[0]["description"]
        sections = optional_sections(sub, text)
        if sections:
            intro += "\n\n" + "\n\n".join(f"### {s['heading']}\n\n{s['body']}" for s in sections)
        # Subclass tables are display text because the schema's progression belongs to the class.
        for group in sub.get("subclassTableGroups", []):
            rows = group.get("rows", group.get("rowsSpellProgression", []))
            intro += "\n\n" + text.render(
                {
                    "type": "table",
                    "caption": group.get("title", ""),
                    "colLabels": ["Level", *group["colLabels"]],
                    "rows": [[i, *row] for i, row in enumerate(rows, 1)],
                }
            )
        entry["subclasses"].append(
            {
                "subclass_key": key,
                "name": sub["name"],
                "source": sub["source"],
                "introduction": intro,
                "features": features,
            }
        )
        text.snapshot.track("subclass", sub, key, entry["local_key"])
    return entry


def item_type(r, snapshot):
    if r.get("type"):
        return snapshot.resolve("itemType", r["type"])
    return {
        "name": "Wondrous Item" if r.get("wondrous") else "Staff" if r.get("staff") else "Other",
        "entries": [],
    }


def mastery_text(value):
    if isinstance(value, dict):
        return value["uid"].split("|")[0] + f" ({value['note']})"
    return value.split("|")[0]


def convert_item(r, text, property_map):
    entry = base_entry("item", r, text)
    type_info = item_type(r, text.snapshot)
    type_code = r.get("type", "").split("|")[0]
    rarity = r.get("rarity", "none")
    magic = rarity not in {"none", "unknown", "unknown (magic)"} or rarity == "unknown (magic)"
    weapon = type_code in {"M", "R"} or r.get("weapon") or ("dmg1" in r and "dmgType" in r)
    armor = type_code in {"LA", "MA", "HA", "S"}
    entry.update(
        kind="magic_item" if magic else "weapon" if weapon else "armor" if armor else "mundane",
        type=type_info["name"],
        subtype=r.get("weaponCategory"),
        rarity=rarity.replace(" ", "_")
        if rarity not in {"none", "unknown", "unknown (magic)", "varies"}
        else None,
        requires_attunement=bool(r.get("reqAttune")),
        attunement_prerequisite=inline(r["reqAttune"])
        if isinstance(r.get("reqAttune"), str)
        else None,
        weight=r.get("weight"),
        weight_display=(
            f"{r['weight']} lb." + (f" ({r['weightNote']})" if r.get("weightNote") else "")
            if "weight" in r
            else None
        ),
        properties=[],
    )
    if "value" in r:
        entry.update(
            cost={"amount": str(r["value"]), "currency": "cp"},
            cost_display=f"{r['value'] / 100:g} GP",
        )
    if weapon:
        # Upstream omits reach/range on ordinary melee weapons; preserve absence as
        # a display statement instead of inventing a numeric combat value.
        entry["weapon"] = {
            "damage_expression": r["dmg1"],
            "damage_type": DAMAGE[r["dmgType"]],
            "range": f"{r['range']} ft." if "range" in r else "Melee",
            "versatile_damage": r.get("dmg2"),
            "mastery": ", ".join(mastery_text(v) for v in r.get("mastery", [])) or None,
        }
    if armor:
        ac = str(r["ac"])
        if type_code == "S":
            ac = "+" + ac
        elif type_code in {"LA", "MA"}:
            ac += " + Dexterity modifier" + (" (maximum 2)" if type_code == "MA" else "")
        if r.get("bonusAc"):
            ac += f"; item grants a {r['bonusAc']} AC bonus"
        entry["armor"] = {
            "armor_category": type_info["name"],
            "ac_expression": ac,
            "strength_requirement": r.get("strength"),
            "stealth_disadvantage": bool(r.get("stealth")),
        }
    for reference in r.get("property", []):
        note = reference.get("note") if isinstance(reference, dict) else None
        reference = reference["uid"] if isinstance(reference, dict) else reference
        reference = PROPERTY_OVERRIDES.get((r.get("_base", uid(r)), reference), reference)
        identity = normalized_ref(reference, "itemProperty")
        prop = property_map[identity]
        code = reference.split("|")[0]
        value = r.get("range") if code in {"A", "T"} else r.get("dmg2") if code == "V" else None
        entry["properties"].append({"property_key": prop["key"], "value": note or value})
    supplemental = text.render(type_info.get("entries", []), r)
    if supplemental:
        entry["description"] += "\n\n" + supplemental
    for mastery in r.get("mastery", []):
        mastery = mastery["uid"] if isinstance(mastery, dict) else mastery
        master = text.snapshot.resolve("itemMastery", mastery)
        entry["description"] += "\n\n" + text.render(master.get("entries"), r)
        mastery_name = master["name"]
        if (
            mastery_name in MASTERY_GLOSSARY_NAMES
            and master["source"] in text.snapshot.source_editions
        ):
            entry.setdefault("references", []).append(
                {
                    "content_type": "rule",
                    "target_key": local_key("rule", master),
                }
            )
    if not entry["description"].strip():
        entry["description"] = type_info["name"] + (
            f" ({r['weaponCategory']})" if r.get("weaponCategory") else ""
        )
    sections = []
    for field, heading in (
        ("charges", "Charges"),
        ("recharge", "Recharge"),
        ("rechargeAmount", "Recharge Amount"),
        ("packContents", "Contents"),
    ):
        if field in r:
            sections.append(
                {
                    "key": slug(heading),
                    "heading": heading,
                    "body": readable(r[field]),
                    "display_order": len(sections),
                }
            )
    if rarity in {"varies", "unknown (magic)"}:
        sections.append(
            {"key": "rarity", "heading": "Rarity", "body": rarity, "display_order": len(sections)}
        )
    entry["sections"] = sections
    return entry


def variant_items(variants, bases, snapshot):
    """Expand structured applicability constraints, not simulated item mechanics."""
    results = []

    def matches(base, requirements):
        return all(
            value in base.get(k, []) if isinstance(base.get(k), list) else base.get(k) == value
            for k, value in requirements.items()
        )

    for variant in variants:
        inherit = variant["inherits"]
        count = 0
        for base in bases:
            if not any(matches(base, req) for req in variant["requires"]):
                continue
            if variant.get("excludes") and matches(base, variant["excludes"]):
                continue
            r = copy.deepcopy(base)
            for field in ("value", "srd52", "basicRules2024", "reprintedAs", "isReprinted"):
                r.pop(field, None)
            r.update(copy.deepcopy(inherit))
            r["name"] = inherit.get("namePrefix", "") + base["name"] + inherit.get("nameSuffix", "")
            r["property"] = list(
                {
                    canonical(v): v
                    for v in base.get("property", []) + inherit.get("propertyAdd", [])
                }.values()
            )
            r["_file"] = variant["_file"]
            r["_variant"] = uid(variant)
            r["_base"] = uid(base)
            r["_generic_entries"] = variant.get("entries", [])
            results.append(r)
            count += 1
        snapshot.inventory["magicvariant"].append(
            {
                "upstream_identity": uid(variant),
                "source": variant["source"],
                "name": variant["name"],
                "conversion_status": "expanded",
                "generated_count": count,
            }
        )
        if not count:
            raise ValueError(f"Variant has no approved bases: {uid(variant)}")
    return results


BUILDER_SKILLS = (
    ("acrobatics", "Acrobatics", "dex"),
    ("animal-handling", "Animal Handling", "wis"),
    ("arcana", "Arcana", "int"),
    ("athletics", "Athletics", "str"),
    ("deception", "Deception", "cha"),
    ("history", "History", "int"),
    ("insight", "Insight", "wis"),
    ("intimidation", "Intimidation", "cha"),
    ("investigation", "Investigation", "int"),
    ("medicine", "Medicine", "wis"),
    ("nature", "Nature", "int"),
    ("perception", "Perception", "wis"),
    ("performance", "Performance", "cha"),
    ("persuasion", "Persuasion", "cha"),
    ("religion", "Religion", "int"),
    ("sleight-of-hand", "Sleight of Hand", "dex"),
    ("stealth", "Stealth", "dex"),
    ("survival", "Survival", "wis"),
)
SKILL_KEYS = {name.casefold(): key for key, name, _ in BUILDER_SKILLS}
ABILITY_KEYS = {key: key for key in ABILITIES}


def builder_key(prefix, *parts):
    identity = "|".join(str(part) for part in parts)
    return f"{prefix}/{slug(identity)[:72]}-{hashlib.sha256(identity.encode()).hexdigest()[:10]}"


def source_ref(snapshot, kind, category, identity, *, strip_suffix=False):
    value = str(identity).strip()
    if strip_suffix:
        value = value.split("#", 1)[0]
    normalized = normalized_ref(value, category) if "|" not in value else value.lower()
    local_key = snapshot.stable_keys.get((category, normalized))
    if local_key is None and category in {"item", "spell", "feat", "subclass"}:
        # Source keys are lower-cased by uid(); resolve casing without name matching.
        local_key = snapshot.stable_keys.get((category, value.lower()))
    return {
        "kind": kind,
        "identity": f"{DATASET_ID}:{local_key}" if local_key else value,
        "resolved": local_key is not None,
    }


def _rule_leaf(operator, **values):
    return {"operator": operator, **values}


def _rule_group(operator, children):
    children = [child for child in children if child]
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    return {"operator": operator, "children": children}


def _source_ability_options(record):
    options = []
    for alternative in record.get("primaryAbility", []):
        values = alternative.keys() if isinstance(alternative, dict) else alternative
        abilities = [str(value).lower() for value in values if str(value).lower() in ABILITY_KEYS]
        if abilities:
            options.append(abilities)
    return options or [["str", "dex", "con", "int", "wis", "cha"]]


def _minimum_ability_requirement(options, minimum):
    alternatives = [
        _rule_group(
            "all",
            [_rule_leaf("ability_score", ability=ability, minimum=minimum) for ability in option],
        )
        for option in options
    ]
    return _rule_group("any", alternatives)


def _proficiency_key(kind, value):
    raw = inline(str(value)).strip().casefold()
    if kind == "skill":
        return SKILL_KEYS.get(raw, slug(raw))
    return slug(raw)


def _grant(
    owner,
    scope,
    kind,
    value=None,
    *,
    proficiency_kind=None,
    reference=None,
    quantity=None,
    unit=None,
    unresolved=False,
):
    return {
        "grant_key": builder_key(
            "grant", owner, scope, kind, value, reference.get("identity") if reference else ""
        ),
        "kind": kind,
        "value": value,
        "reference": reference,
        "proficiency_kind": proficiency_kind,
        "quantity": quantity,
        "unit": unit,
        "source_rule": scope,
        "unresolved": unresolved,
    }


def _explicit_options(values, kind):
    return [
        {
            "option_key": builder_key("option", kind, value),
            "label": str(value).replace("-", " ").title(),
            "value": value,
        }
        for value in sorted({str(value) for value in values})
    ]


def _proficiency_choice(
    owner, scope, count, values=None, *, criteria_kind=None, criteria_values=None, filters=None
):
    options = _explicit_options(values, criteria_kind or "proficiency") if values else []
    criteria = None
    if not options:
        criteria = {
            "kind": criteria_kind or "source_filter",
            "values": criteria_values or ["all"],
            "filters": filters or {},
        }
    return {
        "choice_key": builder_key("choice", owner, scope),
        "kind": "proficiency",
        "count": max(1, int(count)),
        "options": options,
        "criteria": criteria,
        "source_rule": scope,
    }


def _normalize_proficiency_value(owner, scope, kind, value, grants, choices):
    if value is None:
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _normalize_proficiency_value(owner, f"{scope}.{index}", kind, item, grants, choices)
        return
    if isinstance(value, str):
        grants.append(
            _grant(
                owner, scope, "proficiency", _proficiency_key(kind, value), proficiency_kind=kind
            )
        )
        return
    if not isinstance(value, dict):
        return
    if "choose" in value:
        spec = value["choose"]
        if isinstance(spec, dict) and isinstance(spec.get("from"), list):
            options = [_proficiency_key(kind, option) for option in spec["from"]]
            choices.append(
                _proficiency_choice(owner, scope, spec.get("count", 1), options, criteria_kind=kind)
            )
            return
        if isinstance(spec, dict) and spec.get("fromFilter"):
            filter_value = spec["fromFilter"]
            group = (
                filter_value.get("equipmentType", "tool")
                if isinstance(filter_value, dict)
                else str(filter_value)
            )
            criteria_kind = "tool_group" if kind == "tool" else "source_filter"
            choices.append(
                _proficiency_choice(
                    owner,
                    scope,
                    spec.get("count", 1),
                    criteria_kind=criteria_kind,
                    criteria_values=[str(group)],
                    filters=filter_value if isinstance(filter_value, dict) else {},
                )
            )
            return
    if "any" in value:
        criteria_kind = {
            "skill": "skill",
            "tool": "tool_group",
            "language": "language",
            "weapon": "weapon_category",
            "armor": "item_type",
        }.get(kind, "source_filter")
        choices.append(
            _proficiency_choice(
                owner, scope, value["any"], criteria_kind=criteria_kind, criteria_values=["all"]
            )
        )
        return
    if "anyArtisansTool" in value:
        choices.append(
            _proficiency_choice(
                owner,
                scope,
                value["anyArtisansTool"],
                criteria_kind="tool_group",
                criteria_values=["artisan"],
            )
        )
        return
    if "anyMusicalInstrument" in value:
        choices.append(
            _proficiency_choice(
                owner,
                scope,
                value["anyMusicalInstrument"],
                criteria_kind="tool_group",
                criteria_values=["musical instrument"],
            )
        )
        return
    recognized = False
    for label, enabled in sorted(value.items()):
        if enabled is True or isinstance(enabled, (int, str)):
            grants.append(
                _grant(
                    owner,
                    scope,
                    "proficiency",
                    _proficiency_key(kind, label),
                    proficiency_kind=kind,
                )
            )
            recognized = True
    if not recognized and value:
        choices.append(
            _proficiency_choice(
                owner, scope, 1, criteria_kind="source_filter", criteria_values=[canonical(value)]
            )
        )


def _proficiency_fields(owner, fields):
    grants, choices = [], []
    for scope, value, kind in fields:
        _normalize_proficiency_value(owner, scope, kind, value, grants, choices)
    return grants, choices


def _source_reference_list(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _source_requirement(value, snapshot, classes_by_name):
    if not value:
        return None
    if isinstance(value, list):
        return _rule_group(
            "any", [_source_requirement(item, snapshot, classes_by_name) for item in value]
        )
    if not isinstance(value, dict):
        return _rule_leaf("unresolved", source_text=readable(value))
    children = []
    for key, raw in sorted(value.items()):
        if key == "level":
            level = raw
            class_value = None
            if isinstance(raw, dict):
                level = raw.get("level")
                class_value = raw.get("class")
            if isinstance(class_value, dict):
                class_source = str(class_value.get("source", "XPHB")).casefold()
                class_name = str(class_value.get("name", "")).casefold()
                class_key = classes_by_name.get((class_source, class_name))
                if class_key and isinstance(level, int):
                    children.append(_rule_leaf("class_level", class_key=class_key, minimum=level))
                else:
                    children.append(_rule_leaf("unresolved", source_text=canonical({key: raw})))
            elif isinstance(level, int):
                children.append(_rule_leaf("total_level", minimum=level))
            else:
                children.append(_rule_leaf("unresolved", source_text=canonical({key: raw})))
        elif key == "ability":
            alternatives = []
            for spec in raw if isinstance(raw, list) else [raw]:
                if not isinstance(spec, dict):
                    continue
                alternatives.append(
                    _rule_group(
                        "all",
                        [
                            _rule_leaf(
                                "ability_score", ability=str(ability).lower(), minimum=int(score)
                            )
                            for ability, score in spec.items()
                            if str(ability).lower() in ABILITY_KEYS and isinstance(score, int)
                        ],
                    )
                )
            if alternatives:
                children.append(_rule_group("any", alternatives))
            else:
                children.append(_rule_leaf("unresolved", source_text=canonical({key: raw})))
        elif key in {"spell", "spellcasting", "pactMagic"}:
            spellcasting_kind = "pact_magic" if key == "pactMagic" else "spellcasting"
            if key == "spell" and isinstance(raw, list):
                rendered = canonical(raw).casefold()
                if "class=warlock" in rendered or "pact" in rendered:
                    spellcasting_kind = "pact_magic"
            children.append(_rule_leaf("spellcasting", spellcasting_kind=spellcasting_kind))
        elif key == "feat":
            values = raw if isinstance(raw, list) else [raw]
            feat_children = []
            for spec in values:
                if isinstance(spec, str):
                    reference = source_ref(snapshot, "feat", "feat", spec)
                    feat_children.append(_rule_leaf("feat", feat_reference=reference))
                elif isinstance(spec, dict):
                    feat_children.append(_rule_leaf("unresolved", source_text=canonical(spec)))
            children.append(_rule_group("any", feat_children))
        elif key in {"class", "classes"}:
            values = raw if isinstance(raw, list) else [raw]
            class_children = []
            for spec in values:
                if isinstance(spec, str):
                    name, source = spec.split("|", 1) if "|" in spec else (spec, "XPHB")
                elif isinstance(spec, dict):
                    name, source = spec.get("name", ""), spec.get("source", "XPHB")
                else:
                    continue
                class_key = classes_by_name.get((str(source).casefold(), str(name).casefold()))
                if class_key:
                    class_children.append(_rule_leaf("class_membership", class_key=class_key))
                else:
                    class_children.append(_rule_leaf("unresolved", source_text=canonical(spec)))
            children.append(_rule_group("any", class_children))
        else:
            children.append(_rule_leaf("unresolved", source_text=canonical({key: raw})))
    return _rule_group("all", children) or _rule_leaf("unresolved", source_text=canonical(value))


def _ability_choices(owner, raw_ability, source_rule, *, background=False):
    grants, choices = [], []
    if not isinstance(raw_ability, list):
        raw_ability = [raw_ability] if raw_ability else []
    if background:
        # Enumerate only the ability allocations permitted by each weighted source rule.
        options = []
        seen = set()
        for raw in raw_ability:
            weighted = raw.get("choose", {}).get("weighted") if isinstance(raw, dict) else None
            if not isinstance(weighted, dict):
                continue
            abilities = [str(item).lower() for item in weighted.get("from", [])]
            weights = weighted.get("weights", [])
            if not weights or len(abilities) < len(weights):
                continue
            for allocation in itertools.permutations(abilities, len(weights)):
                increases = sorted(
                    (
                        (ability, amount)
                        for ability, amount in zip(allocation, weights)
                        if ability in ABILITY_KEYS and isinstance(amount, int)
                    ),
                )
                signature = tuple(increases)
                if len(increases) != len(weights) or signature in seen:
                    continue
                seen.add(signature)
                increases_json = [
                    {"ability": ability, "amount": amount} for ability, amount in increases
                ]
                options.append(
                    {
                        "option_key": builder_key(
                            "option", owner, "background-ability", increases_json
                        ),
                        "label": ", ".join(
                            f"+{item['amount']} {item['ability'].upper()}"
                            for item in increases_json
                        ),
                        "ability_increases": increases_json,
                    }
                )
        if options:
            choices = [
                {
                    "choice_key": builder_key("choice", owner, "background-ability-scores"),
                    "kind": "ability_score",
                    "count": 1,
                    "options": options,
                    "source_rule": source_rule,
                }
            ]
        return grants, choices

    option_rows = []
    for index, raw in enumerate(raw_ability):
        if not isinstance(raw, dict):
            continue
        choice = raw.get("choose")
        if isinstance(choice, dict):
            values = [str(value).lower() for value in choice.get("from", [])]
            count = int(choice.get("count", 1))
            amount = int(choice.get("amount", 1))
            if values and 0 < count <= len(values):
                for allocation in itertools.combinations(sorted(set(values)), count):
                    increases = [
                        {"ability": ability, "amount": amount}
                        for ability in allocation
                        if ability in ABILITY_KEYS
                    ]
                    if len(increases) == count:
                        option_rows.append(
                            {
                                "option_key": builder_key(
                                    "option", owner, "ability", index, increases
                                ),
                                "label": ", ".join(
                                    f"+{amount} {ability.upper()}" for ability in allocation
                                ),
                                "ability_increases": increases,
                            }
                        )
        else:
            for ability, amount in raw.items():
                if str(ability).lower() in ABILITY_KEYS and isinstance(amount, int) and amount > 0:
                    grants.append(
                        _grant(
                            owner,
                            source_rule,
                            "ability_score",
                            str(ability).lower(),
                            quantity=amount,
                            unit="ability_points",
                        )
                    )
    if option_rows:
        choices.append(
            {
                "choice_key": builder_key("choice", owner, "feat-ability-increase"),
                "kind": "ability_score",
                "count": 1,
                "options": option_rows,
                "source_rule": source_rule,
            }
        )
    return grants, choices


def _equipment_choice(snapshot, owner, scope, groups, *, includes_background=False):
    choices = []
    for group_index, group in enumerate(groups or []):
        if not isinstance(group, dict):
            continue
        package_choice_key = builder_key("choice", owner, scope, group_index)
        options = []
        dependent_choices = []
        for option_label, items in sorted(group.items()):
            if not isinstance(items, list):
                continue
            option_grants = []
            option_resolved = True
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("item"), str):
                    reference = source_ref(snapshot, "item", "item", item["item"])
                    option_grants.append(
                        _grant(
                            owner,
                            f"{scope}.{option_label}.{index}",
                            "equipment",
                            reference=reference,
                            quantity=item.get("quantity")
                            if isinstance(item.get("quantity"), int)
                            else None,
                            unit="item",
                            unresolved=not reference["resolved"],
                        )
                    )
                    option_resolved = option_resolved and reference["resolved"]
                elif isinstance(item.get("value"), int):
                    option_grants.append(
                        _grant(
                            owner,
                            f"{scope}.{option_label}.{index}",
                            "currency",
                            str(item["value"]),
                            quantity=item["value"],
                            unit="cp",
                        )
                    )
                elif item.get("equipmentType"):
                    equipment_type = str(item["equipmentType"])
                    is_tool = "tool" in equipment_type.casefold()
                    dependent_choices.append(
                        {
                            "choice_key": builder_key(
                                "choice",
                                owner,
                                scope,
                                group_index,
                                option_label,
                                index,
                                equipment_type,
                            ),
                            "kind": "equipment",
                            "count": item.get("quantity", 1)
                            if isinstance(item.get("quantity", 1), int)
                            else 1,
                            "criteria": {
                                "kind": "tool_group" if is_tool else "item_type",
                                "values": [
                                    "artisan"
                                    if equipment_type.casefold() == "toolartisan"
                                    else equipment_type
                                ],
                            },
                            "depends_on_choice": package_choice_key,
                            "depends_on_option": builder_key(
                                "option",
                                owner,
                                scope,
                                group_index,
                                option_label,
                            ),
                            "source_rule": f"{scope}.{option_label}.{index}",
                        }
                    )
                else:
                    option_grants.append(
                        _grant(
                            owner,
                            f"{scope}.{option_label}.{index}",
                            "other",
                            canonical(item),
                            unresolved=True,
                        )
                    )
                    option_resolved = False
            if not option_grants:
                continue
            options.append(
                {
                    "option_key": builder_key("option", owner, scope, group_index, option_label),
                    "label": str(option_label).replace("_", " ").title(),
                    "value": option_label,
                    "grants": option_grants,
                    "resolved": option_resolved,
                }
            )
        if options:
            choices.append(
                {
                    "choice_key": package_choice_key,
                    "kind": "equipment_package",
                    "count": 1,
                    "options": options,
                    "source_rule": scope,
                }
            )
            choices.extend(dependent_choices)
    return {"choices": choices, "includes_background_equipment": includes_background}


def _progression_count(values, level):
    if isinstance(values, dict):
        value = values.get(str(level), 0)
        return int(value) if isinstance(value, (str, int)) and str(value).isdigit() else 0
    if isinstance(values, list) and level <= len(values):
        value = values[level - 1]
        return int(value) if isinstance(value, (str, int)) and str(value).isdigit() else 0
    return 0


def _spellcasting_rules(
    record, owner_type, owner_key, snapshot, *, table_groups=None, classes_by_name=None
):
    progression = record.get("casterProgression")
    ability = str(record.get("spellcastingAbility", "")).lower()
    if progression not in {"full", "half", "artificer", "pact", "1/3", "third"}:
        return None
    if ability not in ABILITY_KEYS:
        return None
    contribution = {
        "full": "full",
        "half": "half_round_down",
        "artificer": "half_round_up",
        "pact": "pact_magic_separate",
        "1/3": "third_round_down",
        "third": "third_round_down",
    }[progression]
    access = (
        "spellbook"
        if record.get("spellsKnownProgressionFixed")
        else ("prepared" if record.get("preparedSpellsProgression") else "known")
    )
    if progression == "pact":
        model = "pact_magic"
    else:
        model = "spellcasting"
    if owner_type == "class":
        list_reference = {
            "kind": "class",
            "identity": f"{DATASET_ID}:{owner_key}",
            "resolved": True,
        }
    else:
        list_reference = {
            "kind": "subclass",
            "identity": f"{DATASET_ID}:{owner_key}",
            "resolved": True,
        }
    groups = table_groups if table_groups is not None else record.get("classTableGroups", [])
    standalone, pact_slots = [], []
    for group in groups:
        labels = [text_label for text_label in group.get("colLabels", [])]
        lowered = [
            re.sub(r"\{@filter ([^|]+)\|.*?}", r"\1", str(label)).casefold() for label in labels
        ]
        rows = group.get("rowsSpellProgression")
        if rows and "spell slots per spell level" in str(group.get("title", "")).casefold():
            for level, row in enumerate(rows[:20], 1):
                slots = [int(value) if str(value).isdigit() else 0 for value in row]
                standalone.append({"class_level": level, "slots_by_spell_level": slots})
        elif "spell slots" in lowered and "slot level" in lowered:
            slot_index = lowered.index("spell slots")
            spell_level_index = lowered.index("slot level")
            rows = group.get("rows", [])
            for level, row in enumerate(rows[:20], 1):
                if len(row) <= max(slot_index, spell_level_index):
                    continue
                slot_count, spell_level = row[slot_index], row[spell_level_index]
                if str(slot_count).isdigit() and str(spell_level).isdigit():
                    counts = [0] * max(1, int(spell_level))
                    counts[int(spell_level) - 1] = int(slot_count)
                    pact_slots.append({"class_level": level, "slots_by_spell_level": counts})
    cantrips = record.get("cantripProgression", [])
    prepared = record.get("preparedSpellsProgression", [])
    known = record.get("spellsKnownProgressionFixed", [])
    additional_spells, spell_choices = _spell_access_rules(
        record,
        owner_key,
        snapshot,
        classes_by_name or {},
        f"{owner_type}:{owner_key}.additional-spells",
        default_level=1 if owner_type == "class" else 3,
        level_scope="class",
    )
    return {
        "ability": ability,
        "model": model,
        "multiclass_contribution": contribution,
        "acquisition": access,
        "spell_list_reference": list_reference,
        "prepared_spells_change": record.get("preparedSpellsChange"),
        "cantrips": [
            {"class_level": level, "count": int(count)}
            for level, count in enumerate(cantrips, 1)
            if isinstance(count, int)
        ],
        "prepared_spells": [
            {"class_level": level, "count": int(count)}
            for level, count in enumerate(prepared, 1)
            if isinstance(count, int)
        ],
        "known_spells": [
            {"class_level": level, "count": int(count)}
            for level, count in enumerate(known, 1)
            if isinstance(count, int)
        ],
        "standalone_slots": standalone,
        "pact_slots": pact_slots,
        "additional_spells": additional_spells,
        "spell_choices": spell_choices,
        "source_rule": f"{owner_type}:{owner_key}.spellcasting",
    }


def _spell_access_rules(
    record,
    owner_key,
    snapshot,
    classes_by_name,
    source_rule,
    *,
    default_level=1,
    level_scope="total",
):
    """Normalize exact spells and source-defined spell-list filters without scraping prose."""
    rows = []
    choices = []
    groups = record.get("additionalSpells", [])
    if not isinstance(groups, list):
        groups = [groups]
    named_groups = [group for group in groups if isinstance(group, dict) and group.get("name")]
    shared_choice_key = None
    option_keys = {}
    if len(named_groups) > 1 and len(named_groups) == len(groups):
        shared_choice_key = builder_key("choice", owner_key, "additional-spells")
        options = []
        for group in named_groups:
            option_key = builder_key("option", owner_key, "additional-spells", group["name"])
            option_keys[group["name"]] = option_key
            options.append(
                {
                    "option_key": option_key,
                    "label": str(group["name"]),
                    "value": str(group["name"]),
                }
            )
        choices.append(
            {
                "choice_key": shared_choice_key,
                "kind": "spell",
                "count": 1,
                "options": options,
                "source_rule": source_rule,
            }
        )
    access_names = {
        "known": "known",
        "prepared": "prepared",
        "innate": "innate",
        "expanded": "expanded",
        "spellbook": "spellbook",
    }

    def spell_filter_criteria(expression):
        level_match = re.search(r"(?:^|\|)level=([^|]+)", expression, re.I)
        class_match = re.search(r"(?:^|\|)class=([^|]+)", expression, re.I)
        if class_match is None:
            return None
        class_keys = []
        for class_name in class_match.group(1).split(";"):
            matching = [
                key
                for (_, name), key in classes_by_name.items()
                if name == class_name.casefold()
            ]
            if len(matching) != 1:
                return None
            class_keys.extend(matching)
        filters = {}
        if level_match:
            level_values = [
                item for item in level_match.group(1).split(";") if item.isdigit()
            ]
            if level_values:
                filters["spell_level"] = level_values
        school_match = re.search(r"(?:^|\|)school=([^|]+)", expression, re.I)
        if school_match:
            filters["school"] = school_match.group(1).split(";")
        return {"kind": "spell_list", "values": sorted(set(class_keys)), "filters": filters}

    def add_from_node(node, access_name, group, path):
        if isinstance(node, list):
            for index, child in enumerate(node):
                add_from_node(child, access_name, group, (*path, str(index)))
            return
        if isinstance(node, dict):
            if isinstance(node.get("choose"), str):
                expression = node["choose"]
                criteria = spell_filter_criteria(expression)
                count = node.get("count", 1)
                uses = None
                if "daily" in path:
                    daily_index = path.index("daily")
                    uses_per_day = path[daily_index + 1] if daily_index + 1 < len(path) else 1
                    uses = f"{uses_per_day}/day"
                elif "rest" in path:
                    uses = "per rest"
                ability = group.get("ability")
                ability_options = []
                if isinstance(ability, dict) and isinstance(ability.get("choose"), list):
                    ability_options = [
                        str(item).lower()
                        for item in ability["choose"]
                        if str(item).lower() in ABILITY_KEYS
                    ]
                elif isinstance(ability, str) and ability.lower() in ABILITY_KEYS:
                    ability_options = [ability.lower()]
                ability_fixed = (
                    ability.lower()
                    if isinstance(ability, str) and ability.lower() in ABILITY_KEYS
                    else None
                )
                if isinstance(ability, dict) and isinstance(ability.get("choose"), list):
                    ability_selection = "choice"
                elif ability == "inherit":
                    ability_selection = "inherited"
                elif isinstance(ability, str) and ability.lower() in ABILITY_KEYS:
                    ability_selection = "fixed"
                else:
                    ability_selection = None
                path_before_daily = path[: path.index("daily")] if "daily" in path else path
                level_tokens = [
                    int(token)
                    for token in path_before_daily
                    if str(token).isdigit() and 1 <= int(token) <= 20
                ]
                class_level = level_tokens[0] if level_tokens else default_level
                rows.append(
                    {
                        "access": access_name,
                        "class_level": class_level,
                        "level_scope": level_scope,
                        "criteria": criteria,
                        "count": int(count) if isinstance(count, int) and count > 0 else None,
                        "uses": uses,
                        "ability": ability_fixed,
                        "ability_options": ability_options,
                        "ability_selection": ability_selection,
                        "choice_group": shared_choice_key,
                        "choice_option": option_keys.get(group.get("name")),
                        "source_rule": source_rule,
                        "unresolved_details": None if criteria else canonical(node),
                    }
                )
                return
            for key, child in sorted(node.items()):
                if key in access_names:
                    add_from_node(child, access_names[key], group, (*path, key))
                elif key in {"choose", "count", "ability", "name", "hidden"}:
                    continue
                else:
                    add_from_node(child, access_name, group, (*path, str(key)))
            return
        if isinstance(node, str) and "|" in node:
            criteria = spell_filter_criteria(node)
            if criteria is not None:
                path_before_daily = path[: path.index("daily")] if "daily" in path else path
                level_tokens = [
                    int(token)
                    for token in path_before_daily
                    if str(token).isdigit() and 1 <= int(token) <= 20
                ]
                ability = group.get("ability")
                ability_fixed = (
                    ability.lower()
                    if isinstance(ability, str) and ability.lower() in ABILITY_KEYS
                    else None
                )
                ability_options = (
                    [
                        str(item).lower()
                        for item in ability["choose"]
                        if str(item).lower() in ABILITY_KEYS
                    ]
                    if isinstance(ability, dict) and isinstance(ability.get("choose"), list)
                    else [ability_fixed]
                    if ability_fixed
                    else []
                )
                rows.append(
                    {
                        "access": access_name,
                        "class_level": level_tokens[0] if level_tokens else default_level,
                        "level_scope": level_scope,
                        "criteria": criteria,
                        "count": None,
                        "uses": None,
                        "ability": ability_fixed,
                        "ability_options": ability_options,
                        "ability_selection": (
                            "choice"
                            if isinstance(ability, dict) and isinstance(ability.get("choose"), list)
                            else "inherited"
                            if ability == "inherit"
                            else "fixed"
                            if ability_fixed
                            else None
                        ),
                        "source_rule": source_rule,
                        "choice_group": shared_choice_key,
                        "choice_option": option_keys.get(group.get("name")),
                        "unresolved_details": None,
                    }
                )
                return
            reference = source_ref(snapshot, "spell", "spell", node, strip_suffix=True)
            path_before_daily = path[: path.index("daily")] if "daily" in path else path
            level_tokens = [
                int(token)
                for token in path_before_daily
                if str(token).isdigit() and 1 <= int(token) <= 20
            ]
            class_level = level_tokens[0] if level_tokens else default_level
            if any(re.fullmatch(r"s\d+", str(token)) for token in path):
                access_name = "expanded"
            ability = group.get("ability")
            ability_fixed = (
                ability.lower()
                if isinstance(ability, str) and ability.lower() in ABILITY_KEYS
                else None
            )
            ability_options = (
                [
                    str(item).lower()
                    for item in ability["choose"]
                    if str(item).lower() in ABILITY_KEYS
                ]
                if isinstance(ability, dict) and isinstance(ability.get("choose"), list)
                else [ability_fixed]
                if ability_fixed
                else []
            )
            rows.append(
                {
                    "access": access_name,
                    "class_level": class_level,
                    "level_scope": level_scope,
                    "spells": [reference],
                    "ability": ability_fixed,
                    "ability_options": ability_options,
                    "ability_selection": (
                        "choice"
                        if isinstance(ability, dict) and isinstance(ability.get("choose"), list)
                        else "inherited"
                        if ability == "inherit"
                        else "fixed"
                        if ability_fixed
                        else None
                    ),
                    "source_rule": source_rule,
                    "choice_group": shared_choice_key,
                    "choice_option": option_keys.get(group.get("name")),
                    "unresolved_details": None if reference["resolved"] else node,
                }
            )

    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        for access_name, source_name in access_names.items():
            if source_name in group:
                add_from_node(group[source_name], access_name, group, (source_name,))
    return rows, choices


def _build_character_builder(snapshot, text, output, class_sources, subclass_sources):
    class_models = {row["local_key"]: row for row in output["classes"]}
    class_names = {
        (raw["source"].casefold(), raw["name"].casefold()): local_key
        for local_key, raw in class_sources.items()
    }
    classes, subclasses, feats, species, backgrounds, optional_features = [], [], [], [], [], []

    for class_key, raw in sorted(class_sources.items()):
        converted = class_models[class_key]
        primary_options = _source_ability_options(raw)
        multiclass_requirement = _rule_group(
            "all",
            [
                _rule_leaf("edition", edition="2024"),
                _rule_leaf("class_entry", entry_mode="multiclass"),
                _minimum_ability_requirement(primary_options, 13),
                _rule_leaf("current_class_primary_abilities", minimum=13),
            ],
        )
        start_profile = raw.get("startingProficiencies", {})
        multi_profile = raw.get("multiclassing", {}).get("proficienciesGained", {})
        starting_grants, starting_choices = _proficiency_fields(
            class_key,
            [
                (f"starting.{field}", value, kind)
                for field, value, kind in (
                    ("skills", start_profile.get("skills"), "skill"),
                    ("weapons", start_profile.get("weapons"), "weapon"),
                    ("armor", start_profile.get("armor"), "armor"),
                    ("tools", start_profile.get("tools"), "tool"),
                    ("languages", start_profile.get("languages"), "language"),
                )
                if value is not None
            ],
        )
        multiclass_grants, multiclass_choices = _proficiency_fields(
            class_key,
            [
                (f"multiclass.{field}", value, kind)
                for field, value, kind in (
                    ("skills", multi_profile.get("skills"), "skill"),
                    ("weapons", multi_profile.get("weapons"), "weapon"),
                    ("armor", multi_profile.get("armor"), "armor"),
                    ("tools", multi_profile.get("tools"), "tool"),
                    ("languages", multi_profile.get("languages"), "language"),
                )
                if value is not None
            ],
        )
        # Saving throw proficiency is a distinct grant and comes from the class source field.
        starting_grants.extend(
            _grant(
                class_key,
                "starting.saving-throw",
                "proficiency",
                ability,
                proficiency_kind="saving_throw",
            )
            for ability in raw.get("proficiency", [])
            if str(ability).lower() in ABILITY_KEYS
        )
        starting_equipment = raw.get("startingEquipment", {})
        equipment = _equipment_choice(
            snapshot,
            class_key,
            "class-starting-equipment",
            starting_equipment.get("defaultData", []),
            includes_background=bool(starting_equipment.get("additionalFromBackground")),
        )
        event_data = defaultdict(
            lambda: {"grants": [], "choices": [], "title": "Class progression"}
        )
        for feature in converted.get("features", []):
            feature_key = feature["feature_key"]
            level = int(feature["level"])
            reference = {
                "kind": "class_feature",
                "identity": f"{DATASET_ID}:{class_key}#{feature_key}",
                "resolved": True,
            }
            event = event_data[level]
            event["title"] = feature["title"]
            event["grants"].append(
                _grant(class_key, f"class-feature.{feature_key}", "feature", reference=reference)
            )
            if "ability score improvement" in feature["title"].casefold():
                event["choices"].append(
                    {
                        "choice_key": builder_key("choice", class_key, "general-feat", level),
                        "kind": "feat",
                        "count": 1,
                        "criteria": {"kind": "feat_category", "values": ["G"]},
                        "source_rule": f"{class_key}.level.{level}.feat-choice",
                    }
                )
        selection_levels = [
            int(ref.get("classFeature", ref).split("|")[3])
            for ref in raw.get("classFeatures", [])
            if isinstance(ref, dict)
            and ref.get("gainSubclassFeature")
            and isinstance(ref.get("classFeature"), str)
            and len(ref["classFeature"].split("|")) > 3
            and ref["classFeature"].split("|")[3].isdigit()
        ]
        if not selection_levels:
            child_levels = [
                feature["level"]
                for sub in converted.get("subclasses", [])
                for feature in sub.get("features", [])
            ]
            if child_levels:
                selection_levels = [min(child_levels)]
        selection_level = min(selection_levels) if selection_levels else None
        owner_subclasses = [
            row
            for row in subclass_sources.values()
            if row.get("className", "").casefold() == raw["name"].casefold()
            and row.get("classSource", "PHB").casefold() == raw["source"].casefold()
        ]
        if selection_level and owner_subclasses:
            event_data[selection_level]["title"] = raw.get("subclassTitle", "Subclass")
            options = []
            for sub in sorted(owner_subclasses, key=uid):
                sub_key = snapshot.stable_keys[("subclass", uid(sub, "subclass"))]
                options.append(
                    {
                        "option_key": builder_key("option", class_key, "subclass", sub_key),
                        "label": sub["name"],
                        "reference": {
                            "kind": "subclass",
                            "identity": f"{DATASET_ID}:{sub_key}",
                            "resolved": True,
                        },
                    }
                )
            event_data[selection_level]["choices"].append(
                {
                    "choice_key": builder_key("choice", class_key, "subclass"),
                    "kind": "subclass",
                    "count": 1,
                    "options": options,
                    "source_rule": f"{class_key}.subclass-selection",
                }
            )
        for progression in raw.get("featProgression", []):
            categories = progression.get("category", [])
            if not categories:
                continue
            counts = progression.get("progression", {})
            for level_text, count in sorted(counts.items(), key=lambda row: int(row[0])):
                level = int(level_text)
                if not 1 <= level <= 20 or not isinstance(count, int) or count < 1:
                    continue
                event_data[level]["title"] = progression.get("name", "Feat choice")
                event_data[level]["choices"].append(
                    {
                        "choice_key": builder_key(
                            "choice", class_key, "feat-progression", level, categories
                        ),
                        "kind": "feat",
                        "count": count,
                        "criteria": {"kind": "feat_category", "values": sorted(categories)},
                        "source_rule": f"{class_key}.feat-progression.{level}",
                    }
                )
        for progression in raw.get("optionalfeatureProgression", []):
            values = progression.get("progression", [])
            previous = 0
            for level in range(1, 21):
                total = _progression_count(values, level)
                count = max(0, total - previous)
                previous = total
                if count:
                    event_data[level]["title"] = progression.get("name", "Optional feature choice")
                    event_data[level]["choices"].append(
                        {
                            "choice_key": builder_key(
                                "choice",
                                class_key,
                                "optional-feature",
                                level,
                                progression.get("featureType", []),
                            ),
                            "kind": "optional_feature",
                            "count": count,
                            "criteria": {
                                "kind": "optional_feature_type",
                                "values": sorted(progression.get("featureType", [])),
                            },
                            "source_rule": f"{class_key}.optional-feature-progression.{level}",
                        }
                    )
        for group in raw.get("classTableGroups", []):
            labels = [
                re.sub(r"\{@filter ([^|]+)\|.*?}", r"\1", str(label)).casefold()
                for label in group.get("colLabels", [])
            ]
            mastery_column = next(
                (i for i, label in enumerate(labels) if "weapon mastery" in label), None
            )
            if mastery_column is None:
                continue
            previous = 0
            mastery_rules = [
                row["local_key"]
                for row in output["rules"]
                if row.get("section") == "Weapon Masteries"
            ]
            for level, values in enumerate(group.get("rows", [])[:20], 1):
                total = (
                    int(values[mastery_column])
                    if len(values) > mastery_column and str(values[mastery_column]).isdigit()
                    else previous
                )
                count = max(0, total - previous)
                previous = total
                if count:
                    event_data[level]["title"] = "Weapon Mastery"
                    event_data[level]["choices"].append(
                        {
                            "choice_key": builder_key("choice", class_key, "weapon-mastery", level),
                            "kind": "weapon_mastery",
                            "count": count,
                            "criteria": {
                                "kind": "weapon_mastery",
                                "values": mastery_rules or ["published"],
                                "filters": {"requires_weapon_proficiency": True},
                            },
                            "source_rule": f"{class_key}.weapon-mastery.{level}",
                        }
                    )
        events = [
            {
                "event_key": builder_key("event", class_key, level),
                "level": level,
                "title": values["title"],
                "source_rule": f"{class_key}.level.{level}",
                "grants": values["grants"],
                "choices": values["choices"],
            }
            for level, values in sorted(event_data.items())
            if values["grants"] or values["choices"]
        ]
        classes.append(
            {
                "class_key": class_key,
                "name": raw["name"],
                "source": raw["source"],
                "progression": converted["progression"],
                "primary_ability_options": primary_options,
                "starting_grants": starting_grants,
                "starting_choices": starting_choices,
                "multiclass_grants": multiclass_grants,
                "multiclass_choices": multiclass_choices,
                "multiclass_requirement": multiclass_requirement,
                "subclass_selection_level": selection_level,
                "starting_equipment": equipment,
                "progression_events": events,
                "spellcasting": _spellcasting_rules(
                    raw, "class", class_key, snapshot, classes_by_name=class_names
                ),
            }
        )

    class_rules = {item["class_key"]: item for item in classes}
    for class_key, raw in sorted(class_sources.items()):
        converted_class = class_models[class_key]
        selection_level = class_rules[class_key]["subclass_selection_level"] or 3
        for raw_subclass in sorted(
            (
                row
                for row in subclass_sources.values()
                if row.get("className", "").casefold() == raw["name"].casefold()
                and row.get("classSource", "PHB").casefold() == raw["source"].casefold()
            ),
            key=uid,
        ):
            subclass_key = snapshot.stable_keys[("subclass", uid(raw_subclass, "subclass"))]
            converted = next(
                row for row in converted_class["subclasses"] if row["subclass_key"] == subclass_key
            )
            events = []
            for feature in converted.get("features", []):
                feature_key = feature["feature_key"]
                reference = {
                    "kind": "subclass_feature",
                    "identity": f"{DATASET_ID}:{subclass_key}#{feature_key}",
                    "resolved": True,
                }
                events.append(
                    {
                        "event_key": builder_key(
                            "event", subclass_key, feature["level"], feature_key
                        ),
                        "level": feature["level"],
                        "title": feature["title"],
                        "source_rule": f"{subclass_key}.level.{feature['level']}",
                        "grants": [
                            _grant(
                                subclass_key,
                                f"feature.{feature_key}",
                                "feature",
                                reference=reference,
                            )
                        ],
                        "choices": [],
                    }
                )
            subclass_spell_record = dict(raw_subclass)
            subclass_spell_record.setdefault(
                "casterProgression", raw_subclass.get("casterProgression")
            )
            subclasses.append(
                {
                    "subclass_key": subclass_key,
                    "class_key": class_key,
                    "name": converted["name"],
                    "source": converted["source"],
                    "selection_level": selection_level,
                    "progression_events": events,
                    "spellcasting": _spellcasting_rules(
                        subclass_spell_record,
                        "subclass",
                        subclass_key,
                        snapshot,
                        table_groups=raw_subclass.get("subclassTableGroups", []),
                        classes_by_name=class_names,
                    ),
                }
            )

    classes_by_name = class_names
    for raw in snapshot.records["feat"]:
        feat_key = snapshot.stable_keys.get(("feat", uid(raw, "feat")))
        if feat_key is None:
            continue
        grants, choices = _ability_choices(
            feat_key, raw.get("ability", []), f"{feat_key}.ability-score"
        )
        for field, kind in (
            ("skillProficiencies", "skill"),
            ("toolProficiencies", "tool"),
            ("weaponProficiencies", "weapon"),
            ("armorProficiencies", "armor"),
            ("languageProficiencies", "language"),
        ):
            if field in raw:
                extra_grants, extra_choices = _proficiency_fields(
                    feat_key, [(field, raw[field], kind)]
                )
                grants.extend(extra_grants)
                choices.extend(extra_choices)
        spell_access, spell_choices = _spell_access_rules(
            raw, feat_key, snapshot, classes_by_name, f"{feat_key}.additional-spells"
        )
        feats.append(
            {
                "feat_key": feat_key,
                "source": raw["source"],
                "category": FEAT_CATEGORIES.get(
                    raw.get("category"), str(raw.get("category", "unknown"))
                ),
                "prerequisite": _source_requirement(
                    raw.get("prerequisite"), snapshot, classes_by_name
                ),
                "repeatable": bool(raw.get("repeatable", False)),
                "ability_choices": choices,
                "spell_choices": spell_choices,
                "grants": grants,
                "spell_access": spell_access,
            }
        )

    for raw in snapshot.select("optionalfeature"):
        option_key = local_key("optional-feature", raw)
        snapshot.track("optionalfeature", raw, option_key)
        optional_features.append(
            {
                "option_key": option_key,
                "name": raw["name"],
                "source": raw["source"],
                "option_type": (raw.get("featureType") or ["unknown"])[0],
                "feature_types": sorted(raw.get("featureType", [])),
                "prerequisite": _source_requirement(
                    raw.get("prerequisite"), snapshot, classes_by_name
                ),
                "repeatable": bool(raw.get("repeatable", False)),
                "description": text.render(raw.get("entries", []), raw),
            }
        )

    for raw in snapshot.select("race"):
        species_key = local_key("species", raw)
        snapshot.track("species", raw, species_key)
        fluff = snapshot.index.get(("raceFluff", f"{raw['name']}|{raw['source']}".lower()))
        grants, choices = [], []
        raw_size = raw.get("size", ["M"])
        raw_size = raw_size if isinstance(raw_size, list) else [raw_size]
        sizes = [
            {"S": "small", "M": "medium", "L": "large", "T": "tiny", "H": "huge"}.get(
                str(value), "medium"
            )
            for value in raw_size
        ]
        if len(sizes) == 1:
            grants.append(_grant(species_key, f"{species_key}.size", "size", sizes[0]))
        else:
            choices.append(
                {
                    "choice_key": builder_key("choice", species_key, "size"),
                    "kind": "size",
                    "count": 1,
                    "options": [
                        {
                            "option_key": builder_key("option", species_key, size),
                            "label": size.title(),
                            "value": size,
                        }
                        for size in sorted(set(sizes))
                    ],
                    "source_rule": f"{species_key}.size",
                }
            )
        creature_types = [str(value).casefold() for value in raw.get("creatureTypes", ["humanoid"])]
        for creature_type in creature_types:
            grants.append(
                _grant(species_key, f"{species_key}.creature-type", "creature_type", creature_type)
            )
        raw_speed = raw.get("speed", 30)
        movement = []
        if isinstance(raw_speed, int):
            movement = [{"kind": "walk", "feet": raw_speed}]
        elif isinstance(raw_speed, dict):
            movement = [
                {"kind": key.casefold(), "feet": int(value)}
                for key, value in sorted(raw_speed.items())
                if key.casefold() in {"walk", "burrow", "climb", "fly", "swim"}
                and isinstance(value, (int, float))
            ]
        for speed in movement:
            grants.append(
                _grant(
                    species_key,
                    f"{species_key}.speed.{speed['kind']}",
                    "speed",
                    str(speed["feet"]),
                    quantity=speed["feet"],
                    unit=speed["kind"],
                )
            )
        darkvision = raw.get("darkvision")
        if isinstance(darkvision, int):
            grants.append(
                _grant(
                    species_key,
                    f"{species_key}.darkvision",
                    "darkvision",
                    str(darkvision),
                    quantity=darkvision,
                    unit="feet",
                )
            )
        for field, kind in (
            ("skillProficiencies", "skill"),
            ("toolProficiencies", "tool"),
            ("weaponProficiencies", "weapon"),
            ("armorProficiencies", "armor"),
            ("languageProficiencies", "language"),
        ):
            if field in raw:
                extra_grants, extra_choices = _proficiency_fields(
                    species_key, [(field, raw[field], kind)]
                )
                grants.extend(extra_grants)
                choices.extend(extra_choices)
        for feat_index, spec in enumerate(raw.get("feats", [])):
            if not isinstance(spec, dict):
                continue
            if "anyFromCategory" in spec:
                details = spec["anyFromCategory"]
                choices.append(
                    {
                        "choice_key": builder_key("choice", species_key, "feat", feat_index),
                        "kind": "feat",
                        "count": int(details.get("count", 1)),
                        "criteria": {
                            "kind": "feat_category",
                            "values": details.get("category", []),
                        },
                        "source_rule": f"{species_key}.feat-choice",
                    }
                )
            else:
                for identity in sorted(spec):
                    reference = source_ref(snapshot, "feat", "feat", identity)
                    if reference["resolved"]:
                        grants.append(
                            _grant(
                                species_key,
                                f"{species_key}.feat.{feat_index}",
                                "feat",
                                reference=reference,
                            )
                        )
        traits = []
        raw_entries = raw.get("entries") or (fluff.get("entries", []) if fluff else [])
        description = text.render(raw_entries, raw)
        for index, entry in enumerate(raw_entries if isinstance(raw_entries, list) else []):
            if isinstance(entry, dict) and entry.get("name"):
                body = text.render(entry.get("entries", entry.get("entry", [])), raw)
                if body.strip():
                    traits.append(
                        {
                            "trait_key": builder_key("trait", species_key, entry["name"]),
                            "name": inline(entry["name"]),
                            "description": body,
                            "source_rule": f"{species_key}.trait.{index}",
                        }
                    )
        if not traits and description.strip():
            traits.append(
                {
                    "trait_key": builder_key("trait", species_key, "species-traits"),
                    "name": "Species Traits",
                    "description": description,
                    "source_rule": f"{species_key}.traits",
                }
            )
        spell_access, spell_choices = _spell_access_rules(
            raw,
            species_key,
            snapshot,
            classes_by_name,
            f"{species_key}.additional-spells",
            default_level=1,
            level_scope="total",
        )
        choices.extend(spell_choices)
        species.append(
            {
                "species_key": species_key,
                "name": raw["name"],
                "source": raw["source"],
                "creature_types": creature_types,
                "sizes": sorted(set(sizes)),
                "movement": movement or [{"kind": "walk", "feet": 30}],
                "darkvision_feet": darkvision if isinstance(darkvision, int) else None,
                "grants": grants,
                "choices": choices,
                "traits": traits,
                "spell_access": spell_access,
                "description": description,
            }
        )

    for raw in snapshot.select("background"):
        background_key = local_key("background", raw)
        snapshot.track("background", raw, background_key)
        fluff = snapshot.index.get(
            ("backgroundFluff", f"{raw['name']}|{raw['source']}".lower())
        )
        grants, choices = _ability_choices(
            background_key,
            raw.get("ability", []),
            f"{background_key}.ability-score",
            background=True,
        )
        for field, kind in (
            ("skillProficiencies", "skill"),
            ("toolProficiencies", "tool"),
            ("weaponProficiencies", "weapon"),
            ("armorProficiencies", "armor"),
            ("languageProficiencies", "language"),
        ):
            if field in raw:
                extra_grants, extra_choices = _proficiency_fields(
                    background_key, [(field, raw[field], kind)]
                )
                grants.extend(extra_grants)
                choices.extend(extra_choices)
        feat_reference = None
        origin_feat_variant = None
        for feat_group in raw.get("feats", []):
            if isinstance(feat_group, dict):
                if "anyFromCategory" in feat_group:
                    details = feat_group["anyFromCategory"]
                    choices.append(
                        {
                            "choice_key": builder_key("choice", background_key, "origin-feat"),
                            "kind": "feat",
                            "count": int(details.get("count", 1)),
                            "criteria": {
                                "kind": "feat_category",
                                "values": details.get("category", []),
                            },
                            "source_rule": f"{background_key}.origin-feat",
                        }
                    )
                    continue
                for identity in sorted(feat_group):
                    candidate = source_ref(snapshot, "feat", "feat", identity)
                    if not candidate["resolved"] and ";" in identity:
                        feat_identity, variant_and_source = identity.split(";", 1)
                        origin_feat_variant, separator, source_code = variant_and_source.partition(
                            "|"
                        )
                        source_identity = (
                            f"{feat_identity.strip()}|{source_code.strip()}"
                            if separator
                            else feat_identity.strip()
                        )
                        candidate = source_ref(snapshot, "feat", "feat", source_identity)
                    if candidate["resolved"]:
                        feat_reference = candidate
                    else:
                        feat_reference = candidate
        if feat_reference and feat_reference["resolved"]:
            grants.append(
                _grant(
                    background_key,
                    f"{background_key}.origin-feat",
                    "feat",
                    value=origin_feat_variant.strip() if origin_feat_variant else None,
                    reference=feat_reference,
                )
            )
        equipment = _equipment_choice(
            snapshot,
            background_key,
            "background-starting-equipment",
            raw.get("startingEquipment", []),
        )
        backgrounds.append(
            {
                "background_key": background_key,
                "name": raw["name"],
                "source": raw["source"],
                "grants": grants,
                "choices": choices,
                "origin_feat": feat_reference,
                "origin_feat_variant": origin_feat_variant.strip() if origin_feat_variant else None,
                "starting_equipment": equipment,
                "description": text.render(
                    raw.get("entries") or (fluff.get("entries", []) if fluff else []), raw
                ),
            }
        )

    equipment_rows = []
    source_items = {}
    for category in ("baseitem", "item"):
        for raw in snapshot.records[category]:
            stable_key = snapshot.stable_keys.get(("item", uid(raw)))
            if stable_key:
                source_items[stable_key] = raw
    for row in snapshot.inventory["item"]:
        if row.get("variant") and row.get("base"):
            base = snapshot.index.get(("baseitem", row["base"]))
            if base is not None:
                source_items[row["stable_key"]] = base
    for item in output["items"]["items"]:
        if snapshot.source_editions.get(item["source"]) != "2024":
            continue
        raw = source_items.get(item["local_key"])
        if raw is None and item.get("kind") not in {"weapon", "armor"}:
            continue
        raw = raw or {}
        type_code = str(raw.get("type", "")).split("|")[0].upper()
        is_weapon = bool(item.get("weapon")) or type_code in {"M", "R"}
        is_armor = bool(item.get("armor")) or type_code in {"LA", "MA", "HA", "S"}
        category = "weapon" if is_weapon else "armor" if is_armor else "other"
        if category == "other":
            continue
        row = {"item_key": item["local_key"], "source": item["source"], "category": category}
        if is_weapon:
            weapon = item.get("weapon") or {}
            weapon_category = str(raw.get("weaponCategory", item.get("subtype", ""))).lower()
            unresolved_fields = []
            if weapon_category not in {"simple", "martial"}:
                weapon_category = None
                unresolved_fields.append("weapon_category")
            attack_type = "ranged" if type_code == "R" else "melee" if type_code == "M" else None
            if attack_type is None:
                unresolved_fields.append("attack_type")
            range_value = raw.get("range")
            range_feet = []
            if isinstance(range_value, int) and not isinstance(range_value, bool):
                range_feet.append(range_value)
                long_range = raw.get("longRange")
                if isinstance(long_range, int) and not isinstance(long_range, bool):
                    range_feet.append(long_range)
            elif isinstance(range_value, str):
                range_match = re.fullmatch(r"\s*(\d+)\s*(?:/\s*(\d+)\s*)?", range_value)
                if range_match is not None:
                    range_feet = [int(value) for value in range_match.groups() if value is not None]
            if range_value is not None and not range_feet:
                unresolved_fields.append("range")
            row.update(
                weapon_category=weapon_category,
                attack_type=attack_type,
                damage=weapon.get("damage_expression"),
                damage_type=weapon.get("damage_type"),
                range_feet=range_feet,
                properties=[value["property_key"] for value in item.get("properties", [])],
                versatile_damage=weapon.get("versatile_damage"),
                unresolved_fields=unresolved_fields,
            )
            mastery_refs = []
            for mastery in raw.get("mastery", []):
                mastery_uid = mastery.get("uid") if isinstance(mastery, dict) else mastery
                master_identity = normalized_ref(mastery_uid, "itemMastery")
                master = snapshot.index.get(
                    ("itemMastery", master_identity)
                )
                if master:
                    target = text.reference_targets.get(
                        ("itemMastery", master["source"].casefold(), master["name"].casefold())
                    )
                    if target:
                        mastery_refs.append(
                            {
                                "kind": "rule",
                                "identity": f"{DATASET_ID}:{target[1]}",
                                "resolved": True,
                            }
                        )
                    else:
                        mastery_refs.append(
                            {"kind": "rule", "identity": master_identity, "resolved": False}
                        )
                else:
                    mastery_refs.append(
                        {"kind": "rule", "identity": str(mastery_uid), "resolved": False}
                    )
            row["mastery_references"] = mastery_refs
            if raw.get("ammoType"):
                row["ammunition_reference"] = source_ref(snapshot, "item", "item", raw["ammoType"])
        else:
            armor = item.get("armor") or {}
            armor_category = {"LA": "light", "MA": "medium", "HA": "heavy", "S": "shield"}.get(
                type_code
            )
            if armor_category is None:
                armor_category = str(armor.get("armor_category", "")).casefold()
                armor_category = next(
                    (
                        key
                        for key in ("light", "medium", "heavy", "shield")
                        if key in armor_category
                    ),
                    None,
                )
            if armor_category is None:
                continue
            raw_ac = raw.get("ac")
            if not isinstance(raw_ac, int):
                ac_match = re.search(r"\d+", str(raw_ac or armor.get("ac_expression", "")))
                if ac_match is None:
                    continue
                raw_ac = int(ac_match.group())
            row.update(
                armor_category=armor_category,
                base_ac=raw_ac,
                dexterity_rule={"LA": "full", "MA": "cap", "HA": "none", "S": "shield_bonus"}.get(
                    type_code, "none"
                ),
                dexterity_cap=2 if type_code == "MA" else None,
                strength_requirement=int(raw["strength"])
                if str(raw.get("strength", "")).isdigit()
                else None,
                stealth_disadvantage=bool(raw.get("stealth")),
            )
        equipment_rows.append(row)

    catalog = {
        "edition": "2024",
        "skills": [
            {"key": key, "name": name, "ability": ability} for key, name, ability in BUILDER_SKILLS
        ],
        "classes": classes,
        "subclasses": subclasses,
        "feats": feats,
        "species": species,
        "backgrounds": backgrounds,
        "optional_features": optional_features,
        "equipment": equipment_rows,
    }
    return CharacterBuilderCatalog.model_validate(catalog).model_dump(mode="json")


def build(root, revision):
    snapshot = Snapshot(root)
    text = Text(snapshot)
    for r in snapshot.records["condition"]:
        if r["source"] in BOOKS and not exclusion(r):
            text.reference_targets[("condition", r["source"].casefold(), r["name"].casefold())] = (
                "condition",
                local_key("condition", r),
            )
    for category in ("variantrule", "action"):
        for r in snapshot.records[category]:
            if (
                r["name"] in RULE_GLOSSARY_NAMES
                and r["source"] in snapshot.source_editions
                and not exclusion(r)
            ):
                text.reference_targets[(category, r["source"].casefold(), r["name"].casefold())] = (
                    "rule",
                    local_key("rule", r),
                )
    for category, allowed_names in (
        ("status", STATUS_GLOSSARY_NAMES),
        ("itemMastery", MASTERY_GLOSSARY_NAMES),
    ):
        for r in snapshot.records[category]:
            if r["name"] in allowed_names and r["source"] in snapshot.source_editions:
                text.reference_targets[(category, r["source"].casefold(), r["name"].casefold())] = (
                    "rule",
                    local_key("rule", r),
                )
    classes = snapshot.select("class")
    class_keys = {(r["source"].lower(), r["name"].lower()): local_key("class", r) for r in classes}
    subclasses = defaultdict(list)
    for r in snapshot.select("subclass"):
        parent = (r.get("classSource", "PHB").lower(), r["className"].lower())
        if parent not in class_keys:
            snapshot.reject("subclass", r, "owner-not-selected-2024-class")
        else:
            subclasses[parent].append(r)
    output = {"classes": [], "spells": [], "feats": [], "conditions": [], "rules": []}
    for r in classes:
        owner = (r["source"].lower(), r["name"].lower())
        converted = convert_class(r, subclasses[owner], text)
        output["classes"].append(converted)
        snapshot.track("class", r, converted["local_key"])
    for category, convert in (
        ("spell", lambda r: convert_spell(r, text, class_keys)),
        ("feat", lambda r: convert_feat(r, text)),
    ):
        for r in snapshot.select(category):
            converted = convert(r)
            output[category + "s"].append(converted)
            snapshot.track(category, r, converted["local_key"])
    for r in snapshot.records["condition"]:
        if r["source"] not in BOOKS or r["source"] not in snapshot.source_editions:
            snapshot.reject("condition", r, exclusion(r) or "source-not-allowlisted")
            continue
        if exclusion(r):
            snapshot.reject("condition", r, exclusion(r))
            continue
        converted = base_entry("condition", r, text)
        output["conditions"].append(converted)
        snapshot.track("condition", r, converted["local_key"])
    for category in ("variantrule", "action"):
        for r in snapshot.records[category]:
            if r["name"] not in RULE_GLOSSARY_NAMES or r["source"] not in snapshot.source_editions:
                continue
            if exclusion(r):
                snapshot.reject("rules", r, exclusion(r))
                continue
            converted = base_entry("rule", r, text)
            converted["section"] = (
                RULE_TYPE_LABELS.get(r.get("ruleType")) if category == "variantrule" else "Actions"
            )
            output["rules"].append(converted)
            snapshot.track("rule", r, converted["local_key"])
    for category, allowed_names, section in (
        ("status", STATUS_GLOSSARY_NAMES, "Statuses"),
        ("itemMastery", MASTERY_GLOSSARY_NAMES, "Weapon Masteries"),
    ):
        for r in snapshot.records[category]:
            if r["name"] not in allowed_names or r["source"] not in snapshot.source_editions:
                continue
            if exclusion(r):
                snapshot.reject("rules", r, exclusion(r))
                continue
            converted = base_entry("rule", r, text)
            converted["section"] = section
            output["rules"].append(converted)
            snapshot.track("rule", r, converted["local_key"])
    property_map = {}
    for r in snapshot.select("itemProperty"):
        name = r.get("name") or r["entries"][0]["name"]
        prop = {
            "key": local_key("property", r),
            "name": name,
            "source": r["source"],
            "description": text.render(r["entries"], r),
        }
        property_map[uid(r)] = prop
        snapshot.track("itemProperty", r, prop["key"])
        snapshot.inventory["itemProperty"][-1]["name"] = name
    bases = snapshot.select("baseitem")
    items = snapshot.select("item")
    variants = snapshot.select("magicvariant")
    items += variant_items(variants, bases, snapshot)
    groups = defaultdict(list)
    for group in snapshot.select("itemGroup"):
        if group.get("entries"):
            for member in group.get("items", []):
                groups[normalized_ref(member, "item")].append(group)
        else:
            snapshot.reject("itemGroup", group, "navigation-group-not-an-additional-item")
    catalog = []
    for original in bases + items:
        r = copy.deepcopy(original)
        if r.get("type", "").split("|")[0] in OUT_OF_SCOPE_ITEM_TYPES:
            snapshot.reject("item", r, "out-of-scope-vehicle-or-mount")
            continue
        if r.get("baseItem"):
            base = snapshot.resolve("baseitem", r["baseItem"])
            if exclusion(base):
                raise ValueError(f"Item requires excluded base: {uid(r)}: {r['baseItem']}")
            base.update(r)
            r = base
        converted = convert_item(r, text, property_map)
        supplemental = {}
        for identity in (uid(original), original.get("_variant")):
            for group in groups.get(identity, []):
                supplemental[uid(group)] = group
        for group in sorted(supplemental.values(), key=uid):
            converted["sections"].append(
                {
                    "key": local_key("item-group", group),
                    "heading": group["name"],
                    "body": text.render(group["entries"], group),
                    "display_order": len(converted["sections"]),
                }
            )
            snapshot.track(
                "itemGroup",
                group,
                local_key("item-group", group),
                converted["local_key"],
                status="embedded",
            )
        if original.get("_generic_entries"):
            converted["sections"].append(
                {
                    "key": "generic-variant-rules",
                    "heading": "General Variant Rules",
                    "body": text.render(original["_generic_entries"], r),
                    "display_order": len(converted["sections"]),
                }
            )
        catalog.append(converted)
        snapshot.track("item", original, converted["local_key"])
        if "_variant" in original:
            snapshot.inventory["item"][-1].update(
                variant=original["_variant"], base=original["_base"]
            )
    output["items"] = {
        "properties": sorted(property_map.values(), key=lambda v: v["key"]),
        "items": sorted(catalog, key=lambda v: v["local_key"]),
    }
    for category in ("classes", "spells", "feats", "conditions", "rules"):
        output[category].sort(key=lambda v: v["local_key"])
    class_source_map = {local_key("class", record): record for record in classes}
    subclass_source_map = {
        uid(record, "subclass"): record for rows in subclasses.values() for record in rows
    }
    output["character_builder"] = _build_character_builder(
        snapshot, text, output, class_source_map, subclass_source_map
    )
    output["manifest"] = {
        "schema_version": "1.0",
        "dataset_id": DATASET_ID,
        "title": "Official D&D 2024 Reference",
        "version": revision,
        "ruleset": "D&D 5.5e / 2024 rules",
        "language": "en",
        "license_identifier": "LicenseRef-Proprietary-Wizards-of-the-Coast",
        "attribution": "Dungeons & Dragons content by Wizards of the Coast. "
        "Community-structured data provided by 5etools. Personal local reference; "
        "this dataset is not SRD or an assertion of redistribution rights.",
        "origin_url": UPSTREAM,
        "sources": [
            {
                "key": code,
                "title": BOOKS[code],
                "edition": snapshot.source_editions.get(code),
                "citation": f"{BOOKS[code]}, Wizards of the Coast, "
                f"{snapshot.books[code]['published']}",
            }
            for code in sorted(BOOKS)
        ],
    }
    pack = validate_dataset(DatasetPack.model_validate(output))
    for category in ("classFeature", "subclassFeature"):
        for r in snapshot.records[category]:
            if not any(t[0] == category and t[1] == uid(r, category) for t in snapshot.used):
                reason = exclusion(r) or (
                    "embedded-in-feature-text"
                    if (category, uid(r, category)) in snapshot.embedded
                    else "not-owned-by-selected-class"
                )
                snapshot.reject(category, r, reason)
    for category, identity in sorted(snapshot.embedded):
        r = snapshot.resolve(category, identity)
        snapshot.track("embedded-" + category, r, local_key(category, r), status="embedded")
    reconcile(pack, snapshot)
    return pack, snapshot


def reconcile(pack, snapshot):
    """Require exact inventory coverage, including feature and embedded-rule owners."""
    expected = defaultdict(list)
    for category, records in (
        ("item", pack.items.items),
        ("spell", pack.spells),
        ("feat", pack.feats),
        ("class", pack.classes),
        ("condition", pack.conditions),
        ("rule", pack.rules),
    ):
        expected[category] = [(r.local_key, r.name, r.source, None) for r in records]
    expected["itemProperty"] = [(r.key, r.name, r.source, None) for r in pack.items.properties]
    for cls in pack.classes:
        expected["classFeature"] += [
            (f.feature_key, f.title, f.source, cls.local_key) for f in cls.features
        ]
        for sub in cls.subclasses:
            expected["subclass"].append((sub.subclass_key, sub.name, sub.source, cls.local_key))
            expected["subclassFeature"] += [
                (f.feature_key, f.title, f.source, sub.subclass_key) for f in sub.features
            ]
    for category, rows in expected.items():
        actual = [
            (r["stable_key"], r["name"], r["source"], r.get("owner"))
            for r in snapshot.inventory[category]
        ]
        if Counter(rows) != Counter(actual):
            raise ValueError(f"Inventory does not reconcile: {category}")
    items = {i.local_key: i for i in pack.items.items}
    groups_used = set()
    for row in snapshot.inventory["itemGroup"]:
        if row["stable_key"] not in {s.key for s in items[row["owner"]].sections}:
            raise ValueError(f"Embedded item-group ownership does not resolve: {row}")
        groups_used.add(row["upstream_identity"])
    for group in snapshot.records["itemGroup"]:
        if not exclusion(group) and group.get("entries") and uid(group) not in groups_used:
            raise ValueError(f"Unpreserved item-group rules: {uid(group)}")
    generated = Counter(r.get("variant") for r in snapshot.inventory["item"])
    for variant in snapshot.inventory["magicvariant"]:
        if generated[variant["upstream_identity"]] != variant["generated_count"]:
            raise ValueError(
                f"Variant expansion does not reconcile: {variant['upstream_identity']}"
            )


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def artifacts(pack, snapshot, revision):
    raw = pack.model_dump(mode="json")
    files = {
        ("character-builder.json" if key == "character_builder" else f"{key}.json"): json_bytes(
            value
        )
        for key, value in raw.items()
        if value is not None
    }
    # Monster Manual records are built by build_monsters_dataset.py from the
    # separately pinned bestiary inputs. Keep that artifact outside this write set.
    files.pop("monsters.json", None)
    filenames = {
        "item": "items",
        "itemProperty": "properties",
        "spell": "spells",
        "feat": "feats",
        "class": "classes",
        "subclass": "subclasses",
        "classFeature": "class-features",
        "subclassFeature": "subclass-features",
        "condition": "conditions",
        "rule": "rules",
    }
    for category, rows in snapshot.inventory.items():
        files[f"inventory/{filenames.get(category, category)}.json"] = json_bytes(
            sorted(rows, key=lambda r: (r.get("stable_key", ""), r["upstream_identity"]))
        )
    files["inventory/excluded.json"] = json_bytes(sorted(snapshot.excluded, key=canonical))
    counts = {k: len(v) for k, v in snapshot.inventory.items()}
    counts.update(
        weapons=sum(i.weapon is not None for i in pack.items.items),
        armor=sum(i.armor is not None for i in pack.items.items),
        magic_items=sum(i.kind == "magic_item" for i in pack.items.items),
    )
    report = {
        "upstream": UPSTREAM,
        "revision": revision,
        "upstream_version": snapshot.version,
        "upstream_files": snapshot.hashes,
        "counts": counts,
        "excluded_counts": dict(Counter(r["reason"] for r in snapshot.excluded)),
        "edition_warnings": snapshot.edition_warnings,
        "output_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())},
    }
    files["inventory/reconciliation.json"] = json_bytes(report)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Local 5etools repository snapshot root")
    parser.add_argument("--output", type=Path, default=Path("src/dndref/datasets") / DATASET_ID)
    parser.add_argument("--revision", help="Snapshot commit; auto-detected for Git checkouts")
    parser.add_argument(
        "--check", action="store_true", help="Reconvert and compare without writing"
    )
    args = parser.parse_args()
    revision = (
        args.revision
        or subprocess.check_output(
            ["git", "-C", str(args.snapshot), "rev-parse", "HEAD"], text=True
        ).strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        parser.error("revision must be a full 40-character commit hash")
    pack, snapshot = build(args.snapshot, revision)
    files = artifacts(pack, snapshot, revision)
    if args.check:
        for name, raw in files.items():
            if (args.output / name).read_bytes() != raw:
                raise ValueError(f"Generated output differs: {name}")
        # Monsters are generated separately from the same pinned snapshot.
        actual = {p.relative_to(args.output).as_posix() for p in args.output.rglob("*.json")}
        actual.discard("monsters.json")
        if actual != set(files):
            raise ValueError(
                f"Unexpected/missing output files: {actual.symmetric_difference(files)}"
            )
    else:
        manifest = args.output / "manifest.json"
        if manifest.exists() and json.loads(manifest.read_text())["dataset_id"] != DATASET_ID:
            parser.error("Refusing to overwrite a different dataset (including the SRD baseline)")
        # Run the real loader, including storage invariants, before replacing output.
        with tempfile.TemporaryDirectory(prefix="dndref-conversion-") as staging:
            for name, raw in files.items():
                path = Path(staging) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            load_dataset(staging)
        for name, raw in files.items():
            path = args.output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    loaded = load_dataset(args.output)
    print(
        f"{'Checked' if args.check else 'Generated'} {loaded.dataset_id}: "
        f"{loaded.entry_count} entries; {loaded.content_hash}"
    )


if __name__ == "__main__":
    main()
