# D&D Reference

D&D Reference (`dndref`) is a Linux-first, offline terminal browser for D&D
items, spells, feats, classes, subclasses, and monsters. It searches local SQLite data and works in
text-only terminals. The first launch creates user directories and imports the
two bundled datasets without a network connection.

## Install and launch

Requires Python 3.12 or newer, a Linux terminal, and a Python SQLite build with
FTS5. The interface needs at least 50 columns and 16 rows. This release was
verified on Linux; Windows and macOS have not been verified.

Install a locally built wheel into a virtual environment or other isolated
Python environment:

```sh
python -m pip install dist/dnd_reference-0.1.0-py3-none-any.whl
dndref --version
dndref
```

`python -m dndref` uses the same entrypoint. Install the optional image extra
from the same wheel with:

```sh
python -m pip install 'dist/dnd_reference-0.1.0-py3-none-any.whl[images]'
```

The base install includes full text browsing. With the image extra, `auto`
uses a terminal capability query to select Kitty graphics or Sixel. `TERM` and
Kitty environment variables are diagnostic hints, not proof by themselves.
Unsupported terminals, missing dependencies or artwork, invalid image files,
and image failures fall back to text without reserving an artwork panel.
`TERM=dumb` and `--images off` do not probe. Use `--images kitty` or
`--images sixel` to request a backend when probing is inconclusive; `NO_COLOR`
still disables Kitty graphics because its image placeholders require color.
The `i` key clears visible artwork immediately and restores the current
selection when pressed again. It cannot override `off`.

Run `dndref image-diagnostics` for a concise capability and dependency report,
or `dndref image-test` in an interactive terminal to display and clear a
generated sample outside the full browser. Both commands accept a preceding
`--images` override. If Kitty is detected but `NO_COLOR` appears in the report,
run the app with `env -u NO_COLOR dndref`. Kitty 0.48.1 was visually verified
on Ubuntu 26.04.1; the Sixel output path is implementation-tested but has not
been visually verified in a Sixel-capable terminal. See
[the verification record](docs/image-rendering-verification.md) for the test
matrix and remaining limits.

## Browse

The browser opens on Spells. Result rows include the sourcebook. Start with
these eight controls:

| Key | Action |
| --- | --- |
| `↑` / `↓` or `j` / `k` | Move through lists |
| `Enter` | Open or select |
| `Esc` | Go back or close |
| `/` | Search this list |
| `Ctrl+K` | Search the entire dictionary |
| `Ctrl+P` | Open Commands |
| `?` | Open Help |
| `q` | Quit |

Category tabs are shown across the top. Commands includes **Change Category**,
**Filters**, Search All, Favorites, Collections, Recently Viewed, Browse Sources,
Toggle Images, and entry actions. Press Enter to select a command. On an entry,
Commands also offers Favorite, Add to Collection, Edit Tags, and Edit Note.
Escape closes a dialog, leaves a search field, or goes back from detail to
results or the previous view; at the top-level browser it does nothing.

### Advanced shortcuts

These aliases remain for experienced users; text fields treat their letters as
text. `Ctrl+F` focuses current-list search. `Alt+Left` / `Alt+Right` move Back / Forward.
`1`–`8` switch categories; `F2` changes search mode; `F3` opens Image and Data Info.
`e` / `s` open edition and source filters; `f` filters subclasses by parent class;
`c` / `t` / `z` filter monsters, while `c` on a subclass detail opens its parent
class; `p` opens filter presets. `g` groups source
versions, `v` chooses a version, `b` opens Browse Sources, `r` opens Recently
Viewed, and `i` toggles images. `F` / `C` open Favorites / Collections. On an
entry, `*` toggles Favorite, `m` edits collection membership, `T` edits tags,
and `n` edits a note. `F1` is a Help alias. `Ctrl+Q` quits outside text fields
and overlays.

The default edition is 2024 / 5.5e; each category starts at All Sources. The
edition and source dialogs allow multiple selections: Space toggles a choice,
Enter applies, and Escape cancels. Filters are kept separately for each category
in the current session. Built-in presets include Everything 2024, Everything
2014, and All Content.

