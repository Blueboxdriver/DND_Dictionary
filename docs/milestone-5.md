# Milestone 5: search

The production search API is in `dndref.search`. It is independent of Textual
and opens a caller-owned SQLite connection per `SearchService` operation.

`SearchQuery` is scoped to one category (`items`, `spells`, `feats`, or
`classes`) and supports `Names` and `All text` modes. Names mode is the
default. Names and query text are case-folded, de-accented, whitespace
collapsed, and normalized from typographic to straight apostrophes. Original
names remain unchanged for display. Every name token must occur as a
deterministic substring of the normalized name; this means `fire ball` can
match `Fireball`, but this is not fuzzy or typo search.

Results rank exact normalized-name matches first, then full-name prefixes, then
other name matches. All-text mode places these name matches ahead of entries
that only match the SQLite FTS5 document. Empty text browses the selected
category in stable alphabetical order. Pages use SQL `offset`/`limit` with a
default of 50, a maximum of 200, and a separate total count.

Migration `003_search_fts.sql` creates the FTS5 index. The document includes
entry descriptions and sections, spell higher-level text, item properties,
feat benefits, and class/subclass content. Dataset replacement rebuilds the
affected FTS rows in the same transaction as the content tables. SQLite FTS5
is required; initialization fails with an actionable error when it is absent.
