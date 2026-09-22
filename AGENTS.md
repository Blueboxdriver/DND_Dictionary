# Repository Guidelines

## Project Structure & Module Organization

The production application is Python-based and lives under `src/dndref/`. The Milestone 1 spike remains under `src/dndref_spike/`. Tests live in `tests/`, static spike assets in `assets/`, and project documentation in `docs/`.

Keep application code, item data, tests, and static assets in distinct directories when needed; suggested names are `src/`, `data/`, `tests/`, and `assets/`. These are proposed conventions, not existing paths. Avoid creating empty directories for hypothetical features.

## Build, Test, and Development Commands

Use the committed `uv.lock` for reproducible setup. Install with `uv sync --extra dev`; run tests with `.venv/bin/pytest` and lint with `.venv/bin/ruff check .`.

The development-only community-data converter is `tools/build_5etools_dataset.py`.
Run `PYTHONPATH=src .venv/bin/python tools/build_5etools_dataset.py SNAPSHOT` and repeat
with `--check` to verify deterministic output. See `docs/milestone-9-10.md` for the
pinned source, explicit book allowlist, and update workflow. Keep its production pack
separate from `src/dndref/datasets/srd-5.2.1/`; never relabel non-SRD content as SRD.

When introducing tooling, provide reproducible dependency installation, local development, build, and test commands in `README.md`. Commit the appropriate dependency lockfile and update this guide with verified commands.

## Coding Style & Naming Conventions

Follow the language's standard conventions once the stack is selected. Configure a formatter and linter with the first implementation, and use their settings consistently. Until then, keep Markdown readable, use descriptive filenames, and avoid unrelated formatting changes.

Keep modules focused. Use consistent field names and stable identifiers if structured item records are introduced.

## Testing Guidelines

Tests use pytest. Add tests alongside new behavior using the selected framework's standard naming and discovery conventions. For item data in later milestones, consider validation of required fields, unique identifiers, and malformed records.

Document how to run tests. Report which checks ran and any checks that remain unavailable.

## Commit & Pull Request Guidelines

Git currently exposes no usable history, so existing commit conventions cannot be established. Use concise, imperative subjects such as `Add item schema validation` and keep commits focused.

Pull requests should describe the change, its purpose, and verification performed. Link relevant issues when available; include screenshots for visible interface changes. Never commit credentials, local secrets, or generated dependency directories.
