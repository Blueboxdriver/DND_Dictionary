# Milestones 9/10: community structured-data ingestion

This content-source pivot supersedes the original SRD-only Items/Classes scope.
The existing Milestone 8 SRD pack, converter, inventories, and tests remain intact.
No Milestone 11 image work or Milestone 12 release work is included.

## Source and snapshot

The live [5e.tools site](https://5e.tools/) links to
[5etools-mirror-3/5etools-src](https://github.com/5etools-mirror-3/5etools-src)
as its current source mirror; the 2014 site/repository is separate. Verified
2026-09-22. This is the community provider, not the publisher of the rules.

The production conversion uses version **2.36.0**, commit
`3a09c05a3a3be94423cd2b3c33936034eeae02f2`. Every consumed input file is
SHA-256 recorded in `inventory/reconciliation.json`. Only local structured JSON
is converted; no rendered HTML is scraped and the runtime makes no network calls.

`src/dndref/datasets/official-5etools-2024/` is the separate
**Official D&D 2024 Reference** pack. Its dataset ID is `official-5etools-2024`.
It is for personal local reference, contains non-SRD text, and carries a
proprietary-content license identifier rather than the SRD's CC-BY license.
Each record cites its actual book; the provider is recorded at dataset level.
Per-record pages, input identities, file paths, and normalized-record hashes
are retained in inventories, not copied as giant upstream objects into the pack.

## Explicit inclusion policy

| Code | Book |
| --- | --- |
| XPHB | Player's Handbook (2024) |
| XDMG | Dungeon Master's Guide (2024) |
| XMM | Monster Manual (2025) |
| FRHoF | Forgotten Realms: Heroes of Faerûn |
| FRAiF | Forgotten Realms: Adventures in Faerûn |
| EFA | Eberron: Forge of the Artificer |
| RHW | Ravenloft: The Horrors Within |
| AU | Arcana Unleashed |

`BOOKS` in the converter is the reviewed allowlist. Book titles and the
`Wizards RPG Team` author identity must still match `data/books.json` or
conversion stops. Publication dates are provenance, not an official-status test.
Upstream `CONTRIBUTING.md` describes the main repository's WotC-content policy;
`js/parser.js` additionally distinguishes legacy, nonstandard, partnered, and
core sources. Main-repository membership alone is insufficient for inclusion.

All other sources are excluded, including unreviewed official supplements and
adventures. Homebrew/prerelease directories are never loaded. Homebrew flags,
UA source codes, legacy edition flags, and unapproved source codes are rejected.
This is complete coverage of the supported records in the eight selected books,
not a claim to contain every compatible official publication.

Items and classes can carry `edition: "one"` (2024) or `"classic"` (2014).
Many spells/feats lack this field, so their approved book source is decisive.
`isReprinted` excludes a record; `reprintedAs` suppresses an older record only
when its explicitly named replacement exists and is eligible. Matching names
alone never imply replacement. Distinct eligible records remain distinct.
`otherSources` and `referenceSources` are not replacements or permission to
promote an old record into the new edition.

Subclasses must cite a selected class through `className`/`classSource` and
pass the same source/edition filter. A legacy subclass pointing at XPHB alone
does not establish compatibility. No unreviewed historical options are included;
future additions require explicit source/compatibility review.

Vehicles and mounts are excluded even when supplied in upstream item files.
No Monsters, Species, Backgrounds, Conditions, Rules, or other categories are
added. References to these remain readable names, not imported stat blocks.

## Upstream structure and mappings

| Input | Use |
| --- | --- |
| `data/books.json` | Book identities and publication metadata |
| `data/items.json` | Named items and item-group shared text |
| `data/items-base.json` | Base equipment, types, properties, mastery, text templates |
| `data/magicvariants.json` | Applicability constraints and inherited magic-item variants |
| `data/spells/index.json`, `spells-*.json` | Structured spell fields and entries |
| `data/generated/gendata-spell-source-lookup.json` | Actual unconditional spell/class references |
| `data/feats.json` | Categories, prerequisites, ability increases, ordered benefits |
| `data/class/index.json`, `class-*.json` | Classes, subclasses, feature UIDs, progression tables |
| `data/class/fluff-class-*.json` | Class introductions |
| `data/optionalfeatures.json` | Class-owned choices, embedded in existing class text/sections |
| `data/book/book-xphb.json` | Exact multiclass rules and proficiency-bonus table |

The converter resolves `_copy` inheritance and supported modification operations,
`{#itemEntry ...}` templates, and nested feature references. Unknown modification
operations, entry types, inline tags, missing template fields, unresolved references,
and mismatched feature owners stop conversion rather than silently dropping rules.

Magic variants are expanded against approved base items using upstream `requires`
and `excludes`. Each generated item records both variant and base identities.
Shared item-group rules (including paired-artifact rules) and generic variant text
are embedded in the relevant item sections; navigation-only groups are reported as
excluded aggregates. Range/versatile values and conditional notes stay on property
references. Mastery definitions remain readable item text. Armor AC is display text;
magic AC bonuses are shown separately without calculating a final AC.

Class and subclass features are separately owned records with source-qualified
UIDs, levels, and original traversal order. Progression columns come from upstream
table labels and row values; proficiency bonuses come from the published table.
Subclass progression tables remain Markdown in the subclass introduction because
the current schema has a dedicated progression structure only for base classes.
Invocations, maneuvers, and similar class options remain embedded reference text,
not new application categories. Conditional subclass/feat spell access stays in
feature text; it is not misrepresented as unconditional base-class spell access.

Keys contain category, source, readable name slug, and a short SHA-256 suffix of
the upstream UID. Feature UIDs include owning class/subclass and level. The suffix
prevents punctuation collisions without making existing keys depend on which
other records are present. Text/page edits do not change keys; upstream identity
renames do. No SQLite IDs, timestamps, random values, or array-position identities
are used.

Two explicit upstream repairs are in `PROPERTY_OVERRIDES`: XPHB **Staff** and
**Wooden Staff** contain the sourceless property `V`; these reference XPHB
Versatile. No global reinterpretation of legacy property sources is performed.

## Rebuild and reconcile

Use the existing locked Python environment (`uv sync --extra dev`). Obtain a
snapshot deliberately, outside the application directory:

```sh
git clone https://github.com/5etools-mirror-3/5etools-src.git /tmp/dndref-5etools-snapshot
git -C /tmp/dndref-5etools-snapshot checkout 3a09c05a3a3be94423cd2b3c33936034eeae02f2
PYTHONPATH=src .venv/bin/python tools/build_5etools_dataset.py /tmp/dndref-5etools-snapshot
PYTHONPATH=src .venv/bin/python tools/build_5etools_dataset.py /tmp/dndref-5etools-snapshot --check
.venv/bin/dndref validate src/dndref/datasets/official-5etools-2024
.venv/bin/dndref import src/dndref/datasets/official-5etools-2024 --dry-run
.venv/bin/dndref import src/dndref/datasets/official-5etools-2024
```

For an unpacked source archive, pass `--revision FULL_COMMIT_HASH`. `--output`
supports a review directory. Conversion validates Pydantic models, references,
inventory/ownership equality, variant expansion counts, and the existing loader's
storage invariants before writing production output. It refuses to overwrite a
different dataset ID. `--check` reconverts and compares every generated byte and
the exact JSON file set without changing the pack.

Inventories cover items, properties, spells, feats, classes, subclasses, class
features, subclass features, embedded dependencies, and expanded variants.
`excluded.json` accounts for rejected candidate records. `reconciliation.json`
records the revision, input/output hashes, counts, and exclusion reasons.

To update, obtain a newer snapshot, convert into a review directory, inspect
inventories/exclusions and the content diff, rerun validation/tests, then import
the approved snapshot through the existing importer. Source additions are
deliberate edits to `BOOKS`, not automatic admission by date. Review any changed
upstream identities and refresh the pinned counts/revision in content tests.
There is no updater daemon, startup download, or runtime dependency on 5etools.

## Verification and limits

Focused tests: `.venv/bin/pytest tests/test_5etools_dataset.py`.
Full checks: `.venv/bin/pytest`, `.venv/bin/ruff check .`, and
`.venv/bin/python -m compileall -q src tests tools`.

Tests cover deterministic synthetic conversion, source/reprint filtering,
collision-safe keys, representative mappings, exact production inventories,
references, CLI validation/import, no-op reimport, replacement, SRD coexistence,
FTS search, detail rendering, and the real Textual progression table.

Upstream artwork, tokens, sound, VTT automation, search tags, alternate
`_versions` character-builder presets, and redundant mechanical index metadata
are omitted. Text hyperlinks become offline display text. External stat blocks
remain named references. The supported rules remain reference text; there is no
character builder, prerequisite evaluator, AC calculator, or combat simulation.
No production model, importer, or search changes were required. One generic TUI
fix guards horizontal progression-table actions when scrolling is unavailable,
preventing an uncaught Textual `SkipAction` during layout changes; a regression
test covers it.
