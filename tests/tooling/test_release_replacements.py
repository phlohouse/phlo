"""Keep ReleaseX replacements aligned with release consistency checks."""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]


def test_release_refreshes_example_locks_for_unpublished_versions(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "release_example_locks_transformer.py"
    shutil.copyfile(ROOT / script.relative_to(tmp_path), script)
    provider = tmp_path / "provider"
    provider.mkdir()
    metadata = provider / "pyproject.toml"
    metadata.write_text('[project]\nname = "local-release-provider"\nversion = "0.1.0"\n')
    example = tmp_path / "examples" / "sample"
    example.mkdir(parents=True)
    manifest = example / "pyproject.toml"
    manifest.write_text(
        '[project]\nname = "release-example"\nversion = "0.1.0"\n'
        'requires-python = ">=3.12"\ndependencies = ["local-release-provider==0.1.0"]\n'
        '[tool.uv.sources]\nlocal-release-provider = {path = "../../provider", editable = true}\n'
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["uv", "lock", "--directory", str(example)], check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    metadata.write_text(metadata.read_text().replace("0.1.0", "99.1.0"))
    manifest.write_text(manifest.read_text().replace("provider==0.1.0", "provider==99.1.0"))
    assert subprocess.run(["uv", "lock", "--check", "--directory", str(example)]).returncode != 0

    # Match ReleaseX's cleared environment, with only executable discovery supplied.
    env = {"PATH": os.environ["PATH"]}
    command = [sys.executable, "-B", str(script)]
    first = subprocess.run(
        command, input='{"schema_version":1}', text=True, capture_output=True, env=env, check=True
    )
    assert json.loads(first.stdout) == {
        "schema_version": 1,
        "changed_files": ["examples/sample/uv.lock"],
    }
    lock = tomllib.loads((example / "uv.lock").read_text())
    assert next(p for p in lock["package"] if p["name"] == "local-release-provider")["version"] == (
        "99.1.0"
    )
    second = subprocess.run(
        command, input='{"schema_version":1}', text=True, capture_output=True, env=env, check=True
    )
    assert json.loads(second.stdout) == {"schema_version": 1, "changed_files": []}
    subprocess.run(["uv", "lock", "--check", "--directory", str(example)], check=True)
    manifest.write_text(manifest.read_text().replace("../../provider", "../../missing-provider"))
    failed = subprocess.run(
        command, input='{"schema_version":1}', text=True, capture_output=True, env=env
    )
    assert failed.returncode != 0
    assert not failed.stdout


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
