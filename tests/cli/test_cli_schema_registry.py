"""Tests for the schema registry CLI: invalid input surfaces as a clean user error.

Malformed schema files and registry backend failures both exit nonzero with a
readable message and never leak an unhandled traceback.
"""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from phlo.cli.commands import schema_registry_cli


def test_contracts_snapshot_rejects_invalid_schema_without_traceback(monkeypatch, tmp_path) -> None:
    schema_file = tmp_path / "schema.json"
    schema_file.write_text("{bad json", encoding="utf-8")
    monkeypatch.setenv("PHLO_REGISTRY_DB_URL", "postgresql://example")

    result = CliRunner().invoke(
        schema_registry_cli.contracts,
        [
            "snapshot",
            "--table",
            "warehouse.orders",
            "--schema-file",
            str(schema_file),
        ],
    )

    assert result.exit_code == 1
    assert "Failed to read schema file" in result.output
    assert "Traceback" not in result.output


def test_contracts_check_wraps_registry_failures(monkeypatch) -> None:
    class BrokenRegistry:
        def __init__(self, db_url: str) -> None:
            self.db_url = db_url

        def get_latest_snapshots(self, table: str, limit: int = 2):  # noqa: ANN201
            raise RuntimeError("database unavailable")

    monkeypatch.setenv("PHLO_REGISTRY_DB_URL", "postgresql://example")
    monkeypatch.setattr(schema_registry_cli, "SchemaRegistry", BrokenRegistry)

    result = CliRunner().invoke(
        schema_registry_cli.contracts,
        ["check", "--table", "warehouse.orders"],
    )

    assert result.exit_code == 1
    assert "Failed to check schema compatibility: database unavailable" in result.output
    assert "Traceback" not in result.output


def test_contracts_check_wraps_schema_parse_failures(monkeypatch) -> None:
    class RegistryWithInvalidSnapshot:
        def __init__(self, db_url: str) -> None:
            self.db_url = db_url

        def get_latest_snapshots(self, table: str, limit: int = 2):  # noqa: ANN201
            return [
                SimpleNamespace(schema_json="{bad json"),
                SimpleNamespace(schema_json="{bad json"),
            ]

    monkeypatch.setenv("PHLO_REGISTRY_DB_URL", "postgresql://example")
    monkeypatch.setattr(schema_registry_cli, "SchemaRegistry", RegistryWithInvalidSnapshot)

    result = CliRunner().invoke(
        schema_registry_cli.contracts,
        ["check", "--table", "warehouse.orders"],
    )

    assert result.exit_code == 1
    assert "Failed to check schema compatibility:" in result.output
    assert "Traceback" not in result.output
