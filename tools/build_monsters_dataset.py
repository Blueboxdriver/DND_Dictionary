"""Convert the pinned 5etools XMM bestiary JSON into the packaged monster file."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from dndref.models import Monster

SIZE = {"T": "Tiny", "S": "Small", "M": "Medium", "L": "Large", "H": "Huge", "G": "Gargantuan"}
ALIGNMENT = {
    "L": "Lawful", "N": "Neutral", "C": "Chaotic", "G": "Good",
    "E": "Evil", "U": "Unaligned", "A": "Any",
}
TAG = re.compile(r"\{@([A-Za-z][A-Za-z0-9]*)\s*([^{}]*)}")
CONDITION_TAG = re.compile(r"\{@condition ([^|{}]+)\|([^|{}]+)(?:\|[^{}]*)?}")
RULE_TAG = re.compile(
    r"\{@(?:variantrule|action|status|itemMastery) "
    r"([^|{}]+)\|([^|{}]+)(?:\|[^{}]*)?}"
)
XMM_SHA256 = "213c51a0ecb333cabb2e0ccd97af9b2bd661794cf5a89b6b5e0acafd4f6552f3"
GROUPS_SHA256 = "3c944fb66c2f094123e9109d16b133e474a2bacd0b7388a80a98e9c060a01d98"


def local_glossary_key(content_type, name, source):
    identity = f"{name}|{source}".lower()
    source_value = unicodedata.normalize("NFKD", source).encode("ascii", "ignore").decode()
    name_value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    source_slug = re.sub(r"[^a-z0-9]+", "-", source_value.lower()).strip("-")
    name_slug = re.sub(r"[^a-z0-9]+", "-", name_value.lower().replace("'", "")).strip("-")
    return (
        f"{content_type}/{source_slug}/{name_slug[:100]}-"
        f"{hashlib.sha256(identity.encode()).hexdigest()[:10]}"
    )


def display(value):
    """Render source text for terminal reading without interpreting combat rules."""
    if isinstance(value, list):
        return "\n\n".join(filter(None, (display(part) for part in value)))
    if isinstance(value, dict):
        if "entries" in value:
            body = display(value["entries"])
            return (f"{value['name']}. " if value.get("name") else "") + body
        if "items" in value:
            return "\n".join("- " + display(part) for part in value["items"])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value)

    def replace(match):
        tag, payload = match.groups()
        parts = payload.split("|")
        if tag == "dc":
            return "DC " + parts[0]
        if tag == "hit":
            return ("" if parts[0].startswith("-") else "+") + parts[0]
        if tag == "atkr":
            return "Melee or Ranged Attack:" if "," in parts[0] else (
                "Melee Attack:" if "m" in parts[0] else "Ranged Attack:")
        if tag == "h":
            return "Hit: "
        if tag == "hom":
            return "Hit or Miss: "
        if tag == "recharge":
            low = parts[0] if parts[0] else "6"
            return f"Recharge {low}–6" if low != "6" else "Recharge 6"
        action_terms = {
            "actSave": "Saving Throw:", "actSaveFail": "Failure:",
            "actSaveSuccess": "Success:", "actSaveSuccessOrFail": "Success or Failure:",
            "actSaveFailBy": "Failure by", "actTrigger": "Trigger:",
            "actResponse": "Response:",
        }
        if tag in action_terms:
            return action_terms[tag] + (" " + parts[0] if parts[0] else "")
        if tag in {"b", "bold"}:
            return "**" + parts[0] + "**"
        if tag in {"i", "italic"}:
            return "*" + parts[0] + "*"
        if tag in {"dice", "damage", "d20", "spell", "condition", "item", "action",
                   "creature", "variantrule", "skill", "status", "hazard", "book",
                   "filter", "link"}:
            return parts[2] if len(parts) > 2 and parts[2] else parts[0]
        raise ValueError(f"unsupported bestiary tag: {tag}")

    while "{@" in text:
        revised = TAG.sub(replace, text)
        if revised == text:
            raise ValueError(f"malformed bestiary tag: {text[:120]}")
        text = revised
    return text.strip()


def defense(value):
    if not value:
        return None
    def part_text(part):
        if not isinstance(part, dict):
            return display(part)
        if part.get("special"):
            return display(part["special"])
        values = next(
            (part[key] for key in ("vulnerable", "resist", "immune", "conditionImmune")
             if key in part), []
        )
        base = ", ".join(part_text(item) for item in values)
        note = display(part.get("note", ""))
        return f"{base} ({note})" if note else base

    return ", ".join(part_text(part) for part in value)


def spellcasting(record):
    parts = [display(record.get("headerEntries", []))]
    if record.get("will") and "will" not in record.get("hidden", []):
        parts.append("At will: " + ", ".join(map(display, record["will"])))
    for key, label in (("daily", "/day"), ("weekly", "/week"), ("rest", "/rest")):
        if key in record and key not in record.get("hidden", []):
            for count, spells in record[key].items():
                parts.append(f"{count}{label}: " + ", ".join(map(display, spells)))
    for block in record.get("spells", []):
        level = block.get("level", 0)
        slots = block.get("slots")
        suffix = f" ({slots} slots)" if slots is not None else ""
        parts.append(f"Level {level}{suffix}: " + ", ".join(map(display, block.get("spells", []))))
    parts.append(display(record.get("footerEntries", [])))
    return "\n\n".join(filter(None, parts))


def convert(record, legendary_groups=None, rule_targets=None):
    if record.get("source") != "XMM" or "_copy" in record:
        raise ValueError(f"unreviewed monster source or copy: {record.get('name')}")
    kind = record["type"]
    creature_type = kind["type"] if isinstance(kind, dict) else kind
    if isinstance(creature_type, dict):
        creature_type = " or ".join(creature_type["choose"])
    subtype = defense(kind.get("tags")) if isinstance(kind, dict) else None
    hp = record["hp"]
    speed = {}
    speed_order = ("walk", "burrow", "climb", "fly", "swim")
    for name, value in sorted(record["speed"].items(), key=lambda item: (
        speed_order.index(item[0]) if item[0] in speed_order else len(speed_order), item[0]
    )):
        if name == "canHover":
            continue
        if isinstance(value, bool):
            continue
        number = value.get("number") if isinstance(value, dict) else value
        condition = value.get("condition", "") if isinstance(value, dict) else ""
        speed[name] = f"{number} ft. {condition}".strip()
    ac = ", ".join(display(part) for part in record["ac"])
    cr = record["cr"]
    if isinstance(cr, dict):
        cr = cr["cr"]
    actions = []
    sections = (
        ("trait", "traits"), ("action", "actions"), ("bonus", "bonus_actions"),
        ("reaction", "reactions"), ("legendary", "legendary_actions"),
    )
    for source_key, section in sections:
        for order, ability in enumerate(record.get(source_key, [])):
            actions.append({
                "section": section, "name": display(ability["name"]),
                "description": display(ability.get("entries", [])),
                "display_order": order,
                "cost": ability.get("cost") if section == "legendary_actions" else None,
            })
    section_counts = {}
    for ability in record.get("spellcasting", []):
        section = ("innate_spellcasting" if "innate" in ability.get("name", "").lower()
                   else "spellcasting")
        section_counts[section] = section_counts.get(section, 0) + 1
        actions.append({
            "section": section, "name": display(ability["name"]),
            "description": spellcasting(ability), "display_order": section_counts[section] - 1,
        })
    group_ref = record.get("legendaryGroup")
    group = (
        (legendary_groups or {}).get((group_ref["name"], group_ref["source"]))
        if group_ref else None
    )
    if group:
        for source_key, section in (("lairActions", "lair_actions"),
                                    ("regionalEffects", "regional_effects")):
            if group.get(source_key):
                actions.append({"section": section, "name": "Effects",
                                "description": display(group[source_key]), "display_order": 0})
    identity = f"{record['name']}|{record['source']}".lower()
    digest = hashlib.sha256(identity.encode()).hexdigest()[:10]
    name_slug = re.sub(r"[^a-z0-9]+", "-", record["name"].lower()).strip("-")[:100]
    alignment = record.get("alignment")
    alignment_text = (" ".join(ALIGNMENT.get(part, part) for part in alignment)
                      if isinstance(alignment, list) else display(alignment) if alignment else None)
    data = {
        "local_key": f"monster/xmm/{name_slug}-{digest}", "name": record["name"],
        "description": record["name"],
        "source": "XMM", "page": record.get("page"), "group": defense(record.get("group")),
        "size": SIZE[record["size"][0]], "creature_type": creature_type.title(),
        "subtype": subtype, "alignment": alignment_text, "armor_class": ac,
        "hit_points": hp.get("average"), "hit_points_text": str(hp["average"]),
        "hit_dice": hp.get("formula"), "speed": speed,
        "abilities": {key: record[key] for key in ("str", "dex", "con", "int", "wis", "cha")},
        "saving_throws": record.get("save", {}), "skills": record.get("skill", {}),
        "damage_vulnerabilities": defense(record.get("vulnerable")),
        "damage_resistances": defense(record.get("resist")),
        "damage_immunities": defense(record.get("immune")),
        "condition_immunities": defense(record.get("conditionImmune")),
        "senses": defense(record.get("senses")), "passive_perception": record.get("passive"),
        "languages": defense([language for language in record.get("languages", [])
                              if not isinstance(language, str) or
                              "telepathy" not in language.lower()]),
        "telepathy": defense([language for language in record.get("languages", [])
                              if isinstance(language, str) and "telepathy" in language.lower()]),
        "challenge_rating": str(cr),
        "legendary_intro": (
            f"In its lair, the creature can take {record['legendaryActionsLair']} "
            "legendary actions." if record.get("legendaryActionsLair") else None
        ),
        "abilities_and_actions": actions,
        "references": [
            {
                "content_type": "condition",
                "target_key": local_glossary_key("condition", name, source),
            }
            for name, source in sorted(
                {
                    (name, source)
                    for name, source in CONDITION_TAG.findall(
                        json.dumps(record, ensure_ascii=False)
                    )
                    if source == "XPHB"
                }
            )
        ],
    }
    for name, source in set(RULE_TAG.findall(json.dumps(record, ensure_ascii=False))):
        target = (source.casefold(), name.casefold())
        if rule_targets and target in rule_targets:
            data["references"].append(
                {"content_type": "rule", "target_key": rule_targets[target]}
            )
    data["references"] = sorted(
        { (ref["content_type"], ref["target_key"]): ref for ref in data["references"] }.values(),
        key=lambda ref: (ref["content_type"], ref["target_key"]),
    )
    return Monster.model_validate(data).model_dump(mode="json", exclude_none=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--legendary-groups", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path,
        default=Path("src/dndref/datasets/official-5etools-2024/monsters.json"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    groups_bytes = args.legendary_groups.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != XMM_SHA256:
        raise SystemExit("XMM source differs from pinned snapshot")
    if hashlib.sha256(groups_bytes).hexdigest() != GROUPS_SHA256:
        raise SystemExit("legendary groups differ from pinned snapshot")
    records = json.loads(source_bytes)["monster"]
    source_groups = json.loads(groups_bytes)["legendaryGroup"]
    groups = {(group["name"], group["source"]): group for group in source_groups}
    rules_path = args.output.parent / "rules.json"
    rule_targets = {
        (entry["source"].casefold(), entry["name"].casefold()): entry["local_key"]
        for entry in json.loads(rules_path.read_text(encoding="utf-8"))
    }
    converted = sorted(
        (convert(record, groups, rule_targets) for record in records),
        key=lambda item: item["local_key"],
    )
    content = json.dumps(converted, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if args.output.read_text(encoding="utf-8") != content:
            raise SystemExit("monster dataset differs from conversion output")
    else:
        args.output.write_text(content, encoding="utf-8")
    print(f"{len(converted)} monsters")


if __name__ == "__main__":
    main()
