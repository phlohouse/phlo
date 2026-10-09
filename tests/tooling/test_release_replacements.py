"""Keep ReleaseX replacements aligned with release consistency checks."""

from __future__ import annotations

import fnmatch
import json
import tomllib
from pathlib import Path
from typing import Any

import yaml
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


def test_phlo_releases_preserve_independent_minio_image_pins() -> None:
    versions = _workspace_versions()
    files = [
        "registry/support/v1.json",
        "src/phlo/support_data/v1.json",
        "packages/phlo-minio/src/phlo_minio/service.yaml",
        "packages/phlo-minio/src/phlo_minio/minio-setup.yaml",
    ]
    for file_name in files:
        source = (ROOT / file_name).read_text(encoding="utf-8")
        result = source
        for replacement in _replacements():
            if file_name not in replacement["files"]:
                continue
            for name in replacement["packages"]:
                search = replacement["search"].format(name=name, current_version=versions[name])
                replace = replacement["replace"].format(name=name, next_version="99.1.0")
                result = result.replace(search, replace)
        if file_name.endswith(".json"):
            original = json.loads(source)
            rewritten = json.loads(result)
            pins = {
                service["name"]: service["image_reference"]
                for service in original["release_set"]["services"]
                if service["name"] in {"minio", "minio-setup"}
            }
            assert set(pins) == {"minio", "minio-setup"}
            assert {
                service["name"]: service["image_reference"]
                for service in rewritten["release_set"]["services"]
                if service["name"] in pins
            } == pins, file_name
        else:
            original = yaml.safe_load(source)
            rewritten = yaml.safe_load(result)
            assert rewritten["image"] == original["image"], file_name
