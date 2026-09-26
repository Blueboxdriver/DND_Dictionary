# Character Level-Up

Level Up advances a saved, complete 2024 character by exactly one character level. The ordered `user_character_levels` history remains authoritative; class totals and the sheet summary are derived from it. Characters can advance through level 20. A level-20 character cannot start another advancement.

## Starting a level

Open a complete character sheet and choose **Level Up** from the sheet actions or Commands. The first step lists every existing class track that can advance and exact 2024 builder classes that can be added. Existing tracks are shown as **Advance**. Other class options show whether their structured multiclass requirement is satisfied, unsatisfied, or unresolved. An unsatisfied or unresolved option cannot be selected. Exact class identity and edition are preserved.

Before progression begins, the service validates character creation, saved character state, and derived statistics. An incomplete Draft remains in Character Creation until its blocking choices are resolved. Level-up drafts are held in memory and are discarded when canceled or when the application exits; the saved character is unchanged until confirmation.

## Class advancement and multiclass entry

Advancing a class appends one history row with that class's next level and loads only the structured progression events for that exact class level. It does not apply starting-class or multiclass-entry grants again. A new multiclass appends class level 1 for the exact new class and uses that class's multiclass-entry grants and choices. It does not receive starting-class grants or starting saving throw proficiencies. Multiclass prerequisites are evaluated from normalized requirement records and the prospective character's derived ability scores. Missing or unsupported prerequisite rules remain unresolved and block entry.

Automatic structured progression effects remain derived from the saved history and selected references. The service does not copy feature prose or create duplicate feature records. Required choices are loaded from their event owner, including parent-scoped subclass owners; proficiency, mastery, optional feature, feat, ASI, and subclass choices use the shared choice model. Unresolved grants and prose-only descriptive effects are shown as issues and are not inferred.

## Subclasses and feats

At a structured subclass-selection level, the wizard requires one subclass from the exact parent class and 2024 edition. Subclasses from any 2024 source are available. A chosen subclass is stored against its parent class track; multiple class tracks can retain separate subclasses. Later levels load events from that exact subclass. A stale subclass identity stays unresolved rather than being replaced by a same-name option.

Feat and Ability Score Improvement selections come from structured progression choices. Feat prerequisites are read from the owner's normalized requirement record and evaluated against the prospective character. Options with unmet or unresolved requirements cannot be selected. Structured feat ability adjustments are stored as sourced modifications; base scores are not overwritten. The derived-statistics service applies those modifications and reports the resulting scores. Ability-increase limits are enforced when the metadata represents a maximum; the service does not invent a maximum absent from the rules data.

## Hit Points

Every added class level records either the fixed published average (`floor(Hit Die / 2) + 1`) or a manually entered result from 1 through that class's Hit Die. Level-up never rolls automatically. The first character-level maximum Hit Die rule does not apply to a later multiclass entry. Constitution is added by `DerivedCharacterService`, including changes from an ASI or feat. The preview displays projected maximum HP from that service.

## Spellcasting

The selected class or subclass's structured spellcasting progression drives new cantrip, known, prepared, spellbook, and Pact Magic choices. Candidate spells are filtered by exact spell-list owner, 2024 edition, acquisition type, and that class or subclass's own accessible spell levels. Shared multiclass slots do not grant spell access to a lower-level class. Automatic granted spells remain derived with their grant provenance and do not consume ordinary choices unless the structured rule says so.

The preview shows individual spell access and capacity, plus changes to the shared **Standard Slots** pool. Pact Magic slots are shown separately with their own count and slot level. Standard caster contributions such as full, half, half-round-up, and third progression are left to `DerivedCharacterService` and its structured contribution metadata. Prose-only spell replacement or prepared-spell effects are not guessed; structured choice rules are required.

## Preview, commit, cancel, and undo

Every wizard step edits an in-memory prospective character. The preview includes the next history level, class level, automatic progression events, pending decisions, HP choice, spell progression, derived changes, and blocking or warning issues. Searchable lists render a bounded page at a time. Reference details open through dictionary navigation and return to the same wizard step and list state.

The review step requires explicit confirmation. Commit rechecks that the saved character still matches the draft, then writes the class history, HP decision, subclass, choices, feat and ability modifications, and spell decisions in one database transaction. Any failure rolls the entire transaction back. Cancel discards the draft without database cleanup.

**Undo Last Level** removes only the final ordered level and deletes decisions whose provenance belongs to that character level or class level, including HP, subclass selection, feats/ASI, spells, equipment, currency from that level's choices, and other choice resolutions. Older and unrelated decisions remain. The starting level cannot be undone here. Arbitrary middle-level edits are not supported; undo later levels first, then add the intended progression.

Level-up does not track current HP, expended spell slots, rests, death saves, combat actions, or other live-play resources.
