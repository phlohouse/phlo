"""Contract tests for the ref-aware Trino maintenance executor.

Every maintenance operation runs a preflight snapshot check against the
selected ref before submitting SQL; a stale revision aborts before any
write and preflight failures are never submitted. Submission failures
are surfaced as phase-tagged MaintenanceExecutionError (outcome
unknown), identifiers are validated, and retain_last is reported as
unenforced by Trino's threshold-only expire_snapshots.
"""

from unittest.mock import MagicMock

import pytest

from phlo.capabilities import MaintenanceExecutionError, MaintenanceExecutionPhase
from phlo_trino.resource import TrinoResource
from phlo_trino.plugin import TrinoResourceProvider


def _resource(ref: str = "main") -> tuple[TrinoResource, MagicMock]:
    resource = TrinoResource(ref=ref)
    execute = MagicMock(side_effect=[[(41,)], []])
    resource.execute = execute
    return resource, execute


def test_compaction_checks_snapshot_and_executes_on_selected_ref() -> None:
    resource, execute = _resource(ref="dev")

    result = resource.compact_table(
        table_name="raw.events",
        ref="dev",
        expected_revision=41,
        operation_id="run-41",
    )

    assert result["ref"] == "dev"
    assert result["catalog"] == "iceberg_dev"
    assert result["sql"] == 'ALTER TABLE "raw"."events" EXECUTE optimize'
    assert execute.call_args_list[0].args[0] == (
        'SELECT snapshot_id FROM "raw"."events$history" '
        "WHERE is_current_ancestor ORDER BY made_current_at DESC LIMIT 1"
    )
    assert execute.call_args_list[1].args[0] == result["sql"]
    assert resource._resolved_catalog() == "iceberg_dev"


def test_compaction_blocks_stale_snapshot_before_optimize() -> None:
    resource, execute = _resource(ref="main")

    with pytest.raises(ValueError, match="snapshot changed"):
        resource.compact_table(
            table_name="raw.events",
            ref="main",
            expected_revision=40,
        )

    assert execute.call_count == 1
    assert "EXECUTE optimize" not in execute.call_args.args[0]


@pytest.mark.parametrize(
    ("side_effect", "expected_phase", "expected_calls"),
    [
        (RuntimeError("connection refused"), MaintenanceExecutionPhase.PREFLIGHT, 1),
        ([[(41,)], RuntimeError("connection reset")], MaintenanceExecutionPhase.SUBMISSION, 2),
    ],
    ids=["preflight-failure", "submission-failure"],
)
def test_compaction_failure_reports_the_failing_phase(
    side_effect, expected_phase, expected_calls
) -> None:
    resource, execute = _resource(ref="main")
    execute.side_effect = side_effect

    with pytest.raises(MaintenanceExecutionError) as raised:
        resource.compact_table(
            table_name="raw.events",
            ref="main",
            expected_revision=41,
        )

    assert raised.value.phase is expected_phase
    assert execute.call_count == expected_calls


def test_snapshot_expiry_checks_snapshot_and_submits_threshold_on_selected_ref() -> None:
    resource, execute = _resource(ref="dev")

    result = resource.expire_snapshots_table(
        table_name="raw.events",
        ref="dev",
        expected_revision=41,
        retention_hours=168,
        retain_last=5,
        operation_id="run-41",
    )

    assert result["ref"] == "dev"
    assert result["catalog"] == "iceberg_dev"
    assert result["preflight"] == {"snapshot_id": 41}
    assert result["retain_last"] == {
        "requested": 5,
        "enforced": False,
        "reason": "trino_expire_snapshots_supports_retention_threshold_only",
    }
    assert execute.call_args_list[1].args[0] == (
        'ALTER TABLE "raw"."events" EXECUTE expire_snapshots(retention_threshold => \'168h\')'
    )


def test_snapshot_expiry_preflight_failure_never_submits() -> None:
    resource, execute = _resource(ref="main")
    execute.side_effect = RuntimeError("connection refused")

    with pytest.raises(MaintenanceExecutionError) as raised:
        resource.expire_snapshots_table(
            table_name="raw.events",
            ref="main",
            expected_revision=41,
            retention_hours=168,
            retain_last=5,
        )

    assert raised.value.phase is MaintenanceExecutionPhase.PREFLIGHT
    assert execute.call_count == 1


@pytest.mark.parametrize(
    ("table_name", "ref", "expected_error"),
    [
        ('raw.events; DROP TABLE "other"', "main", "namespace.table"),
        ("raw.events", "dev", "configured for ref"),
    ],
    ids=["unsafe-identifier", "ref-mismatch"],
)
def test_compaction_rejects_invalid_requests(
    table_name: str, ref: str, expected_error: str
) -> None:
    resource = TrinoResource(ref="main")

    with pytest.raises(ValueError, match=expected_error):
        resource.compact_table(
            table_name=table_name,
            ref=ref,
            expected_revision=41,
        )


def test_provider_exposes_explicit_maintenance_executor_capability() -> None:
    specs = TrinoResourceProvider().get_maintenance_executors()

    assert len(specs) == 1
    assert specs[0].name == "trino"
    assert specs[0].provider.for_ref("dev").ref == "dev"
