"""Regression tests for schema-migrate CLI commands.

Pins migrator resolution (explicit schema_migrator config, table_store
derivation, deterministic failure on ambiguity), history/export/scaffold/
plan/apply behavior, scaffold determinism, and clean error surfaces without
tracebacks.
"""

import json
import os
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner
from pyiceberg.exceptions import NoSuchTableError

from phlo.capabilities.specs import (
    FieldSpec,
    NormalizedSchema,
    SchemaChange,
    SchemaMigrationPlan,
    SchemaMigrationSpec,
)
from phlo.cli.commands import schema_migrate as schema_migrate_commands
from phlo.cli.main import cli
from phlo.schema_migration.planning import SchemaMigrationInstructions


def test_schema_migrate_history_renders_timestamp_ms(monkeypatch) -> None:
    """Renders snapshot timestamp when migrator returns timestamp_ms keys."""

    class FakeMigrator:
        def get_schema_history(
            self, *, table_name: str, limit: int = 10
        ) -> list[dict[str, object]]:
            assert table_name == "warehouse.customers"
            assert limit == 5
            return [
                {
                    "snapshot_id": 123,
                    "timestamp_ms": 1709251200000,
                    "summary": {"operation": "append"},
                }
            ]

    monkeypatch.setattr(schema_migrate_commands, "_resolve_migrator", lambda: FakeMigrator())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["schema-migrate", "history", "warehouse.customers", "--limit", "5"],
    )

    assert result.exit_code == 0
    assert "1709251200000" in result.output
    assert "123" in result.output


def test_schema_migrate_history_backend_failure_is_clean_error(monkeypatch) -> None:
    class FakeMigrator:
        def get_schema_history(self, *, table_name: str, limit: int = 10) -> list[dict]:
            raise RuntimeError("backend unavailable")

    monkeypatch.setattr(schema_migrate_commands, "_resolve_migrator", lambda: FakeMigrator())

    result = CliRunner().invoke(cli, ["schema-migrate", "history", "warehouse.customers"])

    assert result.exit_code == 1
    assert (
        "Could not load schema history for warehouse.customers: backend unavailable"
        in result.output
    )
    assert "Traceback" not in result.output


def test_resolve_migrator_prefers_matching_table_store_default(monkeypatch) -> None:
    """Schema migrator follows the configured table_store when names align."""

    iceberg_migrator = object()
    delta_migrator = object()
    migrators = [
        SchemaMigrationSpec(name="delta", provider=delta_migrator),
        SchemaMigrationSpec(name="iceberg", provider=iceberg_migrator),
    ]

    class FakeRegistry:
        def list(self, family: str) -> list[SchemaMigrationSpec]:
            assert family == "schema_migrator"
            return migrators

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        schema_migrate_commands,
        "configured_capability_name",
        lambda capability_type: None if capability_type == "schema_migrator" else "iceberg",
    )
    monkeypatch.setattr("phlo.capabilities.discovery.discover_capabilities", lambda: None)

    resolved = schema_migrate_commands._resolve_migrator()

    assert resolved is iceberg_migrator


def test_resolve_migrator_prefers_explicit_schema_migrator_default(monkeypatch) -> None:
    """Explicit schema_migrator config wins over table_store-derived selection."""

    iceberg_migrator = object()
    delta_migrator = object()
    migrators = [
        SchemaMigrationSpec(name="delta", provider=delta_migrator),
        SchemaMigrationSpec(name="iceberg", provider=iceberg_migrator),
    ]

    class FakeRegistry:
        def list(self, family: str) -> list[SchemaMigrationSpec]:
            assert family == "schema_migrator"
            return migrators

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        schema_migrate_commands,
        "configured_capability_name",
        lambda capability_type: "delta" if capability_type == "schema_migrator" else "iceberg",
    )
    monkeypatch.setattr("phlo.capabilities.discovery.discover_capabilities", lambda: None)

    resolved = schema_migrate_commands._resolve_migrator()

    assert resolved is delta_migrator


