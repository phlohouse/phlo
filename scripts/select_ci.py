#!/usr/bin/env python3
"""Select PR test groups from changed paths and workspace dependencies."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_PATTERN = re.compile(r"^(phlo(?:-[a-z0-9-]+)?)\b")


def select(paths: set[str], root: Path = ROOT) -> dict[str, object]:
    """Fail open for unrecognised paths; expand package changes to their dependents."""
    groups = {
        group: packages.split()
        for group, packages in json.loads(
            (root / ".github/ci-package-groups.json").read_text(encoding="utf-8")
        ).items()
    }
    if not groups:
        raise ValueError("No package test groups found")
    package_names = {package for packages in groups.values() for package in packages}
    docs = all(path.startswith("docs/") or path.endswith(".md") for path in paths) and bool(paths)
    if docs:
        return {
            "groups": [],
            "python": False,
            "frontend": False,
            "writer": False,
            "integration": False,
        }

    package_changes = {
        path.split("/")[1]
        for path in paths
        if path.startswith("packages/") and len(path.split("/")) > 2
    }
    broad = (
        not paths
        or bool(package_changes - package_names)
        or any(
            not path.startswith(("packages/", "docs/", "apps/phlo-github-writer/"))
            and not path.endswith(".md")
            for path in paths
        )
    )
    if broad:
        return {
            "groups": [
                {"group": group, "packages": " ".join(packages), "python-version": "3.12"}
                for group, packages in groups.items()
            ],
            "python": True,
            "frontend": True,
            "writer": True,
            "integration": True,
        }

    dependents: dict[str, set[str]] = {package: set() for package in package_names}
    for package in package_names:
        metadata = tomllib.loads((root / "packages" / package / "pyproject.toml").read_text())
        project = metadata["project"]
        requirements = project.get("dependencies", []) + [
            requirement
            for extra in project.get("optional-dependencies", {}).values()
            for requirement in extra
        ]
        for requirement in requirements:
            match = DEPENDENCY_PATTERN.match(requirement)
            if match and match[1] in dependents:
                dependents[match[1]].add(package)
    affected = set(package_changes)
    pending = list(affected)
    while pending:
        for dependent in dependents[pending.pop()] - affected:
            affected.add(dependent)
            pending.append(dependent)
    selected = [
        {"group": group, "packages": " ".join(packages), "python-version": "3.12"}
        for group, packages in groups.items()
        if affected.intersection(packages)
    ]
    frontend = any(
        path.startswith("packages/phlo-observatory/src/phlo_observatory/") for path in paths
    )
    writer = any(path.startswith("apps/phlo-github-writer/") for path in paths)
    return {
        "groups": selected,
        "python": bool(package_changes),
        "frontend": frontend,
        "writer": writer,
        "integration": bool(package_changes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    paths = set(
        subprocess.check_output(
            ["git", "diff", "--name-only", "-z", args.base, args.head], cwd=ROOT
        )
        .decode()
        .strip("\0")
        .split("\0")
    )
    print(json.dumps(select(paths), separators=(",", ":")))


if __name__ == "__main__":
    main()
