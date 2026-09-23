# Image rendering verification (Milestone 14)

Verified on 2026-09-22/23. This record distinguishes visible terminal results
from tests of the implementation.

## Current data flow

`--images` overrides `[ui].images` from the config file. `ImageAdapter` resolves
the mode before Textual starts reading terminal input. `off` and `TERM=dumb`
return text mode without loading the image library or probing. In `auto`, the
`textual-image` terminal query must confirm Kitty or Sixel; environment names
alone never prove support. SSH and tmux/screen sessions default to text mode.
Explicit `kitty` and `sixel` can select a backend after an inconclusive query,
provided the widget is installed. `NO_COLOR` disables Kitty rendering because
its virtual placement needs RGB foreground colors on Unicode placeholders.

The selected detail supplies an installed local asset path and stored media
type. A debounce timer starts `ImageLoader` in a thread. The loader checks file
size, decodes PNG/JPEG/WebP with Pillow, validates the stored media type, limits
pixel count, and returns a bounded thumbnail. The UI accepts a worker result
only when its monotonically increasing request ID and selected variant still
match. A stationary panel owns the optional `textual-image` widget outside the
scrolling detail content. Selection, filters, category changes, toggles,
overlays, resize, and exit invalidate the request and clear the widget. Kitty's
renderable deletes its terminal image on cleanup. The Sixel widget relies on
Textual repainting or alternate-screen exit; its library cleanup is a no-op.

Kitty's `textual-image` widget used to replace and delete its renderable on an
unrelated redraw. Textual could retain identical placeholder cells, leaving an
empty pane after list scrolling. The adapter now keeps that renderable until
the owning widget is replaced or cleared. Resize still clears and remounts it.

## Kitty: visually verified

- Terminal: Kitty 0.48.1, `TERM=xterm-kitty`, native Kitty graphics query
  confirmed. Image dependency versions: textual-image 0.14.0, Pillow 12.3.0,
  Textual 0.89.1.
- Session: Ubuntu 26.04.1 LTS, GNOME Wayland with a Kitty Xwayland window.
  Screenshots were captured from the actual Kitty window. The temporary test
  pack was based on the project's original fixture, with generated red, blue,
  and green PNGs; it was installed under temporary XDG directories.
- `NO_COLOR` was unset for graphics tests. With `NO_COLOR=1`, Kitty rendered a
  blank image under the old detection path. The repaired `auto` path reports
  text fallback and explains why. Kitty's own `icat` rendered the same PNG.

| Case | Visible result |
| --- | --- |
| Generated `image-test` | Blue image appeared; Enter cleared it and exited. |
| First artwork and selection switching | Red A, blue B, and green C matched their records; old colors disappeared. |
| Rapid changes ending on B | Blue B remained; no late A or C replacement was observed. |
| List scrolling and return | Blue artwork remained attached to the B detail after scrolling through 27 results and returning; no detached image. This failed before the renderable fix. |
| Long detail scrolling | Text scrolled inside its pane while B artwork stayed stationary. |
| Help, source browser, edition filter, presets | Artwork cleared under each screen and returned for B after closing. |
| Category and subclass pages | Switching to Subclasses cleared B artwork; long subclass detail showed no ghost. |
| `i` toggle | Off cleared B; on restored B. Search-input isolation is also covered by the Textual pilot test. |
| Resize | 140×40 showed artwork; 100×30, 80×24, and 60×20 hid it; growing back to 140×40 restored it. Repeated cycles showed no stale image or text overlap. |
| Quit and successive launches | No artwork remained at the shell prompt. Repeated launches rendered the selected artwork correctly. |

The temporary pack and screenshots are test evidence, not bundled application
content. In-flight completion after selection/resize is covered by request-ID
tests; a deliberately delayed real terminal decode was not visually staged.

## Sixel: implementation-tested, visually unverified

No Sixel-capable terminal was available in this session. Kitty's terminal
capability response reported no Sixel support. A generated RGB image was
encoded through `textual-image`'s Sixel renderable; the output contained one
bounded Sixel DCS frame and terminator. Backend selection, missing dependencies,
the explicit override, widget clearing, resize, modal restoration, and exit
cleanup paths have automated coverage. Sixel has no image-delete sequence in
this library. Its repaint behavior, clipping, and ghost-free exit still require
a real Sixel terminal. Do not treat these implementation checks as visual
verification.

## Verification commands

```sh
.venv/bin/dndref image-diagnostics
.venv/bin/dndref image-test
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python -m compileall -q src tests tools
UV_CACHE_DIR=/tmp/dndref-uv-cache .venv/bin/uv lock --check
git diff --check
```

The built wheel was installed with `pip` into separate temporary base and
`[images]` virtual environments. The base environment had neither Pillow nor
textual-image, opened and quit the full TUI in Kitty in text mode, and the image-extra environment
imported both dependencies and passed sample decoding. Automated tests and
wheel checks do not substitute for the Kitty visual observations above.
