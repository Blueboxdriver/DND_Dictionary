# D&D Reference

D&D Reference (`dndref`) is a Linux-first, offline terminal browser for D&D
items, spells, feats, classes, and subclasses. It searches local SQLite data and works in
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
enables Kitty or Sixel artwork only when terminal capability is detected.
Unsupported terminals, missing artwork, and image failures fall back to text
without reserving an artwork panel. Use `dndref --images off` for explicit text
mode, or `--images kitty` / `--images sixel` to request a backend. Protocol
rendering still needs verification in a compatible real terminal.

## Browse

The browser opens on Spells. Result rows include the sourcebook. These are the
main keyboard controls:

| Key | Action |
| --- | --- |
| `/`, `Ctrl+F` | Focus search |
| `F2` | Toggle Names / All text search |
| `1`–`5` | Items / Spells / Feats / Classes / Subclasses |
| `e`, `s` | Edition / source filter |
| `f` | Parent class filter while browsing Subclasses |
| `p` | Filter presets |
| `g`, `v` | Toggle alternate-source grouping / choose a variant |
| `b` | Sourcebook browser |
| `i` | Toggle artwork when available |
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
