"""Regression tests for schema migration contract helpers.

Pins table-to-artifact naming and default contract/scaffold paths plus contract
write/read round trips with force-overwrite semantics.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from phlo.capabilities.specs import FieldSpec, NormalizedSchema, SchemaChange, SchemaMigrationPlan
from phlo.schema_migration import artifacts as contracts
from phlo.schema_migration.instructions import resolve_migration_instructions


def test_table_artifact_stem_and_default_paths() -> None:
    """Table names should map to stable filesystem artifacts."""
    assert contracts.table_to_artifact_stem("warehouse.customers") == "warehouse__customers"
    assert contracts.table_to_artifact_stem("raw.customer events") == "raw__customer_events"
    assert contracts.default_contract_path("warehouse.customers") == Path(
        ".phlo/contracts/warehouse__customers.json"
    )
    assert contracts.default_migration_instruction_path("warehouse.customers") == Path(
        ".phlo/migrations/warehouse__customers.yaml"
    )


def test_write_and_read_contract_round_trip(tmp_path: Path) -> None:
    """Contracts should round-trip cleanly and respect force semantics."""
    path = tmp_path / "contracts" / "warehouse__customers.json"
    payload = {
        "contract_version": 1,
        "table_name": "warehouse.customers",
        "quality_checks": [],
    }

    assert contracts.write_contract(payload, output_path=path) == path

    assert contracts.read_contract(path) == payload

    with pytest.raises(FileExistsError):
        contracts.write_contract(payload, output_path=path)

    updated = {**payload, "contract_version": 2}
    contracts.write_contract(updated, output_path=path, force=True)

    assert contracts.read_contract(path) == updated


def test_read_contract_rejects_non_object(tmp_path: Path) -> None:
    """read_contract should reject non-object JSON roots."""
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    with pytest.raises(ValueError, match="Contract root must be an object"):
        contracts.read_contract(path)


def test_list_recent_contract_paths_filters_orders_and_limits(tmp_path: Path) -> None:
    """Recent contract discovery should filter by age and preserve newest-first order."""
    contracts_dir = tmp_path / ".phlo/contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)

    recent_old = contracts_dir / "older.json"
    recent_old.write_text("{}", encoding="utf-8")
    older = contracts_dir / "newest.json"
    older.write_text("{}", encoding="utf-8")
    stale = contracts_dir / "stale.json"
    stale.write_text("{}", encoding="utf-8")

    now = datetime.now(UTC)
    recent_old_ts = (now - timedelta(hours=2)).timestamp()
    older_ts = (now - timedelta(minutes=5)).timestamp()
    stale_ts = (now - timedelta(days=3)).timestamp()
    os.utime(recent_old, (recent_old_ts, recent_old_ts))
    os.utime(older, (older_ts, older_ts))
    os.utime(stale, (stale_ts, stale_ts))

    with pytest.raises(ValueError, match="since_hours must be >= 0"):
        contracts.list_recent_contract_paths(contracts_dir=contracts_dir, since_hours=-1)

    recent_paths = contracts.list_recent_contract_paths(
        contracts_dir=contracts_dir,
        since_hours=24,
        limit=1,
    )

    assert recent_paths == [older]


def test_scaffold_contract_preserves_review_context_and_stable_ids(tmp_path, monkeypatch) -> None:
    """Scaffolds preserve decoded schemas, review context, IDs, and overwrite protection."""
    monkeypatch.chdir(tmp_path)
    migration_plan = SchemaMigrationPlan(
        table_name="warehouse.customers",
        changes=[
            SchemaChange(
                field_name="email",
                change_type="update",
                old_value="varchar",
                new_value="string",
                classification="safe",
            ),
            SchemaChange(
                field_name="age",
                change_type="drop",
                old_value="int64",
                new_value=None,
                classification="breaking",
            ),
        ],
        classification="warning",
        requires_approval=True,
        recommendations=["keep the column nullable"],
    )
    contract = {
        "table_name": "warehouse.customers",
        "contract_version": 3,
        "normalized_schema": {
            "fields": [{"name": "email", "dtype": "string", "nullable": False}],
            "metadata": {"owner": "analytics"},
        },
        "table_store": "iceberg",
        "schema_migrator": "iceberg",
        "quality_checks": [{"name": "freshness"}],
        "transform_refs": ["transform_a"],
    }

    class Migrator:
        def diff_schema(self, *, table_name, desired):
            assert table_name == "warehouse.customers"
            assert desired == NormalizedSchema(
                fields=[FieldSpec(name="email", dtype="string", nullable=False)],
                metadata={"owner": "analytics"},
            )
            return migration_plan

    source = contracts.write_contract(contract)
    assert source == Path(".phlo/contracts/warehouse__customers.json")
    before = datetime.now(UTC)
    destination = contracts.scaffold_contract(
        contract_path=source, resolve_migrator=lambda: Migrator()
    )
    assert destination == Path(".phlo/migrations/warehouse__customers.yaml")
    original = destination.read_text(encoding="utf-8")
    payload = yaml.safe_load(original)

    assert payload["schema_migration_version"] == contracts.MIGRATION_SCAFFOLD_VERSION
    assert before <= datetime.fromisoformat(payload["generated_at"]) <= datetime.now(UTC)
    assert payload["table_name"] == "warehouse.customers"
    assert payload["contract_version"] == 3
    assert payload["classification"] == "warning"
    assert payload["requires_approval"] is True
    assert payload["recommendations"] == ["keep the column nullable"]
    assert payload["context"] == {
        "table_store": "iceberg",
        "schema_migrator": "iceberg",
        "quality_checks": [{"name": "freshness"}],
        "transform_refs": ["transform_a"],
    }
    assert payload["operations"] == [
        {
            "operation_id": "95aca5fd9436f301",
            "field_name": "email",
            "change_type": "update",
            "old_value": "varchar",
            "new_value": "string",
            "classification": "safe",
        },
        {
            "operation_id": "fa35df8230e9d521",
            "field_name": "age",
            "change_type": "drop",
            "old_value": "int64",
            "new_value": None,
            "classification": "breaking",
        },
    ]
    payload["renames"] = {"old_email": "email"}
    reviewed = yaml.safe_dump(payload, sort_keys=False)
    destination.write_text(reviewed, encoding="utf-8")
    with pytest.raises(FileExistsError, match="use --force to overwrite"):
        contracts.scaffold_contract(
            table_name="warehouse.customers", resolve_migrator=lambda: Migrator()
        )
    assert destination.read_text(encoding="utf-8") == reviewed
    assert resolve_migration_instructions(
        table_name="warehouse.customers", migration_file=None, rename_flags=()
    ).renames == {"old_email": "email"}

    contracts.scaffold_contract(
        table_name="warehouse.customers", resolve_migrator=lambda: Migrator(), force=True
    )
    regenerated = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert regenerated["renames"] == {}
    assert regenerated["operations"] == payload["operations"]