def test_resolve_migrator_fails_when_configured_schema_migrator_missing(monkeypatch) -> None:
    """Missing explicit schema_migrator config errors deterministically."""

    class FakeRegistry:
        def list(self, family: str) -> list[SchemaMigrationSpec]:
            assert family == "schema_migrator"
            return [SchemaMigrationSpec(name="iceberg", provider=object())]

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        schema_migrate_commands,
        "configured_capability_name",
        lambda capability_type: "delta" if capability_type == "schema_migrator" else None,
    )
    monkeypatch.setattr(
        schema_migrate_commands,
        "list_capabilities",
        lambda capability_type: ["iceberg"] if capability_type == "schema_migrator" else [],
    )
    monkeypatch.setattr("phlo.capabilities.discovery.discover_capabilities", lambda: None)

    with pytest.raises(click.ClickException) as exc_info:
        schema_migrate_commands._resolve_migrator()

    assert exc_info.value.exit_code == 1


def test_resolve_migrator_fails_when_multiple_installed_without_selection(monkeypatch) -> None:
    """Ambiguous schema migrators require config instead of first-provider fallback."""

    class FakeRegistry:
        def list(self, family: str) -> list[SchemaMigrationSpec]:
            assert family == "schema_migrator"
            return [
                SchemaMigrationSpec(name="delta", provider=object()),
                SchemaMigrationSpec(name="iceberg", provider=object()),
            ]

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        schema_migrate_commands,
        "configured_capability_name",
        lambda capability_type: None,
    )
    monkeypatch.setattr(
        schema_migrate_commands,
        "list_capabilities",
        lambda capability_type: (
            ["delta", "iceberg"] if capability_type == "schema_migrator" else []
        ),
    )
    monkeypatch.setattr("phlo.capabilities.discovery.discover_capabilities", lambda: None)

    with pytest.raises(click.ClickException) as exc_info:
        schema_migrate_commands._resolve_migrator()

    assert exc_info.value.exit_code == 1


def test_resolve_migrator_reports_table_store_mismatch_when_ambiguous(monkeypatch) -> None:
    """Ambiguous error explains when configured table_store did not match any migrator."""

    class FakeRegistry:
        def list(self, family: str) -> list[SchemaMigrationSpec]:
            assert family == "schema_migrator"
            return [
                SchemaMigrationSpec(name="delta", provider=object()),
                SchemaMigrationSpec(name="iceberg", provider=object()),
            ]

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        schema_migrate_commands,
        "configured_capability_name",
        lambda capability_type: None if capability_type == "schema_migrator" else "delta_lake",
    )
    monkeypatch.setattr(
        schema_migrate_commands,
        "list_capabilities",
        lambda capability_type: (
            ["delta", "iceberg"] if capability_type == "schema_migrator" else []
        ),
    )
    monkeypatch.setattr("phlo.capabilities.discovery.discover_capabilities", lambda: None)

    with pytest.raises(click.ClickException) as exc_info:
        schema_migrate_commands._resolve_migrator()

    assert exc_info.value.exit_code == 1


