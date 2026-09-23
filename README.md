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

The browser opens on Spells. Result rows include the sourcebook. These are the
main keyboard controls:

| Key | Action |
| --- | --- |
| `/`, `Ctrl+F` | Focus search |
| `F2` | Toggle Names / All text search |
| `1`–`6` | Items / Spells / Feats / Classes / Subclasses / Monsters |
| `e`, `s` | Edition / source filter |
| `f` | Parent class filter while browsing Subclasses |
| `c`, `t`, `z` | CR / creature type / size filter while browsing Monsters |
| `p` | Filter presets |
| `g`, `v` | Toggle alternate-source grouping / choose a variant |
| `b` | Sourcebook browser |
| `i` | Toggle artwork when available |
| `Alt+Left`, `Alt+Right` | Back / Forward |
| `r` | Recently Viewed |
| Arrows, `j`/`k`, `Enter`, `Escape` | Navigate, open, and return |
| `c` | Open the matching edition parent class from a subclass detail |
| `?`, `F1`; `F3` | Help; About/Data and installed dataset status |
| `q`, `Ctrl+Q` | Quit; global quit |

The default edition is 2024 / 5.5e; each category starts at All Sources. The
`e` and `s` dialogs allow multiple selections: Space toggles a choice, Enter
applies, and Escape cancels. Filters are kept separately for each category in
the current session. Built-in presets include Everything 2024, Everything
2014, and All Content.

A *dataset* is an imported provider pack; an *edition* is a rules version; a
*sourcebook* identifies the cited book within a pack. Entries with the same
name from different sources may be legitimate alternatives with different
mechanics. Grouping only combines their result rows. Use `v` to inspect each
variant, or `g` to list them separately. Preferred sources choose the initially
shown variant. The sourcebook browser (`b`) shows installed books and their
category counts.

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
`Alt+Left` and `Alt+Right` move
through in-memory Back/Forward history; `r` opens the last 30 distinct records
viewed in this session. Search text, filters, selected variants, list selection,
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
