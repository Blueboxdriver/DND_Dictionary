# Milestone 23: Character persistence

Characters are local, user-owned records in the application's SQLite database.
Migration `011_character_persistence.sql` creates a separate `user_characters`
aggregate and `user_character_*` child tables. Dataset import and replacement
delete imported rows through their dataset foreign keys; character rows have no
foreign keys to published data and therefore survive those operations.

## Stored state

Characters are restricted to the published 2024 edition. A new character starts
as a `draft` with a stable UUID, schema version, timestamps, optional species and
background references, and a plain-text character note. Drafts may have no class
or any other incomplete state. A character can be marked `complete`; validation
reports missing required structure without rewriting or deleting draft data.

Every class level is one row in `user_character_levels`, ordered by total
character level. Each row stores the exact class identity, its display snapshot,
edition, and resulting level in that class. The first row identifies the starting
class. For example, Warlock 1, Warlock 2, Fighter 1, Fighter 2, Fighter 3,
Warlock 3 is six ordered rows. Total level, per-class levels, starting class, and
the class summary are calculated from this history; aggregate counts are not
stored as authoritative data.

Subclasses are stored per exact class track, so a future multiclass character can
have a distinct subclass for each class. The selected class level is retained.
Subclass parent and selection-level checks use published parent identity and
builder metadata when available.

Base ability scores are stored separately from sourced modifications. Each
modification can retain its background, ASI, feat, or other provenance, owner
choice, and character/class level. The service does not calculate final ability
scores. Likewise, per-level HP rows store the player's fixed-average or rolled
amount; they do not store max HP.

Choice records are scoped by rules dataset, owner type, owner key, choice key,
and character/class-level context. This preserves the Milestone 22 owner-local
choice identity and allows the same key on different owners. Explicit options
are checked against the normalized published choice record and selection count.
Criteria-based selections retain the chosen value or exact reference; cases the
current metadata cannot verify must be marked unresolved. Provenance stores the
granting owner and level context instead of copying rules prose.

Feat records retain exact feat identity and grant provenance. Spell records keep
the exact spell plus an acquisition kind such as cantrip, known, prepared,
always-prepared, spellbook, Pact Magic, or innate, and optional class/choice
context. Equipment is character-owned state: an exact Item reference or an
explicit unresolved generic selection, quantity, equipped flag, and optional
carried/stowed state. It does not copy Item descriptions. Containers, coins,
attunement, charges, ammunition, and depletion are outside this milestone.

## Published references and replacement

Published entries use the repository's stable identity convention:
`dataset_id:local_key`. Subclasses use
`dataset_id:subclass:parent_local_key:subclass_key`. Character rows store these
logical identities and a display-name snapshot. They do not foreign-key into
imported rows and do not use names to find replacements.

On read, the service resolves each identity exactly. If the entry is absent or
has an incompatible edition, the saved decision remains and its reference is
marked missing. If that exact identity returns, it resolves again. A same-name
entry under another dataset or key is never substituted. This follows the
existing Favorites and Collections stale-reference behavior.

## Service surface

`dndref.characters.CharacterService` is independent of Textual screens. Its API
covers create/read/list, rename/state change, duplicate/delete, species and
background selection, ordered class-level mutation, subclass selection,
ability-state replacement, choice resolution/removal, feat/spell/equipment
records, HP choices, notes, class-entry/subclass validation hooks, and a
structured character validation report.

Multi-table changes use SQLite transactions. Duplicate copies all character
children and notes under a new ID in one transaction. Deleting a character
cascades only to its user-owned child tables; it never deletes imported data.
Removing a level is limited to the latest total level and removes decisions
bound to that level or to class levels that no longer exist.

`list_characters()` returns name, edition, state, level count, class summary,
and updated timestamp from one aggregate query. It does not load notes, choices,
spells, feats, or equipment. Character services are not invoked during normal
dictionary browsing. Saved characters stay out of Universal Search and the
entry-based Recently Viewed history; character navigation can be added
separately later.

The in-memory `Character` model exposes structural derivations such as
`total_level`, `class_levels`, `starting_class`, and `class_summary`. Its
`to_dict()` method provides a stable JSON-compatible representation for
round-trip checks. It does not calculate proficiency bonus, ability modifiers,
initiative, AC, saving throws, skills, passive Perception, spell DCs, spell
slots, attack bonuses, or final HP.

## Backup and next milestones

The database file at
`${XDG_DATA_HOME:-~/.local/share}/dndref/dndref.sqlite3` contains imported
references and the local user-owned Favorites, Collections, Tags, entry Notes,
Recent Searches, and Characters. Back up this SQLite file to preserve that
state. There is no cloud sync or user-facing character import/export.

Milestone 24 can use the service to populate an interrupted draft through
guided choices. Milestone 25 can derive character statistics from ordered class
levels, stored ability inputs, choices, grants, spells, and equipment without
changing the persisted inputs into authoritative calculated values.
