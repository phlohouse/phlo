#!/usr/bin/env python3
"""Fail on version drift across package metadata, registries, and docs.

Each distribution's ``pyproject.toml`` is the single version authority; its
Python sources may not carry a hand-maintained
``__version__`` literal, the plugin registries may not carry a hand-maintained
per-plugin ``version`` column, and the support manifest's release set must
agree with the package metadata it pins.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATHS = ("registry/plugins.json", "src/phlo/plugins/registry_data.json")
SUPPORT_MANIFEST_PATH = ROOT / "registry" / "support" / "v1.json"
DYNAMIC_VERSION_RE = re.compile(r'^__version__ = version\("[^"]+"\)$', re.M)


def workspace_distributions() -> dict[str, str]:
    """Return {distribution name: declared version} for the workspace."""
    distributions: dict[str, str] = {}
    for pyproject in [
        ROOT / "pyproject.toml",
        *sorted((ROOT / "packages").glob("*/pyproject.toml")),
    ]:
        with pyproject.open("rb") as handle:
            project = tomllib.load(handle)["project"]
        distributions[project["name"]] = project["version"]
    return distributions


def version_literal_errors(distributions: dict[str, str]) -> list[str]:
    """Every ``__version__`` assignment must be dynamic resolution."""
    errors: list[str] = []
    source_roots = [ROOT / "src", *(ROOT / "packages").glob("*/src")]
    for root in source_roots:
        for path in sorted(root.rglob("__init__.py")):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if not line.startswith("__version__"):
                    continue
                statement = line.strip()
                if DYNAMIC_VERSION_RE.match(statement + "\n"):
                    continue
                errors.append(
                    f"{path.relative_to(ROOT)}: hand-maintained {statement!r}; "
                    "use __version__ = version(<distribution name>)"
                )
    return errors


def registry_version_column_errors() -> list[str]:
    """Plugin registry entries may not carry a hand-maintained version column."""
    errors: list[str] = []
    for relative in REGISTRY_PATHS:
        path = ROOT / relative
        registry = json.loads(path.read_text(encoding="utf-8"))
        for name, entry in registry.get("plugins", {}).items():
            if "version" in entry:
                errors.append(
                    f"{relative}: plugin {name!r} carries a hand-maintained "
                    "'version' column; derive it from package metadata instead"
                )
    return errors


def support_manifest_errors(distributions: dict[str, str]) -> list[str]:
    """The support manifest release set must match the metadata it pins."""
    errors: list[str] = []
    manifest = json.loads(SUPPORT_MANIFEST_PATH.read_text(encoding="utf-8"))
    release_set = manifest.get("release_set", {})
    for pinned in release_set.get("packages", []):
        name = pinned.get("name", "")
        declared = distributions.get(name)
        if declared is not None and pinned.get("version") != declared:
            errors.append(
                f"{SUPPORT_MANIFEST_PATH.relative_to(ROOT)}: release_set package "
                f"{name!r} pins {pinned.get('version')!r} but package metadata declares {declared!r}"
            )
    current = manifest.get("current_release", {}).get("version")
    root_version = distributions.get("phlo")
    if current and root_version and current != root_version:
        errors.append(
            f"{SUPPORT_MANIFEST_PATH.relative_to(ROOT)}: current_release "
            f"{current!r} disagrees with phlo package metadata {root_version!r}"
        )
    return errors


def main() -> int:
    distributions = workspace_distributions()
    errors = (
        version_literal_errors(distributions)
        + registry_version_column_errors()
        + support_manifest_errors(distributions)
    )
    for error in errors:
        print(f"version drift: {error}")
    if not errors:
        print(
            f"version drift: none across {len(distributions)} distributions, "
            f"{len(REGISTRY_PATHS)} registry copies, and the support manifest"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
