"""Packaging boundary checks for the root phlo package.

The root package must not depend on provider runtime stacks (dagster, pandas,
pandera, db drivers, libcst). Those ship as optional runtime/codemods extras.
Core cannot import provider packages, even from nested functions; optional
runtime imports at module scope have one explicitly allowed path.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement


def _dependency_name(spec: str) -> str:
    return Requirement(spec).name


def test_root_dependencies_do_not_pull_provider_runtime_stacks() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {_dependency_name(spec) for spec in pyproject["project"].get("dependencies", [])}

    assert (
        not {
            "dagster",
            "dagster-webserver",
            "fastapi",
            "pandas",
            "pandera",
            "psycopg2-binary",
            "asyncpg",
            "duckdb",
            "libcst",
        }
        & dependencies
    )


def test_provider_runtime_stacks_are_available_as_extras() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    optional = pyproject["project"].get("optional-dependencies", {})

    assert "runtime" in optional
    assert "codemods" in optional
    runtime = {_dependency_name(spec) for spec in optional["runtime"]}
    codemods = {_dependency_name(spec) for spec in optional["codemods"]}

    assert {
        "asyncpg",
        "dagster",
        "dagster-webserver",
        "duckdb",
        "fastapi",
        "pandas",
        "pandera",
        "psycopg2-binary",
    } <= runtime
    assert {"libcst"} <= codemods


def test_root_modules_do_not_import_providers_or_eager_optional_runtime_deps() -> None:
    forbidden_roots = {"libcst", "pandas", "pandera", "psycopg2"}
    provider_roots = {
        module.name
        for src in Path("packages").glob("*/src")
        for module in src.glob("phlo_*")
        if module.is_dir() and (module / "__init__.py").exists()
    }
    allowed_paths = {
        Path("src/phlo/cli/templates/builtin.py"),
    }
    violations: list[str] = []

    class ImportVisitor(ast.NodeVisitor):
        def __init__(self, path: Path, tree: ast.Module) -> None:
            self.path = path
            self.tree = tree

        def visit_If(self, node: ast.If) -> None:
            if (
                isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
                or isinstance(node.test, ast.Attribute)
                and isinstance(node.test.value, ast.Name)
                and node.test.value.id == "typing"
                and node.test.attr == "TYPE_CHECKING"
            ):
                for statement in node.orelse:
                    self.visit(statement)
            else:
                self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            self.check(node, {alias.name.split(".", 1)[0] for alias in node.names})

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module:
                self.check(node, {node.module.split(".", 1)[0]})

        def check(self, node: ast.AST, names: set[str]) -> None:
            blocked = names & provider_roots
            if node in self.tree.body and self.path not in allowed_paths:
                blocked |= names & forbidden_roots
            if blocked:
                violations.append(f"{self.path}:{node.lineno} imports {', '.join(sorted(blocked))}")

    for path in Path("src/phlo").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        ImportVisitor(path, tree).visit(tree)

    assert violations == []


def test_nested_provider_import_fails_but_type_only_import_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = tmp_path / "packages/phlo-trino/src/phlo_trino"
    provider.mkdir(parents=True)
    (provider / "__init__.py").touch()
    core = tmp_path / "src/phlo"
    core.mkdir(parents=True)
    source = core / "nested.py"
    source.write_text("def run():\n    import phlo_trino\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(AssertionError, match="phlo_trino"):
        test_root_modules_do_not_import_providers_or_eager_optional_runtime_deps()

    source.write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import phlo_trino\n",
        encoding="utf-8",
    )
    test_root_modules_do_not_import_providers_or_eager_optional_runtime_deps()