def _patch_schema_resolution(monkeypatch) -> None:
    class FakeExtractor:
        def extract(self, native_schema: object) -> NormalizedSchema:
            return NormalizedSchema(
                fields=[
                    FieldSpec(name="id", dtype="int64", nullable=False),
                    FieldSpec(name="name", dtype="string", nullable=True),
                ],
            )

    class FakeMigrator:
        def diff_schema(self, *, table_name: str, desired: NormalizedSchema) -> SchemaMigrationPlan:
            assert table_name == "warehouse.customers"
            assert len(desired.fields) >= 1
            return SchemaMigrationPlan(
                table_name=table_name,
                changes=[
                    SchemaChange(
                        field_name="name",
                        change_type="add",
                        new_value="string",
                        classification="safe",
                    )
                ],
                classification="safe",
                recommendations=[],
                requires_approval=False,
            )

    class CustomerSchema:
        __name__ = "CustomerSchema"

    monkeypatch.setattr(schema_migrate_commands, "_resolve_extractor", lambda: FakeExtractor())
    monkeypatch.setattr(schema_migrate_commands, "_resolve_migrator", lambda: FakeMigrator())
    monkeypatch.setattr(
        schema_migrate_commands,
        "_find_native_schema",
        lambda table_name, schema_class: CustomerSchema,
    )
    monkeypatch.setattr(
        schema_migrate_commands, "_select_default_table_store_name", lambda: "iceberg"
    )
    monkeypatch.setattr(schema_migrate_commands, "_select_default_migrator_name", lambda: "iceberg")
    monkeypatch.setattr(
        schema_migrate_commands,
        "_collect_contract_metadata",
        lambda table_name: {
            "owner": "platform-team",
            "consumers": [{"name": "analytics", "contact": "#analytics", "usage": None}],
            "sla": {"quality_threshold": 0.99},
        },
    )
    monkeypatch.setattr(schema_migrate_commands, "_collect_quality_checks", lambda table_name: [])
    monkeypatch.setattr(schema_migrate_commands, "_collect_transform_refs", lambda table_name: [])


def test_schema_migrate_export_contract_writes_default_path(monkeypatch) -> None:
    """Writes a contract file to default location."""
    _patch_schema_resolution(monkeypatch)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["schema-migrate", "export-contract", "warehouse.customers"])
        assert result.exit_code == 0

        contract_path = Path(".phlo/contracts/warehouse__customers.json")
        assert contract_path.exists()
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
        assert payload["table_name"] == "warehouse.customers"
        assert payload["schema_migrator"] == "iceberg"
        assert payload["contract_version"] == 1
        assert payload["contract_metadata"]["owner"] == "platform-team"


def test_schema_migrate_scaffold_yaml_reads_contract(monkeypatch) -> None:
    """Generates migration scaffold YAML from an existing contract file."""
    _patch_schema_resolution(monkeypatch)
    runner = CliRunner()
    with runner.isolated_filesystem():
        export_result = runner.invoke(
            cli, ["schema-migrate", "export-contract", "warehouse.customers"]
        )
        assert export_result.exit_code == 0

        result = runner.invoke(cli, ["schema-migrate", "scaffold-yaml", "warehouse.customers"])
        assert result.exit_code == 0

        yaml_path = Path(".phlo/migrations/warehouse__customers.yaml")
        assert yaml_path.exists()
        payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert payload["table_name"] == "warehouse.customers"
        assert payload["classification"] == "safe"
        assert payload["renames"] == {}
        assert payload["operations"][0]["change_type"] == "add"
        assert payload["operations"][0]["operation_id"]


