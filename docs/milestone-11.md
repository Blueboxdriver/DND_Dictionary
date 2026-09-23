# Milestone 11: production images and bounded UI polish

This milestone adds optional local artwork through the existing importer and
asset store. It does not acquire artwork, add network access, change either
production pack, or begin Milestone 12.

## Configuration and detection

```sh
.venv/bin/dndref --images auto
.venv/bin/dndref --images off
.venv/bin/dndref --images kitty
.venv/bin/dndref --images sixel
```

The configuration equivalent is:

```toml
[ui]
images = "auto"
```

Precedence is explicit CLI option, configuration, then the built-in `auto`
default. Install optional support with `uv sync --extra dev --extra images`.
The lockfile keeps Textual `0.89.1` and the existing textual-image/Pillow bounds.

Auto mode performs one pre-Textual capability probe with a one-second total bound.
The textual-image widget import itself probes terminal cell size, so its
internal read timeout is capped before import.
SSH and tmux/screen sessions default to text-only. Kitty is selected only when
the TGP capability and usable cell dimensions are proven; Sixel is the fallback
when its capability and dimensions are proven. Environment variables are hints,
not proof. Explicit modes request a backend but still fall back to text on
missing dependencies, timeout, bad dimensions, or renderer errors. The adapter
uses the integrated textual-image widget and never reads terminal input or emits
graphics sequences itself. Help, version, validation, import, and `off` mode do
not probe the terminal.

## Asset lifecycle

The existing importer accepts only local PNG, JPEG, and WebP references, rejects
path traversal, and stages bytes under the existing application data directory:

```text
<data directory>/assets/sha256/<prefix>/<content hash>
```

Detail retrieval resolves that stored path. The browser never needs the original
dataset directory and never loads a remote URL. Selection changes are debounced
150 ms; decoding and resizing run in a Textual worker. Pillow limits files to
8 MiB and decoded images to 16 million pixels, turns decompression-bomb warnings
into failures, converts to RGBA, and preserves aspect ratio when resizing.
Staged assets have extensionless hash paths. Decoding checks the stored media
type against the decoded PNG, JPEG, or WebP format.

The thumbnail cache is an LRU bounded to 24 entries and 4 million decoded
pixels. Evictions and widget cleanup release image objects. Selection changes,
category changes, overlays, layout changes, missing assets, failed decodes, and
exit clear the panel. Request IDs reject stale workers. Image failures affect
only the artwork panel, so text browsing remains usable.

The stationary panel appears only at approximately `120×30` or larger and is
bounded to `28×12`. It is separate from rules scrolling. Entries without
artwork reserve no blank space. The text-first behavior remains at `140×40`,
`120×30`, `100×30`, `80×24`, `60×20`, and below the minimum size.
`i` toggles artwork outside text entry. About/Data reports the selected backend
and the latest image status.

Filter dialogs show `>` for the highlighted row and `[x]` / `[ ]` for enabled
state. Presets show their edition and source selections before application,
including explicit All Editions and All Sources states. Variant rows mark the
current source independently of the cursor.

## Help and About/Data

Help documents the implemented search, navigation, About/Data, and image
controls. `F3` opens About/Data and restores focus after dismissal. Metadata is
read from the installed database and includes application version, dataset
ID/title/version, ruleset, sources/books and citations, license identifiers,
origin, and attribution. The SRD pack is explicitly distinguished from the
separate official-content pack. Current packs contain no artwork attribution
metadata, so no artwork credits are invented.

Empty databases show `dndref import PATH`; no-result, loading, detail, and
compact-terminal states remain readable. Textual pilot tests cover keyboard and
focus behavior, class progression scrolling, responsive layouts, and text
browsing after image failures.

## Isolated demonstration and verification

The image tests generate a valid PNG in a temporary directory, load it through
the production `ImageLoader`, verify aspect-ratio sizing, and exercise corrupt,
missing, oversized, and pixel-limit inputs. They do not touch either production
dataset:

```sh
.venv/bin/pytest -q tests/test_images.py
```

With the image extra installed, the valid-image test uses Pillow to generate the
local test image. Importer asset staging remains exercised with temporary test
packs and temporary databases in `tests/test_importer.py`.

Automated checks:

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m compileall -q src tests tools
```

Manual terminal matrix:

| Session | Mode | Sizes | Status |
|---|---|---|---|
| Kitty terminal | `--images kitty` | `140×40`, `120×30`, `100×30`, `80×24` | Needs real-terminal verification, including `i`, entry changes, and exit cleanup |
| Sixel terminal such as foot | `--images sixel` | `140×40`, `120×30`, `100×30`, `80×24` | Needs real-terminal verification, including scrolling and exit cleanup |
| Unsupported terminal | `--images auto` with `TERM=dumb` PTY | requested sizes in headless layout tests | Text fallback verified; no graphics attempted |
| SSH or tmux/screen | `--images auto` | all requested sizes | Auto text fallback covered |
| Any terminal | `--images off` | all requested sizes and below minimum | Text path covered |

This matrix records the Milestone 11 state. Milestone 14 later visually
verified Kitty; see [the current verification record](image-rendering-verification.md).
No compatible Kitty or Sixel terminal was available for the Milestone 11 run. Do not
treat forced environment variables or pilot tests as protocol verification.
The `TERM=dumb` PTY check returned the no-image backend for both `auto` and
`off`; headless tests covered text layout at all requested sizes.
In a matching real terminal, import a small local test pack with one valid image,
move to an entry without artwork, open and close both overlays, resize through
the matrix, and quit to complete the remaining manual checks.
