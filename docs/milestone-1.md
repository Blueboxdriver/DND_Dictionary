# Milestone 1: framework/image spike

## Scope

The prototype is intentionally disposable. It uses four original placeholder entries, a local PPM test image, a Textual list/detail layout, keyboard selection with Up/Down and `j`/`k`, and CSS breakpoints matching the plan:

- `80×24` and `100×30`: side-by-side list/detail.
- `140×40`: side-by-side layout with room for a stationary artwork panel when image support is available.
- `60×20`: stacked list/detail layout.
- Below `50×16`: compact message; the app still exposes quit/help text and does not crash.

The image adapter recognizes Kitty, Sixel, and no-image states. `textual-image` and Pillow are optional. If the dependency, backend class, image file, or dimensions are unusable, the image panel is omitted and the text interface continues to work.

## Automated verification

`tests/test_spike.py` covers list navigation, all requested simulated sizes, the compact layout, disabled-image behavior, conservative capability selection, and the optional `textual-image` import/API. Textual's test pilot can verify widget state and resizing, but it cannot prove that a real terminal displayed Kitty graphics or Sixel escape sequences.

## Manual terminal verification

Run these in a real terminal, not a redirected log or ordinary CI pseudo-terminal:

```sh
.venv/bin/python -m dndref_spike --images off
KITTY_WINDOW_ID=1 .venv/bin/python -m dndref_spike --images kitty
DNDREF_SIXEL=1 .venv/bin/python -m dndref_spike --images sixel
```

In each session, resize to `80×24`, `100×30`, `140×40`, and `60×20`; move through all entries; confirm that the text remains readable when the image panel disappears. The environment variables are explicit test hints, not terminal capability proofs, so a forced mode must be run only in the corresponding terminal.

## Findings

The Textual list/detail layout and resize model are viable for the planned application. The optional image path is viable as a stationary detail-panel enhancement, but protocol rendering remains a manual integration test. The spike does not establish reliable automated Kitty/Sixel detection, screenshot fidelity, or Sixel scrolling behavior. The production design should keep image probing before Textual starts, treat environment hints as non-authoritative, and preserve the no-image path as the default safe fallback.
