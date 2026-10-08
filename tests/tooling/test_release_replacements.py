"""Keep ReleaseX replacements aligned with release consistency checks."""

from __future__ import annotations

import fnmatch
import json
import subprocess
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


def test_release_rewrites_every_bounded_workspace_requirement() -> None:
    versions = _workspace_versions()
    config = tomllib.loads((ROOT / "relx.toml").read_text(encoding="utf-8"))
    rules = config["workspace"]["dependencies"]["rules"]

    for path in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        groups = {
            "dependencies": project.get("dependencies", []),
            **project.get("optional-dependencies", {}),
        }
        dependent = path.parent.relative_to(ROOT).as_posix()
        for group, specs in groups.items():
            for spec in specs:
                requirement = Requirement(spec)
                if requirement.name not in versions or not any(
                    item.operator in {"<", "<=", "==", "~=", "==="}
                    for item in requirement.specifier
                ):
                    continue
                matching = [
                    rule
                    for rule in rules
                    if rule["dependency"] == requirement.name
                    and any(fnmatch.fnmatchcase(dependent, glob) for glob in rule["dependents"])
                ]
                assert len(matching) == 1, f"{dependent} [{group}]: no unique rule for {spec}"
                assert matching[0]["when"] == "dependency_selected"
                assert matching[0]["range"] == ">={version},<{next_minor}"


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


def test_release_replaces_every_minio_image_pin_using_the_core_version() -> None:
    versions = _workspace_versions()
    # The Python plugin can advance independently of the promoted container.
    versions["phlo-minio"] = "7.6.0"
    repository = "ghcr.io/phlohouse/phlo-minio"
    current_pin = f"{repository}:{versions['phlo']}"
    next_pin = f"{repository}:99.1.0"
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    pinned_files = {
        name
        for name in tracked
        if Path(name).suffix in {".json", ".md", ".py", ".toml", ".yaml", ".yml"}
        and current_pin in (ROOT / name).read_text(encoding="utf-8")
    }
    rewritten_files: set[str] = set()
    for replacement in _replacements():
        if repository not in replacement["search"]:
            continue
        assert replacement["packages"] == ["phlo"]
        search = replacement["search"].format(current_version=versions[replacement["packages"][0]])
        replace = replacement["replace"].format(next_version="99.1.0")
        for name in replacement["files"]:
            assert name not in rewritten_files
            source = (ROOT / name).read_text(encoding="utf-8")
            assert source.count(search) == replacement["expected_matches"], name
            result = source.replace(search, replace)
            assert current_pin not in result, name
            assert result.count(next_pin) == replacement["expected_matches"], name
            rewritten_files.add(name)
    assert pinned_files
    assert rewritten_files == pinned_files