def test_schema_migrate_scaffold_yaml_backend_failure_is_clean_error(monkeypatch) -> None:
    """Backend failures while scaffolding do not leak tracebacks."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        contract_path = Path(".phlo/contracts/warehouse__customers.json")
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract_path.write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "table_name": "warehouse.customers",
                    "normalized_schema": {"fields": [], "metadata": {}},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            schema_migrate_commands,
            "_build_scaffold_payload_from_contract",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("backend unavailable")),
        )

        result = runner.invoke(cli, ["schema-migrate", "scaffold-yaml", "warehouse.customers"])

        assert result.exit_code == 1
        assert "backend unavailable" in result.output
        assert "Traceback" not in result.output


def test_schema_migrate_plan_passes_yaml_and_cli_renames(monkeypatch) -> None:
    """Plan reads default migration YAML and additive CLI rename flags."""

    class FakeExtractor:
        def extract(self, native_schema: object) -> NormalizedSchema:
            return NormalizedSchema(fields=[FieldSpec(name="email", dtype="string")])

    class FakeMigrator:
        def diff_schema(
            self,
            *,
            table_name: str,
            desired: NormalizedSchema,
            instructions: SchemaMigrationInstructions | None = None,
        ) -> SchemaMigrationPlan:
            assert table_name == "warehouse.customers"
            assert instructions is not None
            assert instructions.renames == {
                "customer_email": "email",
                "surname": "last_name",
            }
            return SchemaMigrationPlan(
                table_name=table_name,
                changes=[
                    SchemaChange(
                        field_name="customer_email",
                        change_type="rename",
                        old_value="customer_email",
                        new_value="email",
                        classification="safe",
                    )
                ],
                classification="safe",
            )

    class CustomerSchema:
        pass

    monkeypatch.setattr(schema_migrate_commands, "_resolve_extractor", lambda: FakeExtractor())
    monkeypatch.setattr(schema_migrate_commands, "_resolve_migrator", lambda: FakeMigrator())
    monkeypatch.setattr(
        schema_migrate_commands,
        "_find_native_schema",
        lambda table_name, schema_class: CustomerSchema,
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        migration_path = Path(".phlo/migrations/warehouse__customers.yaml")
        migration_path.parent.mkdir(parents=True, exist_ok=True)
        migration_path.write_text(
            yaml.safe_dump(
                {
                    "table_name": "warehouse.customers",
                    "renames": {"customer_email": "email"},
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            cli,
            [
                "schema-migrate",
                "plan",
                "warehouse.customers",
                "--rename",
                "surname=last_name",
            ],
        )

    assert result.exit_code == 0
    assert "rename" in result.output


def test_schema_migrate_rename_conflict_tells_user_to_sort_yaml_or_cli(monkeypatch) -> None:
    """Conflicting YAML/CLI rename instructions fail before planning."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        migration_path = Path(".phlo/migrations/warehouse__customers.yaml")
        migration_path.parent.mkdir(parents=True, exist_ok=True)
        migration_path.write_text(
            yaml.safe_dump(
                {
                    "table_name": "warehouse.customers",
                    "renames": {"customer_email": "email"},
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            cli,
            [
                "schema-migrate",
                "diff",
                "warehouse.customers",
                "--rename",
                "customer_email=primary_email",
            ],
        )

    assert result.exit_code == 1
    assert "Sort out the YAML or CLI flags" in result.output


def test_schema_migrate_apply_refuses_rename_when_migrator_does_not_support_it(
    monkeypatch,
) -> None:
    """Apply stops when the selected migrator cannot execute an explicit rename."""

    class FakeExtractor:
        def extract(self, native_schema: object) -> NormalizedSchema:
            return NormalizedSchema(fields=[FieldSpec(name="email", dtype="string")])

    class FakeMigrator:
        def supported_changes(self) -> set[str]:
            return {"add", "drop"}

        def diff_schema(
            self,
            *,
            table_name: str,
            desired: NormalizedSchema,
            instructions: SchemaMigrationInstructions | None = None,
        ) -> SchemaMigrationPlan:
            return SchemaMigrationPlan(
                table_name=table_name,
                changes=[
                    SchemaChange(
                        field_name="customer_email",
                        change_type="rename",
                        old_value="customer_email",
                        new_value="email",
                        classification="safe",
                    )
                ],
                classification="safe",
            )

        def apply_plan(
            self, *, plan: SchemaMigrationPlan, approved: bool = False
        ) -> dict[str, object]:
            raise AssertionError("apply_plan should not run for unsupported rename")

    class CustomerSchema:
        pass

    monkeypatch.setattr(schema_migrate_commands, "_resolve_extractor", lambda: FakeExtractor())
    monkeypatch.setattr(schema_migrate_commands, "_resolve_migrator", lambda: FakeMigrator())
    monkeypatch.setattr(
        schema_migrate_commands,
        "_find_native_schema",
        lambda table_name, schema_class: CustomerSchema,
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "schema-migrate",
                "apply",
                "warehouse.customers",
                "--rename",
                "customer_email=email",
            ],
        )

    assert result.exit_code == 1
    assert "unsupported change type" in result.output
    assert "rename" in result.output


def test_schema_migrate_apply_dry_run_refuses_unsupported_rename(monkeypatch) -> None:
    """Dry-run still answers whether the selected migrator can apply the plan."""

    class FakeExtractor:
        def extract(self, native_schema: object) -> NormalizedSchema:
            return NormalizedSchema(fields=[FieldSpec(name="email", dtype="string")])

    class FakeMigrator:
        def supported_changes(self) -> set[str]:
            return {"add", "drop"}

        def diff_schema(
            self,
            *,
            table_name: str,
            desired: NormalizedSchema,
            instructions: SchemaMigrationInstructions | None = None,
        ) -> SchemaMigrationPlan:
            return SchemaMigrationPlan(
                table_name=table_name,
                changes=[
                    SchemaChange(
                        field_name="customer_email",
                        change_type="rename",
                        old_value="customer_email",
                        new_value="email",
                        classification="safe",
                    )
                ],
                classification="safe",
            )

    class CustomerSchema:
        pass

    monkeypatch.setattr(schema_migrate_commands, "_resolve_extractor", lambda: FakeExtractor())
    monkeypatch.setattr(schema_migrate_commands, "_resolve_migrator", lambda: FakeMigrator())
    monkeypatch.setattr(
        schema_migrate_commands,
        "_find_native_schema",
        lambda table_name, schema_class: CustomerSchema,
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "schema-migrate",
                "apply",
                "warehouse.customers",
                "--rename",
                "customer_email=email",
                "--dry-run",
            ],
        )

    assert result.exit_code == 1
    assert "unsupported change type" in result.output
    assert "Dry run" not in result.output


