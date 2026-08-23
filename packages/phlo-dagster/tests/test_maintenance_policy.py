"""Tests for automated table maintenance policies.

evaluate_table thresholds are strict inequalities against Iceberg table
stats: expire fires above snapshot_count_gt, optimize below
avg_file_size_mb_lt, with a zero file count skipping optimize rather
than dividing. Also covers YAML policy loading (default ref "main")
and the sensor/job Dagster definitions.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from phlo_dagster.maintenance_policy import (
    ExpireSnapshotsPolicy,
    NamespacePolicy,
    OptimizePolicy,
    evaluate_table,
    load_policies,
)


def test_evaluate_table_triggers_expire() -> None:
    """Expire is triggered when snapshot_count exceeds the threshold."""
    policy = NamespacePolicy(
        namespace="raw",
        expire=ExpireSnapshotsPolicy(snapshot_count_gt=10),
    )
    stats = {"snapshot_count": 15, "total_size_mb": 100.0, "file_count": 5}

    action = evaluate_table("raw.my_table", stats, policy)

    assert action.expire_snapshots is True
    assert action.table_name == "raw.my_table"


def test_evaluate_table_no_expire() -> None:
    """Expire is not triggered when snapshot_count is at or below the threshold."""
    policy = NamespacePolicy(
        namespace="raw",
        expire=ExpireSnapshotsPolicy(snapshot_count_gt=20),
    )
    stats = {"snapshot_count": 20, "total_size_mb": 100.0, "file_count": 5}

    action = evaluate_table("raw.my_table", stats, policy)

    assert action.expire_snapshots is False


def test_evaluate_table_triggers_optimize() -> None:
    """Optimize is triggered when average file size is below the threshold."""
    policy = NamespacePolicy(
        namespace="raw",
        optimize=OptimizePolicy(avg_file_size_mb_lt=64.0),
    )
    # avg = 100 / 10 = 10 MB, below 64
    stats = {"snapshot_count": 5, "total_size_mb": 100.0, "file_count": 10}

    action = evaluate_table("raw.my_table", stats, policy)

    assert action.optimize is True


def test_evaluate_table_no_optimize() -> None:
    """Optimize is not triggered when average file size meets the threshold."""
    policy = NamespacePolicy(
        namespace="raw",
        optimize=OptimizePolicy(avg_file_size_mb_lt=64.0),
    )
    # avg = 640 / 10 = 64 MB, not below 64
    stats = {"snapshot_count": 5, "total_size_mb": 640.0, "file_count": 10}

    action = evaluate_table("raw.my_table", stats, policy)

    assert action.optimize is False


def test_evaluate_table_missing_stats_skips_optimize() -> None:
    """Optimize is skipped when file_count is 0 (avoids division by zero)."""
    policy = NamespacePolicy(
        namespace="raw",
        optimize=OptimizePolicy(avg_file_size_mb_lt=64.0),
    )
    stats = {"snapshot_count": 5, "total_size_mb": 0.0, "file_count": 0}

    action = evaluate_table("raw.my_table", stats, policy)

    assert action.optimize is False


def test_load_policies_yaml(tmp_path) -> None:
    """Policies are correctly parsed from a YAML file."""
    policy_file = tmp_path / "policies.yaml"
    policy_file.write_text(
        """\
policies:
  - namespace: raw
    expire:
      snapshot_count_gt: 25
      older_than_days: 14
      retain_last: 3
    optimize:
      avg_file_size_mb_lt: 32.0
  - namespace: curated
    ref: dev
    expire:
      snapshot_count_gt: 10
