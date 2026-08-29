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


def test_contracts_snapshot_authorizes_before_schema_file_read(monkeypatch, tmp_path) -> None:
    """A denied snapshot must not read its input or open the registry."""
    schema_file = tmp_path / "schema.json"
    schema_file.write_text('{"fields": []}', encoding="utf-8")
    authorization_call: tuple[tuple[object, ...], dict[str, object]] | None = None

    def deny_before_side_effect(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        nonlocal authorization_call
        authorization_call = (args, kwargs)
        raise SystemExit(1)

    monkeypatch.setattr(
        schema_registry_cli,
        "enforce_surface_mutation_authorization",
        deny_before_side_effect,
        raising=False,
    )
    monkeypatch.setattr(
        schema_registry_cli.Path,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("schema must not open")),
    )

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
    assert authorization_call is not None
    assert authorization_call[0][0] == "contracts.snapshot"
    assert authorization_call[1]["resource_id"] == "warehouse.orders"


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