def test_schema_migrate_scaffold_yaml_is_deterministic(monkeypatch) -> None:
    """Stable operation IDs are emitted for identical plans."""
    _patch_schema_resolution(monkeypatch)
    runner = CliRunner()
    with runner.isolated_filesystem():
        contract_path = Path(".phlo/contracts/warehouse__customers.json")
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract_path.write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "table_name": "warehouse.customers",
                    "normalized_schema": {
                        "fields": [
                            {
                                "name": "id",
                                "dtype": "int64",
                                "nullable": False,
                                "default": None,
                                "metadata": {},
                            }
                        ],
                        "metadata": {},
                    },
                    "quality_checks": [],
                    "transform_refs": [],
                }
            ),
            encoding="utf-8",
        )

        first = runner.invoke(cli, ["schema-migrate", "scaffold-yaml", "warehouse.customers"])
        assert first.exit_code == 0
        first_yaml = yaml.safe_load(Path(".phlo/migrations/warehouse__customers.yaml").read_text())
        first_operation_id = first_yaml["operations"][0]["operation_id"]

        second = runner.invoke(
            cli, ["schema-migrate", "scaffold-yaml", "warehouse.customers", "--force"]
        )
        assert second.exit_code == 0
        second_yaml = yaml.safe_load(Path(".phlo/migrations/warehouse__customers.yaml").read_text())
        assert second_yaml["operations"][0]["operation_id"] == first_operation_id


