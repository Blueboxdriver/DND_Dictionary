# D&D Reference

The production browser is under `src/dndref/ui/`. Milestone 1 remains a disposable Textual/image integration spike under `src/dndref_spike/`; it is retained for reference and regression testing. Production image support is under `src/dndref/images.py`.

## Layout

- `src/dndref/` — production CLI, configuration/path handling, and SQLite migration infrastructure.
- `src/dndref_spike/` — small Textual list/detail prototype retained for reference.
- `assets/placeholder.ppm` — original placeholder artwork used only by the spike.
- `tests/test_foundation.py` — Milestone 2 path, configuration, database, migration, and CLI tests.
- `src/dndref/models/` — Milestone 3 Pydantic dataset contracts and on-demand JSON Schema generation.
- `src/dndref/importer.py` and `src/dndref/storage/repository.py` — dataset loading, planning, asset staging, persistence, and search SQL.
- `src/dndref/search.py` — Milestone 5 typed search API, normalization, ranking, paging, and detail lookup.
- `src/dndref/ui/` — production keyboard-driven Textual browser for items, spells, feats, and classes.
- `tests/test_models.py` and `tests/fixtures/dataset/` — representative data-contract tests and original fixtures.
- `tests/test_search.py` — Milestone 5 normalization, ranking, FTS, paging, migration, and detail tests.
- `tests/test_spike.py` — keyboard, resize, capability, and optional-integration tests.
- `tests/test_ui.py` — production browser interaction and responsive-layout tests.
- `tests/test_images.py` — production image capability, cache, and local-decoding tests.
- `docs/milestone-1.md` — spike manual terminal test matrix and findings.
- `docs/milestone-11.md` — production image lifecycle, fallback behavior, and manual checks.
- `docs/milestone-12.md` — 0.1 packaging, clean-install verification, and release limitations.
- `docs/milestone-7.md` — class detail structure and progression-table controls.
- `docs/milestone-8.md` — reviewed SRD 5.2.1 spells/feats dataset and reconciliation.
- `docs/milestone-9-10.md` — offline community-data conversion and official 2024 source policy.
- `tools/build_5etools_dataset.py` — deterministic local 5etools snapshot converter.
- `docs/dataset-format.md` — versioned dataset layout, stable references, and schema generation.
- `plan.txt` — implementation plan and milestone boundaries.

## Requirements

The release supports Python 3.12 or newer and is Linux-first. It is intended
for a real terminal with at least 50×16 cells; 80×24 or larger is more useful.
Text browsing works without terminal graphics. Kitty/TGP and Sixel artwork are
optional and depend on terminal protocol support.

## Install

Install the locally built release wheel in a clean environment:

```sh
python -m pip install dnd_reference-0.1.0-py3-none-any.whl
```

For a source checkout, install the development environment with the committed
lockfile:

```sh
uv sync --extra dev
```

The base install does not install Pillow or textual-image. To enable optional
image support from the wheel or checkout:

```sh
python -m pip install 'dnd_reference-0.1.0-py3-none-any.whl[images]'
uv sync --extra dev --extra images
```

The image extra is optional. A base installation remains a complete text-only
reference browser.

## Run and verify

The production application initializes the platform application directories,
reads optional TOML configuration, creates its SQLite database, applies
migrations, installs the two bundled datasets on first run, and opens the
browser in an interactive terminal:

```sh
dndref
dndref --help
dndref --version
dndref --images off
dndref --images auto
```

The browser starts on Spells. Use `/` or `Ctrl+F` to focus search, `F2` to
toggle Names and All text search, `1`–`4` to switch Items, Spells, Feats, and
Classes, `j`/`k` or the arrows to navigate, `Enter` to open a narrow detail
view, `Escape` to return, `?` or `F1` for help, `F3` for About/Data, and `q` or
`Ctrl+Q` to quit. At least `120×30` may show an imported local image in a
stationary panel bounded to `28×12`; text scrolling and keyboard navigation stay
unchanged.
Search and selection state is session-only and kept separately for each
category. At 80 columns and wider the browser uses a list/detail split; from
50–79 columns it switches to a list then detail flow. Below 50 columns or 16
rows it shows a compact resize message while help and quit remain available.

Class details show metadata, a dynamic progression table, ordered class features,
and separate subclass sections. Focus the progression table to use Left/Right or
`h`/`l` for horizontal scrolling; Up/Down, `j`/`k`, and page keys navigate its
rows.

On Linux, `platformdirs` follows XDG locations. The configuration file is `config.toml` in the user config directory; the database is `dndref.sqlite3` in the user data directory. The only supported setting is currently:

```toml
[ui]
images = "auto" # auto, off, kitty, or sixel
```

Unknown settings are rejected so misspellings do not silently change behavior. Application paths can be redirected in tests with `ApplicationPaths.for_root(...)` or XDG environment variables.

The browser also accepts `dndref --images auto|off|kitty|sixel`. Precedence is
explicit CLI option, then `[ui].images`, then the built-in `auto` default. `off`
skips optional imports and graphics probing. `auto` probes before Textual starts
managing terminal input, waits at most one second, disables images under SSH or a
multiplexer, and requires proven backend support. Explicit modes still fall back
to text when dependencies, probes, dimensions, or renderers fail. Help, version,
validation, and import commands do not probe the terminal.

