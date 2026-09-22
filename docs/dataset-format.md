# Dataset format

Milestone 3 defines versioned UTF-8 JSON packs. A pack has this layout:

```text
dataset/
├── manifest.json
├── items.json
├── spells.json
├── feats.json
├── classes.json
└── images/
```

The Pydantic models under `dndref.models` are authoritative. Category files use
arrays for spells, feats, and classes. `items.json` is an object containing
`properties` and `items`, so reusable item properties are declared once and
linked by `property_key`.

Every entry has a dataset-independent `local_key`, such as
`spell/comet-burst`. Its stable identity is `dataset_id:local_key`, such as
`example-5e:spell/comet-burst`. A reference without a dataset prefix is local
to the current dataset; a prefixed reference is cross-dataset and is not
resolved until the importer milestone. Keys are relative and cannot contain
path traversal, backslashes, whitespace, or a colon.

`manifest.json` declares `schema_version` (`"1.0"`), dataset identity and
version, ruleset, language, license and attribution, sources, and optional
dependencies. Entries store source keys, not copied source metadata. Markdown
descriptions are plain local content; HTML and remote content are not part of
the contract. Image references are relative paths under the pack.

The four category models cover structured spell components, item weapon/armor
details, feat benefits, and class features, subclasses, and presentation-only
progression columns and values. Prerequisites and rules text remain display
content; the models do not implement a rules engine.

## Import and storage

Use `dndref validate PATH` to parse and validate a pack without touching the
database. `dndref import PATH --dry-run` performs the same validation and
reports additions, changes, removals, and unchanged entries. A normal import
replaces the complete snapshot for that `dataset_id` in one SQLite transaction;
other installed datasets are not affected. Reimporting the same content hash is
a no-op, and `dataset_id:local_key` remains the stable external identity even
when a database row is recreated.

Referenced local PNG, JPEG, and WebP assets must stay inside the pack. They are
hashed before import and staged under content-addressed names in application
data storage. Files staged before a failed database transaction are left
unreferenced for later cleanup. Cross-dataset content references are not yet
supported and are rejected; manifest dependencies are stored but not resolved.

## JSON Schema

Generate schemas on demand from the Pydantic models. No hand-maintained schema
files are checked in:

```sh
PYTHONPATH=src .venv/bin/python -m dndref.models.schema --output schemas
```

This writes deterministic `manifest.schema.json`, `items.schema.json`,
`spells.schema.json`, `feats.schema.json`, and `classes.schema.json` files.