def test_schema_migrate_scaffold_yaml_recent_reads_recent_contracts(monkeypatch) -> None:
    """Scaffold recent contract additions into migration YAML files."""
    _patch_schema_resolution(monkeypatch)
    runner = CliRunner()
    with runner.isolated_filesystem():
        contracts_dir = Path(".phlo/contracts")
        contracts_dir.mkdir(parents=True, exist_ok=True)

        recent_contract = contracts_dir / "warehouse__customers.json"
        recent_contract.write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "table_name": "warehouse.customers",
                    "normalized_schema": {
                        "fields": [
                            {
                                "name": "id",
                                "dtype": "int64",
                                "nullable": False,
                                "default": None,
                                "metadata": {},
                            }
                        ],
                        "metadata": {},
                    },
                    "quality_checks": [],
                    "transform_refs": [],
                }
            ),
            encoding="utf-8",
        )

        stale_contract = contracts_dir / "warehouse__orders.json"
        stale_contract.write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "table_name": "warehouse.orders",
                    "normalized_schema": {
                        "fields": [
                            {
                                "name": "id",
                                "dtype": "int64",
                                "nullable": False,
                                "default": None,
                                "metadata": {},
                            }
                        ],
                        "metadata": {},
                    },
                    "quality_checks": [],
                    "transform_refs": [],
                }
            ),
            encoding="utf-8",
        )
        stale_ts = recent_contract.stat().st_mtime - (48 * 3600)
        os.utime(stale_contract, (stale_ts, stale_ts))

        result = runner.invoke(
            cli,
            ["schema-migrate", "scaffold-yaml-recent", "--since-hours", "24"],
        )
        assert result.exit_code == 0
        assert "Generated 1 migration scaffolds." in result.output

        assert Path(".phlo/migrations/warehouse__customers.yaml").exists()
        assert not Path(".phlo/migrations/warehouse__orders.yaml").exists()


def test_schema_migrate_scaffold_yaml_recent_continues_after_error(monkeypatch) -> None:
    """Recent scaffold continues processing valid contracts after per-item failures."""
    _patch_schema_resolution(monkeypatch)
    runner = CliRunner()
    with runner.isolated_filesystem():
        contracts_dir = Path(".phlo/contracts")
        contracts_dir.mkdir(parents=True, exist_ok=True)

        valid_contract = contracts_dir / "warehouse__customers.json"
        valid_contract.write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "table_name": "warehouse.customers",
                    "normalized_schema": {
                        "fields": [
                            {
                                "name": "id",
                                "dtype": "int64",
                                "nullable": False,
                                "default": None,
                                "metadata": {},
                            }
                        ],
                        "metadata": {},
                    },
                    "quality_checks": [],
                    "transform_refs": [],
                }
            ),
            encoding="utf-8",
        )

        broken_contract = contracts_dir / "warehouse__broken.json"
        broken_contract.write_text("{not-json}", encoding="utf-8")

        result = runner.invoke(
            cli,
            ["schema-migrate", "scaffold-yaml-recent", "--since-hours", "24"],
        )
        assert result.exit_code == 1
        assert "Generated 1 migration scaffolds." in result.output
        assert "Encountered 1 errors while scaffolding recent contracts." in result.output
        assert Path(".phlo/migrations/warehouse__customers.yaml").exists()


def test_find_native_schema_uses_schema_discovery_capability(monkeypatch) -> None:
    """Schema lookup depends on the neutral schema discovery capability."""

    class PrimarySchema:
        pass

    class FakeDiscovery:
        def discover_schemas(self) -> dict[str, type[PrimarySchema]]:
            return {"RawContractDemo": PrimarySchema}

    monkeypatch.setattr(schema_migrate_commands, "_resolve_extractor", lambda: FakeDiscovery())

    resolved = schema_migrate_commands._find_native_schema(
        table_name="raw.contract_demo",
        schema_class="RawContractDemo",
    )
    assert resolved is PrimarySchema


def test_discover_schema_for_table_uses_schema_discovery_capability(monkeypatch) -> None:
    """Table lookup depends on the neutral schema discovery capability."""

    class RawContractDemo:
        pass

    class FakeDiscovery:
        def discover_schemas(self) -> dict[str, type[RawContractDemo]]:
            return {"RawContractDemo": RawContractDemo}

    monkeypatch.setattr(schema_migrate_commands, "_resolve_extractor", lambda: FakeDiscovery())

    resolved = schema_migrate_commands._discover_schema_for_table("raw.contract_demo")
    assert resolved is RawContractDemo