A *dataset* is an imported provider pack; an *edition* is a rules version; a
*sourcebook* identifies the cited book within a pack. Entries with the same
name from different sources may be legitimate alternatives with different
mechanics. Grouping only combines their result rows. Use Commands → Choose
Version to inspect an alternative, or Group Source Versions to list them
separately. Preferred sources choose the initially shown version. Browse Sources
shows installed books and their category counts.

Search All (`Ctrl+K`) keeps category browsing intact and searches Items,
Spells, Feats, Classes, Subclasses, Monsters, Conditions, and Rules together. It supports `item:`,
`spell:`, `feat:`, `class:`, `subclass:`, `monster:`, `condition:`, `rule:`, `edition:`, and `source:`
prefixes. For example:

```text
fireball
spell:fireball
monster:dragon edition:2024
source:XMM dragon
condition:prone
rule:cover
```

Results include category, edition, and source and retain separate records for
different editions and variants. Recent Searches are stored locally in the
user-owned SQLite database, capped at 25, and cleared with `Ctrl+L` from
Search All. Opening a result puts Search All into ordinary
Back/Forward history with its query and selected result.

Commands (`Ctrl+P`) shows common actions first. Type a command or reference name
to find less-used actions, categories such as Conditions and Rules, image and
data diagnostics, and matching entries. It opens entries through normal
navigation.

Subclasses can be browsed and searched directly by subclass or parent class name.
Class pages list compatible subclasses; Tab to the list and press Enter to open
one. Compatibility uses parent class identity and edition. A class page shows
same-edition subclasses from every installed sourcebook, even when the class
browser has a source filter. Standalone Subclasses browsing applies its own
edition, source, and parent class filters. Cross-edition compatibility is not
assumed.

Monster spellcasting lists link exact spell names found in structured
spellcasting fields, filtered to the monster's edition. Same-edition source
variants open through a chooser. Class-to-subclass and subclass-to-parent links
use stored relationships and stable entry IDs. Feat prerequisites link only
references explicitly labeled `Feat:` when an exact same-edition feat exists.
Conditions and Rules come from typed 5etools source references where available;
condition fallback matching requires the exact condition name followed by
“condition.” Rules links use a short allowlist of named mechanics. Both resolve
only within the same canonical edition. The Rules list is a curated glossary,
not a complete reproduction of either handbook. The bundled 2024 source
provides 15 formal Conditions and 30 selected Rules, statuses, actions, and
weapon mastery terms.

`Alt+Left` and `Alt+Right` move
through in-memory Back/Forward history; Recently Viewed lists the last 30 distinct records
viewed in this session. Search text, filters, selected versions, list selection,
and practical detail scroll position are included in history states. History and
Recently Viewed reset when the application exits. Ambiguous references are
offered as choices, missing targets remain ordinary text, and arbitrary rules
prose is intentionally not auto-linked. Item and feat prose is not scanned for
guessed spell, class, or feat references.

Monsters show source, edition, page, size, type, AC, HP, multiple speeds, six
ability scores, defenses, senses, languages, CR, and ordered abilities and
actions where the source provides them. Sparse stat blocks omit empty sections.
The Monsters list shows CR and type; narrow layouts keep the stat block
scrollable and split abilities into two short rows. Edition, source, exact CR,
creature type, and size filters combine. CR choices follow numeric order, so
fractional values such as `1/8` and `1/2` stay distinct. Names search matches
monster names and creature type; All text also searches languages, traits, and
actions. Same named monster stat blocks stay separate, including across
editions. The existing image pane shows monster artwork when a local pack
provides an image; the packaged monsters do not require artwork.

## Configuration

On Linux, put `config.toml` in `${XDG_CONFIG_HOME:-~/.config}/dndref/`. The file
is optional. This example uses real bundled source identities:

