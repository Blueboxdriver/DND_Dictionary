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

The thumbnail cache is an LRU bounded to 24 entries and 4 million decoded
pixels. Evictions and widget cleanup release image objects. Selection changes,
category changes, overlays, layout changes, missing assets, failed decodes, and
exit clear the panel. Request IDs reject stale workers. Image failures affect
only the artwork panel, so text browsing remains usable.

The stationary panel appears only at approximately `120×30` or larger and is
bounded to `28×12`. It is separate from rules scrolling. Entries without
artwork reserve no blank space. The text-first behavior remains at `140×40`,
`120×30`, `100×30`, `80×24`, `60×20`, and below the minimum size.

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
| Kitty terminal | `--images kitty` | `140×40`, `120×30`, `100×30`, `80×24` | Needs real-terminal verification |
| Sixel terminal such as foot | `--images sixel` | `140×40`, `120×30`, `100×30`, `80×24` | Needs real-terminal verification |
| Unsupported terminal | `--images auto` | all requested sizes | Text fallback covered; graphics unverified |
| SSH or tmux/screen | `--images auto` | all requested sizes | Auto text fallback covered |
| Any terminal | `--images off` | all requested sizes and below minimum | Text path covered |

No compatible Kitty or Sixel terminal was available for this checkout. Do not
treat forced environment variables or pilot tests as protocol verification.
In a matching real terminal, import a small local test pack with one valid image,
move to an entry without artwork, open and close both overlays, resize through
the matrix, and quit to complete the remaining manual checks.
