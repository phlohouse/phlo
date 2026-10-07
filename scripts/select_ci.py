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


def inert_doc(path: str) -> bool:
    """Only known documentation formats can omit executable lanes."""
    return Path(path).suffix.lower() in {".md", ".mdx", ".rst"} and (
        path.startswith("docs/") or "/" not in path
    )


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
    docs = all(inert_doc(path) for path in paths) and bool(paths)
    if docs:
        return {
            "groups": [],
            "python": False,
            "frontend": False,
            "writer": False,
            "integration": False,
            "docs": True,
            "plugin": False,
            "mutation": False,
            "reasons": {"docs": "explicit inert documentation", "code": "no executable changes"},
        }

    package_changes = {
        path.split("/")[1]
        for path in paths
        if path.startswith("packages/") and len(path.split("/")) > 2
    }
    broad = (
        not paths
        or bool(package_changes - package_names)
        or any(path.startswith("packages/") and len(path.split("/")) < 3 for path in paths)
        or any(
            not path.startswith(("packages/", "apps/phlo-github-writer/", ".amp/plugins/"))
            and not inert_doc(path)
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
            "docs": True,
            "plugin": True,
            "mutation": True,
            "reasons": {"all": "empty diff or unrecognised path requires full validation"},
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
    frontend = any(path.startswith("packages/phlo-observatory/") for path in paths)
    writer = any(path.startswith("apps/phlo-github-writer/") for path in paths)
    return {
        "groups": selected,
        "python": bool(package_changes),
        "frontend": frontend,
        "writer": writer,
        "integration": bool(package_changes),
        "docs": bool(package_changes) or any(inert_doc(path) for path in paths),
        "plugin": any(path.startswith(".amp/plugins/") for path in paths),
        "mutation": "phlo-api" in affected,
        "reasons": {
            "packages": "changed packages and reverse dependencies: " + ", ".join(sorted(affected)),
            "frontend": "all Observatory package inputs" if frontend else "no Observatory changes",
            "docs": "package API or documentation changes",
            "plugin": "plugin source changes",
        },
    }


def git_paths(
    base: str, head: str = "HEAD", *, working_tree: bool = False, root: Path = ROOT
) -> set[str]:
    """Read committed changes, optionally including all local edits and untracked files."""
    commands = [["git", "diff", "--name-only", "--no-renames", "-z", base, head]]
    if working_tree:
        commands.extend(
            [
                ["git", "diff", "--name-only", "--no-renames", "-z", "HEAD"],
                ["git", "diff", "--name-only", "--no-renames", "-z", "--cached"],
                ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            ]
        )
    return {
        path
        for command in commands
        for path in subprocess.check_output(command, cwd=root).decode().split("\0")
        if path
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--working-tree", action="store_true")
    args = parser.parse_args()
    paths = git_paths(args.base, args.head, working_tree=args.working_tree)
    print(json.dumps(select(paths), separators=(",", ":")))


if __name__ == "__main__":
    main()
