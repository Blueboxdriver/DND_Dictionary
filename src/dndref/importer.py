"""Dataset loading, validation, planning, asset staging, and import orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .models import DatasetPack, DatasetValidationError, split_reference, validate_dataset
from .storage.database import Database
from .storage.repository import (
    InstalledDataset,
    apply_dataset,
    get_installed_dataset,
)

REQUIRED_FILES = ("manifest.json", "items.json", "spells.json", "feats.json", "classes.json")
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class DatasetError(ValueError):
    """Base class for actionable dataset and import errors."""


class DatasetLoadError(DatasetError):
    """Raised when a dataset pack cannot be read or parsed."""


class AssetValidationError(DatasetLoadError):
    """Raised when a referenced local asset is unsafe or unavailable."""


class DatasetImportError(RuntimeError):
    """Raised when a validated dataset cannot be applied atomically."""


@dataclass(frozen=True)
class AssetRecord:
    relative_path: str
    source_path: Path
    content_hash: str
    media_type: str


@dataclass(frozen=True)
class LoadedDataset:
    root: Path
    pack: DatasetPack
    assets: tuple[AssetRecord, ...]
    content_hash: str
    source_hash: str
    entry_hashes: dict[str, str]

    @property
    def dataset_id(self) -> str:
        return str(self.pack.manifest.dataset_id)

    @property
    def entry_count(self) -> int:
        return len(self.entry_hashes)


@dataclass(frozen=True)
class ImportReport:
    """Entry-level comparison between an incoming pack and its installed version."""

    dataset_id: str
    content_hash: str
    added: int
    changed: int
    removed: int
    unchanged: int
    total: int
    content_unchanged: bool = False

    @property
    def additions(self) -> int:
        return self.added

    @property
    def changes(self) -> int:
        return self.changed

    @property
    def removals(self) -> int:
        return self.removed

    @property
    def is_noop(self) -> bool:
        return self.content_unchanged


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key '{key}'")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DatasetLoadError(f"Missing required file: {path.name}") from exc
    except UnicodeError as exc:
        raise DatasetLoadError(f"Invalid UTF-8 in {path.name}: {exc}") from exc
    except OSError as exc:
        raise DatasetLoadError(f"Cannot read {path.name}: {exc}") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DatasetLoadError(f"Malformed JSON in {path.name}: {exc}") from exc


def _validate_model(filename: str, model_type: Any, raw: Any) -> Any:
    try:
        if hasattr(model_type, "model_validate"):
            return model_type.model_validate(raw)
        return model_type.validate_python(raw)
    except ValidationError as exc:
        raise DatasetLoadError(f"Invalid {filename}: {exc}") from exc


def _all_entries(pack: DatasetPack) -> tuple[tuple[str, Any], ...]:
    return tuple(
        [("item", item) for item in pack.items.items]
        + [("spell", spell) for spell in pack.spells]
        + [("feat", feat) for feat in pack.feats]
        + [("class", character_class) for character_class in pack.classes]
        + [("monster", monster) for monster in pack.monsters]
        + [("condition", entry) for entry in pack.conditions]
        + [("rule", entry) for entry in pack.rules]
    )


def _validate_storage_invariants(pack: DatasetPack) -> None:
    errors: list[str] = []
    dataset_id = str(pack.manifest.dataset_id)
    for spell in pack.spells:
        for index, reference in enumerate(spell.class_references):
            reference_dataset, _ = split_reference(reference, dataset_id)
            if reference_dataset != dataset_id:
                errors.append(
                    f"spells.json[{spell.local_key}].class_references[{index}]: "
                    f"cross-dataset reference '{reference}' is not supported"
                )

    for filename, entries in (
        ("items.json", pack.items.items),
        ("spells.json", pack.spells),
        ("feats.json", pack.feats),
        ("classes.json", pack.classes),
        ("monsters.json", pack.monsters),
        ("conditions.json", pack.conditions),
        ("rules.json", pack.rules),
    ):
        for entry in entries:
            sections = list(entry.sections)
            if filename == "feats.json":
                sections.extend(entry.benefits)
            keys = [section.key for section in sections]
            if len(keys) != len(set(keys)):
                errors.append(f"{filename}:{entry.local_key}: duplicate entry section key")

    for item in pack.items.items:
        property_keys = [reference.property_key for reference in item.properties]
        if len(property_keys) != len(set(property_keys)):
            errors.append(f"items.json:{item.local_key}: duplicate item property reference")

    if errors:
        raise DatasetValidationError("dataset storage validation failed:\n" + "\n".join(errors))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AssetValidationError(f"Cannot read image asset {path}: {exc}") from exc
    return digest.hexdigest()


def dataset_source_hash(
    root: Path | str,
    asset_paths: tuple[str, ...] | list[str] = (),
    *,
    known_asset_hashes: dict[str, str] | None = None,
) -> str | None:
    """Hash loader inputs for safe bundled-pack reuse without parsing every JSON file."""
    root_path = Path(root).resolve()
    names = {
        name
        for name in (
            *REQUIRED_FILES,
            "monsters.json",
            "conditions.json",
            "rules.json",
            "character-builder.json",
        )
        if (root_path / name).is_file()
    }
    names.update(asset_paths)
    known_hashes = known_asset_hashes or {}
    digest = hashlib.sha256()
    for name in sorted(names):
        path = (root_path / name).resolve()
        try:
            path.relative_to(root_path)
        except ValueError:
            return None
        digest.update(b"\0file\0")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        known_hash = known_hashes.get(name)
        if known_hash is not None:
            digest.update(known_hash.encode("ascii"))
            continue
        try:
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
    return digest.hexdigest()


def _validate_assets(root: Path, pack: DatasetPack) -> tuple[AssetRecord, ...]:
    root = root.resolve()
    references = sorted(
        {str(entry.image) for _, entry in _all_entries(pack) if entry.image is not None}
    )
    assets: list[AssetRecord] = []
    for reference in references:
        source_path = (root / reference).resolve()
        try:
            relative_path = source_path.relative_to(root).as_posix()
        except ValueError as exc:
            raise AssetValidationError(
                f"Image path escapes dataset directory: {reference}"
            ) from exc
        suffix = source_path.suffix.lower()
        media_type = _MEDIA_TYPES.get(suffix)
        if media_type is None:
            raise AssetValidationError(
                f"Unsupported image format for {reference}; allowed formats are PNG, JPEG, and WebP"
            )
        if not source_path.is_file():
            raise AssetValidationError(f"Missing image asset: {reference}")
        assets.append(
            AssetRecord(
                relative_path=relative_path,
                source_path=source_path,
                content_hash=_hash_file(source_path),
                media_type=media_type,
            )
        )
    return tuple(assets)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _entry_hash(kind: str, entry: Any) -> str:
    payload = {"kind": kind, "entry": entry.model_dump(mode="json")}
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _content_hash(
    pack: DatasetPack, assets: tuple[AssetRecord, ...], *, include_monsters: bool = True
) -> str:
    raw = pack.model_dump(mode="json")
    if not include_monsters:
        for category in ("monsters", "conditions", "rules"):
            raw.pop(category, None)
    raw["items"]["properties"] = sorted(raw["items"]["properties"], key=lambda item: item["key"])
    raw["items"]["items"] = sorted(raw["items"]["items"], key=lambda item: item["local_key"])
    categories = ("spells", "feats", "classes", "monsters", "conditions", "rules")
    for category in categories if include_monsters else categories[:-3]:
        raw[category] = sorted(raw[category], key=lambda item: item["local_key"])
    digest = hashlib.sha256(_canonical(raw).encode())
    for asset in assets:
        digest.update(b"\0asset\0")
        digest.update(asset.relative_path.encode())
        digest.update(b"\0")
        digest.update(asset.content_hash.encode())
    return digest.hexdigest()


def legacy_monsterless_hash(loaded: LoadedDataset) -> str:
    """Hash the pre-Milestone-15 shape to identify an unmodified bundled pack."""
    raw = loaded.pack.model_dump(mode="json")
    for category in ("monsters", "conditions", "rules"):
        raw.pop(category, None)
    raw.pop("character_builder", None)
    return _hash_pack_shape(raw, loaded.assets)


def legacy_glossaryless_hash(loaded: LoadedDataset) -> str:
    """Hash the Milestone 18 shape to safely identify the prior bundled snapshot."""
    raw = loaded.pack.model_dump(mode="json")
    raw.pop("conditions", None)
    raw.pop("rules", None)
    raw.pop("character_builder", None)
    return _hash_pack_shape(raw, loaded.assets)


def legacy_pre_character_builder_hash(loaded: LoadedDataset) -> str:
    """Hash the previous full pack shape before Milestone 22 builder metadata."""

    raw = loaded.pack.model_dump(mode="json")
    raw.pop("character_builder", None)
    raw["items"]["properties"] = sorted(raw["items"]["properties"], key=lambda item: item["key"])
    raw["items"]["items"] = sorted(raw["items"]["items"], key=lambda item: item["local_key"])
    for category in ("spells", "feats", "classes", "monsters", "conditions", "rules"):
        raw[category] = sorted(raw[category], key=lambda item: item["local_key"])
    digest = hashlib.sha256(_canonical(raw).encode())
    for asset in loaded.assets:
        digest.update(b"\0asset\0")
        digest.update(asset.relative_path.encode())
        digest.update(b"\0")
        digest.update(asset.content_hash.encode())
    return digest.hexdigest()


def _hash_pack_shape(raw: dict[str, Any], assets: tuple[AssetRecord, ...]) -> str:
    def remove_references(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: remove_references(child) for key, child in value.items() if key != "references"
            }
        if isinstance(value, list):
            return [remove_references(child) for child in value]
        return value

    raw = remove_references(raw)
    raw["items"]["properties"] = sorted(raw["items"]["properties"], key=lambda item: item["key"])
    raw["items"]["items"] = sorted(raw["items"]["items"], key=lambda item: item["local_key"])
    for category in ("spells", "feats", "classes", "monsters", "conditions", "rules"):
        if category in raw:
            raw[category] = sorted(raw[category], key=lambda item: item["local_key"])
    digest = hashlib.sha256(_canonical(raw).encode())
    for asset in assets:
        digest.update(b"\0asset\0")
        digest.update(asset.relative_path.encode())
        digest.update(b"\0")
        digest.update(asset.content_hash.encode())
    return digest.hexdigest()


def load_dataset(path: Path | str) -> LoadedDataset:
    """Load, parse, validate, and inspect a local dataset pack without writing state."""
    root = Path(path)
    if not root.is_dir():
        raise DatasetLoadError(f"Dataset path is not a directory: {root}")
    files = {name: root / name for name in REQUIRED_FILES}
    for name, file_path in files.items():
        if not file_path.is_file():
            raise DatasetLoadError(f"Missing required file: {name}")

    from .models import (
        CharacterBuilderCatalog,
        CharacterClass,
        DatasetManifest,
        Feat,
        GlossaryEntry,
        ItemCatalog,
        Monster,
        Spell,
    )

    manifest = _validate_model("manifest.json", DatasetManifest, _read_json(files["manifest.json"]))
    items = _validate_model("items.json", ItemCatalog, _read_json(files["items.json"]))
    spells = _validate_model(
        "spells.json", TypeAdapter(list[Spell]), _read_json(files["spells.json"])
    )
    feats = _validate_model("feats.json", TypeAdapter(list[Feat]), _read_json(files["feats.json"]))
    classes = _validate_model(
        "classes.json", TypeAdapter(list[CharacterClass]), _read_json(files["classes.json"])
    )
    monsters = _validate_model(
        "monsters.json",
        TypeAdapter(list[Monster]),
        _read_json(root / "monsters.json") if (root / "monsters.json").is_file() else [],
    )
    glossary = {}
    for name in ("conditions", "rules"):
        path = root / f"{name}.json"
        glossary[name] = _validate_model(
            f"{name}.json",
            TypeAdapter(list[GlossaryEntry]),
            _read_json(path) if path.is_file() else [],
        )
    builder_path = root / "character-builder.json"
    character_builder = (
        _validate_model(
            "character-builder.json",
            CharacterBuilderCatalog,
            _read_json(builder_path),
        )
        if builder_path.is_file()
        else None
    )
    pack = DatasetPack(
        manifest=manifest,
        items=items,
        spells=spells,
        feats=feats,
        classes=classes,
        monsters=monsters,
        conditions=glossary["conditions"],
        rules=glossary["rules"],
        character_builder=character_builder,
    )
    try:
        validate_dataset(pack)
        _validate_storage_invariants(pack)
    except DatasetValidationError as exc:
        raise DatasetLoadError(str(exc)) from exc
    assets = _validate_assets(root, pack)
    source_hash = dataset_source_hash(
        root,
        tuple(asset.relative_path for asset in assets),
        known_asset_hashes={asset.relative_path: asset.content_hash for asset in assets},
    )
    if source_hash is None:
        raise DatasetLoadError("Cannot hash all dataset files safely")
    entry_hashes = {
        str(entry.local_key): _entry_hash(kind, entry) for kind, entry in _all_entries(pack)
    }
    return LoadedDataset(
        root=root.resolve(),
        pack=pack,
        assets=assets,
        content_hash=_content_hash(pack, assets),
        source_hash=source_hash,
        entry_hashes=entry_hashes,
    )


def load_dataset_pack(path: Path | str) -> LoadedDataset:
    """Compatibility spelling for callers that prefer the pack terminology."""
    return load_dataset(path)


def _report(loaded: LoadedDataset, installed: InstalledDataset | None) -> ImportReport:
    if installed is not None and installed.content_hash == loaded.content_hash:
        return ImportReport(
            dataset_id=loaded.dataset_id,
            content_hash=loaded.content_hash,
            added=0,
            changed=0,
            removed=0,
            unchanged=loaded.entry_count,
            total=loaded.entry_count,
            content_unchanged=True,
        )
    old = installed.entry_hashes if installed is not None else {}
    incoming = loaded.entry_hashes
    added = len(set(incoming) - set(old))
    removed = len(set(old) - set(incoming))
    changed = sum(1 for key in set(incoming) & set(old) if incoming[key] != old[key])
    unchanged = len(set(incoming) & set(old)) - changed
    return ImportReport(
        dataset_id=loaded.dataset_id,
        content_hash=loaded.content_hash,
        added=added,
        changed=changed,
        removed=removed,
        unchanged=unchanged,
        total=loaded.entry_count,
    )


def plan_import(database: Database, loaded: LoadedDataset) -> ImportReport:
    """Compare a validated pack with the installed snapshot without changing it."""
    if not database.path.exists():
        return _report(loaded, None)
    with database.connection() as connection:
        return _report(loaded, get_installed_dataset(connection, loaded.dataset_id))


class AssetStore:
    """Content-addressed application-managed storage for imported local assets."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def stage(self, assets: tuple[AssetRecord, ...]) -> dict[str, str]:
        staged: dict[str, str] = {}
        for asset in assets:
            relative_target = Path("sha256") / asset.content_hash[:2] / asset.content_hash
            target = self.root / relative_target
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and _hash_file(target) != asset.content_hash:
                raise AssetValidationError(f"Existing staged asset has wrong hash: {target}")
            if not target.exists():
                temporary: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
                    ) as output:
                        temporary = Path(output.name)
                        with asset.source_path.open("rb") as source:
                            shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                except OSError as exc:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                    raise AssetValidationError(
                        f"Cannot stage image asset {asset.relative_path}: {exc}"
                    ) from exc
            staged[asset.relative_path] = relative_target.as_posix()
        return staged