def test_collect_quality_checks_parses_contract_tags(monkeypatch) -> None:
    """Quality check payload includes parsed owner/consumers/sla from tags."""

    class FakeRegistry:
        def list(self, family: str):
            assert family == "check"

            class Check:
                asset_key = "dlt_contract_demo"
                name = "quality_contract_demo"
                severity = "error"
                blocking = True
                description = "quality checks"
                tags = {
                    "contract_owner": "platform-team",
                    "contract_consumers": "analytics,ml-pipeline",
                    "contract_sla": '{"quality_threshold": 0.99}',
                }

            return [Check()]

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())
    checks = schema_migrate_commands._collect_quality_checks("raw.contract_demo")
    assert len(checks) == 1
    assert checks[0]["owner"] == "platform-team"
    assert checks[0]["consumers"] == ["analytics", "ml-pipeline"]
    assert checks[0]["sla"] == {"quality_threshold": 0.99}


def test_collect_contract_metadata_filters_matching_asset(monkeypatch) -> None:
    """Contract metadata should be sourced from the matching asset only."""

    class FakeRegistry:
        def list(self, family: str):
            assert family == "asset"

            class OtherAsset:
                key = "dlt_other"
                metadata = {
                    "table_name": "warehouse.other",
                    "owner": "wrong-team",
                    "consumers": [{"name": "ignore"}],
                    "sla": {"quality_threshold": 0.5},
                }

            class MatchingAsset:
                key = "dlt_contract_demo"
                metadata = {
                    "table_name": "raw.contract_demo",
                    "owner": "platform-team",
                    "consumers": [
                        {"name": "analytics", "contact": "#analytics", "usage": None},
                        "ignore-me",
                    ],
                    "sla": {"quality_threshold": 0.99},
                }

            return [OtherAsset(), MatchingAsset()]

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())

    metadata = schema_migrate_commands._collect_contract_metadata("raw.contract_demo")

    assert metadata["owner"] == "platform-team"
    assert metadata["consumers"] == [{"name": "analytics", "contact": "#analytics", "usage": None}]
    assert metadata["sla"] == {"quality_threshold": 0.99}


def test_collect_transform_refs_filters_and_deduplicates(monkeypatch) -> None:
    """Only dbt assets that depend on the target table should be returned."""

    class FakeRegistry:
        def list(self, family: str):
            assert family == "asset"

            class MatchingByKey:
                key = "transform_a"
                kinds = {"dbt"}
                deps = ["dlt_contract_demo"]

            class MatchingByShortName:
                key = "transform_b"
                kinds = {"dbt"}
                deps = ["contract_demo"]

            class DuplicateMatch:
                key = "transform_a"
                kinds = {"dbt"}
                deps = ["dlt_contract_demo"]

            class NonDbt:
                key = "other"
                kinds = {"service"}
                deps = ["dlt_contract_demo"]

            class Irrelevant:
                key = "transform_c"
                kinds = {"dbt"}
                deps = ["dlt_other"]

            return [
                MatchingByKey(),
                MatchingByShortName(),
                DuplicateMatch(),
                NonDbt(),
                Irrelevant(),
            ]

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())

    refs = schema_migrate_commands._collect_transform_refs("raw.contract_demo")

    assert refs == ["transform_a", "transform_b"]


def test_build_scaffold_payload_from_contract_rejects_table_mismatch(monkeypatch) -> None:
    """A contract file whose table name does not match the request should fail."""

    monkeypatch.setattr(
        schema_migrate_commands.schema_migrate_contracts,
        "read_contract",
        lambda path: {
            "table_name": "warehouse.other",
            "normalized_schema": {"fields": [], "metadata": {}},
        },
    )

    with pytest.raises(ValueError, match="Contract table mismatch"):
        schema_migrate_commands._build_scaffold_payload_from_contract(
            table_name="warehouse.customers",
            contract_path=Path(".phlo/contracts/warehouse__customers.json"),
        )


