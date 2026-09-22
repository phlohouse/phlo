"""Check that the machine-readable example catalogue covers every lakehouse."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
LAKEHOUSES = ROOT / "examples/lakehouses"


def test_example_catalog_matches_example_directories() -> None:
    catalog = json.loads((LAKEHOUSES / "catalog.json").read_text(encoding="utf-8"))
    entries = catalog["examples"]
    names = [entry["name"] for entry in entries]
    directories = sorted(path.name for path in LAKEHOUSES.iterdir() if path.is_dir())

    assert catalog["schema_version"] == 1
    assert names == sorted(names)
    assert names == directories
    assert len(names) == len(set(names))

    for entry in entries:
        assert entry["title"].strip()
        assert entry["focus"]
        assert (LAKEHOUSES / entry["name"] / "README.md").is_file()


def test_lakehouse_phlo_dependencies_match_workspace_releases() -> None:
    versions = {"phlo": tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]}
    for pyproject_path in (ROOT / "packages").glob("*/pyproject.toml"):
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
        versions[project["name"]] = project["version"]

    for pyproject_path in sorted(LAKEHOUSES.glob("*/pyproject.toml")):
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        requirements = project["project"].get("dependencies", [])
        requirements += project.get("dependency-groups", {}).get("dev", [])

        for value in requirements:
            requirement = Requirement(value)
            if requirement.name not in versions:
                continue
            assert requirement.url is None, f"{pyproject_path}: VCS dependency {value!r}"
            assert str(requirement.specifier) == f"=={versions[requirement.name]}", (
                f"{pyproject_path}: stale Phlo dependency {value!r}"
            )
