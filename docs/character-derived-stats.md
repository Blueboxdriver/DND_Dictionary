# Derived Character Statistics

Milestone 25 adds a read-only rules engine for 2024 character statistics. It consumes persisted character decisions and the normalized character-builder rules imported by Milestone 22. It does not write derived values back to the character, cache them, or add a character-sheet UI.

## API and result

Use `dndref.derived_character.DerivedCharacterService`:

```python
engine = DerivedCharacterService(database)
result = engine.derive_character(character_id)
# Or derive a previously loaded Character snapshot:
result = engine.derive(character)
```

The immutable result types live in `dndref.models.derived_character` and are re-exported from `dndref.models`. `DerivedCharacter.to_dict()` returns a deterministic JSON-compatible snapshot. Numeric values that can be incomplete use `DerivedValue` or a result with a `complete`, `partial`, or `unresolved` state. `issues` carries warnings, validation errors, stale references, and unresolved rules with category and source identity.

Only 2024 characters are calculated. Other editions return unresolved values and an `unsupported_edition` error. The service resolves exact identities; it never reconnects an unavailable reference by name.

## Persisted inputs and calculated values

The character tables remain authoritative for player decisions: base ability scores, sourced ability changes, ordered class-level history, subclass and feat selections, spell selections, equipped/carried items, choices, and later-level Hit Point choices. No migration was needed for this milestone.

The engine derives final ability scores by adding persisted sourced adjustments to base scores. `ability_modifier(score)` is the shared implementation used by checks, saves, skills, initiative, attacks, and spellcasting. Proficiency Bonus follows total character level: `2 + floor((level - 1) / 4)`. A Fighter 3 / Wizard 2 therefore has a +3 bonus.

Effective proficiencies include active starting-class grants, multiclass-entry grants, species/background/feat grants, progression features, and saved choices. Repeated grants retain distinct provenance but do not stack. Structured `expertise` grants are represented as `expertise` and double Proficiency Bonus once. The current production catalog has no normalized Expertise grant; the engine warns when an active feature describes Expertise only in prose.

All six saving throws and all 18 skills are calculated. Starting-class saving-throw grants are kept separate from multiclass-entry grants. Skill-to-ability associations come from the normalized Milestone 22 skill map. Passive Perception is `10 + Perception modifier`; Initiative starts with Dexterity. Structured static bonuses are included when available. Situational prose is not executed.

Movement speeds come from structured species movement metadata and structured speed grants. Walk, burrow, climb, fly, and swim are kept as separate entries. Conflicting structured speeds are unresolved.

## Armor Class

AC uses exact equipped Item references and Milestone 22 armor metadata. The engine calculates the unarmored baseline as `10 + Dexterity`, or uses an armor base AC with its full, capped, or absent Dexterity contribution. One shield bonus may then be added. Armor and shield are separate equipment categories.

More than one equipped armor item or shield is an error. Missing equipped-item metadata makes AC unresolved rather than silently selecting the unarmored baseline. The current rules catalog has no normalized alternative AC formula model. If an active feature's text mentions AC or Unarmored Defense, AC is marked unresolved; the prose is not interpreted.

## Hit Points and Hit Dice

The first character level receives the class Hit Die maximum plus Constitution modifier. Each later ordered level uses the Hit Die for the class gained at that level, plus the current Constitution modifier. Fixed-average choices are checked against `floor(Hit Die / 2) + 1`; rolled choices must be within the die. Missing or conflicting choices leave maximum HP unresolved. Since Constitution is read on every derivation, a changed score recalculates all levels.

Hit Dice are summarized by size and class identity. Different die sizes are never combined. Permanent Hit Point bonuses in prose are not applied; if an active feature appears to change maximum HP, the numeric maximum is withheld as unresolved.

## Features and attacks

Features use exact class-feature, subclass-feature, feat, species, or background references plus source provenance. Same-name features from different owners remain distinct. Full text is left in the dictionary.

Carried, non-stowed weapons with enough structured metadata produce attack summaries with exact weapon reference, selected ability, proficiency state, attack bonus, damage, damage type, range, property labels, and known mastery labels. Ranged weapons use Dexterity; melee weapons use Strength; Finesse weapons use the higher available Strength or Dexterity modifier for the displayed baseline. Proficiency Bonus is applied only for an effective weapon proficiency. A missing `attack_type` or property reference leaves the attack unresolved. Attack and damage rolling are outside this service.

## Spellcasting profiles and slots

Each active class or spellcasting subclass receives its own profile with owner identity, class level, spellcasting ability, save DC, attack bonus, class-level cantrip/prepared/known counts, individual spell access, individual slots, and spell selections. Species and feat spell access remains in separate source profiles. Known, prepared, spellbook, always-prepared, cantrip, innate, and granted spells preserve their acquisition and provenance.

Spell Save DC is `8 + that profile's ability modifier + character Proficiency Bonus`; spell attack is that ability modifier plus Proficiency Bonus. Spell access uses that profile's own standalone progression. Shared multiclass slots never increase an individual class's level for learning or preparing spells.

For standard Spellcasting progressions, the engine reads class/subclass contribution mode and individual progression from normalized rules. Full, half-round-up, half-round-down, and third-round-down modes are handled generically. Artificer, Paladin, and Ranger use the contribution modes present in the imported 2024 metadata. Third-caster subclasses use their own structured subclass metadata.

When multiple progressions contribute, the engine sums their effective contributions and uses one 2024 multiclass slot table. The M22 catalog currently stores each class's individual slots but not the shared multiclass table, so that table is represented once in the engine. An unknown contribution mode makes shared slots unresolved rather than contributing zero. No custom per-class schedule exists in the current metadata schema; it cannot be inferred.

Pact Magic remains separate from standard Spellcasting. Warlock class level determines its Pact Magic slot count and level; those slots are returned in `pact_magic` and are never added to standard slots. A Wizard / Cleric / Warlock build can therefore return two full-caster profiles, one Pact Magic profile, the combined standard pool, and the separate Pact Magic pool.

Persisted spell selections are validated against exact 2024 spell references, class-level spell access, available list membership, structured source access, and acquisition semantics. Invalid choices stay saved and appear with validation issues. Structured automatically granted spells appear as granted or always-prepared entries and are not repeated as player choices.

## Data safety and performance

The service reads normalized SQLite rules and saved character rows; it does not load or parse the 4.55 MB builder JSON. Rules, items, spells, and saved references are fetched in batches. `CharacterService` also resolves referenced spell/item/feat rows in kind-based batches, so query count is bounded by reference groups and chunk size instead of individual spells or items. No derived-result cache is used.

Prose-only effects are never executed. AC, Initiative, Expertise, possible attack-bonus changes, and permanent maximum-HP changes are reported when detected but remain uncalculated. A stale exact reference only makes dependent values unresolved; unrelated values continue to derive.

## Current limits

- No normalized alternative AC formula or generic static-combat-bonus records exist in the current builder catalog.
- Structured Expertise is supported by the grant model, but current production Expertise is prose-only.
- Unknown/custom multiclass contribution schedules are not calculable without structured per-level contribution data.
- Weapon attacks are summaries, not action choices; Finesse displays the higher ability baseline.
- Resource uses, current HP, spell-slot expenditure, combat state, and user-facing sheet workflows are not part of this milestone.
