# Character Sheet

The Characters screen opens a saved character into a terminal-native sheet. The sheet reads the persisted `Character` aggregate and one `DerivedCharacterService` result. It does not own a second character model or calculate game statistics.

## Sections and controls

The sheet has seven sections: Overview, Skills, Combat, Features, Spells, Equipment, and Notes. Left and Right change sections. Up and Down move through the current section; Enter opens a selected published reference or secondary detail. `Ctrl+P` opens section and character actions. `/` filters Skills, Features, Spells, or Equipment. Long sections mount at most 24 rows at a time and use `Ctrl+PageUp` / `Ctrl+PageDown` to change pages. Each section retains its selection, scroll, page, and search query when switching sections.

The compact header retains the character name, Draft or Complete state, edition, level/class summary, species, background, and a short derived-stat summary. Overview shows all ability scores and modifiers, identity, subclasses, HP maximum, AC, initiative, speed, proficiency bonus, passive Perception, and derived issues. Skills shows all derived skills and saving throws with ability, final modifier, and proficiency or expertise label. Selecting values with calculation components opens a provenance pane.

Combat shows maximum HP, AC and its available components, initiative, speed, separate Hit Die pools, equipped records, engine-provided weapon attack summaries, and combat-related derived issues. It does not track current HP. Features are grouped by Class, Subclass, Species, Background, Feats, and Other, with concise source and level metadata. Feature names and exact references come from the derived model; the sheet does not copy feature prose.

## Spellcasting

Each spellcasting profile remains separate and shows its owner, class level where available, spellcasting ability, Spell Save DC, Spell Attack Bonus, accessible spell levels, and selected spells grouped by acquisition type (Cantrips, Known, Prepared, Spellbook, Always Prepared, Granted, Pact Magic, or Innate). Spell rows show level and school when the derived result provides them.

The shared Standard Spell Slots pool is displayed separately from each profile's spell access. Pact Magic is shown in its own block. A character's shared higher-level slots therefore do not change the accessible levels shown for an individual class profile. The sheet displays the derived results and does not recalculate caster level, DC, attack bonus, or slots.

## Equipment, notes, and unresolved values

Equipment comes from persisted character state and is grouped as Equipped, Carried, or Stowed. Exact Item references open the dictionary detail; generic unresolved labels remain plain text. Equip and Unequip are available for a selected equipment record through Commands and persist through `CharacterService`; equipment changes trigger a fresh derivation. No encumbrance or attunement is tracked.

Character-level notes are displayed and edited with the existing multiline editor. Saving uses `CharacterService.update_note`; Cancel leaves the saved note unchanged. These are separate from notes attached to dictionary entries.

The header and Overview expose derived issues. The issue pane reports the engine's severity, category, message, and source identity. Incomplete or unresolved derived values render as Partial, `?`, or an incomplete label; the sheet does not fill them from prose or guesses. Drafts open normally and keep Resume Character Creation available. Edit Character reopens the level-one builder; it does not add level-up support.

## Reference navigation and lifecycle

Exact published Item, Spell, Feat, Class, and Subclass identities use the existing dictionary detail views, image lifecycle, Back/Forward history, and Recently Viewed behavior. The sheet saves section selection and scroll context while a published detail is open and restores it when Back returns to the originating browser location. Species and Background records that exist only in the builder catalog use the existing concise builder-reference view.

The character itself is not added to entry-based Recently Viewed. Published records opened from the sheet continue to appear there. The sheet does not track current HP, expended Hit Dice, used spell slots, rests, death saves, conditions, combat actions, or resource counters.
