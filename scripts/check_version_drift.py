#!/usr/bin/env python3
"""Fail on version drift across package metadata, registries, and docs.

Each distribution's ``pyproject.toml`` is the single version authority; its
Python sources may not carry a hand-maintained
``__version__`` literal, the plugin registries may not carry a hand-maintained
per-plugin ``version`` column, and the support manifest's release set must
agree with the package metadata it pins. Workspace dependency ranges must
also accept the versions built from this checkout.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

try:
    from packaging.requirements import Requirement
    from packaging.version import Version
except ModuleNotFoundError:  # Bare python3 may not have the project environment installed.
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.version import Version

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


def workspace_requirement_errors(distributions: dict[str, str]) -> list[str]:
    """Workspace requirements must accept the version built from this checkout."""
    errors: list[str] = []
    for pyproject in [
        ROOT / "pyproject.toml",
        *sorted((ROOT / "packages").glob("*/pyproject.toml")),
    ]:
        with pyproject.open("rb") as handle:
            metadata = tomllib.load(handle)
        project = metadata["project"]
        groups = {
            "dependencies": project.get("dependencies", []),
            **project.get("optional-dependencies", {}),
            **metadata.get("dependency-groups", {}),
        }
        for group, specs in groups.items():
            for spec in specs:
                requirement = Requirement(spec)
                version = distributions.get(requirement.name)
                if version is not None and Version(version) not in requirement.specifier:
                    errors.append(
                        f"{pyproject.relative_to(ROOT)} [{group}]: {spec!r} excludes "
                        f"workspace {requirement.name}=={version}"
                    )
    return errors


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
        workspace_requirement_errors(distributions)
        + version_literal_errors(distributions)
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
