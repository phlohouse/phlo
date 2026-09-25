"""Check imports between workspace distributions against metadata and approved edges."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages"

# Existing integration edges. New provider-to-provider imports need explicit review.
ALLOWED_PROVIDER_EDGES = {
    ("phlo-api", "phlo-dagster"),
    ("phlo-core-plugins", "phlo-pandera"),
    ("phlo-dbt", "phlo-pandera"),
    ("phlo-observe-plugin", "phlo-dagster"),
    ("phlo-openmetadata", "phlo-pandera"),
    ("phlo-pandera", "phlo-trino"),
    ("phlo-polaris", "phlo-postgres"),
    ("phlo-testing", "phlo-dbt"),
    ("phlo-testing", "phlo-iceberg"),
    ("phlo-testing", "phlo-nessie"),
    ("phlo-testing", "phlo-trino"),
}


def _module_to_distribution_map(packages_dir: Path) -> dict[str, str]:
    mapping = {"phlo": "phlo"}
    for pyproject in packages_dir.glob("*/pyproject.toml"):
        with pyproject.open("rb") as handle:
            distribution = tomllib.load(handle)["project"]["name"]
        for module_dir in (pyproject.parent / "src").glob("phlo_*"):
            if module_dir.is_dir() and (module_dir / "__init__.py").exists():
                mapping[module_dir.name] = distribution
    return mapping


def _declared_internal_dependencies(pyproject: Path) -> set[str]:
    with pyproject.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    groups = [project.get("dependencies", []), *project.get("optional-dependencies", {}).values()]
    return {Requirement(spec).name for group in groups for spec in group}


def _imported_internal_dependencies(
    src_dir: Path, module_to_distribution: dict[str, str]
) -> set[str]:
    imported: set[str] = set()
    for pyfile in src_dir.rglob("*.py"):
        tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = (node.module,)
            else:
                continue
            for module in modules:
                distribution = module_to_distribution.get(module.split(".", 1)[0])
                if distribution:
                    imported.add(distribution)
    return imported


def _graph_errors(packages_dir: Path, mapping: dict[str, str]) -> list[str]:
    edges: dict[str, set[str]] = {}
    errors: list[str] = []
    for pyproject in sorted(packages_dir.glob("*/pyproject.toml")):
        with pyproject.open("rb") as handle:
            package = tomllib.load(handle)["project"]["name"]
        declared = _declared_internal_dependencies(pyproject)
        imported = _imported_internal_dependencies(pyproject.parent / "src", mapping) - {package}
        edges[package] = imported - {"phlo"}
        for dependency in sorted(imported):
            if dependency not in declared:
                errors.append(
                    f"{package} imports {dependency} without a declared requirement or extra"
                )
            if dependency != "phlo" and (package, dependency) not in ALLOWED_PROVIDER_EDGES:
                errors.append(f"{package} imports {dependency} outside approved provider edges")

    visited: set[str] = set()
    active: set[str] = set()

    def visit(package: str, path: list[str]) -> None:
        if package in active:
            errors.append("package import cycle: " + " -> ".join([*path, package]))
            return
        if package in visited:
            return
        active.add(package)
        for dependency in sorted(edges.get(package, ())):
            visit(dependency, [*path, package])
        active.remove(package)
        visited.add(package)

    for package in sorted(edges):
        visit(package, [])
    return errors


def test_runtime_internal_imports_are_declared_and_approved_without_cycles() -> None:
    assert _graph_errors(PACKAGES_DIR, _module_to_distribution_map(PACKAGES_DIR)) == []


def test_graph_rejects_nested_undeclared_imports_and_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packages = tmp_path / "packages"
    for name in ("alpha", "beta"):
        module = packages / f"phlo-{name}" / "src" / f"phlo_{name}"
        module.mkdir(parents=True)
        (module / "__init__.py").touch()
        (module.parent.parent / "pyproject.toml").write_text(
            f"[project]\nname = 'phlo-{name}'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
    alpha = packages / "phlo-alpha/src/phlo_alpha/__init__.py"
    alpha.write_text("def use_beta():\n    from phlo_beta import plugin\n", encoding="utf-8")
    mapping = _module_to_distribution_map(packages)

    assert "phlo-alpha imports phlo-beta without a declared requirement or extra" in _graph_errors(
        packages, mapping
    )
    assert "phlo-alpha imports phlo-beta outside approved provider edges" in _graph_errors(
        packages, mapping
    )

    for name, dependency in (("alpha", "beta"), ("beta", "alpha")):
        project = packages / f"phlo-{name}" / "pyproject.toml"
        project.write_text(
            project.read_text(encoding="utf-8") + f"dependencies = ['phlo-{dependency}>=0.1']\n",
            encoding="utf-8",
        )
    (packages / "phlo-beta/src/phlo_beta/__init__.py").write_text(
        "import phlo_alpha\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        f"{__name__}.ALLOWED_PROVIDER_EDGES",
        {("phlo-alpha", "phlo-beta"), ("phlo-beta", "phlo-alpha")},
    )
    assert any(
        error.startswith("package import cycle:") for error in _graph_errors(packages, mapping)
    )
