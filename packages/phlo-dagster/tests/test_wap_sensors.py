"""Tests for WAP (Write-Audit-Publish) lifecycle sensors.

Launch manifests immutably bind logical run ids to Dagster run ids via tags and
checksums, failing closed when any binding field is tampered with. Promotion
sensors require every quality check to pass before merging a WAP branch, and
terminal runs are reported but never promoted.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, MagicMock, patch

import dagster as dg
import pytest

from phlo_dagster.wap_sensors import (
    _all_checks_passed,
    _cleanup_owned_wap_branch,
    _quality_evidence,
    _project_identity_for_run,
    _verify_wap_launch_manifest,
    _wap_branch_name,
    wap_auto_promotion_sensor,
    wap_branch_cleanup_sensor,
    write_wap_report,
)
from phlo_dagster.wap_launch import _write_launch_manifest as _write_immutable_launch_manifest
from phlo.hooks import HookBus
from phlo.run_evidence import SQLiteRunEvidenceStore
from phlo.run_evidence.hooks import CoreRunEvidenceHookProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_launch_manifest(
    logical_run_id: str,
    dagster_run_id: str,
    branch: str,
    *,
    project_id: str = "project",
    attempt: int = 1,
    source_hash: str | None = None,
    target_hash_before: str | None = None,
) -> None:
    tags = {
        "phlo/run_id": logical_run_id,
        "phlo/wap_branch": branch,
        "phlo/ref": branch,
        "phlo/project_id": project_id,
        "phlo/attempt": str(attempt),
    }
    checksum = _write_immutable_launch_manifest(
        logical_run_id=logical_run_id,
        dagster_run_id=dagster_run_id,
        branch=branch,
        tags=tags,
        source_hash=source_hash,
        target_hash_before=target_hash_before,
    )
    assert checksum is not None
    write_wap_report(
        logical_run_id,
        status="launched",
        branch=branch,
        dagster_run_id=dagster_run_id,
        launch_tags=tags,
        launch_manifest_checksum=checksum,
        launch_source_hash=source_hash,
        launch_target_hash_before=target_hash_before,
    )


def test_wap_branch_name():
    assert _wap_branch_name("abc123") == "pipeline-run-abc123"


def test_wap_launch_manifest_requires_immutable_project_and_attempt_tags(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-correlation"
    dagster_run_id = "run-correlation"
    branch = _wap_branch_name(logical_run_id)
    _write_launch_manifest(
        logical_run_id,
        dagster_run_id,
        branch,
        project_id="warehouse",
        attempt=2,
    )
    run = SimpleNamespace(
        run_id=dagster_run_id,
        tags={
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": branch,
            "phlo/project_id": "warehouse",
            "phlo/attempt": "2",
        },
    )

    assert _verify_wap_launch_manifest(run, branch) is not None

    run.tags["phlo/project_id"] = "other-project"
    assert _verify_wap_launch_manifest(run, branch) is None

    run.tags["phlo/project_id"] = "warehouse"
    run.tags.pop("phlo/attempt")
    assert _verify_wap_launch_manifest(run, branch) is None


def test_wap_launch_manifest_uses_launch_hashes_after_lifecycle_hashes_advance(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-hash-ownership"
    dagster_run_id = "run-hash-ownership"
    branch = _wap_branch_name(logical_run_id)
    _write_launch_manifest(
        logical_run_id,
        dagster_run_id,
        branch,
        source_hash="launch-h0",
        target_hash_before="main-h0",
    )
    run = SimpleNamespace(
        run_id=dagster_run_id,
        tags={
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": branch,
            "phlo/project_id": "project",
            "phlo/attempt": "1",
        },
    )

    write_wap_report(
        logical_run_id,
        status="promotion_pending",
        source_hash="branch-h1",
        target_hash_before="main-h1",
    )

    assert _verify_wap_launch_manifest(run, branch) is not None


@pytest.mark.parametrize("tamper", ["tag", "dagster_run_id", "launch_hash", "checksum"])
def test_wap_launch_manifest_fails_closed_when_immutable_binding_is_tampered(
    monkeypatch, tmp_path, tamper
):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-tampered-binding"
    dagster_run_id = "run-tampered-binding"
    branch = _wap_branch_name(logical_run_id)
    _write_launch_manifest(
        logical_run_id,
        dagster_run_id,
        branch,
        source_hash="launch-h0",
        target_hash_before="main-h0",
    )
    run = SimpleNamespace(
        run_id=dagster_run_id,
        tags={
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": branch,
            "phlo/project_id": "project",
            "phlo/attempt": "1",
        },
    )
    if tamper == "tag":
        run.tags["phlo/ref"] = "pipeline-run-other"
    elif tamper == "dagster_run_id":
        run.run_id = "different-dagster-run"
    elif tamper == "launch_hash":
        write_wap_report(logical_run_id, launch_source_hash="tampered-hash")
    else:
        write_wap_report(logical_run_id, launch_manifest_checksum="0" * 64)

    assert _verify_wap_launch_manifest(run, branch) is None


def test_wap_launch_manifest_backfills_hashes_for_existing_reports(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-legacy-binding"
    dagster_run_id = "run-legacy-binding"
    branch = _wap_branch_name(logical_run_id)
    _write_launch_manifest(
        logical_run_id,
        dagster_run_id,
        branch,
        source_hash="launch-h0",
        target_hash_before="main-h0",
    )
    write_wap_report(
        logical_run_id,
        status="promotion_pending",
        source_hash="branch-h1",
        target_hash_before="main-h1",
    )
    report_path = tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json"
    report = json.loads(report_path.read_text())
    report.pop("launch_source_hash")
    report.pop("launch_target_hash_before")
    report_path.write_text(json.dumps(report))
    run = SimpleNamespace(
        run_id=dagster_run_id,
        tags={
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": branch,
            "phlo/project_id": "project",
            "phlo/attempt": "1",
        },
    )

    assert _verify_wap_launch_manifest(run, branch) is not None
    migrated = json.loads(report_path.read_text())
    assert migrated["launch_source_hash"] == "launch-h0"
    assert migrated["launch_target_hash_before"] == "main-h0"


def test_wap_sensors_default_to_running() -> None:
    assert wap_auto_promotion_sensor.default_status == dg.DefaultSensorStatus.RUNNING
    assert wap_branch_cleanup_sensor.default_status == dg.DefaultSensorStatus.RUNNING


def test_write_wap_report_updates_run_json(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    write_wap_report("run-1", status="branch_created", branch="pipeline-run-1")
    write_wap_report(
        "run-1",
        status="promoted",
        branch="pipeline-run-1",
        source_hash="source",
        target_hash_before="before",
        target_hash_after="after",
    )

    payload = json.loads((tmp_path / ".phlo" / "wap-reports" / "run-1.json").read_text())
    assert payload["status"] == "promoted"
    assert payload["branch"] == "pipeline-run-1"
    assert payload["target_hash_after"] == "after"
    assert payload["run_id"] == "run-1"
    assert payload["created_at"]


def test_write_wap_report_keeps_identity_fields_and_ignores_write_failure(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    write_wap_report(
        "run-1",
        schema_version="wrong",
        updated_at="wrong",
        status="branch_created",
    )

    payload = json.loads((tmp_path / ".phlo" / "wap-reports" / "run-1.json").read_text())
    assert payload["run_id"] == "run-1"
    assert payload["schema_version"] == "phlo.wap_report.v2"
    assert payload["updated_at"] != "wrong"

    def raise_write_error(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("pathlib.Path.write_text", raise_write_error)
    write_wap_report("run-2", status="branch_created")


# ---------------------------------------------------------------------------
# _all_checks_passed
# ---------------------------------------------------------------------------


class _FakeCheckEvaluation:
    def __init__(
        self,
        passed: bool,
        severity: str | None = None,
        blocking: bool | None = None,
    ):
        self.passed = passed
        if severity is not None:
            self.severity = severity
        if blocking is not None:
            self.blocking = blocking


class _FakeEvent:
    def __init__(self, passed: bool, severity: str | None = None, blocking: bool | None = None):
        self.asset_check_evaluation = _FakeCheckEvaluation(passed, severity, blocking)


class _FakeRecord:
    def __init__(self, passed: bool, severity: str | None = None, blocking: bool | None = None):
        self.event_log_entry = _FakeEvent(passed, severity, blocking)


def test_all_checks_passed_no_events():
    """No check events means nothing failed — returns True."""
    instance = MagicMock()
    instance.get_records_for_run.return_value = MagicMock(records=[])
    assert _all_checks_passed(instance, "run-1") is True


def test_all_checks_passed_one_fails():
    """A single failing check returns False."""
    instance = MagicMock()
    instance.get_records_for_run.return_value = MagicMock(
        records=[
            _FakeRecord(passed=True),
            _FakeRecord(passed=False),
        ]
    )
    assert _all_checks_passed(instance, "run-1") is False


def test_all_checks_passed_warn_failure_is_not_gating():
    """A failed WARN-severity check never blocks WAP promotion."""
    instance = MagicMock()
    instance.get_records_for_run.return_value = MagicMock(
        records=[
            _FakeRecord(passed=True),
            _FakeRecord(passed=False, severity="WARN"),
        ]
    )
    assert _all_checks_passed(instance, "run-1") is True


def test_all_checks_passed_missing_severity_fails_closed():
    """Legacy evaluations without a severity field classify as blocking."""
    instance = MagicMock()
    instance.get_records_for_run.return_value = MagicMock(
        records=[
            _FakeRecord(passed=True),
            _FakeRecord(passed=False),
        ]
    )
    assert _all_checks_passed(instance, "run-1") is False


# ---------------------------------------------------------------------------
# NessieResource.create_branch / merge_branch
# ---------------------------------------------------------------------------


class TestNessieResourceBranchOps:
    """Unit tests for NessieResource create_branch and merge_branch."""

    def _make_resource(self):
        from phlo_nessie.resource import NessieResource

        return NessieResource(base_url="http://localhost:19120")

    @patch("phlo_nessie.resource.requests")
    def test_create_branch_success(self, mock_requests):
        """create_branch returns hash on success."""
        nessie = self._make_resource()

        # get_branch_hash for source
        mock_get = MagicMock()
        mock_get.status_code = 200
        mock_get.json.return_value = {"hash": "abc123"}

        # create_branch POST
        mock_post = MagicMock()
        mock_post.status_code = 200
        mock_post.json.return_value = {"hash": "def456"}

        mock_requests.get.return_value = mock_get
        mock_requests.post.return_value = mock_post

        result = nessie.create_branch("pipeline-run-1", from_ref="main")
        assert result == "def456"
        mock_requests.post.assert_called_once()

    @patch("phlo_nessie.resource.requests")
    def test_create_branch_source_missing(self, mock_requests):
        """create_branch returns None when source ref doesn't exist."""
        nessie = self._make_resource()

        mock_get = MagicMock()
        mock_get.status_code = 404
        mock_get.json.return_value = {}
        mock_requests.get.return_value = mock_get

        result = nessie.create_branch("pipeline-run-1", from_ref="nonexistent")
        assert result is None

    @patch("phlo_nessie.resource.requests")
    def test_merge_branch_success(self, mock_requests):
        """merge_branch returns True on success."""
        nessie = self._make_resource()

        source_get = MagicMock()
        source_get.status_code = 200
        source_get.json.return_value = {"hash": "source-hash"}

        target_get = MagicMock()
        target_get.status_code = 200
        target_get.json.return_value = {"hash": "main-hash"}

        mock_post = MagicMock()
        mock_post.status_code = 200

        mock_requests.get.side_effect = [source_get, target_get]
        mock_requests.post.return_value = mock_post

        result = nessie.merge_branch(source="pipeline-run-1", target="main")
        assert result is True
        merge_url = mock_requests.post.call_args.args[0]
        assert merge_url.endswith("/api/v2/trees/main@main-hash/history/merge")
        assert mock_requests.post.call_args.kwargs["json"]["fromHash"] == "source-hash"
        assert "params" not in mock_requests.post.call_args.kwargs

    @patch("phlo_nessie.resource.requests")
    def test_merge_branch_conflict(self, mock_requests):
        """merge_branch returns False on conflict."""
        nessie = self._make_resource()

        source_get = MagicMock()
        source_get.status_code = 200
        source_get.json.return_value = {"hash": "source-hash"}

        target_get = MagicMock()
        target_get.status_code = 200
        target_get.json.return_value = {"hash": "main-hash"}

        mock_post = MagicMock()
        mock_post.status_code = 409

        mock_requests.get.side_effect = [source_get, target_get]
        mock_requests.post.return_value = mock_post

        result = nessie.merge_branch(source="pipeline-run-1", target="main")
        assert result is False
        merge_url = mock_requests.post.call_args.args[0]
        assert merge_url.endswith("/api/v2/trees/main@main-hash/history/merge")
        assert mock_requests.post.call_args.kwargs["json"]["fromHash"] == "source-hash"
        assert "params" not in mock_requests.post.call_args.kwargs

    @patch("phlo_nessie.resource.requests")
    def test_delete_branch_uses_branch_endpoint(self, mock_requests):
        """delete_branch uses the Nessie branch delete endpoint."""
        nessie = self._make_resource()

        branch_get = MagicMock()
        branch_get.status_code = 200
        branch_get.json.return_value = {"hash": "branch-hash"}

        delete_response = MagicMock()
        delete_response.status_code = 204

        mock_requests.get.return_value = branch_get
        mock_requests.delete.return_value = delete_response

        result = nessie.delete_branch("pipeline-run-1")

        assert result is True
        delete_url = mock_requests.delete.call_args.args[0]
        assert delete_url.endswith("/api/v1/trees/branch/pipeline-run-1")
        assert mock_requests.delete.call_args.kwargs["params"] == {"expectedHash": "branch-hash"}


