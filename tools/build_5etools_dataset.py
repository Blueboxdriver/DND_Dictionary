"""Offline, fail-closed 5etools snapshot converter. See docs/milestone-9-10.md."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from dndref.importer import load_dataset
from dndref.models import DatasetPack, validate_dataset

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
        ]
        for directory in ("class", "spells"):
            index = self.read(f"data/{directory}/index.json")
            paths += [f"data/{directory}/{name}" for name in sorted(set(index.values()))]
        paths += [
            f"data/class/{p.name}"
            for p in sorted((self.root / "data/class").glob("fluff-class-*.json"))
        ]
        for path in sorted(set(paths)):
            for category, records in self.read(path).items():
                if not isinstance(records, list):
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


def build(root, revision):
    snapshot = Snapshot(root)
    text = Text(snapshot)
    classes = snapshot.select("class")
    class_keys = {(r["source"].lower(), r["name"].lower()): local_key("class", r) for r in classes}
    subclasses = defaultdict(list)
    for r in snapshot.select("subclass"):
        parent = (r.get("classSource", "PHB").lower(), r["className"].lower())
        if parent not in class_keys:
            snapshot.reject("subclass", r, "owner-not-selected-2024-class")
        else:
            subclasses[parent].append(r)
    output = {"classes": [], "spells": [], "feats": []}
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
    for category in ("classes", "spells", "feats"):
        output[category].sort(key=lambda v: v["local_key"])
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
    files = {f"{k}.json": json_bytes(v) for k, v in raw.items()}
    filenames = {
        "item": "items",
        "itemProperty": "properties",
        "spell": "spells",
        "feat": "feats",
        "class": "classes",
        "subclass": "subclasses",
        "classFeature": "class-features",
        "subclassFeature": "subclass-features",
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
        actual = {p.relative_to(args.output).as_posix() for p in args.output.rglob("*.json")}
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
