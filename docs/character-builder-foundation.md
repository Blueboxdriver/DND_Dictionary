# Milestone 22: Character-builder rules foundation

Milestone 22 packages deterministic, structured 2024 character-building rules
alongside the official reference dataset. `character-builder.json` is generated
from the same pinned 5etools source snapshot as the rest of that dataset. It is
validated during dataset load and imported into migration-010 tables. Dataset
attribution and book citations remain in the shared manifest because the builder
catalog uses the same reviewed sources.

The catalog contains 13 classes, 76 subclasses, 19 species, 65 backgrounds,
179 feats, 70 optional features, 18 skills, and 1,682 weapon/armor metadata
records. The equipment records include 1,252 weapons and 430 armor entries. The
database import contains 2,104 owner rows, 1,610 grants, 433 choices, 1,321
choice options, 189 requirements, 741 progression events, 12 spellcasting
owners, 240 spellcasting-level rows, 1,520 spell-slot progression rows, 307
spell-access rows, 71 species traits, and 18 skill rows.

## Rule records

`Grant` describes an automatic effect such as a proficiency, feature, feat,
spell, weapon mastery, speed, size, or equipment reference. A grant carries a
stable grant key, source-rule path, optional exact reference, and optional
activation requirement. Unresolved references are marked explicitly.

`Choice` describes a player decision. It records the number of selections and
either explicit options or criteria for a filtered choice. Options can carry
references, ability increases, or grants. Requirements, exclusions, and
conditional dependencies retain source rule paths.

Choice identity is owner-scoped. The effective identity is
`(owner_type, owner_key, choice_key)`. The same `choice_key` is valid for two
different owners; duplicates inside one owner are rejected. A conditional
choice resolves its parent choice and parent option only inside that same owner.
The normalized tables use the same composite identity, and
`CharacterBuilderRules.get_choice_definition()` requires the owner type, owner
key, and choice key.

`Requirement` is a typed rule tree. `all` and `any` nodes combine typed leaves
for abilities, class membership or level, total level, proficiency, feats,
spellcasting, edition, or class-entry mode. A source condition that cannot be
normalized retains its source text as an unresolved requirement; it is not
silently treated as satisfied.

`ProgressionEvent` groups grants and choices at a class or subclass level. Its
level is a class level. Class feature references retain the existing class or
subclass feature identity rather than duplicating the full feature text.

Class starting grants and multiclass-entry grants are separate records and
queries. They can differ: a character starting as a Fighter receives saving
throw proficiencies, while a character multiclassing into Fighter does not.
`CharacterBuilderRules.get_starting_class_grants()` and
`get_multiclass_grants()` keep the distinction explicit.

`CharacterRuleContext.total_level` represents the character's total level;
`class_levels` stores levels in each class. Multiclass entry is checked for a
new class after level 1 and uses the target class's published primary-ability
threshold plus the current classes' primary-ability thresholds. Progression
events and subclass selection use class level. The rules query surface does not
infer missing ability scores or class membership.

## Coverage and limits

### Classes, subclasses, species, and backgrounds

All 13 class owners in the reviewed 2024 dataset load: Artificer, Barbarian,
Bard, Cleric, Druid, Fighter, Monk, Paladin, Ranger, Rogue, Sorcerer, Warlock,
and Wizard. Starting grants, multiclass grants and requirements, starting
equipment choices, and source-backed progression events are queryable.

The 76 subclasses retain their parent class, selection level, and structured
progression feature events. Subclass progression tables that the source exposes
as table groups remain Markdown in the subclass introduction. Feature effects
that cannot be safely mapped to a typed grant remain in their feature text.

The 19 species retain creature types, sizes, movement, darkvision, structured
grants, source-backed choices, spell access, and trait text where the source
provides those fields. Species coverage follows the converter's reviewed book
allowlist; it is not a promise to include every compatible publication. Complex
trait effects and conditional prose remain descriptive text.