Future schema changes must add a file to `src/dndref/storage/migrations/` using the next numeric prefix, for example `004_add_table.sql`. Do not edit an applied migration. Each migration is applied in a transaction and is recorded in `schema_migrations` only after success. Milestone 5 adds `003_search_fts.sql` and requires SQLite FTS5; initialization fails with an actionable error when the Python SQLite build lacks it.

The Milestone 1 spike can still be run independently:

```sh
.venv/bin/dndref-spike --images off
```

Its modes are `auto`, `off`, `kitty`, and `sixel`. Production behavior is
documented in [Milestone 11](docs/milestone-11.md).

## Checks

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m compileall -q src tests tools
```

Generate the Milestone 3 JSON Schemas on demand:

```sh
PYTHONPATH=src .venv/bin/python -m dndref.models.schema --output schemas
```

## Data and dataset import

Every installation bundles the SRD 5.2.1 baseline and the separate 2024/reference
pack. First startup validates and imports both packs into the user database;
later startups reuse the installed snapshots and are safe to repeat. The JSON
resources are read-only package data. The database, staged assets, configuration,
cache, and logs are stored in the platform's user directories rather than in
site-packages or the current working directory.

Runtime browsing, search, detail views, help, and About/Data use local state only
and do not fetch the source URLs in the manifests. The About/Data screen reports
each installed dataset's name, version, sources, license identifier, and
attribution.

Validate or import a complete local dataset snapshot:

```sh
.venv/bin/dndref validate PATH
.venv/bin/dndref import PATH --dry-run
.venv/bin/dndref import PATH
```

Import replaces only the matching `dataset_id`. Stable identity is
`dataset_id:local_key`; names are not identity. Imports are validated before a
single SQLite transaction, and failed writes leave the installed snapshot
unchanged. Dry runs read the existing database for comparison but do not write
SQLite or stage assets.

The bundled SRD 5.2.1 pack contains 339 spells and 17 feats, with empty Items
and Classes. See
`docs/milestone-8.md` for attribution, inventories, and conversion scope.

The separate 2024/reference pack contains 2,541 items, 13 item properties, 444
spells, 179 feats, 13 classes, 76 subclasses, 302 class features, and 508
subclass features. It contains non-SRD content and does not inherit the SRD
license. Both packs can coexist in the same database.

Convert a local upstream checkout, then verify deterministic output:

```sh
PYTHONPATH=src .venv/bin/python tools/build_5etools_dataset.py PATH_TO_5ETOOLS_SNAPSHOT
PYTHONPATH=src .venv/bin/python tools/build_5etools_dataset.py PATH_TO_5ETOOLS_SNAPSHOT --check
.venv/bin/dndref validate src/dndref/datasets/official-5etools-2024
.venv/bin/dndref import src/dndref/datasets/official-5etools-2024 --dry-run
.venv/bin/dndref import src/dndref/datasets/official-5etools-2024
```

See [Milestones 9/10](docs/milestone-9-10.md) for the pinned upstream snapshot,
included books, exclusions, provenance, reconciliation reports, and update workflow.
The normal application remains offline; conversion is a development/admin action.

Referenced local PNG, JPEG, and WebP files are validated and staged under
content-addressed names in the application data directory. Unused staged files
are harmless and may be cleaned later. Cross-dataset content references are
currently rejected; manifest dependencies are recorded but not resolved.

Removing the Python package does not remove the user database, configuration,
cache, logs, or staged assets. Reinstalling the package reuses that user state.

## Search

The production API in `dndref.search` accepts a category-scoped `SearchQuery`.
Names mode is the default and requires every normalized query term to occur in
the normalized name. Normalization case-folds, removes Unicode accents,
collapses whitespace, and maps typographic apostrophes to straight apostrophes;
display names are unchanged. A query such as `fire ball` therefore matches
`Fireball` because terms are deterministic substrings, not fuzzy matches.

Names rank as exact match, full-name prefix, then other matching names. All-text
mode adds literal-token FTS5 matches after name matches. Empty text browses the
selected category alphabetically. Every page is SQL-paginated with a default
limit of 50 and a maximum of 200, and returns the total match count. Imports
replace affected FTS rows in the same transaction as content tables.

## Terminal graphics

The optional image path supports Kitty/TGP and Sixel through `textual-image`.
`--images auto` probes once before Textual starts and falls back to text when the
terminal, dimensions, dependencies, or renderer are unsuitable. Auto mode
disables artwork under SSH and tmux/screen. `--images off` guarantees text-only
startup; explicit `kitty` and `sixel` request a backend but still fall back
safely when it cannot be used. No terminal graphics protocol is assumed to be
available universally. Genuine Kitty/Sixel rendering remains a manual check for
this release environment.

## Release verification

Install the build frontend once, then run the repository's release smoke test:

```sh
python -m pip install build
python scripts/verify_release.py
```

This builds a wheel and sdist, inspects package data, and tests isolated base,
image-extra, and sdist installations outside the repository. It does not publish
anything.
