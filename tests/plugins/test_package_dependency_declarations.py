"""Repo-level consistency tests: every phlo_* module imported by a
package must be declared in that package's pyproject.toml."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages"


def _module_to_distribution_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pyproject in PACKAGES_DIR.glob("*/pyproject.toml"):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        distribution = data["project"]["name"]
        src_dir = pyproject.parent / "src"
        for module_dir in src_dir.glob("phlo_*"):
            if module_dir.is_dir() and (module_dir / "__init__.py").exists():
                mapping[module_dir.name] = distribution
    return mapping


def _declared_internal_dependencies(pyproject: Path) -> set[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = list(data["project"].get("dependencies", []))
    optional_deps = data["project"].get("optional-dependencies", {})
    for group_deps in optional_deps.values():
        deps.extend(group_deps)
    declared: set[str] = set()
    for dep in deps:
        match = re.match(r"(phlo(?:-[a-z0-9-]+)?)", dep)
        if match:
            declared.add(match.group(1))
    return declared


def _imported_internal_dependencies(
    src_dir: Path, module_to_distribution: dict[str, str]
) -> set[str]:
    imported: set[str] = set()
    for pyfile in src_dir.rglob("*.py"):
        text = pyfile.read_text(encoding="utf-8")
        for match in re.finditer(r"^(?:from|import)\s+(phlo_[a-z0-9_]+)", text, re.MULTILINE):
            module_name = match.group(1)
            distribution = module_to_distribution.get(module_name)
            if distribution:
                imported.add(distribution)
    return imported


def test_runtime_internal_imports_are_declared_in_dependencies() -> None:
    module_to_distribution = _module_to_distribution_map()
    missing_by_package: dict[str, set[str]] = {}

    for pyproject in PACKAGES_DIR.glob("*/pyproject.toml"):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        package_name = data["project"]["name"]
        src_dir = pyproject.parent / "src"
        declared = _declared_internal_dependencies(pyproject)
        imported = _imported_internal_dependencies(src_dir, module_to_distribution)

        missing = {dep for dep in imported if dep != package_name and dep not in declared}
        if missing:
            missing_by_package[package_name] = missing

    assert not missing_by_package, "Runtime internal imports missing from dependencies: " + str(
        missing_by_package
    )