def test_refresh_contracts_skips_missing_tables_without_warning(monkeypatch) -> None:
    """First materialization can refresh before the Iceberg table exists."""

    class FakeLogger:
        def __init__(self) -> None:
            self.debug_events: list[tuple[str, dict[str, object]]] = []
            self.warning_events: list[tuple[str, dict[str, object]]] = []

        def debug(self, event: str, **kwargs: object) -> None:
            self.debug_events.append((event, kwargs))

        def warning(self, event: str, **kwargs: object) -> None:
            self.warning_events.append((event, kwargs))

    class FakeAsset:
        key = "dlt_orders"
        kinds = {"dlt"}
        metadata = {"table_name": "raw.orders"}

    class FakeRegistry:
        def list(self, family: str) -> list[FakeAsset]:
            assert family == "asset"
            return [FakeAsset()]

    logger = FakeLogger()
    calls: list[str] = []

    def _missing_table(*, table_name: str, force: bool = True) -> Path:
        calls.append(table_name)
        raise NoSuchTableError(f"Table does not exist: {table_name}")

    monkeypatch.setattr(schema_migrate_commands, "get_capability_registry", lambda: FakeRegistry())
    monkeypatch.setattr(schema_migrate_commands, "export_contract_for_table", _missing_table)
    monkeypatch.setattr(schema_migrate_commands, "logger", logger)

    refreshed = schema_migrate_commands.refresh_contracts_for_selection(selection="dlt_orders")

    assert refreshed == 0
    assert calls == ["raw.orders"]
    assert logger.warning_events == []
    assert logger.debug_events == [
        (
            "schema_contract_refresh_skipped_table_missing",
            {"table_name": "raw.orders", "selection": "dlt_orders"},
        )
    ]


@pytest.mark.parametrize(
    "options,expected_exit,applied",
    [
        (["--dry-run"], 0, False),
        ([], 1, False),
        (["--yes"], 0, True),
    ],
)
def test_apply_json_preview_and_confirmation(monkeypatch, options, expected_exit, applied):
    calls = []

    class Migrator:
        def apply_plan(self, **kwargs):
            calls.append(kwargs)
            return {"snapshot_id": 42}

    plan = SchemaMigrationPlan(
        "warehouse.customers", [SchemaChange("email", "drop")], "breaking", requires_approval=True
    )
    monkeypatch.setattr(
        schema_migrate_commands, "_build_migration_plan", lambda **kwargs: (Migrator(), plan)
    )
    result = CliRunner().invoke(
        schema_migrate_commands.schema_migrate_group,
        ["apply", "warehouse.customers", "--json", *options],
    )
    assert result.exit_code == expected_exit, result.output
    payload = json.loads(result.stdout)
    assert bool(calls) is applied
    if expected_exit == 0:
        assert payload["data"]["applied"] is applied
        assert payload["data"]["plan"]["changes"][0]["field_name"] == "email"
    else:
        assert payload["errors"]


def test_apply_json_backend_failure_is_not_success(monkeypatch):
    class Migrator:
        def apply_plan(self, **kwargs):
            raise RuntimeError("backend unavailable")

    plan = SchemaMigrationPlan("warehouse.customers", [SchemaChange("email", "add")], "safe")
    monkeypatch.setattr(
        schema_migrate_commands, "_build_migration_plan", lambda **kwargs: (Migrator(), plan)
    )
    result = CliRunner().invoke(
        schema_migrate_commands.schema_migrate_group, ["apply", "warehouse.customers", "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["errors"]
    assert "applied successfully" not in result.stdout


def test_apply_declined_confirmation_does_not_mutate(monkeypatch):
    calls = []

    class Migrator:
        def apply_plan(self, **kwargs):
            calls.append(kwargs)

    plan = SchemaMigrationPlan(
        "warehouse.customers", [SchemaChange("email", "drop")], "breaking", requires_approval=True
    )
    monkeypatch.setattr(
        schema_migrate_commands, "_build_migration_plan", lambda **kwargs: (Migrator(), plan)
    )
    monkeypatch.setattr(schema_migrate_commands, "confirm_action", lambda *args, **kwargs: False)
    result = CliRunner().invoke(
        schema_migrate_commands.schema_migrate_group, ["apply", "warehouse.customers"]
    )
    assert result.exit_code == 1
    assert "cancelled" in result.output
    assert not calls
