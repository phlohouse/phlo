"""Contract tests for Iceberg retention: snapshot expiry planning and orphan discovery.

Both operations follow a plan-first protocol. The dry-run plan issues a
plan_token that binds the candidate set, the inventory snapshot, and the
request parameters; execution revalidates the token and refuses provider
submission when any of them moved. A stale or mutated plan therefore fails
closed instead of deleting objects chosen against outdated state.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pyarrow.fs import FileType

from phlo.capabilities import InventoryObject, MaintenanceRetentionStore, ObjectInventory
from phlo.capabilities import MaintenanceExecutionError, MaintenanceExecutionPhase
import phlo_iceberg.resource as resource_module
from phlo_iceberg.resource import (
    SAFE_MIN_RETENTION_HOURS,
    IcebergResource,
    _maintenance_plan_token,
)


class _FakeDataFile:
    def __init__(self, path: str, size: int) -> None:
        self.file_path = path
        self.file_size_in_bytes = size


class _FakeManifest:
    def __init__(self, path: str, files: list[_FakeDataFile]) -> None:
        self.manifest_path = path
        self._entries = [SimpleNamespace(data_file=data_file) for data_file in files]

    def fetch_manifest_entry(self, io: object) -> list[SimpleNamespace]:
        return self._entries


class _FakeSnapshot:
    def __init__(
        self, snapshot_id: int, timestamp: datetime, manifests: list[_FakeManifest]
    ) -> None:
        self.snapshot_id = snapshot_id
        self.timestamp_ms = int(timestamp.timestamp() * 1000)
        self.summary = SimpleNamespace(
            operation=SimpleNamespace(value="append"), additional_properties={}
        )
        self._manifests = manifests

    def manifests(self, io: object) -> list[_FakeManifest]:
        return self._manifests


class _FakeFilesystem:
    def __init__(self, files: list[SimpleNamespace]) -> None:
        self.files = files

    def get_file_info(self, selector: object) -> list[SimpleNamespace]:
        return self.files


class _FakeFileIO:
    properties: dict[str, str] = {}

    def __init__(self, files: list[SimpleNamespace]) -> None:
        self.filesystem = _FakeFilesystem(files)

    def parse_location(self, location: str, properties: dict[str, str]) -> tuple[str, str, str]:
        without_scheme = location.removeprefix("s3://")
        bucket, _, path = without_scheme.partition("/")
        return "s3", bucket, path

    def fs_by_scheme(self, scheme: str, netloc: str) -> _FakeFilesystem:
        return self.filesystem


class _FakeTable:
    def __init__(
        self,
        snapshots: list[_FakeSnapshot],
        io: _FakeFileIO,
        refs: dict[str, int],
    ) -> None:
        self._snapshots = snapshots
        self.io = io
        self._refs = refs

    def snapshots(self) -> list[_FakeSnapshot]:
        return self._snapshots

    def current_snapshot(self) -> _FakeSnapshot:
        return self._snapshots[-1]

    def refs(self) -> dict[str, SimpleNamespace]:
        return {
            name: SimpleNamespace(snapshot_id=snapshot_id)
            for name, snapshot_id in self._refs.items()
        }

    def location(self) -> str:
        return "s3://bucket/warehouse/raw/events"


class _FakeCatalog:
    def __init__(self, table: _FakeTable) -> None:
        self.table = table

    def load_table(self, table_name: str) -> _FakeTable:
        assert table_name == "raw.events"
        return self.table


def _real_planner_resource(monkeypatch) -> tuple[IcebergResource, _FakeTable]:
    now = datetime.now(UTC)
    shared = _FakeDataFile("s3://bucket/warehouse/raw/events/data/shared.parquet", 20)
    candidate_one = _FakeDataFile("s3://bucket/warehouse/raw/events/data/candidate-one.parquet", 10)
    candidate_three = _FakeDataFile(
        "s3://bucket/warehouse/raw/events/data/candidate-three.parquet", 30
    )
    snapshots: list[_FakeSnapshot] = []
    for snapshot_id in range(1, 9):
        files = [shared]
        if snapshot_id == 1:
            files.append(candidate_one)
        if snapshot_id == 3:
            files.append(candidate_three)
        snapshots.append(
            _FakeSnapshot(
                snapshot_id,
                (
                    now - timedelta(days=20)
                    if snapshot_id in {1, 2}
                    else now - timedelta(days=8 - snapshot_id)
                ),
                [_FakeManifest(f"s3://bucket/metadata/{snapshot_id}.avro", files)],
            )
        )
    storage_files = [
        SimpleNamespace(
            type=FileType.File,
            path="bucket/warehouse/raw/events/data/shared.parquet",
            size=20,
            mtime=now - timedelta(days=20),
        ),
        SimpleNamespace(
            type=FileType.File,
            path="bucket/warehouse/raw/events/data/orphan-old.parquet",
            size=70,
            mtime=now - timedelta(days=10),
        ),
        SimpleNamespace(
            type=FileType.File,
            path="bucket/warehouse/raw/events/data/orphan-recent.parquet",
            size=90,
            mtime=now - timedelta(days=1),
        ),
    ]
    table = _FakeTable(snapshots, _FakeFileIO(storage_files), {"release-tag": 2})
    catalog = _FakeCatalog(table)
    monkeypatch.setattr(
        IcebergResource,
        "get_catalog",
        lambda self, override_ref=None: catalog,
    )
    monkeypatch.setattr(
        resource_module,
        "get_table_stats",
        lambda **_: {
            "current_snapshot_id": 8,
            "snapshot_count": 8,
            "file_count": 3,
            "total_size_bytes": 110,
        },
    )
    cutoff = now - timedelta(days=7)
    monkeypatch.setattr(
        IcebergResource,
        "inventory_owned_prefix",
        lambda self, **_: ObjectInventory(
            prefix="s3://bucket/warehouse/raw/events/data/",
            retention_cutoff=cutoff,
            objects=tuple(
                InventoryObject(
                    identity=f"s3://{file_info.path}",
                    size_bytes=file_info.size,
                    modified_at=file_info.mtime,
                    checksum_or_version="test-version",
                )
                for file_info in storage_files
            ),
            page_count=2,
            continuation_exhausted=True,
            complete=True,
            digest="complete-inventory",
        ),
    )
    return IcebergResource(ref="main"), table


def _plan(
    operation: str = "expire_snapshots", candidates: list[int] | None = None
) -> dict[str, Any]:
    candidates = candidates or []
    result: dict[str, Any] = {
        "operation": operation,
        "table_name": "raw.events",
        "ref": "main",
        "catalog": "iceberg",
        "retention_hours": SAFE_MIN_RETENTION_HOURS,
        "retention_threshold": "7d",
        "minimum_safe_retention_hours": SAFE_MIN_RETENTION_HOURS,
        "retain_last": 5,
        "before_snapshot_id": 41,
        "snapshot_count": 8,
        "candidate_snapshots": [
            {"snapshot_id": snapshot_id, "timestamp_ms": snapshot_id, "age_seconds": 1000}
            for snapshot_id in candidates
        ],
        "candidate_files": [],
        "protected_snapshot_ids": [41],
        "table_snapshot_refs": {},
        "table_snapshot_ref_evidence": "available",
        "nessie_ref_evidence": "unavailable",
        "scan_status": "available",
        "affected_objects": len(candidates),
        "affected_bytes": 100 if candidates else 0,
        "max_affected_objects": 10,
        "max_affected_bytes": 1000,
        "unavailable_fields": [],
        "trino_boundary": "pending",
        "observed_at_ms": 1,
    }
    result["plan_token"] = _maintenance_plan_token(result)
    return result


class _FakeSnapshotExpiryExecutor:
    def __init__(self, result: dict[str, object] | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def for_ref(self, ref: str) -> "_FakeSnapshotExpiryExecutor":
        assert ref == "main"
        return self

    def compact_table(self, **_: object) -> dict[str, object]:
        raise AssertionError("snapshot expiry must not invoke compaction")

    def expire_snapshots_table(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_real_snapshot_planner_requires_executor_for_execute(
    monkeypatch,
) -> None:
    resource, _ = _real_planner_resource(monkeypatch)

    planned = resource.expire_snapshots(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=True,
        max_affected_objects=1,
        max_affected_bytes=10,
    )

    plan = planned["planned"]
    assert planned["status"] == "planned"
    assert plan["retain_last"] == 5
    assert [candidate["snapshot_id"] for candidate in plan["candidate_snapshots"]] == [1]
    assert plan["protected_snapshot_ids"] == [2, 4, 5, 6, 7, 8]
    assert 3 in plan["retained_snapshot_ids"]
    assert 3 not in plan["protected_snapshot_ids"]
    assert plan["table_snapshot_refs"] == {"release-tag": 2}
    assert plan["affected_objects"] == 1
    assert plan["affected_bytes"] == 10
    assert plan["affected_objects_scope"] == "candidate_snapshots_only"
    assert plan["retain_last_guarantee"] == "provider_unsupported_not_enforced"

    refused = resource.expire_snapshots(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=planned["before_revision"],
        confirmation_token=planned["plan_token"],
        max_affected_objects=1,
        max_affected_bytes=10,
    )

    assert refused["status"] == "blocked"
    assert refused["accepted"] is False
    assert refused["executed"] is False
    assert refused["failure"]["code"] == "maintenance_executor_required"
    assert refused["planned"]["trino_boundary"] == "not_invoked"


def test_real_orphan_planner_normalizes_paths_and_counts_old_candidates(monkeypatch) -> None:
    resource, _ = _real_planner_resource(monkeypatch)

    result = resource.cleanup_orphan_files(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=True,
    )

    assert result["status"] == "planned"
    assert result["planned"]["scan_status"] == "available"
    assert [candidate["path"] for candidate in result["planned"]["candidate_files"]] == [
        "s3://bucket/warehouse/raw/events/data/orphan-old.parquet"
    ]
    assert result["planned"]["inventory"] | {"retention_cutoff": None} == {
        "prefix": "s3://bucket/warehouse/raw/events/data/",
        "complete": True,
        "page_count": 2,
        "continuation_exhausted": True,
        "digest": "complete-inventory",
        "retention_cutoff": None,
        "failure": None,
    }
    assert result["planned"]["affected_objects"] == 1
    assert result["planned"]["affected_bytes"] == 70
    assert result["planned"]["protected_snapshot_ids"] == [2, 8]
    assert "shared.parquet" not in str(result["planned"]["candidate_files"])


def test_orphan_planner_never_exposes_candidates_from_an_incomplete_inventory(monkeypatch) -> None:
    resource, _ = _real_planner_resource(monkeypatch)
    monkeypatch.setattr(
        resource,
        "inventory_owned_prefix",
        lambda **_: ObjectInventory(
            prefix="s3://bucket/warehouse/raw/events/data/",
            retention_cutoff=datetime.now(UTC),
            objects=(),
            page_count=1,
            continuation_exhausted=False,
            complete=False,
            digest=None,
            failure="S3 pagination ended before completion.",
        ),
    )

    result = resource.cleanup_orphan_files(table_name="raw.events", catalog="iceberg", dry_run=True)

    assert result["status"] == "blocked"
    assert result["accepted"] is False
    assert result["planned"]["candidate_files"] == []
    assert result["planned"]["inventory"]["complete"] is False


def test_support_distinguishes_retention_planning_from_executable_vacuum() -> None:
    resource = IcebergResource(ref="main")

    assert resource.support.supports_vacuum is False
    assert isinstance(resource, MaintenanceRetentionStore)


def test_dry_run_is_provider_free_and_reports_snapshot_age(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan(candidates=[39])
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (plan, object()))

    result = resource.expire_snapshots(table_name="raw.events", catalog="iceberg", dry_run=True)

    assert result["status"] == "planned"
    assert result["executed"] is False
    assert result["planned"]["candidate_snapshots"][0]["snapshot_id"] == 39
    assert result["planned"]["trino_boundary"] == "not_invoked"


def test_execute_requires_exact_token_and_finite_limits(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan(candidates=[39])
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (plan, object()))

    result = resource.expire_snapshots(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token="tampered",
        max_affected_objects=10,
        max_affected_bytes=1000,
    )

    assert result["failure"]["code"] == "plan_token_invalid"


def test_execute_rejects_affected_object_limit_with_current_token(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan(candidates=[39])
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (plan, object()))

    result = resource.expire_snapshots(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token=plan["plan_token"],
        max_affected_objects=0,
        max_affected_bytes=1000,
    )

    assert result["failure"]["code"] == "affected_object_limit_exceeded"


def test_snapshot_execute_blocks_when_provider_surface_exceeds_plan(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan(candidates=[39])
    plan["limits_scope"] = "snapshot_count_and_data_bytes_only"
    plan["plan_token"] = _maintenance_plan_token(plan)
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (plan, object()))

    result = resource.expire_snapshots(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token=plan["plan_token"],
        max_affected_objects=1,
        max_affected_bytes=100,
    )

    assert result["status"] == "blocked"
    assert result["failure"]["code"] == "maintenance_executor_required"
    assert result["failure"]["retryable"] is True
    assert result["retry_safe"] is True
    assert result["planned"]["trino_boundary"] == "not_invoked"


def test_snapshot_execute_revalidates_plan_and_reports_non_atomic_evidence(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    before = _plan(candidates=[39])
    before["snapshot_count"] = 8
    before["file_count"] = 3
    before["total_size_bytes"] = 110
    before["plan_token"] = _maintenance_plan_token(before)
    after = _plan()
    after.update({"before_snapshot_id": 42, "snapshot_count": 7, "file_count": 2})
    after["plan_token"] = _maintenance_plan_token(after)
    plans = iter([before, dict(before), after])
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (next(plans), object()))
    executor = _FakeSnapshotExpiryExecutor(
        {
            "catalog": "iceberg",
            "ref": "main",
            "sql": (
                'ALTER TABLE "raw"."events" EXECUTE expire_snapshots(retention_threshold => \'168h\')'
            ),
            "preflight": {"snapshot_id": 41},
            "retain_last": {
                "requested": 5,
                "enforced": False,
                "reason": "trino_expire_snapshots_supports_retention_threshold_only",
            },
        }
    )

    planned = resource.expire_snapshots(table_name="raw.events", catalog="iceberg", dry_run=True)
    result = resource.expire_snapshots(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token=planned["plan_token"],
        max_affected_objects=10,
        max_affected_bytes=1000,
        executor=executor,
    )

    assert result["status"] == "succeeded"
    assert executor.calls == [
        {
            "table_name": "raw.events",
            "ref": "main",
            "expected_revision": 41,
            "retention_hours": 168,
            "retain_last": 5,
            "operation_id": None,
        }
    ]
    assert result["planned"]["execution_guarantee"] == "non_atomic_threshold_submission"
    assert result["planned"]["retain_last_guarantee"] == "provider_unsupported_not_enforced"
    assert result["affected"]["exact_deleted_snapshot_set"] == "unavailable"
    assert result["affected"]["observed_snapshot_count_reduction"] == 1
    assert result["evidence"]["provider"]["preflight"] == {"snapshot_id": 41}
    assert result["evidence"]["provider"]["retain_last"]["enforced"] is False
    assert result["evidence"]["before"]["file_count"] == 3
    assert result["evidence"]["after"]["file_count"] == 2


def test_snapshot_execute_refuses_a_stale_plan_without_provider_submission(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    planned = _plan(candidates=[39])
    planned["plan_token"] = _maintenance_plan_token(planned)
    revalidated = dict(planned)
    revalidated["before_snapshot_id"] = 42
    revalidated["plan_token"] = _maintenance_plan_token(revalidated)
    plans = iter([planned, revalidated])
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (next(plans), object()))
    executor = _FakeSnapshotExpiryExecutor({})

    dry_run = resource.expire_snapshots(table_name="raw.events", catalog="iceberg", dry_run=True)
    result = resource.expire_snapshots(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token=dry_run["plan_token"],
        max_affected_objects=10,
        max_affected_bytes=1000,
        executor=executor,
    )

    assert result["failure"]["code"] == "plan_token_invalid"
    assert result["planned"]["before_snapshot_id"] == 42
    assert result["planned"]["candidate_snapshots"]
    assert executor.calls == []


def test_snapshot_execute_retains_plan_on_preflight_and_submission_failures(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan(candidates=[39])
    plan["plan_token"] = _maintenance_plan_token(plan)
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (plan, object()))

    def execute_with(error: MaintenanceExecutionError) -> dict[str, object]:
        return resource.expire_snapshots(
            table_name="raw.events",
            catalog="iceberg",
            dry_run=False,
            expected_snapshot_id=41,
            confirmation_token=plan["plan_token"],
            max_affected_objects=10,
            max_affected_bytes=1000,
            executor=_FakeSnapshotExpiryExecutor(error),
        )

    before_submit = execute_with(
        MaintenanceExecutionError(MaintenanceExecutionPhase.PREFLIGHT, TimeoutError("timeout"))
    )
    after_submit = execute_with(
        MaintenanceExecutionError(MaintenanceExecutionPhase.SUBMISSION, ConnectionError("reset"))
    )

    assert before_submit["failure"]["code"] == "failed_before_submission"
    assert before_submit["executed"] is False
    assert before_submit["planned"]["candidate_snapshots"]
    assert after_submit["failure"]["code"] == "outcome_unknown_after_submission"
    assert after_submit["executed"] is True
    assert after_submit["planned"]["candidate_snapshots"]


def test_retention_floor_cannot_be_weakened() -> None:
    result = IcebergResource().cleanup_orphan_files(
        table_name="raw.events", retention_hours=1, dry_run=False
    )

    assert result["failure"]["code"] == "retention_floor_violation"
    assert result["planned"]["minimum_safe_retention_hours"] == SAFE_MIN_RETENTION_HOURS


def test_zero_candidates_is_a_noop_and_still_has_a_plan_token(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan()
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (plan, object()))

    result = resource.expire_snapshots(table_name="raw.events", catalog="iceberg", dry_run=True)

    assert result["status"] == "noop"
    assert result["plan_token"] == plan["plan_token"]


def test_orphan_plan_reports_partial_age_or_size_evidence(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan("cleanup_orphan_files")
    plan["candidate_files"] = [{"path": "s3://lake/warehouse/raw/events/data/orphan.parquet"}]
    plan["affected_objects"] = 1
    plan["affected_bytes"] = None
    plan["unavailable_fields"] = ["candidate_age", "affected_bytes"]
    plan["plan_token"] = _maintenance_plan_token(plan)
    monkeypatch.setattr(resource, "_orphan_retention_metadata", lambda **_: (plan, object()))

    result = resource.cleanup_orphan_files(table_name="raw.events", catalog="iceberg", dry_run=True)

    assert result["planned"]["unavailable_fields"] == ["candidate_age", "affected_bytes"]
    assert result["planned"]["candidate_files"][0]["path"].endswith("orphan.parquet")


def test_orphan_plan_blocks_when_candidate_scan_is_unavailable(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan("cleanup_orphan_files")
    plan["scan_status"] = "unavailable"
    plan["unavailable_fields"] = ["candidate_listing: OSError"]
    plan["plan_token"] = _maintenance_plan_token(plan)
    monkeypatch.setattr(resource, "_orphan_retention_metadata", lambda **_: (plan, object()))

    result = resource.cleanup_orphan_files(table_name="raw.events", catalog="iceberg", dry_run=True)

    assert result["status"] == "blocked"
    assert result["accepted"] is False
    assert result["failure"]["code"] == "orphan_scan_unavailable"
    assert result["planned"]["scan_status"] == "unavailable"


def test_orphan_execute_is_blocked_and_non_retryable(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan("cleanup_orphan_files")
    plan["candidate_files"] = [{"path": "orphan.parquet"}]
    plan["affected_objects"] = 1
    plan["affected_bytes"] = 100
    plan["plan_token"] = _maintenance_plan_token(plan)
    monkeypatch.setattr(resource, "_orphan_retention_metadata", lambda **_: (plan, object()))

    result = resource.cleanup_orphan_files(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token=plan["plan_token"],
        max_affected_objects=10,
        max_affected_bytes=1000,
    )

    assert result["status"] == "blocked"
    assert result["failure"]["code"] == "bounded_execution_unsupported"
    assert result["failure"]["retryable"] is False
    assert result["retry_safe"] is False
    assert result["planned"]["trino_boundary"] == "not_invoked"


def test_orphan_execute_accepts_same_plan_after_only_inventory_cutoff_moves(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    planned = _plan("cleanup_orphan_files")
    planned["inventory"] = {
        "complete": True,
        "digest": "complete-inventory",
        "retention_cutoff": "2026-07-19T09:00:00+00:00",
    }
    planned["plan_token"] = _maintenance_plan_token(planned)
    revalidated = {
        **planned,
        "inventory": {
            **planned["inventory"],
            "retention_cutoff": "2026-07-19T09:00:01+00:00",
        },
    }
    revalidated["plan_token"] = _maintenance_plan_token(revalidated)
    plans = iter([planned, revalidated])
    monkeypatch.setattr(resource, "_orphan_retention_metadata", lambda **_: (next(plans), object()))

    dry_run = resource.cleanup_orphan_files(
        table_name="raw.events", catalog="iceberg", dry_run=True
    )
    result = resource.cleanup_orphan_files(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token=dry_run["plan_token"],
        max_affected_objects=10,
        max_affected_bytes=1000,
    )

    assert dry_run["plan_token"] == revalidated["plan_token"]
    assert result["failure"]["code"] == "bounded_execution_unsupported"


@pytest.mark.parametrize(
    "change",
    [
        {"inventory": {"complete": True, "digest": "changed-inventory"}},
        {"before_snapshot_id": 42},
        {"retention_hours": SAFE_MIN_RETENTION_HOURS + 24},
    ],
    ids=["inventory", "snapshot", "request"],
)
def test_orphan_execute_rejects_changed_inventory_snapshot_or_request(
    monkeypatch, change: dict[str, object]
) -> None:
    resource = IcebergResource(ref="main")
    planned = _plan("cleanup_orphan_files")
    planned["inventory"] = {
        "complete": True,
        "digest": "complete-inventory",
        "retention_cutoff": "2026-07-19T09:00:00+00:00",
    }
    planned["plan_token"] = _maintenance_plan_token(planned)
    revalidated = {**planned, **change}
    revalidated["plan_token"] = _maintenance_plan_token(revalidated)
    plans = iter([planned, revalidated])
    monkeypatch.setattr(resource, "_orphan_retention_metadata", lambda **_: (next(plans), object()))

    dry_run = resource.cleanup_orphan_files(
        table_name="raw.events", catalog="iceberg", dry_run=True
    )
    result = resource.cleanup_orphan_files(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token=dry_run["plan_token"],
        max_affected_objects=10,
        max_affected_bytes=1000,
    )

    assert result["failure"]["code"] == "plan_token_invalid"


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TimeoutError("metadata timeout"), True),
        (ValueError("invalid table metadata"), False),
    ],
)
@pytest.mark.parametrize("operation", ["expire_snapshots", "cleanup_orphan_files"])
def test_metadata_failure_has_one_retry_classification(
    monkeypatch, error: Exception, retryable: bool, operation: str
) -> None:
    resource = IcebergResource(ref="main")
    method_name = (
        "_retention_metadata" if operation == "expire_snapshots" else "_orphan_retention_metadata"
    )

    def raise_error(**_: object) -> tuple[dict[str, object], object]:
        raise error

    monkeypatch.setattr(resource, method_name, raise_error)
    result = getattr(resource, operation)(table_name="raw.events", catalog="iceberg", dry_run=True)

    assert result["status"] == "failed"
    assert result["failure"]["retryable"] is retryable
    assert result["retry_safe"] is retryable


def test_snapshot_plan_counts_only_exact_candidate_snapshots(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan(candidates=[39, 38])
    plan.update(
        {
            "affected_objects": 2,
            "affected_objects_scope": "candidate_snapshots_only",
            "affected_bytes_scope": (
                "observed_unreferenced_data_files_only; evidence_not_deletion_ceiling"
            ),
            "limits_scope": (
                "candidate_snapshot_count_only; provider_metadata_and_data_files_excluded"
            ),
        }
    )
    plan["plan_token"] = _maintenance_plan_token(plan)
    monkeypatch.setattr(resource, "_retention_metadata", lambda **_: (plan, object()))

    result = resource.expire_snapshots(table_name="raw.events", catalog="iceberg", dry_run=True)

    assert result["planned"]["affected_objects"] == len(result["planned"]["candidate_snapshots"])
    assert result["planned"]["affected_objects_scope"] == "candidate_snapshots_only"
    assert "deletion_ceiling" in result["planned"]["affected_bytes_scope"]


def test_orphan_race_discovers_extra_candidate_and_submits_nothing(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    plan = _plan("cleanup_orphan_files")
    plan["candidate_files"] = [{"path": "orphan.parquet"}]
    plan["affected_objects"] = 1
    plan["affected_bytes"] = 100
    plan["plan_token"] = _maintenance_plan_token(plan)
    monkeypatch.setattr(resource, "_orphan_retention_metadata", lambda **_: (plan, object()))
    planned = resource.cleanup_orphan_files(
        table_name="raw.events", catalog="iceberg", dry_run=True
    )

    extra = dict(plan)
    extra["candidate_files"] = [*plan["candidate_files"], {"path": "extra.parquet"}]
    extra["affected_objects"] = 2
    extra["plan_token"] = _maintenance_plan_token(extra)
    monkeypatch.setattr(resource, "_orphan_retention_metadata", lambda **_: (extra, object()))
    result = resource.cleanup_orphan_files(
        table_name="raw.events",
        catalog="iceberg",
        dry_run=False,
        expected_snapshot_id=41,
        confirmation_token=planned["plan_token"],
        max_affected_objects=10,
        max_affected_bytes=1000,
    )

    assert result["status"] == "blocked"
    assert result["failure"]["code"] == "plan_token_invalid"
    assert result["planned"]["trino_boundary"] == "not_invoked"


def test_legacy_snapshot_helper_cannot_bypass_plan_first() -> None:
    from phlo_iceberg.tables import expire_snapshots

    with pytest.raises(ValueError, match="plan-first"):
        expire_snapshots("raw.events")