"""
    )

    policies = load_policies(policy_file)

    assert len(policies) == 2

    raw = policies[0]
    assert raw.namespace == "raw"
    assert raw.ref == "main"
    assert raw.expire is not None
    assert raw.expire.snapshot_count_gt == 25
    assert raw.expire.older_than_days == 14
    assert raw.expire.retain_last == 3
    assert raw.optimize is not None
    assert raw.optimize.avg_file_size_mb_lt == 32.0

    curated = policies[1]
    assert curated.namespace == "curated"
    assert curated.ref == "dev"
    assert curated.expire is not None
    assert curated.expire.snapshot_count_gt == 10
    assert curated.optimize is None


def test_get_policy_maintenance_definitions_returns_sensor() -> None:
    """get_policy_maintenance_definitions includes the sensor and optimize job."""
    from phlo_dagster.maintenance_sensor import get_policy_maintenance_definitions

    defs = get_policy_maintenance_definitions()

    sensor_names = [s.name for s in defs.sensors]
    job_names = [j.name for j in defs.jobs]

    assert "maintenance_policy_sensor" in sensor_names
    assert "optimize_tables_job" in job_names


def test_load_policies_empty_file_returns_empty_list(tmp_path) -> None:
    """Empty policy YAML should parse to an empty policy list."""
    policy_file = tmp_path / "empty.yaml"
    policy_file.write_text("# no policies configured\n")

    assert load_policies(policy_file) == []


def test_maintenance_sensor_run_config_shape() -> None:
    """Sensor should emit RunRequests with op config nested under `config`."""
    from phlo_dagster.maintenance_sensor import maintenance_policy_sensor

    policy = NamespacePolicy(
        namespace="raw",
        expire=ExpireSnapshotsPolicy(snapshot_count_gt=1),
        optimize=OptimizePolicy(avg_file_size_mb_lt=128.0),
    )
    context = MagicMock()
    context.cursor = None

    with (
        patch("phlo_dagster.maintenance_sensor.load_policies", return_value=[policy]),
        patch("phlo_dagster.maintenance_sensor._load_iceberg_stats"),
        patch(
            "phlo_dagster.maintenance_sensor._evaluate_namespace",
            return_value=[
                type(
                    "Action",
                    (),
                    {"table_name": "raw.orders", "expire_snapshots": True, "optimize": True},
                )()
            ],
        ),
    ):
        requests = list(maintenance_policy_sensor._raw_fn(context))

    for request in requests:
        config_dict = (
            request.run_config.to_config_dict()
            if hasattr(request.run_config, "to_config_dict")
            else request.run_config
        )
        op_name = next(iter(config_dict["ops"].keys()))
        assert "config" in config_dict["ops"][op_name]


def test_maintenance_sensor_has_optimize_target_job() -> None:
    """Sensor should be explicitly targeted to at least the optimize job."""
    from phlo_dagster.maintenance_sensor import maintenance_policy_sensor

    target_job_names = {target.job_name for target in maintenance_policy_sensor.targets}
    assert "optimize_tables_job" in target_job_names


def test_load_optimize_query_engine_uses_capability(monkeypatch) -> None:
    """Optimize operations should resolve the query engine via capabilities."""
    from phlo_dagster import maintenance_sensor

    engine = object()
    monkeypatch.setattr(
        maintenance_sensor,
        "resolve_capability",
        lambda capability_type, name: (
            type("Resolution", (), {"provider": engine})()
            if capability_type == "query_engine" and name == "trino"
            else None
        ),
    )

    assert maintenance_sensor._load_optimize_query_engine() is engine


def test_load_optimize_table_store_uses_registered_capability(monkeypatch) -> None:
    """Dagster resolves the maintenance table store through core capabilities."""
    from phlo.capabilities import MaintenanceTableStore
    from phlo_dagster import maintenance_sensor

    class StructuralTableStore:
        def compact(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "planned", "kwargs": kwargs}

    provider = StructuralTableStore()
    calls: list[tuple[str, str]] = []

    def resolve(capability_type: str, name: str) -> SimpleNamespace:
        calls.append((capability_type, name))
        return SimpleNamespace(provider=provider)

    monkeypatch.setattr(maintenance_sensor, "resolve_capability", resolve)

    resolved = maintenance_sensor._load_optimize_table_store()

    assert calls == [("table_store", "iceberg")]
    assert resolved is provider
    assert isinstance(resolved, MaintenanceTableStore)


def test_optimize_table_files_dry_run_does_not_require_executor(monkeypatch) -> None:
    """Dry runs must reach Iceberg planning without resolving Trino."""
    from phlo_dagster import maintenance_sensor

    context = MagicMock()
    context.run_id = "run-91"
    result = {
        "operation": "compact",
        "table_name": "raw.events",
        "ref": "main",
        "dry_run": True,
        "status": "planned",
    }
    compact = MagicMock(return_value=result)
    table_store = MagicMock()
    table_store.compact = compact
    monkeypatch.setattr(maintenance_sensor, "_load_optimize_table_store", lambda: table_store)
    monkeypatch.setattr(
        maintenance_sensor,
        "_load_optimize_maintenance_executor",
        MagicMock(side_effect=AssertionError("dry-run must not resolve an executor")),
    )
    monkeypatch.setattr(
        maintenance_sensor,
        "start_maintenance_op",
        MagicMock(return_value={"started_at": 0}),
    )
    monkeypatch.setattr(
        maintenance_sensor,
        "finish_maintenance_op",
        MagicMock(return_value={"status": "planned"}),
    )

    compute_fn = cast(Any, maintenance_sensor.optimize_table_files.compute_fn)
    output = compute_fn.decorated_fn(
        context,
        maintenance_sensor.OptimizeConfig(table_names=["raw.events"], dry_run=True),
    )

    table_store.compact.assert_called_once_with(
        table_name="raw.events",
        override_ref="main",
        dry_run=True,
        operation_id="run-91:raw.events",
        executor=None,
    )
    assert output["results"] == [result]


def test_optimize_table_files_fallback_uses_operation_result_schema(monkeypatch) -> None:
    """Rejected tables retain the stable operation result contract."""
    from phlo.capabilities import MaintenanceOperationResult, MaintenanceOperationState
    from phlo_dagster import maintenance_sensor

    context = MagicMock()
    context.run_id = "run-91"
    table_store = MagicMock()
    monkeypatch.setattr(maintenance_sensor, "_load_optimize_table_store", lambda: table_store)
    monkeypatch.setattr(
        maintenance_sensor,
        "_load_optimize_maintenance_executor",
        MagicMock(side_effect=AssertionError("dry-run must not resolve an executor")),
    )
    monkeypatch.setattr(
        maintenance_sensor,
        "start_maintenance_op",
        MagicMock(return_value={"started_at": 0}),
    )
    monkeypatch.setattr(
        maintenance_sensor,
        "finish_maintenance_op",
        MagicMock(return_value={"status": "failed"}),
    )

    compute_fn = cast(Any, maintenance_sensor.optimize_table_files.compute_fn)
    output = compute_fn.decorated_fn(
        context,
        maintenance_sensor.OptimizeConfig(table_names=["raw.events;DROP"], dry_run=True),
    )

    result = output["results"][0]
    assert set(result) == set(
        MaintenanceOperationResult(
            operation="compact",
            table_name="raw.events;DROP",
            ref="main",
            dry_run=True,
            status=MaintenanceOperationState.FAILED,
            accepted=False,
            executed=False,
        ).to_dict()
    )
    assert result["operation_id"] == "run-91:raw.events;DROP"
    assert result["before_revision"] is None
    assert result["after_revision"] is None
    assert result["planned"] == {}
    assert result["affected"] == {}
    assert result["evidence"] == {}
    assert result["retry_safe"] is False
    assert result["failure"]["code"] == "invalid_request"
