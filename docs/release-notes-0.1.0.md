# D&D Reference 0.1.0

Initial Linux-first local release. Install the wheel with Python 3.12+ and run
`dndref`; first launch imports the bundled packs offline.

Milestone 15 development adds a Monsters category with XMM stat blocks,
numeric CR filtering, ordered abilities, and monster search. See
[Milestone 15](milestone-15.md). The checklist below describes the original
0.1.0 verification state and is not a claim of a new public release.

- Browse items, spells, feats, and classes with full class progression details.
- Search names or full text. Filter by edition and source, apply presets, browse
  sourcebooks, and switch between alternate source entries.
- Store imported content in SQLite with transactional imports, FTS5 search, and
  automatic database migrations.
- Include the SRD 5.2.1 spells/feats pack and a separate community-structured
  official-content reference pack. Sourcebook and attribution metadata remain
  attached to each pack and are visible in About/Data.
- Support local Kitty and Sixel artwork with the optional `images` extra;
  unsupported terminals and the base install use text-only browsing.
- Provide `dndref validate PATH`, `dndref import PATH [--dry-run]`,
  `dndref --version`, and `python -m dndref`.

## Verification limits

At the time of this release, real Kitty and Sixel rendering was unverified.
Milestone 14 later verified Kitty 0.48.1 visually; Sixel remains visually
unverified. See [the verification record](image-rendering-verification.md).
The release is Linux-first;
Windows and macOS have not been verified. Clean release checks ran on Python
3.14; Python 3.12 and 3.13 were not separately tested here. The
official-content pack retains
its manifest warning that its inclusion is for personal local reference and
does not assert redistribution rights. No external publication is part of this
release preparation.