# ---------------------------------------------------------------------------
# get_wap_definitions
# ---------------------------------------------------------------------------


def test_get_wap_definitions_only_registers_post_run_sensors():
    """Ordinary runs cannot acquire a WAP branch after they have started."""
    from phlo_dagster.wap_sensors import get_wap_definitions

    defs = get_wap_definitions()
    sensors = list(defs.sensors or [])
    sensor_names = {s.name for s in sensors}
    assert sensor_names == {
        "wap_auto_promotion_sensor",
        "wap_branch_cleanup_sensor",
    }


def test_wap_auto_promotion_sensor_uses_updated_after_filter():
    """Promotion sensor should scan by updated timestamp, not creation timestamp."""
    instance = MagicMock()
    instance.get_runs.return_value = []
    context = MagicMock()
    context.instance = instance
    context.cursor = None

    with patch("phlo_dagster.wap_sensors._load_versioned_catalog", return_value=MagicMock()):
        wap_auto_promotion_sensor._raw_fn(context)

    filters = instance.get_runs.call_args.kwargs["filters"]
    assert filters.updated_after is not None
    assert filters.created_after is None
    context.update_cursor.assert_called_once()


def test_wap_successful_promotion_uses_recorded_check_event_identity(monkeypatch, tmp_path):
    """A passing run promotion references its durable aggregate quality result."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    dagster_run_id = "run-promote"
    logical_run_id = "logical-promote"
    branch = _wap_branch_name(logical_run_id)
    _write_launch_manifest(logical_run_id, dagster_run_id, branch, project_id="project-promote")

    check_record = SimpleNamespace(
        storage_id=42,
        event_log_entry=SimpleNamespace(
            asset_check_evaluation=SimpleNamespace(passed=True),
        ),
    )
    instance = MagicMock()
    instance.get_runs.return_value = [
        SimpleNamespace(
            run_id=dagster_run_id,
            tags={
                "phlo/wap_branch": branch,
                "phlo/run_id": logical_run_id,
                "phlo/ref": branch,
                "phlo/project_id": "project-promote",
                "phlo/attempt": "1",
            },
        )
    ]
    instance.get_records_for_run.return_value = SimpleNamespace(records=[check_record])
    catalog = MagicMock()
    catalog.get_branch_hash.side_effect = ["source-before", "target-before", "target-after"]
    catalog.merge_branch.return_value = True
    catalog.delete_branch.return_value = True
    context = MagicMock()
    context.instance = instance
    context.cursor = None
    context.evaluation_time = datetime.now(timezone.utc)

    store = SQLiteRunEvidenceStore(":memory:")
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    monkeypatch.setattr("phlo_dagster.wap_sensors.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors.default_run_evidence_store", lambda: store)
    monkeypatch.setattr("phlo.run_evidence.emit.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    query_catalog_manager = MagicMock()
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    reconciler = MagicMock()
    reconciler_class = MagicMock(return_value=reconciler)
    monkeypatch.setattr("phlo_dagster.wap_sensors.RunReconciler", reconciler_class)

    wap_auto_promotion_sensor._raw_fn(context)

    catalog.merge_branch.assert_called_once_with(source=branch, target="main")
    query_catalog_manager.drop_ref_query_catalog.assert_called_once_with(branch)
    catalog.delete_branch.assert_called_once_with(branch)
    manifest = json.loads(
        (tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json").read_text()
    )
    assert manifest["status"] == "promoted"
    instance.add_run_tags.assert_called_once_with(dagster_run_id, {"phlo/wap_promoted": "true"})
    assert not (tmp_path / ".phlo" / "wap-reports" / f"{dagster_run_id}.json").exists()
    quality_rows = store.list_quality_results("project-promote", logical_run_id, attempt=1)
    quality_id = next(row["quality_result_id"] for row in quality_rows)
    assert quality_id in {row["quality_result_id"] for row in quality_rows}
    assert quality_rows[0]["check_id"] == "wap.aggregate"
    assert any(
        row["merge_outcome"] == "promoted" and row["quality_decision_id"] == quality_id
        for row in store.list_catalog_changes("project-promote", logical_run_id, attempt=1)
    )
    run = store.get_run("project-promote", logical_run_id)
    assert run is not None
    assert run["status"] == "success"
    assert run["finished_at"] is not None
    reconciler.reconcile.assert_called_once_with(
        "project-promote",
        dagster_run_id,
        ANY,
    )


@pytest.mark.parametrize(
    ("dagster_status", "report_status", "failure_reason"),
    [
        (dg.DagsterRunStatus.FAILURE, "failed", "dagster_run_failed"),
        (dg.DagsterRunStatus.CANCELED, "cancelled", "dagster_run_cancelled"),
    ],
)
def test_wap_terminal_run_is_reported_and_not_promoted(
    monkeypatch,
    tmp_path,
    dagster_status,
    report_status,
    failure_reason,
):
    """Failed and cancelled runs retain audit refs until cleanup retention expires."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-failed"
    dagster_run_id = "run-failed"
    branch = _wap_branch_name(logical_run_id)
    _write_launch_manifest(logical_run_id, dagster_run_id, branch)
    run = SimpleNamespace(
        run_id=dagster_run_id,
        status=dagster_status,
        tags={
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": branch,
            "phlo/project_id": "project",
            "phlo/attempt": "1",
        },
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    catalog = MagicMock()
    query_catalog_manager = MagicMock()
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    context = MagicMock(instance=instance, cursor=None)

    wap_auto_promotion_sensor._raw_fn(context)

    report = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json").read_text())
    assert report["status"] == report_status
    assert report["failure_reason"] == failure_reason
    assert report["dagster_run_id"] == dagster_run_id
    catalog.merge_branch.assert_not_called()
    query_catalog_manager.drop_ref_query_catalog.assert_not_called()
    catalog.delete_branch.assert_not_called()


def test_wap_quality_rejection_retains_owned_query_catalog_and_branch(monkeypatch, tmp_path):
    """A rejected quality decision retains the WAP ref for audit."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-rejected"
    branch = _wap_branch_name(run_id)
    _write_launch_manifest(run_id, run_id, branch, project_id="project-rejected")
    run = SimpleNamespace(
        run_id=run_id,
        tags={
            "phlo/wap_branch": branch,
            "phlo/run_id": run_id,
            "phlo/ref": branch,
            "phlo/project_id": "project-rejected",
            "phlo/attempt": "1",
        },
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    instance.get_records_for_run.return_value = SimpleNamespace(records=[_FakeRecord(passed=False)])
    catalog = MagicMock()
    query_catalog_manager = MagicMock()
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._quality_evidence",
        lambda *_args, **_kwargs: ("quality-rejected", {}),
    )
    monkeypatch.setattr("phlo_dagster.wap_sensors._emit_wap_observation", lambda **_kwargs: None)
    context = MagicMock(instance=instance, cursor=None)

    wap_auto_promotion_sensor._raw_fn(context)

    catalog.merge_branch.assert_not_called()
    query_catalog_manager.drop_ref_query_catalog.assert_not_called()
    catalog.delete_branch.assert_not_called()
    payload = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{run_id}.json").read_text())
    assert payload["status"] == "promotion_blocked"
    assert payload["failure_reason"] == "asset_checks_failed"


def test_wap_promotion_rejects_mutated_ref_tag(monkeypatch, tmp_path):
    """A run may promote only the exact branch recorded before GraphQL submission."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-mutated"
    dagster_run_id = "run-mutated"
    branch = _wap_branch_name(logical_run_id)
    _write_launch_manifest(logical_run_id, dagster_run_id, branch)
    run = SimpleNamespace(
        run_id=dagster_run_id,
        tags={
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": "pipeline-run-other",
        },
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    catalog = MagicMock()
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    context = MagicMock(instance=instance, cursor=None)

    wap_auto_promotion_sensor._raw_fn(context)

    catalog.merge_branch.assert_not_called()
    report = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{logical_run_id}.json").read_text())
    assert report["failure_reason"] == "launch_manifest_or_immutable_tags_invalid"


def test_wap_cleanup_keeps_branch_when_query_catalog_cleanup_fails(monkeypatch, tmp_path):
    """A manager failure does not report or perform completed branch cleanup."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-catalog-failure"
    branch = _wap_branch_name(run_id)
    _write_launch_manifest(run_id, run_id, branch, project_id="project-catalog-failure")
    run = SimpleNamespace(
        run_id=run_id,
        tags={
            "phlo/wap_branch": branch,
            "phlo/run_id": run_id,
            "phlo/ref": branch,
            "phlo/project_id": "project-catalog-failure",
            "phlo/attempt": "1",
        },
    )
    check_record = SimpleNamespace(
        storage_id=1,
        event_log_entry=SimpleNamespace(asset_check_evaluation=SimpleNamespace(passed=True)),
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    instance.get_records_for_run.return_value = SimpleNamespace(records=[check_record])
    catalog = MagicMock()
    catalog.get_branch_hash.side_effect = ["source", "target-before", "target-after"]
    catalog.merge_branch.return_value = True
    query_catalog_manager = MagicMock()
    query_catalog_manager.drop_ref_query_catalog.side_effect = RuntimeError("catalog unavailable")
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._quality_evidence",
        lambda *_args, **_kwargs: ("quality-passed", {}),
    )
    monkeypatch.setattr("phlo_dagster.wap_sensors._reconcile_promoted_wap_run", lambda *_args: True)
    monkeypatch.setattr("phlo_dagster.wap_sensors._emit_wap_observation", lambda **_kwargs: None)
    context = MagicMock(instance=instance, cursor=None)

    wap_auto_promotion_sensor._raw_fn(context)

    catalog.merge_branch.assert_called_once_with(source=branch, target="main")
    catalog.delete_branch.assert_not_called()
    payload = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{run_id}.json").read_text())
    assert payload["source_deleted"] is False


def test_wap_cleanup_rejects_unowned_ref_without_calling_either_provider():
    catalog = MagicMock()
    query_catalog_manager = MagicMock()

    assert _cleanup_owned_wap_branch(catalog, "pipeline-legacy", query_catalog_manager) is False

    query_catalog_manager.drop_ref_query_catalog.assert_not_called()
    catalog.delete_branch.assert_not_called()


def test_wap_cleanup_without_query_catalog_manager_keeps_nessie_only_behavior():
    catalog = MagicMock()
    catalog.delete_branch.return_value = True

    assert _cleanup_owned_wap_branch(catalog, "pipeline-run-compatibility", None) is True

    catalog.delete_branch.assert_called_once_with("pipeline-run-compatibility")


def test_wap_quality_evidence_ignores_forged_report_ids_when_checks_unavailable(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-forged-quality"
    write_wap_report(
        run_id,
        status="branch_created",
        quality_result_id="not-a-quality-decision",
        quality_decision_id="also-forged",
        artifact_id="not-a-quality-decision",
    )
    instance = MagicMock()
    instance.get_records_for_run.side_effect = RuntimeError("event log unavailable")

    quality_id, metadata = _quality_evidence(
        run_id, instance, project_id="project-quality", attempt=1
    )

    assert quality_id is None
    assert metadata["quality_evidence"]["status"] == "unavailable"
    assert metadata["quality_evidence"]["identifier_source"] is None


@pytest.mark.parametrize("report", ["[]", '"not-a-report"'])
def test_wap_quality_evidence_ignores_non_mapping_reports(monkeypatch, tmp_path, report):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-non-mapping-quality"
    report_path = tmp_path / ".phlo" / "wap-reports" / f"{run_id}.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(report)
    instance = SimpleNamespace(
        get_records_for_run=lambda *_args, **_kwargs: SimpleNamespace(
            records=[
                SimpleNamespace(
                    storage_id=1,
                    event_log_entry=SimpleNamespace(
                        asset_check_evaluation=SimpleNamespace(passed=False)
                    ),
                )
            ]
        )
    )
    store = SQLiteRunEvidenceStore(":memory:")
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    monkeypatch.setattr("phlo_dagster.wap_sensors.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors.default_run_evidence_store", lambda: store)

    quality_id, metadata = _quality_evidence(
        run_id, instance, project_id="project-quality", attempt=1
    )

    results = store.list_quality_results("project-quality", run_id, attempt=1)
    assert quality_id == results[0]["quality_result_id"]
    assert metadata["quality_evidence"]["status"] == "observed"


def test_wap_quality_evidence_rejects_report_with_wrong_run_id(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-quality"
    report_path = tmp_path / ".phlo" / "wap-reports" / f"{run_id}.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps({"run_id": "different-run"}))
    instance = MagicMock()

    quality_id, metadata = _quality_evidence(
        run_id, instance, project_id="project-quality", attempt=1
    )

    assert quality_id is None
    assert metadata["quality_evidence"]["status"] == "unavailable"
    instance.get_records_for_run.assert_not_called()


def test_wap_quality_evidence_persists_decision_without_a_preexisting_report(monkeypatch, tmp_path):
    """Launches have Dagster check records before the sensor writes its WAP report."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    dagster_run_id = "dagster-run"
    logical_run_id = "logical-run"
    instance = SimpleNamespace(
        get_records_for_run=lambda *_args, **_kwargs: SimpleNamespace(
            records=[
                SimpleNamespace(
                    storage_id=1,
                    event_log_entry=SimpleNamespace(
                        asset_check_evaluation=SimpleNamespace(passed=False)
                    ),
                )
            ]
        )
    )
    store = SQLiteRunEvidenceStore(":memory:")
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    monkeypatch.setattr("phlo_dagster.wap_sensors.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors.default_run_evidence_store", lambda: store)

    quality_id, metadata = _quality_evidence(
        dagster_run_id,
        instance,
        project_id="project-quality",
        attempt=1,
        evidence_run_id=logical_run_id,
    )

    results = store.list_quality_results("project-quality", logical_run_id, attempt=1)
    assert quality_id == results[0]["quality_result_id"]
    assert results[0]["check_id"] == "wap.aggregate"
    assert results[0]["passed"] == 0
    assert metadata["quality_evidence"]["status"] == "observed"
    assert metadata["quality_evidence"]["uri"] is None
    assert metadata["quality_evidence"]["checksum"] is None


def test_wap_merge_failure_preserves_successful_dagster_run_status(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-merge-failure"
    branch = _wap_branch_name(run_id)
    _write_launch_manifest(
        run_id,
        run_id,
        branch,
        project_id="project-merge-failure",
        source_hash="launch-h0",
        target_hash_before="main-h0",
    )
    run = SimpleNamespace(
        run_id=run_id,
        tags={
            "phlo/wap_branch": branch,
            "phlo/run_id": run_id,
            "phlo/ref": branch,
            "phlo/project_id": "project-merge-failure",
            "phlo/attempt": "1",
        },
    )
    check_record = SimpleNamespace(
        storage_id=91,
        event_log_entry=SimpleNamespace(
            asset_check_evaluation=SimpleNamespace(passed=True),
        ),
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    instance.get_records_for_run.return_value = SimpleNamespace(records=[check_record])
    catalog = MagicMock()
    catalog.get_branch_hash.side_effect = [
        "branch-h1",
        "main-h1",
        "branch-h1",
        "main-h1",
    ]
    catalog.merge_branch.return_value = False
    store = SQLiteRunEvidenceStore(":memory:")
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    monkeypatch.setattr("phlo_dagster.wap_sensors.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors.default_run_evidence_store", lambda: store)
    monkeypatch.setattr("phlo.run_evidence.emit.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    context = MagicMock(instance=instance, cursor=None, evaluation_time=datetime.now(timezone.utc))

    wap_auto_promotion_sensor._raw_fn(context)
    wap_auto_promotion_sensor._raw_fn(context)

    run_row = store.get_run("project-merge-failure", run_id)
    assert run_row is not None
    assert run_row["status"] == "success"
    assert run_row["finished_at"] is not None
    assert (
        store.list_catalog_changes("project-merge-failure", run_id, attempt=1)[0]["merge_outcome"]
        == "failed"
    )
    assert catalog.merge_branch.call_count == 2
    report = json.loads((tmp_path / ".phlo" / "wap-reports" / f"{run_id}.json").read_text())
    assert report["status"] == "promotion_failed"
    assert report["source_hash"] == "branch-h1"
    assert report["launch_source_hash"] == "launch-h0"


@pytest.mark.parametrize(
    ("tags", "configured", "expected_project", "expected_error"),
    [
        ({}, "configured-project", "configured-project", None),
        (
            {"phlo/project_id": "configured-project"},
            "configured-project",
            "configured-project",
            None,
        ),
        ({"phlo/project_id": "tag-project"}, "configured-project", None, "project_conflict"),
        ({}, None, None, "project_missing"),
    ],
)
def test_wap_project_identity_requires_authoritative_agreement(
    monkeypatch, tags, configured, expected_project, expected_error
):
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors.get_settings",
        lambda: SimpleNamespace(phlo_project=configured),
    )
    run = SimpleNamespace(run_id="run-project", tags=tags)

    identity = _project_identity_for_run(run)

    assert identity.project_id == expected_project
    assert identity.error == expected_error


def test_wap_cleanup_uses_authoritative_terminated_run_status(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-cleanup"
    branch_name = _wap_branch_name(logical_run_id)
    branch = SimpleNamespace(
        name=branch_name,
        created_at=datetime.now(timezone.utc) - timedelta(hours=25),
        hash="branch-hash",
    )
    dagster_run = SimpleNamespace(
        run_id="run-cleanup",
        status=dg.DagsterRunStatus.SUCCESS,
        tags={
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch_name,
            "phlo/project_id": "project-cleanup",
            "phlo/attempt": "2",
        },
    )
    _write_launch_manifest(logical_run_id, dagster_run.run_id, branch_name)
    instance = MagicMock()
    instance.get_runs.return_value = [dagster_run]
    catalog = MagicMock()
    catalog.list_branches.return_value = [branch]
    catalog.delete_branch.return_value = True
    query_catalog_manager = MagicMock()
    observations: list[dict[str, Any]] = []
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._emit_wap_observation",
        lambda **kwargs: observations.append(kwargs),
    )
    context = MagicMock(instance=instance)

    wap_branch_cleanup_sensor._raw_fn(context)

    instance.get_runs.assert_called_once_with(
        filters=dg.RunsFilter(tags={"phlo/run_id": logical_run_id})
    )
    query_catalog_manager.drop_ref_query_catalog.assert_called_once_with(branch_name)
    catalog.delete_branch.assert_called_once_with(branch_name)
    assert observations[0]["run_status"] == "success"
    assert observations[0]["run"].tags["phlo/project_id"] == "project-cleanup"


def test_wap_cleanup_records_uncorrelated_gap_without_authoritative_status(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    branch_name = _wap_branch_name("run-cleanup-unknown")
    branch = SimpleNamespace(
        name=branch_name,
        created_at=datetime.now(timezone.utc) - timedelta(hours=25),
        hash="branch-hash",
    )
    instance = MagicMock()
    instance.get_runs.return_value = []
    catalog = MagicMock()
    catalog.list_branches.return_value = [branch]
    catalog.delete_branch.return_value = True
    gaps: list[dict[str, object]] = []
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._record_uncorrelated_gap",
        lambda *args, **kwargs: gaps.append(kwargs),
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._emit_wap_observation",
        lambda **kwargs: pytest.fail("unknown cleanup must not emit correlated evidence"),
    )
    context = MagicMock(instance=instance)

    wap_branch_cleanup_sensor._raw_fn(context)

    assert gaps[0]["missing"] == ["run_status"]
    assert gaps[0]["reason"] == "cleanup_no_exact_tagged_run"


# ---------------------------------------------------------------------------
# Logical-run-tag correlation cleanup sensor tests (issue #625)
# ---------------------------------------------------------------------------


def _cleanup_branch(logical_run_id: str, *, hours_old: int = 25) -> SimpleNamespace:
    return SimpleNamespace(
        name=_wap_branch_name(logical_run_id),
        created_at=datetime.now(timezone.utc) - timedelta(hours=hours_old),
        hash="branch-hash",
    )


def _cleanup_run(
    logical_run_id: str,
    physical_run_id: str,
    branch_name: str,
    *,
    status: object = dg.DagsterRunStatus.SUCCESS,
    project_id: str = "project-cleanup",
    attempt: str = "1",
    extra_tags: dict[str, str] | None = None,
) -> SimpleNamespace:
    tags = {
        "phlo/run_id": logical_run_id,
        "phlo/wap_branch": branch_name,
        "phlo/project_id": project_id,
        "phlo/attempt": attempt,
    }
    if extra_tags:
        tags.update(extra_tags)
    return SimpleNamespace(run_id=physical_run_id, status=status, tags=tags)


def _patch_cleanup_sensor(monkeypatch, catalog, query_catalog_manager, instance):
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    monkeypatch.setattr("phlo_dagster.wap_sensors._emit_wap_observation", lambda **_k: None)
    return MagicMock(instance=instance)


def test_wap_cleanup_deletes_branch_when_exact_tagged_run_is_terminal(monkeypatch, tmp_path):
    """AC 1: distinct logical/physical IDs, terminal run → branch deleted."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-1"
    physical_run_id = "dagster-abc"
    branch_name = _wap_branch_name(logical_run_id)
    branch = _cleanup_branch(logical_run_id)
    run = _cleanup_run(logical_run_id, physical_run_id, branch_name)
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    catalog = MagicMock()
    catalog.list_branches.return_value = [branch]
    catalog.delete_branch.return_value = True
    query_catalog_manager = MagicMock()
    context = _patch_cleanup_sensor(monkeypatch, catalog, query_catalog_manager, instance)

    wap_branch_cleanup_sensor._raw_fn(context)

    instance.get_runs.assert_called_once_with(
        filters=dg.RunsFilter(tags={"phlo/run_id": logical_run_id})
    )
    query_catalog_manager.drop_ref_query_catalog.assert_called_once_with(branch_name)
    catalog.delete_branch.assert_called_once_with(branch_name)


def test_wap_cleanup_retains_branch_while_exact_tagged_run_is_active(monkeypatch, tmp_path):
    """AC 2: the exact tagged run exists but is not terminal → retain + gap."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-active"
    physical_run_id = "dagster-running"
    branch_name = _wap_branch_name(logical_run_id)
    branch = _cleanup_branch(logical_run_id)
    run = _cleanup_run(
        logical_run_id,
        physical_run_id,
        branch_name,
        status=dg.DagsterRunStatus.STARTED,
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    catalog = MagicMock()
    catalog.list_branches.return_value = [branch]
    query_catalog_manager = MagicMock()
    gaps: list[dict[str, object]] = []
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._record_uncorrelated_gap",
        lambda *a, **kw: gaps.append(kw),
    )
    context = MagicMock(instance=instance)

    wap_branch_cleanup_sensor._raw_fn(context)

    query_catalog_manager.drop_ref_query_catalog.assert_not_called()
    catalog.delete_branch.assert_not_called()
    assert gaps[0]["reason"] == "cleanup_run_active"
    assert gaps[0]["missing"] == ["run_status"]


def test_wap_cleanup_retains_prefixed_branch_when_run_lacks_exact_wap_branch_tag(
    monkeypatch, tmp_path
):
    """AC 3: a terminal run's physical ID matches the suffix but the WAP branch
    tag does not match the candidate branch → no exact match → retain + gap."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-mismatch"
    branch = _cleanup_branch(logical_run_id)
    # Run carries the logical ID tag but points at a *different* WAP branch.
    run = SimpleNamespace(
        run_id="dagster-physical",
        status=dg.DagsterRunStatus.SUCCESS,
        tags={
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": "pipeline-run-some-other-branch",
            "phlo/project_id": "project-cleanup",
            "phlo/attempt": "1",
        },
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    catalog = MagicMock()
    catalog.list_branches.return_value = [branch]
    query_catalog_manager = MagicMock()
    gaps: list[dict[str, object]] = []
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._record_uncorrelated_gap",
        lambda *a, **kw: gaps.append(kw),
    )
    context = MagicMock(instance=instance)

    wap_branch_cleanup_sensor._raw_fn(context)

    query_catalog_manager.drop_ref_query_catalog.assert_not_called()
    catalog.delete_branch.assert_not_called()
    assert gaps[0]["reason"] == "cleanup_no_exact_tagged_run"


def test_wap_cleanup_retains_branch_when_matching_runs_disagree_on_correlation(
    monkeypatch, tmp_path
):
    """AC 4: two exact-tagged runs disagree on project → retain + conflict gap."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-conflict"
    branch_name = _wap_branch_name(logical_run_id)
    branch = _cleanup_branch(logical_run_id)
    run_a = _cleanup_run(logical_run_id, "dagster-a", branch_name, project_id="project-a")
    run_b = _cleanup_run(logical_run_id, "dagster-b", branch_name, project_id="project-b")
    instance = MagicMock()
    instance.get_runs.return_value = [run_a, run_b]
    catalog = MagicMock()
    catalog.list_branches.return_value = [branch]
    query_catalog_manager = MagicMock()
    gaps: list[dict[str, object]] = []
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._record_uncorrelated_gap",
        lambda *a, **kw: gaps.append(kw),
    )
    context = MagicMock(instance=instance)

    wap_branch_cleanup_sensor._raw_fn(context)

    query_catalog_manager.drop_ref_query_catalog.assert_not_called()
    catalog.delete_branch.assert_not_called()
    assert gaps[0]["reason"] == "cleanup_correlation_conflict"


def test_wap_cleanup_deletes_query_catalog_before_branch(monkeypatch, tmp_path):
    """AC 5: query-catalog cleanup completes before branch deletion."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-order"
    physical_run_id = "dagster-order"
    branch_name = _wap_branch_name(logical_run_id)
    branch = _cleanup_branch(logical_run_id)
    run = _cleanup_run(logical_run_id, physical_run_id, branch_name)
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    catalog = MagicMock()
    catalog.list_branches.return_value = [branch]
    catalog.delete_branch.return_value = True
    query_catalog_manager = MagicMock()
    call_order: list[str] = []
    query_catalog_manager.drop_ref_query_catalog.side_effect = lambda *_a, **_k: call_order.append(
        "query_catalog"
    )
    catalog.delete_branch.side_effect = lambda *_a, **_k: call_order.append("branch")
    context = _patch_cleanup_sensor(monkeypatch, catalog, query_catalog_manager, instance)

    wap_branch_cleanup_sensor._raw_fn(context)

    query_catalog_manager.drop_ref_query_catalog.assert_called_once_with(branch_name)
    catalog.delete_branch.assert_called_once_with(branch_name)
    assert call_order == ["query_catalog", "branch"]


def test_wap_cleanup_retains_ambiguous_tagged_runs_and_emits_gap(monkeypatch, tmp_path):
    """AC 6: multiple exact-tagged terminal runs that agree on correlation are
    still ambiguous → retain + gap."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    logical_run_id = "logical-ambiguous"
    branch_name = _wap_branch_name(logical_run_id)
    branch = _cleanup_branch(logical_run_id)
    run_a = _cleanup_run(logical_run_id, "dagster-a", branch_name)
    run_b = _cleanup_run(logical_run_id, "dagster-b", branch_name)
    instance = MagicMock()
    instance.get_runs.return_value = [run_a, run_b]
    catalog = MagicMock()
    catalog.list_branches.return_value = [branch]
    query_catalog_manager = MagicMock()
    gaps: list[dict[str, object]] = []
    monkeypatch.setattr("phlo_dagster.wap_sensors._load_versioned_catalog", lambda: catalog)
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._load_ref_query_catalog_manager",
        lambda: query_catalog_manager,
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors._record_uncorrelated_gap",
        lambda *a, **kw: gaps.append(kw),
    )
    context = MagicMock(instance=instance)

    wap_branch_cleanup_sensor._raw_fn(context)

    query_catalog_manager.drop_ref_query_catalog.assert_not_called()
    catalog.delete_branch.assert_not_called()
    assert gaps[0]["reason"] == "cleanup_ambiguous_tagged_runs"


@pytest.mark.parametrize(
    ("passed", "expected_passed", "expected_failed"),
    [([True, False], 0, ["dagster-quality:2"]), ([True, True], 1, [])],
)
def test_quality_evidence_uses_decision_correct_aggregate(
    monkeypatch, tmp_path, passed, expected_passed, expected_failed
):
    """Mixed and all-pass checks bind to the aggregate decision, never a first check."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-quality-aggregate"
    write_wap_report(run_id, status="branch_created")
    records = [
        SimpleNamespace(
            storage_id=index,
            event_log_entry=SimpleNamespace(
                asset_check_evaluation=SimpleNamespace(passed=check_passed)
            ),
        )
        for index, check_passed in enumerate(passed, start=1)
    ]
    instance = SimpleNamespace(
        get_records_for_run=lambda *_args, **_kwargs: SimpleNamespace(records=records)
    )
    store = SQLiteRunEvidenceStore(":memory:")
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    monkeypatch.setattr("phlo_dagster.wap_sensors.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors.default_run_evidence_store", lambda: store)

    quality_id, metadata = _quality_evidence(
        run_id,
        instance,
        project_id="project-quality",
        attempt=2,
    )

    result = store.list_quality_results("project-quality", run_id, attempt=2)[0]
    assert quality_id == result["quality_result_id"]
    assert result["check_id"] == "wap.aggregate"
    assert result["passed"] == expected_passed
    assert metadata["quality_evidence"]["failed_check_ids"] == expected_failed


def test_persist_aggregate_quality_decision_allows_empty_checks(monkeypatch, tmp_path) -> None:
    """Check-free runs must still produce a durable decision via _quality_evidence.

    Exercises the full _quality_evidence path (report read + check records +
    aggregate persistence) against an empty check list, matching how Sling
    replications reach the promotion sensor.
    """
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-empty-checks"
    write_wap_report(run_id, status="branch_created")

    instance = MagicMock()
    instance.get_records_for_run.return_value = MagicMock(records=[])

    quality_id, metadata = _quality_evidence(
        run_id,
        instance,
        project_id="p",
        attempt=1,
        evidence_run_id=run_id,
    )

    assert quality_id is not None, "empty checks must produce durable evidence"


def test_persist_aggregate_quality_decision_reflects_failures(monkeypatch, tmp_path) -> None:
    """Failed checks produce a durable failed decision via _quality_evidence."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-failed-checks"
    write_wap_report(run_id, status="branch_created")

    records = [
        SimpleNamespace(
            storage_id=storage_id,
            event_log_entry=SimpleNamespace(
                asset_check_evaluation=SimpleNamespace(
                    passed=check_passed,
                    severity="error",
                )
            ),
        )
        for storage_id, check_passed in [(1, True), (2, False)]
    ]

    instance = MagicMock()
    instance.get_records_for_run.return_value = MagicMock(records=records)

    from phlo_dagster.wap_sensors import _quality_check_records, _quality_evidence

    checks = _quality_check_records(instance, run_id)
    assert checks is not None
    assert len(checks) == 2

    quality_id, metadata = _quality_evidence(
        run_id,
        instance,
        project_id="p",
        attempt=1,
        evidence_run_id=run_id,
    )
    assert quality_id is not None


def test_persist_aggregate_quality_decision_warn_only_passes_with_warnings(
    monkeypatch, tmp_path
) -> None:
    """WARN-only failures pass the aggregate as passed_with_warnings."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-warn-only"
    write_wap_report(run_id, status="branch_created")

    records = [
        SimpleNamespace(
            storage_id=storage_id,
            event_log_entry=SimpleNamespace(
                asset_check_evaluation=SimpleNamespace(
                    passed=check_passed,
                    severity=severity,
                    blocking=False,
                )
            ),
        )
        for storage_id, check_passed, severity in [(1, True, "warn"), (2, False, "warn")]
    ]

    instance = MagicMock()
    instance.get_records_for_run.return_value = MagicMock(records=records)

    from phlo_dagster.wap_sensors import _quality_check_records, _quality_evidence

    checks = _quality_check_records(instance, run_id)
    assert checks is not None
    assert checks[1]["severity"] == "warn"
    assert checks[1]["blocking"] is False

    store = SQLiteRunEvidenceStore(":memory:")
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    monkeypatch.setattr("phlo_dagster.wap_sensors.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors.default_run_evidence_store", lambda: store)

    quality_id, metadata = _quality_evidence(
        run_id,
        instance,
        project_id="p",
        attempt=1,
        evidence_run_id=run_id,
    )

    result = store.list_quality_results("p", run_id, attempt=1)[0]
    assert quality_id == result["quality_result_id"]
    assert result["passed"] is True
    assert result["severity"] == "warn"
    evidence = metadata["quality_evidence"]
    assert evidence["decision"] == "passed_with_warnings"
    assert evidence["failed_check_ids"] == []
    assert len(evidence["warned_check_ids"]) == 1


def test_persist_aggregate_quality_decision_mixed_severity_rejects(monkeypatch, tmp_path) -> None:
    """Any ERROR failure rejects the aggregate even alongside WARN failures."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    run_id = "run-mixed-severity"
    write_wap_report(run_id, status="branch_created")

    records = [
        SimpleNamespace(
            storage_id=storage_id,
            event_log_entry=SimpleNamespace(
                asset_check_evaluation=SimpleNamespace(
                    passed=check_passed,
                    severity=severity,
                    blocking=True,
                )
            ),
        )
        for storage_id, check_passed, severity in [
            (1, False, "error"),
            (2, False, "warn"),
            (3, True, "warn"),
        ]
    ]

    instance = MagicMock()
    instance.get_records_for_run.return_value = MagicMock(records=records)

    from phlo_dagster.wap_sensors import _quality_evidence

    store = SQLiteRunEvidenceStore(":memory:")
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    monkeypatch.setattr("phlo_dagster.wap_sensors.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo_dagster.wap_sensors.default_run_evidence_store", lambda: store)

    quality_id, metadata = _quality_evidence(
        run_id,
        instance,
        project_id="p",
        attempt=1,
        evidence_run_id=run_id,
    )

    result = store.list_quality_results("p", run_id, attempt=1)[0]
    assert result["passed"] is False
    assert result["severity"] == "error"
    evidence = metadata["quality_evidence"]
    assert evidence["decision"] == "rejected"
    assert len(evidence["failed_check_ids"]) == 1
    assert len(evidence["warned_check_ids"]) == 1
