#!/usr/bin/env python3
"""Plan the release dependency refresh lanes without mutating dependency files.

Read-only: scans every pyproject.toml (skipping .venv/dist/docs-site),
maps each declared dependency to its patch or risk-managed lane, and
reports locked versions and manifest locations as JSON or Markdown.
Unmatched or missing-lock dependencies fail validation instead of
defaulting to a lane.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Lane = Literal["patch", "risk-managed"]

PATCH_REFRESH_PACKAGES = ("psycopg2-binary", "pytest", "ruff")
RISK_MANAGED_PACKAGES = (
    "clickhouse-connect",
    "dagster",
    "dagster-webserver",
    "dbt-core",
    "opentelemetry-api",
    "opentelemetry-exporter-otlp",
    "opentelemetry-exporter-otlp-proto-grpc",
    "opentelemetry-sdk",
    "pyarrow",
    "rich",
)
LANES: dict[Lane, tuple[str, ...]] = {
    "patch": PATCH_REFRESH_PACKAGES,
    "risk-managed": RISK_MANAGED_PACKAGES,
}
REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class DependencyRefreshEntry:
    """One lane entry: a package, its locked version, and the manifests declaring it."""

    name: str
    lane: Lane
    locked_version: str | None
    manifest_files: list[str]


def _dependency_name(requirement: str) -> str | None:
    match = REQUIREMENT_NAME.match(requirement)
    if match is None:
        return None
    return match.group(1).lower().replace("_", "-")


def _iter_requirement_strings(pyproject: dict[str, object]) -> list[str]:
    requirements: list[str] = []
    project = pyproject.get("project")
    if isinstance(project, dict):
        dependencies = project.get("dependencies")
        if isinstance(dependencies, list):
            requirements.extend(item for item in dependencies if isinstance(item, str))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group_requirements in optional.values():
                if isinstance(group_requirements, list):
                    requirements.extend(
                        item for item in group_requirements if isinstance(item, str)
                    )

    dependency_groups = pyproject.get("dependency-groups")
    if isinstance(dependency_groups, dict):
        for group_requirements in dependency_groups.values():
            if isinstance(group_requirements, list):
                requirements.extend(item for item in group_requirements if isinstance(item, str))

    return requirements


def _discover_manifests(repo_root: Path) -> dict[str, list[str]]:
    manifests: dict[str, list[str]] = {}
    for manifest in sorted(repo_root.glob("**/pyproject.toml")):
        if any(part in {".venv", "dist", "docs-site"} for part in manifest.parts):
            continue
        try:
            pyproject = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Invalid TOML in {manifest}: {exc}") from exc
        relative_manifest = manifest.relative_to(repo_root).as_posix()
        for requirement in _iter_requirement_strings(pyproject):
            name = _dependency_name(requirement)
            if name is None:
                continue
            manifest_files = manifests.setdefault(name, [])
            if relative_manifest not in manifest_files:
                manifest_files.append(relative_manifest)
    return manifests


def _locked_versions(repo_root: Path) -> dict[str, str]:
    lockfile = repo_root / "uv.lock"
    if not lockfile.exists():
        return {}
    lock = tomllib.loads(lockfile.read_text(encoding="utf-8"))
    versions: dict[str, str] = {}
    packages = lock.get("package", [])
    if isinstance(packages, list):
        for package in packages:
            if not isinstance(package, dict):
                continue
            name = package.get("name")
            version = package.get("version")
            if isinstance(name, str) and isinstance(version, str):
                versions[name.lower().replace("_", "-")] = version
    return versions


def collect_plan(repo_root: Path) -> dict[Lane, list[DependencyRefreshEntry]]:
    """Build per-lane refresh entries from manifests and uv.lock; skip undiscovered packages."""
    repo_root = repo_root.resolve()
    manifests = _discover_manifests(repo_root)
    locked_versions = _locked_versions(repo_root)
    plan: dict[Lane, list[DependencyRefreshEntry]] = {"patch": [], "risk-managed": []}
    for lane, package_names in LANES.items():
        for package_name in package_names:
            # Packages absent from every pyproject are dropped silently here;
            # --check only fails when a whole lane comes back empty.
            manifest_files = sorted(manifests.get(package_name, []))
            if not manifest_files:
                continue
            plan[lane].append(
                DependencyRefreshEntry(
                    name=package_name,
                    lane=lane,
                    locked_version=locked_versions.get(package_name),
                    manifest_files=manifest_files,
                )
            )
    return plan


def validate_plan(plan: dict[Lane, list[DependencyRefreshEntry]]) -> list[str]:
    """Return human-readable errors for empty lanes or entries missing a locked version."""
    errors: list[str] = []
    if not plan["patch"]:
        errors.append("Patch lane has no discovered dependencies.")
    if not plan["risk-managed"]:
        errors.append("Risk-managed lane has no discovered dependencies.")

    missing_lock_versions = [
        entry.name for entries in plan.values() for entry in entries if entry.locked_version is None
    ]
    if missing_lock_versions:
        errors.append("Dependencies missing from uv.lock: " + ", ".join(missing_lock_versions))
    return errors


def _select_lanes(
    plan: dict[Lane, list[DependencyRefreshEntry]], selected_lane: str
) -> dict[Lane, list[DependencyRefreshEntry]]:
    if selected_lane == "all":
        return plan
    return {selected_lane: plan[selected_lane]}  # type: ignore[index, return-value]


def _print_json(plan: dict[Lane, list[DependencyRefreshEntry]]) -> None:
    print(
        json.dumps(
            {lane: [asdict(entry) for entry in entries] for lane, entries in plan.items()},
            indent=2,
            sort_keys=True,
        )
    )


def _print_markdown(plan: dict[Lane, list[DependencyRefreshEntry]]) -> None:
    print("# Dependency Refresh Plan")
    for lane, entries in plan.items():
        print()
        print(f"## {lane.title()} Lane")
        if not entries:
            print()
            print("No matching dependencies discovered.")
            continue
        print()
        print("| Dependency | Locked | Manifest files |")
        print("|---|---:|---|")
        for entry in entries:
            locked = entry.locked_version or "not locked"
            manifests = "<br>".join(entry.manifest_files)
            print(f"| `{entry.name}` | `{locked}` | {manifests} |")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments selecting lanes and the output format."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to inspect.",
    )
    parser.add_argument(
        "--lane",
        choices=("patch", "risk-managed", "all"),
        default="all",
        help="Refresh lane to print.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the lane configuration no longer matches repo manifests and uv.lock.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Print the refresh plan, exiting non-zero under --check when validation fails."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    full_plan = collect_plan(args.repo_root)
    plan = _select_lanes(full_plan, args.lane)
    if args.format == "json":
        _print_json(plan)
    else:
        _print_markdown(plan)

    if args.check:
        errors = validate_plan(full_plan)
        if errors:
            print("Dependency refresh plan validation failed:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
