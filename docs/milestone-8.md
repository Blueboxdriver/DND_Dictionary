# Milestone 8: SRD spells and feats

`src/dndref/datasets/srd-5.2.1/` contains the reviewed English SRD 5.2.1
spells and feats pack. It includes 339 spells and 17 feats from the official
SRD PDF, with empty valid item and class arrays because Items and Classes are
separate milestones.

The dataset is licensed under CC-BY-4.0 and includes the required SRD
attribution in `manifest.json`. The source is the official English SRD 5.2.1
published at <https://www.dndbeyond.com/srd>. No artwork or non-SRD source text
is included.

`inventory/spells.json` and `inventory/feats.json` are reconciliation reports,
not alternate sources of truth. Each records the stable key, source locator,
and reviewed status. The conversion script is
`tools/build_srd_dataset.py`; it extracts only the spell and feat sections from
the official PDF, repairs PDF line-wrap hyphenation, normalizes typographic
punctuation, and writes deterministic JSON.

Spell class lists are retained as `source_classes` in the spell inventory for
review, but production `class_references` are empty until the supported class
records exist in Milestone 10. This follows the current validator/importer
policy and avoids placeholder class content.

Validate and import the pack with:

```sh
PYTHONPATH=src .venv/bin/dndref validate src/dndref/datasets/srd-5.2.1
PYTHONPATH=src .venv/bin/dndref import src/dndref/datasets/srd-5.2.1 --dry-run
```
