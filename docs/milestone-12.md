# Milestone 12: Release

Milestone 12 prepares the repository as a locally testable `0.1.0` release
candidate. It does not publish to PyPI or start Milestone 13 work.

## Scope implemented

- Added release metadata, README metadata, Linux-first classifiers, an
  application-code license file, and the existing `images` extra.
- Added package-resource access for read-only bundled datasets.
- First startup now initializes SQLite and imports any missing bundled dataset;
  repeated startup is idempotent and keeps user state under platform/XDG paths.
- Added package-data coverage for dataset JSON, manifests, inventories, and all
  SQL migrations.
- Added release tests and `scripts/verify_release.py` for local artifact and
  clean-environment smoke verification.
- Added release-oriented installation, data, graphics, uninstall, and manual
  verification documentation to the README.

No database schema or production record was changed.

## Package and artifacts

- Distribution name: `dnd-reference`
- Version: `0.1.0`
- Python: `>=3.12`
- Base runtime: Pydantic, platformdirs, and Textual.
- Optional image extra: Pillow `<13` and textual-image `<1.0`.
- Console scripts: `dndref` and the retained `dndref-spike`.
- Application-code license metadata: `LicenseRef-Proprietary`, with `LICENSE`
  included in the wheel and sdist. Dataset licensing remains separate and is
  recorded in each manifest and the About/Data screen.

Build command:

```sh
python -m build --wheel --sdist --outdir dist
```

Artifacts built:

- `dist/dnd_reference-0.1.0-py3-none-any.whl`
- `dist/dnd_reference-0.1.0.tar.gz`

The wheel was inspected directly: 49 files were present and no `.git`, cache,
virtual-environment, SQLite, or log files were present. The sdist contained 77
files and passed the same junk-file check.

## Bundled datasets and inventories

Both packs are read through `importlib.resources` and are included in the wheel
and sdist.

| Dataset | Version | Items | Item properties | Spells | Feats | Classes | Subclasses | Class features | Subclass features |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SRD 5.2.1 (`srd-5-2-1`) | 5.2.1 | 0 | 0 | 339 | 17 | 0 | 0 | 0 | 0 |
| Production 2024/reference (`official-5etools-2024`) | `3a09c05a3a3be94423cd2b3c33936034eeae02f2` | 2,541 | 13 | 444 | 179 | 13 | 76 | 302 | 508 |

The release wheel contains both manifests, all five runtime JSON files per pack,
and the three migrations. Conversion/reconciliation inventories remain in the
source checkout for development verification and are intentionally excluded
from the runtime distribution. Their hashes and source metadata remain
unchanged.

## Verification performed

The release verification script was run successfully:

```sh
python scripts/verify_release.py --keep
```

It built both artifacts and tested three isolated environments outside the
repository.

### Base wheel

- Installed `dnd_reference-0.1.0-py3-none-any.whl`, not the source tree or an
  editable install.
- `dndref` resolved from the isolated environment's `site-packages`.
- Pillow and textual-image were absent.
- `--help`, `--version`, `--images off`, and `--images auto` worked.
- Explicit `kitty` and `sixel` startup also returned cleanly in the non-graphics
  environment.
- First startup created XDG config/data/cache/state paths, applied migrations,
  and imported both packs. A second startup reused the same database.
- Database verification found 3,533 entries and 3,533 FTS rows.
- Representative installed-package search/detail checks passed for Longsword and
  Fireball.
- Local fixture `validate`, dry-run import, actual import, repeated no-op import,
  and missing-path error handling passed.
- Headless Textual startup, help, About/Data, and clean exit passed.

### Image extra

Installed the wheel with `[images]` in a separate environment. Pillow 12.3.0
and textual-image 0.14.0 imported successfully. BrowserApp and ImageAdapter
construction passed, `--images off` passed, and `--images auto` safely disabled
itself in the current non-compatible terminal environment.

### sdist

The sdist was independently installed into a third isolated environment. Its
package resources were retained, first-run initialization produced the same
3,533 entries, and representative class search passed.

### Offline runtime

Startup, bundled-data initialization, search, detail retrieval, and image
fallback were run with socket connection methods blocked. No network access was
attempted. Source URLs remain metadata only; normal runtime does not fetch them.

### Checks

- Full pytest: **112 passed, 2 skipped**.
- Ruff: passed.
- Python compilation: passed for `src`, `tests`, `tools`, and `scripts`.
- Wheel and sdist build: passed.
- Wheel/sdist content inspection: passed.
- Production inventory and reconciliation tests: passed.

The two skipped tests are the existing optional image tests because the base
development environment does not contain Pillow or textual-image. The separate
image-extra environment passed its import and construction checks.

## Known limitations and manual checks

- Real Kitty/TGP and Sixel protocol rendering was not verified because no
  compatible terminal was available. The existing Milestone 11 manual matrix
  remains authoritative for that check.
- The release is Linux-first. Windows/macOS-specific behavior is not a release
  target.
- The official 2024/reference pack retains its existing personal/local-reference
  provenance and licensing warning. It is not relabeled as SRD content.
- No dedicated benchmark suite was added. Two warm text-only starts from the
  installed wheel measured 0.90 s and 0.87 s in this environment; no separate
  p95 search benchmark was run.

## Reproducibility and cleanup

Build output is kept in `dist/` and ignored by Git. Build scratch directories,
temporary environments, user databases, and caches used for verification were
kept outside the repository. The repository's `.git` directory is empty in this
workspace, so `git status` and `git diff --check` could not be run; no Git
metadata was available to inspect.

Milestone 13 was not started.