```toml
[ui]
images = "auto" # auto, off, kitty, or sixel

[content]
default_editions = ["2024"]
group_alternate_sources = true
preferred_sources = ["official-5etools-2024:XPHB", "srd-5-2-1:srd-5-2-1"]

[[content.filter_presets]]
name = "2024 Core"
editions = ["2024"]
sources = ["official-5etools-2024:XPHB"]
```

Source identities have the form `dataset_id:source_key`. An empty `sources`
array means All Sources. Application data, cache, and state use the corresponding
XDG directories; startup never writes to the checkout or current directory.
The local SQLite database is `${XDG_DATA_HOME:-~/.local/share}/dndref/dndref.sqlite3`.
It contains imported reference tables and separate `user_*` personal-data tables.
Favorites, collections, tags, entry notes, recent searches, and characters stay
on this machine and are not included in dataset packs or changed by reference
imports. Back up this SQLite file to preserve that data.

Use Commands to browse Favorites and create, rename, delete, and open Collections.
From an entry, Commands can toggle Favorite, change collection membership, edit
personal tags, or edit a private note. Enter saves a collection name; Ctrl+S
saves tags or notes, and Escape cancels. The older `F`, `C`, `*`, `m`, `T`, and
`n` shortcuts remain available. Personal records use exact dataset and entry
keys, including version identity. Missing imported entries remain listed as
missing and are never relinked by name; they resolve again if that exact
identity returns.

Saved 2024 characters keep ordered class-level history, player choices, and exact
published identities in local storage. They remain separate from Universal Search
and entry-based Recently Viewed. See [character persistence](docs/character-persistence.md)
for the service and storage contract.

Use Commands → Open Characters to resume, rename, duplicate, or delete a character.
Commands → New Character starts a saved Draft in the guided level-1 builder. Review
validates required choices before marking it complete. See
[character creation](docs/character-creation.md) for the supported steps and limits.
Opening a saved character shows its Overview, Skills, Combat, Features, Spells,
Equipment, and Notes. Section navigation keeps local selection; published
references use normal dictionary details and Back/Forward history. Drafts retain a
Resume Character Creation action. Complete 2024 characters can use Level Up to
advance a class or add a qualifying multiclass through a preview and atomic commit;
Undo Last Level removes only the latest level. See
[character sheet](docs/character-sheet.md) for derived statistics and
[character level-up](docs/character-level-up.md) for progression and undo rules.
Within Favorites or a collection, `/` searches names, category, tags, and notes;
Tab to the entries list and use `t`, `e`, and `g` to cycle category, edition,
and tag filters. In the Collections list, `a` creates, `r` renames, and `x`
starts deletion, which requires typing `DELETE`.

## Data and commands

The wheel includes the reviewed SRD 5.2.1 spells/feats pack and a separate
official-content pack derived from community structured data. Each pack's
`manifest.json` records its provider, sourcebooks, edition, license identifier,
origin, and attribution. The official-content pack is **not SRD** and its
manifest warns that inclusion does not assert redistribution rights. See
[SRD provenance](docs/milestone-8.md) and
[community-data provenance](docs/milestone-9-10.md). About/Data (`F3`) shows
installed metadata. No content or artwork is fetched at runtime.
The Monster Manual (2025) stat blocks are included in the separate official
reference pack. See [Milestone 15 provenance](docs/milestone-15.md).

Additional local packs can be checked and imported with:

```sh
dndref validate PATH
dndref import PATH --dry-run
dndref import PATH
```

Imports are transactional; reimporting unchanged content is a no-op. Bundled
packs are installed automatically on first launch, so these commands are not
needed for initial use.

## Development and release

From a checkout, use the committed lockfile:

```sh
uv sync --extra dev
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m compileall -q src tests tools scripts
.venv/bin/python -m build --wheel --sdist --outdir dist
```

The optional image dependencies can be added to the development environment
with `uv sync --extra dev --extra images`. Production code is in `src/dndref/`;
the Milestone 1 spike under `src/dndref_spike/` is development reference only.
See [release notes](docs/release-notes-0.1.0.md) and the
[release checklist](docs/release-checklist.md) for verification status.
