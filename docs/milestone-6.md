# Milestone 6: text browser

The production Textual browser is launched with `dndref` after at least one
dataset has been imported. It opens on Spells and supports Items, Spells,
Feats, and Classes through the visible tabs or number keys `1`–`4`.

`/` and `Ctrl+F` focus the search field. `F2` toggles Names and All text mode.
Names mode is the default. Search is category-scoped, debounced, paged, and
executed in background workers through `dndref.search`; stale completions are
discarded. Search and selection state is kept in memory per category.

Use the arrows or `j`/`k` to navigate results. `PageUp`, `PageDown`, `Home`,
and `End` operate on the focused pane. The selected entry loads a generic
readable detail view using the Milestone 5 detail API. `Enter` opens detail in
the 50–79 column layout, and `Escape` returns to the list. `?` or `F1` opens
the compact help overlay. `q` quits outside text input; `Ctrl+Q` always quits.

At 80 columns and wider the browser uses a list/detail split. From 50–79
columns it uses separate list and detail views. Below 50 columns or 16 rows it
shows a compact resize message while preserving help and quit controls.

No production terminal image support, favorites, notes, recent history,
advanced filters, SRD conversion, or specialized Milestone 7 class rendering
is included.
