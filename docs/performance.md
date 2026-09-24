# Performance development

Run the application with `DNDREF_PROFILE=1 .venv/bin/dndref` or
`.venv/bin/dndref --profile`. Profiling is off by default. The report contains
aggregate startup, category query/filter/group, row creation/mount, detail, and
personal-data timings. SQLite statement counts are attached to named operations;
SQL text, search strings, notes, and other personal content are not recorded.
The TUI prints the report when it exits.

For repeatable measurements against an installed database, run:

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmark_ui.py [DATABASE_PATH] --iterations 3 --size 100x30
```

The benchmark reports database-only queries and headless Textual navigation at
the requested terminal size. It covers all categories, filtered and universal
search, details, Back restoration, source browsing, Favorites, and Collections.
It also reports the retained Python heap change while warming one page for each
category.
The headless UI timings include Textual's test-runner refresh work and are most
useful for comparing runs on the same machine; they are not a substitute for a
manual terminal protocol check. Use the same populated database, size, and
iteration count before and after a change. Timings are informational and do not
fail CI.

Category pages initially request 20 summaries and load another page as the
selection approaches the end. In-session result reuse is keyed by category,
query, filters, grouping settings, and a reference-data generation made from
the schema version and installed dataset versions/content hashes. A dataset
import or relevant migration changes that generation and invalidates cached
pages. Bundled-pack startup reuse hashes the loader inputs against the value
stored at import; a mismatch falls back to full parsing, validation, and the
normal content-hash upgrade check.
