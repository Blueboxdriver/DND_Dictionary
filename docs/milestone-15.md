# Milestone 15: Monsters

Monsters use the shared `entries` identity, source, edition, image, and FTS
paths. Migration 005 expands the entry kind constraint and adds `monsters` and
`monster_abilities`. Numeric CR order is stored in eighths; the source-facing
fraction is retained as text. Ordered ability sections retain their prose.

The packaged monster records come from the same pinned
[5etools source](https://github.com/5etools-mirror-3/5etools-src) revision
as the official reference pack: `3a09c05a3a3be94423cd2b3c33936034eeae02f2`.
The input files are `data/bestiary/bestiary-xmm.json` and
`data/bestiary/legendarygroups.json`. The converter checks their SHA-256 hashes
before writing. Only XMM records are imported; the SRD pack stays separate.
The converter renders 5etools display tags as reference text and retains
Multiattack, spellcasting, reaction, legendary, and regional prose. It does not
infer combat sequences. XMM provides no explicit XP or proficiency-bonus field
in these stat blocks, so those fields remain empty rather than being invented.
Monster artwork is not packaged.

To reproduce after obtaining the pinned snapshot:

```sh
PYTHONPATH=src .venv/bin/python tools/build_monsters_dataset.py \
  SNAPSHOT/data/bestiary/bestiary-xmm.json \
  --legendary-groups SNAPSHOT/data/bestiary/legendarygroups.json
PYTHONPATH=src .venv/bin/python tools/build_monsters_dataset.py \
  SNAPSHOT/data/bestiary/bestiary-xmm.json \
  --legendary-groups SNAPSHOT/data/bestiary/legendarygroups.json --check
.venv/bin/dndref validate src/dndref/datasets/official-5etools-2024
```

An unchanged previously installed official pack is upgraded on startup. A
locally modified pack with the same ID is left untouched; import the new pack
explicitly if replacement is wanted. The user database remains offline.
