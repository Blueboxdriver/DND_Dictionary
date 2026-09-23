# Milestone 12: release preparation

Version `0.1.0` is defined in `dndref.__version__`; setuptools reads it for
wheel/sdist metadata. The distribution is `dnd-reference`, and the production
console command is `dndref`. The Milestone 1 spike remains in the checkout for
reference and tests, but is excluded from the installed package and console
scripts. Supported runtime is Python 3.12+ on Linux with SQLite FTS5. Base
dependencies are Pydantic, platformdirs, and Textual. Pillow and textual-image
are installed only through the `images` extra. Pytest and Ruff remain in the
development extra.

The wheel and sdist include the two unchanged production packs, their manifests
with attribution and source metadata, four SQL migrations, production Python
modules, README metadata, and the application-code `LICENSE`. First launch
creates XDG directories, migrates SQLite, and installs missing bundled packs
offline. Existing installed datasets are preserved.

Run `.venv/bin/python scripts/verify_release.py` to replace `dist/` with a
wheel and sdist, inspect their contents, install the wheel in independent clean
base and image-extra environments, and run installed-code smoke checks. The
smoke covers CLI commands, package resource access, first import and no-op
reimport, fresh/current/previous-level migration behavior, search, text-only
fallback, and headless TUI operation at 140×40, 100×30, 80×24, and 60×20. Use
`--skip-build` to recheck existing artifacts after changing only verification
code. The temporary XDG directories and environments are removed afterward.

See [release notes](release-notes-0.1.0.md) and
[release checklist](release-checklist.md) for final results and the remaining
Kitty/Sixel manual terminal checks. No publishing, push, or tag was performed.
