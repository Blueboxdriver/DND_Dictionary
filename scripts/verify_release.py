"""Build and smoke-test local 0.1 release artifacts.

Usage: python scripts/verify_release.py

The script expects the PEP 517 frontend ``build`` to be installed in the
invoking environment. It creates all test environments outside the repository
and never publishes artifacts.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DATASETS = {
    "srd-5.2.1": ("manifest.json", "items.json", "spells.json", "feats.json", "classes.json"),
    "official-5etools-2024": (
        "manifest.json",
        "items.json",
        "spells.json",
        "feats.json",
        "classes.json",
    ),
}


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


def venv_python(path: Path) -> Path:
    return path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def smoke_environment(python: Path, wheel: Path, root: Path, *, images: bool = False) -> None:
    requirement = f"{wheel}[images]" if images else str(wheel)
    run([str(python), "-m", "pip", "install", requirement], cwd=ROOT)
    code = (
        "import importlib.util; "
        "from dndref import __version__; "
        "from dndref.bundled import BUNDLED_DATASET_DIRECTORIES; "
        "assert __version__ == '0.1.0'; "
        "assert len(BUNDLED_DATASET_DIRECTORIES) == 2; "
        f"assert bool(importlib.util.find_spec('PIL')) is {images!r}; "
        f"assert bool(importlib.util.find_spec('textual_image')) is {images!r}"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        env[f"XDG_{name}_HOME"] = str(root / f"xdg-{name.lower()}")
    run([str(python), "-c", code], cwd=Path(tempfile.gettempdir()), env=env)
    run([str(python), "-m", "dndref", "--help"], cwd=Path(tempfile.gettempdir()), env=env)
    run([str(python), "-m", "dndref", "--images", "off"], cwd=Path(tempfile.gettempdir()), env=env)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keep", action="store_true", help="keep the temporary verification directory"
    )
    args = parser.parse_args()
    temp_context = tempfile.TemporaryDirectory(prefix="dndref-release-")
    try:
        root = Path(temp_context.name)
        dist = root / "dist"
        run([sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(dist)])
        artifacts = sorted(dist.iterdir())
        wheels = [path for path in artifacts if path.suffix == ".whl"]
        sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
        if len(wheels) != 1 or len(sdists) != 1:
            raise RuntimeError(f"expected one wheel and one sdist, found {artifacts}")
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
        for directory, files in EXPECTED_DATASETS.items():
            for filename in files:
                expected = f"dndref/datasets/{directory}/{filename}"
                if expected not in names:
                    raise RuntimeError(f"wheel is missing {expected}")

        base_env = root / "base"
        image_env = root / "images"
        sdist_env = root / "sdist"
        for environment in (base_env, image_env, sdist_env):
            venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        smoke_environment(venv_python(base_env), wheel, base_env)
        smoke_environment(venv_python(image_env), wheel, image_env, images=True)
        run([str(venv_python(sdist_env)), "-m", "pip", "install", str(sdists[0])])
        run(
            [str(venv_python(sdist_env)), "-m", "dndref", "--version"],
            cwd=Path(tempfile.gettempdir()),
        )
        print(f"Verified {wheel.name} and {sdists[0].name}")
        return 0
    finally:
        if args.keep:
            print(f"Kept verification directory: {temp_context.name}")
            temp_context._finalizer.detach()
        else:
            temp_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
