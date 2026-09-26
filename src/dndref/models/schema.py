"""On-demand JSON Schema generation for dataset authors and tooling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from . import (
    CharacterBuilderCatalog,
    CharacterClass,
    DatasetManifest,
    Feat,
    ItemCatalog,
    Monster,
    Spell,
)


def generate_schemas(output_dir: Path) -> tuple[Path, ...]:
    """Write deterministic schemas for the manifest and category files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    models = {
        "manifest.schema.json": DatasetManifest,
        "items.schema.json": ItemCatalog,
        "spells.schema.json": list[Spell],
        "feats.schema.json": list[Feat],
        "classes.schema.json": list[CharacterClass],
        "monsters.schema.json": list[Monster],
        "character-builder.schema.json": CharacterBuilderCatalog,
    }
    written: list[Path] = []
    for filename, model in models.items():
        if hasattr(model, "model_json_schema"):
            schema = model.model_json_schema()
        else:
            schema = TypeAdapter(model).json_schema()
        path = output_dir / filename
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return tuple(written)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate dndref dataset JSON Schemas.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("schemas"),
        help="directory for generated schema files (default: schemas)",
    )
    args = parser.parse_args()
    for path in generate_schemas(args.output):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
