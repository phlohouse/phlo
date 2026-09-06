"""Tests for the snapshot-based WAP strategy (launch, promotion, cleanup).

The snapshot strategy stages runs as candidate snapshots on a
SnapshotPromotionCatalog and publishes them by advancing a durable release
pointer with a compare-and-swap guard. Branch-based Nessie behavior is
covered by test_wap_launch.py / test_wap_sensors.py and must remain unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import dagster as dg
import pytest

import phlo.infrastructure
from phlo.capabilities.interfaces import CandidateSnapshot
from phlo.config_schema import WapConfig
from phlo_dagster.wap_launch import (
    WAP_ATTEMPT_TAG,
    WAP_BRANCH_TAG,
    WAP_PROJECT_ID_TAG,
    WAP_REF_TAG,
    WAP_RUN_ID_TAG,
    prepare_wap_launch,
)
from phlo_dagster.wap_sensors import (
    _advance_snapshot_promotion,
    wap_auto_promotion_sensor,
    wap_candidate_cleanup_sensor,
    wap_branch_cleanup_sensor,
    get_wap_definitions,
    write_wap_report,
)
from phlo_dagster.wap_launch import (
    _write_launch_manifest as _write_immutable_launch_manifest,
)


class _PromotionCatalog:
    """Fake SnapshotPromotionCatalog with a revisioned release pointer."""

    def __init__(self, revision: int = 0) -> None:
        self.revision = revision
        self.candidates: list[CandidateSnapshot] = [
            CandidateSnapshot(
                table_name="bronze.events",
                snapshot_id=101,
                run_id="logical-snap",
                namespace="pipeline-run-logical-snap",
            )
        ]
        self.promoted: list[dict] = []
        self.aborted: list[str] = []

    def create_candidate(self, *, table_name: str, run_id: str) -> CandidateSnapshot:
        return CandidateSnapshot(table_name=table_name, snapshot_id=101, run_id=run_id)

    def list_candidates(self, *, namespace: str) -> list[CandidateSnapshot]:
        return list(self.candidates)

    def promote_candidates(
        self,
        *,
        namespace: str,
        release_id: str,
        expected_revision: int | None = None,
        tables: list[str] | None = None,
    ) -> list:
        self.promoted.append(
            {
                "namespace": namespace,
                "release_id": release_id,
                "expected_revision": expected_revision,
            }
        )
        self.revision += 1
        return [{"table_name": "bronze.events", "snapshot_id": 101}]

    def resolve_release(self, *, table_name: str):
        return None

    def release_revision(self) -> int:
        return self.revision

    def abort_candidates(self, *, namespace: str) -> bool:
        self.aborted.append(namespace)
        return True

    def prune_candidates(self, *, older_than) -> list[str]:
        return []


class _BranchOnlyCatalog:
    def list_branches(self) -> list:
        return []


def _snapshot_strategy(monkeypatch, strategy: str = "snapshot") -> None:
    monkeypatch.setattr(
        phlo.infrastructure,
        "load_wap_config",
        lambda *a, **k: WapConfig(enabled=True, strategy=strategy),
    )


def _write_snapshot_launch(
    logical_run_id: str,
    dagster_run_id: str,
    namespace: str,
    *,
    revision: str = "0",
    project_id: str = "project",
    attempt: int = 1,
) -> None:
    tags = {
        WAP_RUN_ID_TAG: logical_run_id,
        WAP_BRANCH_TAG: namespace,
        WAP_REF_TAG: namespace,
        WAP_PROJECT_ID_TAG: project_id,
        WAP_ATTEMPT_TAG: str(attempt),
    }
    checksum = _write_immutable_launch_manifest(
        logical_run_id=logical_run_id,
        dagster_run_id=dagster_run_id,
        branch=namespace,
        tags=tags,
        source_hash=revision,
        target_hash_before=revision,
    )
    assert checksum is not None
    write_wap_report(
        logical_run_id,
        status="launched",
        strategy="snapshot",
        branch=namespace,
        dagster_run_id=dagster_run_id,
        launch_tags=tags,
        launch_manifest_checksum=checksum,
        launch_source_hash=revision,
        launch_target_hash_before=revision,
    )


def _snap_run(dagster_run_id: str, logical_run_id: str, namespace: str) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=dagster_run_id,
        status=dg.DagsterRunStatus.SUCCESS,
        tags={
            WAP_RUN_ID_TAG: logical_run_id,
            WAP_BRANCH_TAG: namespace,
            WAP_REF_TAG: namespace,
            WAP_PROJECT_ID_TAG: "project",
            WAP_ATTEMPT_TAG: "1",
        },
    )


# ---------------------------------------------------------------------------
# Launch path
# ---------------------------------------------------------------------------


def test_snapshot_launch_creates_candidate_namespace_binding(monkeypatch, tmp_path) -> None:
    catalog = _PromotionCatalog(revision=5)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    _snapshot_strategy(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.get_settings",
        lambda: SimpleNamespace(phlo_project="warehouse"),
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.resolve_capability",
        lambda _: SimpleNamespace(
            provider=catalog,
            support=SimpleNamespace(
                supports_refs=False, supports_promote=True, supports_snapshots=True
            ),
        ),
    )

    launch = prepare_wap_launch(logical_run_id="request-42")

    assert launch.branch == "pipeline-run-request-42"
    assert launch.strategy == "snapshot"
    assert launch.tags[WAP_REF_TAG] == "pipeline-run-request-42"
    assert launch.source_hash == "5"
    assert launch.target_hash_before == "5"
    report = json.loads(
        (tmp_path / ".phlo" / "wap-reports" / "request-42.json").read_text(encoding="utf-8")
    )
    assert report["strategy"] == "snapshot"
    assert report["branch"] == "pipeline-run-request-42"


def test_snapshot_launch_rejects_branch_only_catalog(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", "/tmp")
    _snapshot_strategy(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.get_settings",
        lambda: SimpleNamespace(phlo_project="warehouse"),
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.resolve_capability",
        lambda _: SimpleNamespace(
            provider=_BranchOnlyCatalog(),
            support=SimpleNamespace(
                supports_refs=True, supports_promote=True, supports_snapshots=False
            ),
        ),
    )

    with pytest.raises(Exception, match="snapshot"):
        prepare_wap_launch(logical_run_id="request-42")


def test_snapshot_launch_cleanup_aborts_candidates(monkeypatch, tmp_path) -> None:
    catalog = _PromotionCatalog()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    _snapshot_strategy(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.get_settings",
        lambda: SimpleNamespace(phlo_project="warehouse"),
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.resolve_capability",
        lambda _: SimpleNamespace(
            provider=catalog,
            support=SimpleNamespace(
                supports_refs=False, supports_promote=True, supports_snapshots=True
            ),
        ),
    )

    launch = prepare_wap_launch(logical_run_id="cleanup-1")
    launch.cleanup_if_created()
    assert catalog.aborted == ["pipeline-run-cleanup-1"]


# ---------------------------------------------------------------------------
# Promotion sensor
# ---------------------------------------------------------------------------


def _promotion_sensor_env(monkeypatch, tmp_path, catalog, *, strategy="snapshot"):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    _snapshot_strategy(monkeypatch, strategy=strategy)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_snapshot_promotion_catalog", lambda: catalog
    )
    monkeypatch.setattr("phlo_dagster.wap_sensors._all_checks_passed", lambda *a: True)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._quality_evidence", lambda *a, **k: ("quality-id", {})
    )
    monkeypatch.setattr("phlo_dagster.wap_sensors._reconcile_promoted_wap_run", lambda *a: True)
    instance = MagicMock()
    return instance


def test_snapshot_promotion_advances_release_pointer_and_aborts_candidates(
    monkeypatch, tmp_path
) -> None:
    logical_run_id = "logical-snap"
    namespace = f"pipeline-run-{logical_run_id}"
    catalog = _PromotionCatalog(revision=0)
    instance = _promotion_sensor_env(monkeypatch, tmp_path, catalog)
    _write_snapshot_launch(logical_run_id, "run-snap", namespace)
    run = _snap_run("run-snap", logical_run_id, namespace)
    instance.get_runs.return_value = [run]

    context = MagicMock(instance=instance, cursor=None)
    wap_auto_promotion_sensor._raw_fn(context)

    assert catalog.promoted == [
        {
            "namespace": namespace,
            "release_id": logical_run_id,
            "expected_revision": 0,
        }
    ]
    assert catalog.aborted == [namespace]
    report = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json").read_text())
    assert report["status"] == "promoted"
    assert report["merge_state"] == "merged"
    assert report["source_deleted"] is True
    assert report["candidates"] == [{"table": "bronze.events", "snapshot_id": "101"}]
    instance.add_run_tags.assert_called_once_with("run-snap", {"phlo/wap_promoted": "true"})


def test_snapshot_promotion_quality_rejection_retains_candidates(monkeypatch, tmp_path) -> None:
    logical_run_id = "logical-snap-q"
    namespace = f"pipeline-run-{logical_run_id}"
    catalog = _PromotionCatalog()
    instance = _promotion_sensor_env(monkeypatch, tmp_path, catalog)
    monkeypatch.setattr("phlo_dagster.wap_sensors._all_checks_passed", lambda *a: False)
    _write_snapshot_launch(logical_run_id, "run-snap-q", namespace)
    run = _snap_run("run-snap-q", logical_run_id, namespace)
    instance.get_runs.return_value = [run]

    context = MagicMock(instance=instance, cursor=None)
    wap_auto_promotion_sensor._raw_fn(context)

    assert catalog.promoted == []
    assert catalog.aborted == []
    report = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json").read_text())
    assert report["status"] == "promotion_blocked"
    assert report["failure_reason"] == "asset_checks_failed"


def test_snapshot_promotion_refuses_revision_changed_since_launch(monkeypatch, tmp_path) -> None:
    logical_run_id = "stale-snapshot"
    namespace = f"pipeline-run-{logical_run_id}"
    catalog = _PromotionCatalog(revision=1)
    instance = _promotion_sensor_env(monkeypatch, tmp_path, catalog)
    _write_snapshot_launch(logical_run_id, "run-stale", namespace, revision="0")
    instance.get_runs.return_value = [_snap_run("run-stale", logical_run_id, namespace)]

    wap_auto_promotion_sensor._raw_fn(MagicMock(instance=instance, cursor=None))

    assert catalog.promoted == []
    assert catalog.aborted == []
    assert _read(logical_run_id)["failure_reason"] == "release_pointer_conflict"


def test_snapshot_promotion_release_conflict_fails_without_promotion(monkeypatch, tmp_path) -> None:
    logical_run_id = "logical-snap-c"
    namespace = f"pipeline-run-{logical_run_id}"
    catalog = _PromotionCatalog()

    def conflict(*, namespace, release_id, expected_revision=None, tables=None):
        raise RuntimeError("release pointer moved")

    catalog.promote_candidates = conflict
    instance = _promotion_sensor_env(monkeypatch, tmp_path, catalog)
    _write_snapshot_launch(logical_run_id, "run-snap-c", namespace)
    run = _snap_run("run-snap-c", logical_run_id, namespace)
    instance.get_runs.return_value = [run]

    context = MagicMock(instance=instance, cursor=None)
    wap_auto_promotion_sensor._raw_fn(context)

    assert catalog.aborted == []
    report = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json").read_text())
    assert report["status"] == "promotion_failed"
    assert report["failure_reason"] == "release_promotion_failed"


def test_snapshot_run_under_branch_strategy_is_blocked(monkeypatch, tmp_path) -> None:
    logical_run_id = "logical-mixed"
    namespace = f"pipeline-run-{logical_run_id}"
    catalog = MagicMock()
    instance = _promotion_sensor_env(monkeypatch, tmp_path, catalog, strategy="branch")
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    _write_snapshot_launch(logical_run_id, "run-mixed", namespace)
    run = _snap_run("run-mixed", logical_run_id, namespace)
    instance.get_runs.return_value = [run]

    context = MagicMock(instance=instance, cursor=None)
    wap_auto_promotion_sensor._raw_fn(context)

    report = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json").read_text())
    assert report["status"] == "promotion_blocked"
    assert report["failure_reason"] == "wap_strategy_mismatch"
    catalog.merge_branch.assert_not_called()


def test_advance_snapshot_promotion_resumes_conflicted_intent_when_release_resolved(
    monkeypatch, tmp_path
) -> None:
    logical_run_id = "logical-resume"
    namespace = f"pipeline-run-{logical_run_id}"
    catalog = _PromotionCatalog()
    catalog.revision = 3  # the release pointer moved after the durable intent
    resolved = SimpleNamespace(release_id=logical_run_id, revision=3)
    catalog.resolve_release = lambda *, table_name: resolved
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    write_wap_report(
        logical_run_id,
        status="promotion_pending",
        strategy="snapshot",
        merge_state="merge_started",
        branch=namespace,
        target_hash_before="0",
    )

    advance = _advance_snapshot_promotion(
        catalog=catalog,
        run=SimpleNamespace(run_id="run-resume"),
        branch_name=namespace,
        logical_run_id=logical_run_id,
        prior_report=_read(logical_run_id),
        quality_decision_id="q",
        quality_metadata={},
    )

    assert advance is not None
    assert catalog.aborted == [namespace]


def _read(logical_run_id: str):
    from phlo_dagster.wap_launch import read_wap_report

    return read_wap_report(logical_run_id)


def test_snapshot_intent_retries_when_release_revision_is_unchanged(monkeypatch, tmp_path):
    logical_run_id = "retry-intent"
    namespace = f"pipeline-run-{logical_run_id}"
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    _write_snapshot_launch(logical_run_id, "retry-run", namespace)
    write_wap_report(
        logical_run_id,
        status="promotion_pending",
        merge_state="merge_started",
        target_hash_before="0",
    )
    catalog = _PromotionCatalog()

    result = _advance_snapshot_promotion(
        catalog=catalog,
        run=SimpleNamespace(run_id="retry-run"),
        branch_name=namespace,
        logical_run_id=logical_run_id,
        prior_report=_read(logical_run_id),
        quality_decision_id="q",
        quality_metadata={},
    )

    assert result is not None
    assert catalog.revision == 1
    assert catalog.aborted == [namespace]


# ---------------------------------------------------------------------------
# Candidate cleanup sensor
# ---------------------------------------------------------------------------


def test_candidate_cleanup_sensor_aborts_stale_namespace(monkeypatch, tmp_path) -> None:
    logical_run_id = "logical-old"
    namespace = f"pipeline-run-{logical_run_id}"
    catalog = _PromotionCatalog()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    _snapshot_strategy(monkeypatch)
    _write_snapshot_launch(logical_run_id, "run-old", namespace)
    backdated = datetime.now(timezone.utc) - timedelta(hours=48)
    report_path = tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json"
    payload = json.loads(report_path.read_text())
    payload["updated_at"] = backdated.isoformat()
    report_path.write_text(json.dumps(payload))
    run = SimpleNamespace(
        run_id="run-old",
        status=dg.DagsterRunStatus.SUCCESS,
        tags={
            WAP_RUN_ID_TAG: logical_run_id,
            WAP_BRANCH_TAG: namespace,
            WAP_REF_TAG: namespace,
            WAP_PROJECT_ID_TAG: "project",
            WAP_ATTEMPT_TAG: "1",
        },
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    _snapshot_strategy(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_snapshot_promotion_catalog", lambda: catalog
    )

    context = MagicMock(instance=instance, cursor=None)
    wap_candidate_cleanup_sensor._raw_fn(context)

    assert catalog.aborted == [namespace]
    report = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json").read_text())
    assert report["status"] == "cleanup_complete"


def test_candidate_cleanup_sensor_retains_active_run_candidates(monkeypatch, tmp_path) -> None:
    logical_run_id = "logical-active"
    namespace = f"pipeline-run-{logical_run_id}"
    catalog = _PromotionCatalog()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    _snapshot_strategy(monkeypatch)
    _write_snapshot_launch(logical_run_id, "run-active", namespace)
    backdated = datetime.now(timezone.utc) - timedelta(hours=48)
    report_path = tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json"
    payload = json.loads(report_path.read_text())
    payload["updated_at"] = backdated.isoformat()
    report_path.write_text(json.dumps(payload))
    run = SimpleNamespace(
        run_id="run-active",
        status=dg.DagsterRunStatus.STARTED,
        tags={
            WAP_RUN_ID_TAG: logical_run_id,
            WAP_BRANCH_TAG: namespace,
            WAP_REF_TAG: namespace,
            WAP_PROJECT_ID_TAG: "project",
            WAP_ATTEMPT_TAG: "1",
        },
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    _snapshot_strategy(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_snapshot_promotion_catalog", lambda: catalog
    )

    context = MagicMock(instance=instance, cursor=None)
    wap_candidate_cleanup_sensor._raw_fn(context)

    assert catalog.aborted == []


def test_get_wap_definitions_selects_cleanup_sensor_by_strategy(monkeypatch) -> None:
    _snapshot_strategy(monkeypatch, strategy="snapshot")
    snapshot_defs = get_wap_definitions()
    assert wap_candidate_cleanup_sensor in snapshot_defs.sensors

    _snapshot_strategy(monkeypatch, strategy="branch")
    branch_defs = get_wap_definitions()
    assert wap_branch_cleanup_sensor in branch_defs.sensors
