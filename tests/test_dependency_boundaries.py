"""Packaging boundary checks for the root phlo package.

The root package must not depend on provider runtime stacks (dagster, pandas,
pandera, db drivers, libcst) — those ship as optional runtime/codemods extras
— and must never import them at module import time outside the explicitly
allow-listed paths.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

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


def test_root_modules_do_not_import_optional_runtime_deps_at_module_import_time() -> None:
    forbidden_roots = {"libcst", "pandas", "pandera", "psycopg2"}
    allowed_paths = {
        Path("src/phlo/cli/templates/builtin.py"),
    }
    violations: list[str] = []

    for path in Path("src/phlo").rglob("*.py"):
        if path in allowed_paths:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module.split(".", 1)[0]}
            else:
                continue
            blocked = names & forbidden_roots
            if blocked:
                violations.append(f"{path}:{node.lineno} imports {', '.join(sorted(blocked))}")

    assert violations == []