The 65 backgrounds expose skill/tool proficiencies, ability-score choices,
origin-feat references, and starting-equipment packages. Equipment options with
an exact dataset item reference are linked. Generic focus names that do not
identify one exact item are retained as unresolved source references.

### Feats and optional features

All 179 feats in the dataset have builder records with source, category,
repeatability, parsed prerequisites, structured ability/proficiency grants,
choices, and spell access where the source exposes those fields. Detailed feat
benefit prose remains in the reference feat record; the catalog is not a complete
mechanical evaluator.

The 70 optional features have stable owner keys, source, feature types,
prerequisites, repeatability, and descriptions. The catalog does not add a new
search category for optional features.

### Spellcasting

There are nine class spellcasting owners and three spellcasting subclass owners.
The catalog distinguishes the standard `spellcasting` model from Warlock
`pact_magic`, and stores acquisition as prepared, known, spellbook, or special.
The standard spellcasting model uses prepared acquisition for Artificer, Bard,
Cleric, Druid, Paladin, Ranger, and Sorcerer; Wizard uses a spellbook. Warlock
uses the separate Pact Magic model and has prepared acquisition metadata. Three
third-caster subclasses are represented: Eldritch Knight, Arcane Trickster, and
Warrior of the Mystic Arts.

Class-level cantrip, prepared-spell, known-spell, and slot progressions are
stored separately from total-level spell access. Pact Magic slot progressions
use their own progression kind and the `pact_magic_separate` multiclass
contribution. No final multiclass slot total is calculated. Dynamic source spell
selectors with supported class and level filters become `spell_list` criteria;
exact spell identities remain references. Access rules whose source expression
cannot be parsed keep the source expression in their unresolved detail field.

### Equipment metadata

The 1,682 structured equipment records include 1,252 weapons and 430 armor
records, including shields. Weapon rows carry category, attack type, damage,
range, properties, mastery references, ammunition references, and versatile
damage where supplied. Armor rows carry category, base AC, Dexterity rule/cap,
Strength requirement, and stealth disadvantage.

The pinned source leaves attack type unavailable for 41 weapon metadata rows.
Those rows retain `attack_type` in `unresolved_fields`; the converter does not
infer melee or ranged status from names or prose. Ranged weapon ranges supplied
as strings such as `150/600` are parsed as normal and long range in feet.

## Reference integrity and source-driven exceptions

Every reference marked resolved is checked against the packaged production
database, including class and subclass features, classes, subclasses, feats,
spells, items, optional features, and Rules. The pinned catalog has no invalid
resolved references. Five item references are intentionally unresolved because
the source names a generic focus rather than one exact item: three Holy Symbol
occurrences, one Druidic Focus, and one Arcane Focus. They remain visibly
unresolved and are not linked by approximate name matching.

The source also contains verbose feature and benefit prose that is preserved in
the reference records instead of guessed into mechanical effects. Subclass
progression tables and conditional trait text are examples of source content
that remains prose-backed. These limitations are retained in the normalized
metadata rather than filled with inferred mechanics.

## Database behavior and Milestone 23 contract

Migration 010 stores builder owners, requirements, grants, choices and options,
progression events, spellcasting progressions, spell access, equipment, traits,
and skills. A normal startup imports the packaged catalog automatically.
Reimporting identical content is a no-op; replacing a dataset snapshot removes
its old builder rows in the same transaction. The migration-009 upgrade test
checks that the previous production pack is recognized, migration 010 runs,
builder records appear without a manual import, reference entries stay intact,
and Favorites, Collections, Tags, Notes, and Recent Searches survive.

Ordinary category browsing uses the installed reference tables and does not
query or deserialize `character-builder.json`. The source-hash check detects the
new packaged file on an unchanged warm startup and avoids repeating the import.

Milestone 23 uses these stable keys, owner-scoped choices, and rules queries for
saved character state. It adds no derived statistics or guided character UI; see
[Character persistence](character-persistence.md) for the new tables, service,
and stale-reference behavior. Final spell-slot calculation remains future work.