def import_dataset(
    database: Database,
    loaded: LoadedDataset,
    asset_store: AssetStore | None = None,
    *,
    dry_run: bool = False,
) -> ImportReport:
    """Plan or apply one complete dataset snapshot."""
    if dry_run:
        return plan_import(database, loaded)

    database.initialize()
    with database.connection() as connection:
        installed = get_installed_dataset(connection, loaded.dataset_id)
        report = _report(loaded, installed)
    if report.is_noop:
        if installed is not None and installed.source_hash != loaded.source_hash:
            with database.connection() as connection:
                has_source_hash = any(
                    str(column[1]) == "source_hash"
                    for column in connection.execute("PRAGMA table_info(datasets)").fetchall()
                )
                if has_source_hash:
                    connection.execute(
                        "UPDATE datasets SET source_hash = ? WHERE dataset_id = ?",
                        (loaded.source_hash, loaded.dataset_id),
                    )
                    connection.commit()
        return report

    store = asset_store or AssetStore(database.path.parent / "assets")
    staged_assets = store.stage(loaded.assets)
    with database.connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            apply_dataset(connection, loaded, staged_assets)
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise DatasetImportError(
                f"Dataset '{loaded.dataset_id}' import failed; database unchanged: {exc}"
            ) from exc
    return report


def format_report(report: ImportReport, *, dry_run: bool = False) -> str:
    prefix = "Import plan" if dry_run else "Imported"
    if report.is_noop:
        return f"{prefix} dataset '{report.dataset_id}': no changes ({report.unchanged} unchanged)."
    return (
        f"{prefix} dataset '{report.dataset_id}': "
        f"{report.added} added, {report.changed} changed, {report.removed} removed, "
        f"{report.unchanged} unchanged."
    )


def format_validation(loaded: LoadedDataset) -> str:
    return (
        f"Validated dataset '{loaded.dataset_id}': {loaded.entry_count} entries, "
        f"{len(loaded.pack.items.properties)} item properties, {len(loaded.assets)} assets."
    )
