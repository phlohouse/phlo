#!/usr/bin/env python3
"""Validate that CI package test groups cover every package test directory."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GROUPS_FILE = REPO_ROOT / ".github" / "ci-package-groups.json"
PACKAGES_DIR = REPO_ROOT / "packages"


def main() -> int:
    """Validate the package groups against packages/; return non-zero on any mismatch."""
    groups = {
        group: packages.split()
        for group, packages in json.loads(GROUPS_FILE.read_text(encoding="utf-8")).items()
    }

    errors: list[str] = []
    if not groups:
        errors.append("No package test groups found in .github/ci-package-groups.json.")

    listed_packages = [package for packages in groups.values() for package in packages]
    counts = Counter(listed_packages)
    duplicate_packages = sorted(package for package, count in counts.items() if count > 1)
    if duplicate_packages:
        errors.append(
            "Packages listed in multiple CI package groups: " + ", ".join(duplicate_packages)
        )

    empty_groups = sorted(group for group, packages in groups.items() if not packages)
    if empty_groups:
        errors.append("Empty CI package groups: " + ", ".join(empty_groups))

    actual_packages = sorted(
        package_dir.parent.name
        for package_dir in PACKAGES_DIR.glob("*/tests")
        if package_dir.is_dir()
    )
    missing_packages = sorted(set(actual_packages) - set(listed_packages))
    extra_packages = sorted(set(listed_packages) - set(actual_packages))

    if missing_packages:
        errors.append(
            "Package test directories missing from CI groups: " + ", ".join(missing_packages)
        )
    if extra_packages:
        errors.append(
            "CI groups reference packages without test directories: " + ", ".join(extra_packages)
        )

    if errors:
        print("CI package group validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        f"CI package groups cover {len(actual_packages)} package test directories "
        f"across {len(groups)} groups."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
