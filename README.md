# D&D Reference

D&D Reference is an offline terminal application for browsing D&D items,
spells, feats, and classes. It stores its data in a local SQLite database and
does not need network access while running.

## Requirements

- Python 3.12 or newer
- Linux or another POSIX-like terminal environment
- A terminal at least 50 columns wide and 16 rows tall

## Install

From a source checkout, install the project and development dependencies with
the committed lockfile:

```sh
uv sync --extra dev
```

Optional terminal image support is available with:

```sh
uv sync --extra dev --extra images
```

## Run

Start the application with:

```sh
.venv/bin/dndref
```

The first run creates the application directories and database, then installs
the bundled datasets. To force text-only mode, use:

```sh
.venv/bin/dndref --images off
```

The application also provides dataset commands:

```sh
.venv/bin/dndref validate PATH
.venv/bin/dndref import PATH --dry-run
.venv/bin/dndref import PATH
```

## Controls

- `/` or `Ctrl+F`: focus search
- `F2`: switch between name search and full-text search
- `1`–`4`: switch between items, spells, feats, and classes
- Arrow keys or `j`/`k`: move through results
- `Enter`: open details
- `Escape`: return to the list
- `F1` or `?`: show help
- `q` or `Ctrl+Q`: quit

## Bundled data

The application includes an SRD 5.2.1 dataset and a separate 2024/reference
dataset. Both are imported into the local database on first startup. The
runtime browser reads local data only.

## Development

Run the test suite and lint checks with:

```sh
.venv/bin/pytest
.venv/bin/ruff check .
```

Production code is under `src/dndref/`, tests are under `tests/`, and project
documentation is under `docs/`.
