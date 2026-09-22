"""Keep ReleaseX replacements aligned with release consistency checks."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]


def _workspace_versions() -> dict[str, str]:
    versions = {"phlo": tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]}
    for path in (ROOT / "packages").glob("*/pyproject.toml"):
        project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        versions[project["name"]] = project["version"]
    return versions


def _replacements() -> list[dict[str, Any]]:
    config = tomllib.loads((ROOT / "relx.toml").read_text(encoding="utf-8"))
    return config["release"]["replacements"]


def test_release_replaces_every_lakehouse_workspace_pin() -> None:
    versions = _workspace_versions()
    replacements = _replacements()

    for path in sorted((ROOT / "examples/lakehouses").glob("*/pyproject.toml")):
        relative_path = path.relative_to(ROOT).as_posix()
        project = tomllib.loads(path.read_text(encoding="utf-8"))
        requirements = project["project"].get("dependencies", [])
        requirements += project.get("dependency-groups", {}).get("dev", [])

        for value in requirements:
            requirement = Requirement(value)
            if requirement.name not in versions:
                continue
            matching = [
                replacement
                for replacement in replacements
                if relative_path in replacement["files"]
                and requirement.name in replacement["packages"]
            ]
            assert len(matching) == 1, (
                f"{relative_path}: expected one ReleaseX replacement for {requirement.name}"
            )
            replacement = matching[0]
            search = replacement["search"].format(
                name=requirement.name,
                current_version=versions[requirement.name],
            )
            assert path.read_text(encoding="utf-8").count(search) == replacement["expected_matches"]


def test_release_replaces_every_support_manifest_package_version() -> None:
    versions = _workspace_versions()
    replacements = _replacements()
    manifest = json.loads((ROOT / "registry/support/v1.json").read_text(encoding="utf-8"))
    package_names = {package["name"] for package in manifest["release_set"]["packages"]}
    replacement = next(
        item
        for item in replacements
        if item["search"] == '"name": "{name}", "version": "{current_version}"'
    )

    assert package_names <= set(replacement["packages"])
    for file_name in replacement["files"]:
        content = (ROOT / file_name).read_text(encoding="utf-8")
        for name in package_names:
            search = replacement["search"].format(name=name, current_version=versions[name])
            assert content.count(search) == replacement["expected_matches"]
