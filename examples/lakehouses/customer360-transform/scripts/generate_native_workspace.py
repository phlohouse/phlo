#!/usr/bin/env python3
"""Regenerate the native Phlo Transform workspace from the dbt project.

The dbt project remains the checked-in source of truth. This script runs the
engine's one-way translator into a temporary directory, verifies the result
compiles, and only then replaces the generated workspace, so a failed
translation never leaves the example half-converted.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# reason: the project root must be importable before the scripts package resolves.
from scripts.dbt_sources import apply_source_assets, source_asset_map  # noqa: E402

NATIVE_ROOT = PROJECT_ROOT / "workflows" / "transforms" / "native"
DBT_ROOT = PROJECT_ROOT / "workflows" / "transforms" / "dbt"
GENERATED_TEST_HEADER = "-- Generated from the dbt project by phlo-transform; do not edit.\n"


def add_generated_headers(directory: Path) -> None:
    """Give generated singular tests the repository's SQL header."""

    for path in (directory / "tests").rglob("*.sql"):
        text = path.read_text(encoding="utf-8")
        if not text.lstrip().startswith("--"):
            path.write_text(GENERATED_TEST_HEADER + text, encoding="utf-8")


def _sync_generated(source: Path, destination: Path, source_assets: dict[str, str]) -> None:
    """Replace generated model, test, and config files with the translated set."""

    destination.mkdir(parents=True, exist_ok=True)
    transforms = destination / "transforms"
    tests = destination / "tests" / "generated"
    for directory in (transforms, tests):
        if directory.exists():
            shutil.rmtree(directory)
    shutil.copytree(source / "transforms", transforms)
    shutil.copytree(source / "tests" / "generated", tests)
    add_generated_headers(destination)
    shutil.copyfile(source / "phlo.toml", destination / "phlo.toml")
    apply_source_assets(destination / "phlo.toml", source_assets)
    manifest = source / ".phlo" / "migration" / "dbt-translation.json"
    if manifest.is_file():
        target = destination / ".phlo" / "migration" / "dbt-translation.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest, target)


def main() -> int:
    """Translate the dbt project and publish the native workspace."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Analyse the dbt project without writing the native workspace.",
    )
    parser.add_argument(
        "--dbt-project",
        type=Path,
        default=DBT_ROOT,
        help="dbt project to translate (default: workflows/transforms/dbt).",
    )
    args = parser.parse_args()

    from phlo_transform.settings import get_settings
    from phlo_transform.translate import translate_dbt_project

    settings = get_settings(PROJECT_ROOT)
    if args.check:
        invocation = translate_dbt_project(
            PROJECT_ROOT,
            settings,
            dbt_project=args.dbt_project,
            check_only=True,
        )
        print(invocation.stdout.strip())
        print(invocation.stderr.strip(), file=sys.stderr)
        return invocation.returncode

    with tempfile.TemporaryDirectory(prefix="phlo-transform-native-") as temporary:
        generated = Path(temporary) / "native"
        invocation = translate_dbt_project(
            PROJECT_ROOT,
            settings,
            dbt_project=args.dbt_project,
            out=generated,
            overwrite=True,
            verify=True,
        )
        print(invocation.stdout.strip())
        if invocation.stderr.strip():
            print(invocation.stderr.strip(), file=sys.stderr)
        if not invocation.ok:
            return invocation.returncode
        _sync_generated(
            generated,
            NATIVE_ROOT,
            source_asset_map(Path(args.dbt_project).resolve()),
        )
    print(f"Native workspace updated: {NATIVE_ROOT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
