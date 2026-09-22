# Milestone 6: text browser

The production Textual browser is launched with `dndref` after at least one
dataset has been imported. It opens on Spells and supports Items, Spells,
Feats, and Classes through the visible tabs or number keys `1`–`4`.

`/` and `Ctrl+F` focus the search field. `F2` toggles Names and All text mode.
Names mode is the default. Search is category-scoped, debounced, paged, and
executed in background workers through `dndref.search`; stale completions are
discarded. Search and selection state is kept in memory per category.

`e` opens the edition filter and `s` opens the source filter when the search
field is not focused. Both selectors use Space to toggle multiple choices and
Enter to apply; Escape cancels. `>` marks the highlighted row; `[x]` marks a
selected row. These remain distinct even in text-only terminals. All Editions and All Sources clear their
respective selections. Source choices follow the active category and edition
selection, and changing edition or category removes selections that are no
longer available. The active filters appear below the search row. The browser
starts each category with the configured default edition when it is available;
the built-in default is 2024 / 5.5e. Set multiple defaults in `config.toml`:

```toml
[content]
default_editions = ["2014", "2024"]
```

`2014` means 2014 / 5e and `2024` means 2024 / 5.5e. Multiple editions are
allowed. Unavailable defaults are ignored; if a category has none of the
configured editions, it uses All Editions. Edition changes, source selections,
and per-category filter state remain in memory for the current session; source
selections always start at All Sources.

Result rows show the sourcebook title, compacted when needed. Identical names
from different sourcebooks are grouped by default after active edition, source,
and text filters. A row shows `+N sources` when alternates match. The detail
pane shows one original record at a time; `v` opens its matching source variants.
`g` toggles grouping for the current session and shows individual records when
off. Grouping changes presentation only; records and mechanics stay separate.
Preferred source order chooses the group's initial visible record. It does not
change ranking between different names.

`p` opens filter presets. Built-ins are Everything 2024, Everything 2014, and
All Content. Configured presets can add stable dataset/source selections.
Unavailable sources are dropped for the current category; manual filter edits
remain possible after applying a preset. `b` opens the Source Browser, which
lists installed searchable sourcebooks and their actual category counts.
Selecting a category opens the normal browser filtered to that source.

For example, extend `[content]` in `config.toml`:

```toml
[content]
group_alternate_sources = true
preferred_sources = ["official-5etools-2024:XPHB", "srd-5-2-1:srd-5-2-1"]

[[content.filter_presets]]
name = "2024 Core"
editions = ["2024"]
sources = ["official-5etools-2024:XPHB"]
```

Source identities use `dataset_id:source_key`; inspect installed metadata for the actual keys.
An empty preset `sources = []` means All Sources.

Use the arrows or `j`/`k` to navigate results. `PageUp`, `PageDown`, `Home`,
and `End` operate on the focused pane. The selected entry loads a generic
readable detail view using the Milestone 5 detail API. `Enter` opens detail in
the 50–79 column layout, and `Escape` returns to the list. `?` or `F1` opens
the compact help overlay. `q` quits outside text input; `Ctrl+Q` always quits.

At 80 columns and wider the browser uses a list/detail split. From 50–79
columns it uses separate list and detail views. Below 50 columns or 16 rows it
shows a compact resize message while preserving help and quit controls.

No favorites, notes, recent history, SRD conversion, or specialized Milestone 7
class rendering is included.
