"""Retail Files project-template package contract tests.

The blueprint package at `examples/lakehouses/retail-files/` must satisfy the
package contract: exact released phlo-family pins, the four-package third-party
allowlist, no VCS/path/editable dependencies, entry-point discovery, and a
static contract whose resource digest matches the shipped template resources.
Rendered output must be equivalent to the canonical resources — the package is
the sole executable source.
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_DIR = REPO_ROOT / "examples" / "lakehouses" / "retail-files"
PACKAGE_SRC = BLUEPRINT_DIR / "src"
PACKAGE_DIR = PACKAGE_SRC / "phlo_retail_files"
SUPPORT_MANIFEST = REPO_ROOT / "registry" / "support" / "v1.json"

THIRD_PARTY_ALLOWLIST = ("pandas", "pyarrow", "duckdb", "dbt-duckdb")


@pytest.fixture()
def blueprint_contract(phlo_retail_files: ModuleType) -> ModuleType:
    """The blueprint's contract module, imported from its source tree."""
    return importlib.import_module("phlo_retail_files.contract")


@pytest.fixture()
def phlo_retail_files(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the blueprint package from its source tree (not installed)."""
    monkeypatch.syspath_prepend(str(PACKAGE_SRC))
    for name in list(sys.modules):
        if name == "phlo_retail_files" or name.startswith("phlo_retail_files."):
            del sys.modules[name]
    return importlib.import_module("phlo_retail_files.provider")


def _load_pyproject(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _requirement_names(requirements: list[str]) -> list[str]:
    """Distribution names for requirement strings like 'phlo[defaults]==0.14.0'."""
    return [
        requirement.split(";")[0].split("[")[0].split("==")[0].split(">")[0].split("<")[0].strip()
        for requirement in requirements
    ]


def _assert_no_floating_references(requirements: list[str]) -> None:
    for requirement in requirements:
        lowered = requirement.lower()
        for marker in ("@", "git+", "file://", "https://", "http://", ".whl", "-e"):
            assert marker not in lowered, f"floating/VCS/path dependency: {requirement!r}"


def test_package_distribution_contract() -> None:
    project = _load_pyproject(BLUEPRINT_DIR / "pyproject.toml")

    assert project["project"]["name"] == "phlo-retail-files"
    assert project["project"]["version"] == "0.1.0"
    entry_points = project["project"]["entry-points"]["phlo.project_templates"]
    assert entry_points == {"retail_files": "phlo_retail_files.provider:templates"}

    dependencies = project["project"]["dependencies"]
    phlo_family = [dep for dep in dependencies if _requirement_names([dep])[0].startswith("phlo")]
    third_party = [dep for dep in dependencies if dep not in phlo_family]
    assert all("==" in dep for dep in phlo_family), f"phlo pins must be exact: {phlo_family}"
    assert set(_requirement_names(third_party)) <= set(THIRD_PARTY_ALLOWLIST)
    _assert_no_floating_references(dependencies)

    dev_group = project["dependency-groups"]["dev"]
    dev_names = _requirement_names(dev_group)
    dev_phlo_family = {name for name in dev_names if name == "phlo" or name.startswith("phlo-")}
    assert all("==" in dep for dep in dev_group if dep.split("==")[0].startswith("phlo"))
    assert set(dev_names) - dev_phlo_family <= set(THIRD_PARTY_ALLOWLIST)
    _assert_no_floating_references(dev_group)


def test_root_blueprints_extra_and_support_boundaries() -> None:
    root = _load_pyproject(REPO_ROOT / "pyproject.toml")
    extras = root["project"]["optional-dependencies"]

    assert extras["blueprints"] == ["phlo-retail-files==0.1.0"]
    assert "phlo-retail-files" not in _requirement_names(extras["defaults"])
    assert "phlo-retail-files" not in _requirement_names(extras["core-services"])

    manifest = json.loads(SUPPORT_MANIFEST.read_text(encoding="utf-8"))
    release_packages = {entry["name"] for entry in manifest["release_set"]["packages"]}
    assert "phlo-retail-files" not in release_packages


def test_contract_matches_package(blueprint_contract: ModuleType) -> None:
    contract = blueprint_contract.load_contract()
    project = _load_pyproject(BLUEPRINT_DIR / "pyproject.toml")
    dependencies = project["project"]["dependencies"]

    assert contract["distribution"] == project["project"]["name"]
    assert contract["version"] == project["project"]["version"]
    assert contract["template"] == "retail-files"
    assert contract["entry_point_group"] == "phlo.project_templates"
    assert contract["third_party_allowlist"] == list(THIRD_PARTY_ALLOWLIST)

    for pin in contract["phlo_family_pins"]:
        assert pin in dependencies + project["dependency-groups"]["dev"], (
            f"contract pin missing from package deps: {pin}"
        )
    for requirement in contract["third_party_dependencies"]["runtime"]:
        assert requirement in dependencies

    # The contract is the single source of truth for both dependency sets.
    assert contract["rendered_dependencies"]["runtime"] == dependencies
    assert contract["rendered_dependencies"]["dev"] == project["dependency-groups"]["dev"]

    recomputed = blueprint_contract.resource_digest()
    assert contract["resources_digest"] == recomputed, (
        "blueprint_contract.json is stale: regenerate resources_digest after "
        "changing packaged template resources"
    )


def test_render_creates_complete_project(
    tmp_path: Path, phlo_retail_files: ModuleType, blueprint_contract: ModuleType
) -> None:
    contract = blueprint_contract.load_contract()
    template = phlo_retail_files.templates()[0]

    assert template.metadata.name == "retail-files"

    project_dir = tmp_path / "retail-demo"
    template.render(
        phlo_retail_files.TemplateRenderContext(project_dir=project_dir, project_name="retail-demo")
    )

    for relative in (
        "phlo.yaml",
        "pyproject.toml",
        "README.md",
        ".gitignore",
        "workflows/ingestion/retail/files.py",
        "workflows/schemas/retail.py",
        "workflows/quality/retail.py",
        "workflows/schedules/retail.py",
        "workflows/transforms/dbt/dbt_project.yml",
        "workflows/transforms/dbt/models/sales_facts.sql",
        "scripts/generate_fixtures.py",
        "scripts/materialize.py",
        "tests/test_retail_files.py",
        "data/sales_2025-01-15.csv",
        "data/inventory.ndjson",
        "data/products.json",
        "docs/retail-files-e2e.md",
    ):
        assert (project_dir / relative).exists(), f"missing generated file: {relative}"

    rendered = _load_pyproject(project_dir / "pyproject.toml")
    dependencies = rendered["project"]["dependencies"]
    dev_group = rendered["dependency-groups"]["dev"]

    assert rendered["project"]["name"] == "retail-demo"
    for pin in contract["phlo_family_pins"][:3]:  # runtime phlo pins
        assert pin in dependencies
    names = _requirement_names(dependencies + dev_group)
    phlo_family = {name for name in names if name == "phlo" or name.startswith("phlo-")}
    assert set(names) - phlo_family <= set(THIRD_PARTY_ALLOWLIST)
    _assert_no_floating_references(dependencies + dev_group)

    assert "name: retail-demo" in (project_dir / "phlo.yaml").read_text(encoding="utf-8")


def test_rendered_python_files_parse(tmp_path: Path, phlo_retail_files: ModuleType) -> None:
    template = phlo_retail_files.templates()[0]
    project_dir = tmp_path / "retail-demo"
    template.render(
        phlo_retail_files.TemplateRenderContext(project_dir=project_dir, project_name="retail-demo")
    )

    python_files = list(project_dir.rglob("*.py"))
    assert python_files, "rendered project must contain workflow python files"
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_render_is_equivalent_to_canonical_resources(
    tmp_path: Path, phlo_retail_files: ModuleType, blueprint_contract: ModuleType
) -> None:
    """The package is the sole executable source: rendered files equal resources."""
    template = phlo_retail_files.templates()[0]
    project_dir = tmp_path / "retail-demo"
    template.render(
        phlo_retail_files.TemplateRenderContext(project_dir=project_dir, project_name="retail-demo")
    )

    resources = blueprint_contract.RESOURCES_DIR
    resource_files = {
        path.relative_to(resources).as_posix() for path in resources.rglob("*") if path.is_file()
    }
    rendered_files = {
        path.relative_to(project_dir).as_posix()
        for path in project_dir.rglob("*")
        if path.is_file()
    }
    # pyproject.toml is rendered from the contract, not shipped as a resource.
    assert rendered_files == resource_files | {"pyproject.toml"}

    for relative in sorted(resource_files - {"phlo.yaml"}):
        assert (project_dir / relative).read_bytes() == (resources / relative).read_bytes(), (
            f"rendered file diverges from canonical resource: {relative}"
        )

    canonical_phlo_yaml = (resources / "phlo.yaml").read_text(encoding="utf-8")
    rendered_phlo_yaml = (project_dir / "phlo.yaml").read_text(encoding="utf-8")
    assert rendered_phlo_yaml == canonical_phlo_yaml.replace(
        "name: retail-files", "name: retail-demo", 1
    )
