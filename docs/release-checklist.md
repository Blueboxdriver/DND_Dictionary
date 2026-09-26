# 0.1.0 release checklist

Status: **ready with documented limitation for local installation**. External
publication was not requested. The official-content pack's manifest states
that its inclusion does not assert redistribution rights; public distribution
needs a separate rights decision.

- [x] Version `0.1.0`: package, wheel metadata, `dndref --version`, and
  `python -m dndref --version` agree.
- [x] Wheel and sdist built; archives inspected for production modules, both
  bundled packs and manifests, the 2024 `character-builder.json`, migrations
  001–012, character creation and persistence modules, README metadata, and LICENSE.
  No spike console script or spike package is installed.
- [x] Second independent build has identical wheel and sdist member contents.
  Archive bytes differ; byte-for-byte reproducibility is not claimed.
- [x] Clean base wheel install: no Pillow or textual-image; first run creates
  temporary XDG paths and imports both datasets offline. A second run is
  idempotent; 4,081 reference entries and FTS rows plus 2,104 builder owners
  remain usable.
- [x] CLI: help, version, `validate PATH`, `import PATH --dry-run`, first import,
  no-op reimport, invalid-path exit code 2, and `python -m dndref` checked from
  the installed wheel.
- [x] Database: fresh schema applies migrations 001–012, current schema
  reapplies none, and upgrades to character persistence preserve reference and
  personal data; foreign keys and post-upgrade search pass.
- [x] Installed-wheel headless TUI: all four categories, Names/All text search,
  edition and source filters, grouping, variant selection, preset, source
  browser, Help, About/Data, and quit. Layouts checked at 140×40, 100×30,
  80×24, and 60×20.
- [x] `TERM=dumb` PTY startup/quit and text fallback: no graphics sequences or
  reserved image panel.
- [x] Separate `[images]` clean install: Pillow and textual-image import; no
  compatible graphics terminal was claimed by the headless check.
- [x] Kitty real-terminal rendering: verified in Milestone 14, including image
  display, switching, scrolling, overlays, resize, `i`, and exit cleanup. See
  [the verification record](image-rendering-verification.md).
- [ ] Sixel real-terminal rendering: unverified. Check stationary panel,
  scrolling, switching, resize, and exit cleanup in a Sixel terminal. Output
  generation and lifecycle code are implementation-tested in Milestone 14.
- [x] README, example configuration, source provenance, release notes, and
  Linux/Python/terminal scope reviewed. Clean-environment checks used Python
  3.14; Python 3.12 and 3.13 were not separately exercised here.
- [x] Full pytest: 291 passed. Ruff, compilation, `uv lock --check`, and
  `git diff --check` pass.
- [x] Production character-builder rebuild passes two independent `--check`
  conversions with identical generated hashes. Production import, idempotent
  reimport, replacement, and packaged-data rule checks pass.
- [x] Structural browsing checks show ordinary category queries do not load or
  query builder data. A local median benchmark measured first initialization at
  0.53 s without and 0.79 s with builder data; warm initialization measured
  19.0 ms and 21.8 ms respectively.
- [x] `git status` reviewed: release files and pre-existing Milestone 11 edits
  only; no virtual environments, databases, caches, or build scratch tracked.
- [x] No publish, push, tag, or remote action performed.

The automated release check is `.venv/bin/python scripts/verify_release.py`.
It uses temporary environments and user directories; real Kitty/Sixel display
requires separate manual access to compatible terminals.
