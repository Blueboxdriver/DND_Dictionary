"""Build and inspect release archives, then smoke-test clean wheel installs.

Run with ``.venv/bin/python scripts/verify_release.py``. Artifacts remain in
``dist/``; test environments and application state are temporary.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path

from dndref import __version__

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERSION = __version__
WHEEL_NAME = f"dnd_reference-{VERSION}-py3-none-any.whl"
SDIST_NAME = f"dnd_reference-{VERSION}.tar.gz"
BASE_DATA_FILES = ("manifest.json", "items.json", "spells.json", "feats.json", "classes.json")
GLOSSARY_DATA_FILES = ("conditions.json", "rules.json")
BUILDER_DATA_FILES = ("character-builder.json",)
DATASETS = ("srd-5.2.1", "official-5etools-2024")
MIGRATIONS = (
    "001_initial.sql",
    "002_content.sql",
    "003_search_fts.sql",
    "004_canonical_source_editions.sql",
    "005_monsters.sql",
    "006_personal_organization.sql",
    "007_recent_searches.sql",
    "008_conditions_rules.sql",
    "009_dataset_source_hash.sql",
    "010_character_builder.sql",
    "011_character_persistence.sql",
    "012_character_creation.sql",
)


def run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def audit_artifacts(wheel: Path, sdist: Path) -> None:
    expected = {
        f"dndref/datasets/{pack}/{name}"
        for pack in DATASETS
        for name in BASE_DATA_FILES
    }
    expected.update(
        f"dndref/datasets/official-5etools-2024/{name}" for name in GLOSSARY_DATA_FILES
    )
    expected.update(
        f"dndref/datasets/official-5etools-2024/{name}" for name in BUILDER_DATA_FILES
    )
    expected.update(f"dndref/storage/migrations/{name}" for name in MIGRATIONS)
    expected.update(
        (
            "dndref/characters.py",
            "dndref/character_creation.py",
            "dndref/character_progression.py",
            "dndref/derived_character.py",
            "dndref/models/character.py",
            "dndref/models/derived_character.py",
            "dndref/models/progression.py",
            "dndref/ui/character_screens.py",
            "dndref/ui/character_progression.py",
        )
    )
    expected.update(("dndref/__init__.py", "dndref/__main__.py", "dndref/cli.py"))
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
        metadata = archive.read(f"dnd_reference-{VERSION}.dist-info/METADATA").decode()
        entrypoints = archive.read(f"dnd_reference-{VERSION}.dist-info/entry_points.txt").decode()
    with tarfile.open(sdist) as archive:
        sdist_names = {
            name.removeprefix(f"dnd_reference-{VERSION}/").removeprefix("src/")
            for name in archive.getnames()
        }
    for label, names in (("wheel", wheel_names), ("sdist", sdist_names)):
        missing = expected - names
        if missing:
            raise RuntimeError(f"{label} missing resources: {sorted(missing)}")
        if any(
            "dndref_spike" in name or name.endswith((".pyc", ".db", ".sqlite3")) for name in names
        ):
            raise RuntimeError(f"{label} contains unintended runtime files")
    if not any(name.endswith("/LICENSE") for name in wheel_names) or "LICENSE" not in sdist_names:
        raise RuntimeError("application license missing from an archive")
    if "Requires-Python: >=3.12" not in metadata or "Provides-Extra: images" not in metadata:
        raise RuntimeError("incorrect wheel metadata")
    if (
        "pytest" in metadata.split("Provides-Extra: dev")[0]
        or "ruff" in metadata.split("Provides-Extra: dev")[0]
    ):
        raise RuntimeError("development dependency leaked into base runtime")
    if "dndref = dndref.__main__:main" not in entrypoints or "dndref-spike" in entrypoints:
        raise RuntimeError("incorrect console scripts")
    print(f"Inspected wheel ({len(wheel_names)} files) and sdist ({len(sdist_names)} files)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true", help="verify existing dist artifacts")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=DIST,
        help="artifact directory (defaults to the repository dist directory)",
    )
    args = parser.parse_args()
    output_dir = args.outdir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    wheel_name = f"dnd_reference-{VERSION}-py3-none-any.whl"
    sdist_name = f"dnd_reference-{VERSION}.tar.gz"
    clean_environment = dict(os.environ)
    clean_environment.pop("PYTHONPATH", None)
    if not args.skip_build:
        for artifact in output_dir.iterdir():
            if artifact.is_file():
                if artifact.name not in {wheel_name, sdist_name}:
                    raise RuntimeError(f"refusing to remove unrelated artifact: {artifact}")
                artifact.unlink()
            else:
                raise RuntimeError(f"refusing to replace non-file artifact: {artifact}")
        run(
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--sdist",
            "--outdir",
            str(output_dir),
            env=clean_environment,
        )
    wheel = output_dir / WHEEL_NAME
    sdist = output_dir / SDIST_NAME
    if {p.name for p in output_dir.iterdir()} != {WHEEL_NAME, SDIST_NAME}:
        names = sorted(path.name for path in output_dir.iterdir())
        raise RuntimeError(f"unexpected release contents: {names}")
    audit_artifacts(wheel, sdist)

    with tempfile.TemporaryDirectory(prefix="dndref-release-") as temporary:
        root = Path(temporary)
        for extra in (False, True):
            label = "images" if extra else "base"
            environment = root / label
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment / "bin" / "python"
            requirement = f"{wheel}[images]" if extra else str(wheel)
            run(
                str(python),
                "-m",
                "pip",
                "install",
                requirement,
                env=clean_environment,
            )
            test_env = dict(clean_environment)
            test_env.update(
                {
                    f"XDG_{name}_HOME": str(root / label / name.lower())
                    for name in ("CONFIG", "DATA", "CACHE", "STATE")
                }
            )
            test_env["TERM"] = "dumb"
            run(
                str(python),
                str(ROOT / "scripts" / "release_smoke.py"),
                "images" if extra else "base",
                str(ROOT / "tests" / "fixtures" / "dataset"),
                cwd=root,
                env=test_env,
            )
    print(f"Verified {wheel.name} and {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
