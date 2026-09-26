# Guided Character Creation

Character Creation builds a saved 2024 character at level 1 with one starting
class. It does not calculate combat statistics, create a final character sheet,
or support level-up and multiclass workflows.

## Start, save, and resume

Use Commands → Open Characters to open, resume, rename, duplicate, or delete a
saved character. Commands → New Character immediately creates a local Draft and
opens the builder. Choices are written as they are made, so closing the builder
does not discard progress. Reopening a Draft derives the next step from the
character's saved name, references, choices, ability scores, equipment, and
spells. Completed level-1 characters reopen at Review and remain editable.

The manager renders the persistence service's summary rows. It does not load
full character aggregates for the list.

## Creation steps

The builder guides the user through Name, Species, Background, Starting Class,
Ability Scores, required choices, Starting Equipment, Spells when needed, and
Review. Choice steps appear only when the selected published rules provide
choices. A progress header shows the current position. Reference actions open
the existing published entry details; Back returns to the same builder state.

Species, Background, Class, Feat, Spell, and Item decisions retain exact
published identities and edition. Species and Background grants are read from
the structured character-builder catalog. The Background's exact Origin Feat,
ability increases, and equipment grants are applied through the rules and
character services. Background increases remain separate from base scores.

The first Class selection creates the ordered level-1 class-history row and
applies its `starting_class` grants and choices. Multiclass-entry grants are not
used. Shared Choice handling covers explicit options, filtered references, and
conditional equipment choices. Weapon Mastery selections point to eligible
mundane Item references and use the class's structured weapon proficiencies.

## Ability scores

- **Standard Array** assigns 15, 14, 13, 12, 10, and 8 once each.
- **Point Buy** uses the 2024 27-point budget, scores from 8 to 15, and costs
  0/1/2/3/4/5/7/9 points for scores 8/9/10/11/12/13/14/15.
- **Manual Entry** accepts externally rolled base scores from 3 through 18.

All six base scores must be entered. Background increases show separately in the
preview and do not overwrite those base values.

## Equipment and spells

Starting equipment options come from published package data. Selecting a
package applies its exact item, quantity, currency, and provenance grants.
Generic equipment that has no exact Item record stays as an unresolved label and
appears as a Review warning; the builder does not guess a specific item.

The Spells step appears for level-1 class, Species, or Feat spell choices.
Search and bounded pages keep large spell lists manageable. The step preserves
the source's acquisition model: cantrips, known spells, prepared spells,
spellbook selections, and Pact Magic remain distinct. Spell options are limited
to the exact 2024 class lists and levels supplied by metadata. This milestone
does not calculate spell slots.

If a required spell choice is present only as prose and cannot be filtered
safely, Review reports a blocking issue and the character stays a Draft. The
builder does not guess a count or legal spell list.

## Review and edits

Review summarizes identity, class and level, base scores and increases, feats,
selected choices, equipment, currency, and spells. Structural validation and
builder-choice validation run before completion. Missing required decisions and
stale references block completion; unresolved generic equipment is a warning.
Mark Complete is available only when no blocking errors remain.

Replacing Species, Background, or Starting Class runs transactionally. The
service removes decisions and grants tied to the previous exact owner
provenance, including dependent choices and feat-owned data, then applies the
new metadata. Unrelated choices with matching display values remain. If
replacement fails, the previous saved character state is restored.

## Persistence and limits

Migration 012 adds draft name confirmation, the selected ability-score method,
and currency records required by starting equipment. The current step and
progress are derived rather than copied into a second persisted workflow state.
Published-data replacement preserves the character and its stored references;
references absent from the installed data are shown as missing and are not
relinked by name.

No AC, initiative, proficiency bonus, saves, skills, passive Perception, HP,
attack modifiers, spell save DC, final spell slots, resource use, PDF export,
or cloud sync is implemented. Level-up, adding multiclass levels, 2014 builds,
and homebrew content are outside this milestone.
