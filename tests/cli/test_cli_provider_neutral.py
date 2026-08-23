"""Architecture and dependency-inversion coverage for provider-neutral CLI commands.

Asserts core never imports provider packages statically and that the CLI
contract helpers accept fake providers.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from phlo.capabilities import (
    NamespaceResolverSpec,
    SchemaDiscoverySpec,
    WorkflowValidationSpec,
    clear_capabilities,
    register_capability,
)
from phlo.cli.commands import schema_migrate, workflow


def test_core_does_not_import_provider_packages() -> None:
    """Core depends on capability contracts, not provider packages."""
    repository_root = Path(__file__).parents[2]
    core_dir = repository_root / "src" / "phlo"
    forbidden = {
        package_dir.name
        for package_dir in (repository_root / "packages").glob("*/src/phlo_*")
        if tomllib.loads((package_dir.parents[1] / "pyproject.toml").read_text())["project"]["name"]
        != "phlo"
    }

    for path in core_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = [
            name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
        ]
        assert not any(name.split(".")[0] in forbidden for name in imports), path


def test_cli_contract_helpers_accept_fake_providers(monkeypatch, tmp_path) -> None:
    """The CLI works with neutral provider contracts rather than native packages."""

    class FakeValidator:
        def validate_workflow_file(self, path: Path) -> None:
            assert path == tmp_path / "workflow.py"

        def validate_schema_file(self, path: Path) -> None:
            assert path == tmp_path / "schema.py"

    class FakeDiscovery:
        def extract(self, native_schema: object) -> object:
            return native_schema

        def discover_schemas(self) -> dict[str, object]:
            return {"OrdersSchema": object()}

    class FakeNamespaceResolver:
        def resolve_namespace(self, table_name: str) -> str:
            return f"staging.{table_name}"

    monkeypatch.setattr(workflow, "discover_capabilities", lambda: None)
    monkeypatch.setattr(schema_migrate, "discover_capabilities", lambda: None)
    clear_capabilities("workflow_validation")
    clear_capabilities("schema_discovery")
    clear_capabilities("namespace_resolver")
    register_capability("workflow_validation", WorkflowValidationSpec("fake", FakeValidator()))
    register_capability("schema_discovery", SchemaDiscoverySpec("fake", FakeDiscovery()))
    register_capability(
        "namespace_resolver", NamespaceResolverSpec("fake", FakeNamespaceResolver())
    )
    try:
        workflow._validate_workflow_file(str(tmp_path / "workflow.py"))
        workflow._validate_schema_file(str(tmp_path / "schema.py"))
        assert schema_migrate._find_native_schema("raw.orders", "OrdersSchema") is not None
        assert (
            schema_migrate._resolve_namespace_resolver().resolve_namespace("orders")
            == "staging.orders"
        )
    finally:
        clear_capabilities("workflow_validation")
        clear_capabilities("schema_discovery")
        clear_capabilities("namespace_resolver")
