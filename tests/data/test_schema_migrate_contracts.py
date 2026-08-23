"""Regression tests for schema migration contract helpers.

Pins table-to-artifact naming and default contract/scaffold paths plus contract
write/read round trips with force-overwrite semantics.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from phlo.cli.commands import schema_migrate_contracts as contracts


def test_table_artifact_stem_and_default_paths() -> None:
    """Table names should map to stable filesystem artifacts."""
    assert contracts.table_to_artifact_stem("warehouse.customers") == "warehouse__customers"
    assert contracts.table_to_artifact_stem("raw.customer events") == "raw__customer_events"
    assert contracts.default_contract_path("warehouse.customers") == Path(
        ".phlo/contracts/warehouse__customers.json"
    )
    assert contracts.default_scaffold_yaml_path("warehouse.customers") == Path(
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

    contracts.write_contract(path, payload)

    assert contracts.read_contract(path) == payload

    with pytest.raises(FileExistsError):
        contracts.write_contract(path, payload)

    updated = {**payload, "contract_version": 2}
    contracts.write_contract(path, updated, force=True)

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


def test_build_scaffold_payload_uses_stable_operation_ids() -> None:
    """build_scaffold_payload should preserve contract context and deterministic ids."""
    migration_plan = SimpleNamespace(
        changes=[
            SimpleNamespace(
                field_name="email",
                change_type="update",
                old_value="varchar",
                new_value="string",
                classification="safe",
            ),
            SimpleNamespace(
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
        "contract_version": 3,
        "table_store": "iceberg",
        "schema_migrator": "iceberg",
        "quality_checks": [{"name": "freshness"}],
        "transform_refs": ["transform_a"],
    }

    payload = contracts.build_scaffold_payload(
        table_name="warehouse.customers",
        contract=contract,
        migration_plan=migration_plan,
        generated_at="2026-04-09T09:00:00+00:00",
    )

    assert payload["schema_migration_version"] == contracts.MIGRATION_SCAFFOLD_VERSION
    assert payload["generated_at"] == "2026-04-09T09:00:00+00:00"
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
    assert payload["operations"][0]["operation_id"] == contracts.stable_operation_id(
        table_name="warehouse.customers",
        field_name="email",
        change_type="update",
        old_value="varchar",
        new_value="string",
    )
    assert payload["operations"][1]["operation_id"] == contracts.stable_operation_id(
        table_name="warehouse.customers",
        field_name="age",
        change_type="drop",
        old_value="int64",
        new_value=None,
    )
