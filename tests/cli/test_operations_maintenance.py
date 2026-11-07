"""Tests for the guarded plan-first maintenance CLI (Plan 010)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from phlo.cli.commands.operations import maintenance_group


def _plan_json(
    table: str = "lake.orders", operation: str = "compact", token: str = "plan-tok-1"
) -> str:
    return json.dumps(
        {
            "operation": operation,
            "table_name": table,
            "ref": "main",
            "plan_token": token,
            "before_revision": 7,
            "thresholds": {"target_size_mb": 512},
        }
    )


def _compact_result(**kwargs: Any) -> dict[str, Any]:
    """Fake store compaction: dry-run returns a plan, execute returns a result."""
    if kwargs.get("dry_run"):
        return {
            "operation": "compact",
            "table_name": kwargs["table_name"],
            "ref": kwargs.get("override_ref") or "main",
            "accepted": True,
            "status": "planned",
            "before_revision": 7,
            "plan_token": "plan-tok-1",
            "planned": {"before_snapshot_id": 7},
            "thresholds": {"target_size_mb": 512},
        }
    return {"accepted": True, "status": "succeeded", "operation": "compact"}


@pytest.fixture()
def provider(monkeypatch):
    from phlo_iceberg.plugin import IcebergResourceProvider

    from phlo.capabilities.registry import clear_capabilities, register_capability

    spec = IcebergResourceProvider().get_table_stores()[0]
    store = spec.provider
    monkeypatch.setattr(store, "compact", _compact_result)
    monkeypatch.setattr(
        store,
        "expire_snapshots",
        lambda **kwargs: {
            "accepted": True,
            "status": "noop",
            "operation": "expire_snapshots",
        },
    )
    clear_capabilities("table_store")
    clear_capabilities("maintenance_executor")
    register_capability("table_store", spec)
    monkeypatch.setattr("phlo.operations.maintenance.discover_capabilities", lambda: None)
    yield store
    clear_capabilities("table_store")
    clear_capabilities("maintenance_executor")


def _invoke(args: list[str], journal_dir: Path | None = None) -> Any:
    return CliRunner().invoke(
        maintenance_group,
        args if "--format" in args or "--json" in args else [*args, "--format", "json"],
        env={"PHLO_OPERATIONS_JOURNAL_DIR": str(journal_dir)} if journal_dir else {},
    )


def test_plan_returns_json_without_mutation(provider) -> None:
    result = _invoke(["plan", "--operation", "compact", "--table", "lake.orders"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["plan_token"] == "plan-tok-1"


def test_plan_fails_without_store(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.operations.maintenance.resolve_capability", lambda _kind, _name=None: None
    )
    result = _invoke(["plan", "--operation", "compact", "--table", "x"])
    assert result.exit_code != 0
    assert "no maintenance store" in result.output


@pytest.mark.parametrize(
    ("plan_token", "cli_token", "expect_success"),
    [("plan-tok-1", "plan-tok-1", True), ("plan-tok-A", "plan-tok-B", False)],
    ids=["matching-token", "mismatched-token"],
)
def test_apply_checks_confirmation_token(provider, tmp_path, plan_token, cli_token, expect_success):
    p = tmp_path / "plan.json"
    p.write_text(_plan_json(token=plan_token))
    result = _invoke(
        ["apply", "--plan", str(p), "--confirmation-token", cli_token],
        journal_dir=tmp_path / "journal",
    )
    if expect_success:
        assert result.exit_code == 0, result.output
    else:
        assert result.exit_code != 0
        assert "confirmation token does not match the plan token" in result.output


def test_apply_fails_closed_without_a_durable_journal(provider, tmp_path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(_plan_json())
    result = _invoke(["apply", "--plan", str(p), "--confirmation-token", "plan-tok-1"])
    assert result.exit_code != 0
    assert "PHLO_OPERATIONS_JOURNAL_DIR" in result.output


def test_apply_rejects_orphan_deletion(provider, tmp_path) -> None:
    p = tmp_path / "orphan-plan.json"
    p.write_text(
        json.dumps(
            {"operation": "orphan_delete", "table_name": "t", "ref": "main", "plan_token": "tok"}
        )
    )
    result = _invoke(
        ["apply", "--plan", str(p), "--confirmation-token", "tok"],
        journal_dir=tmp_path / "journal",
    )
    assert result.exit_code != 0
    assert "orphan deletion is unsupported in v1" in result.output


def test_plan_defaults_to_human_summary_and_supports_envelope(provider):
    args = ["plan", "--operation", "compact", "--table", "lake.orders"]
    human = CliRunner().invoke(maintenance_group, args)
    assert human.exit_code == 0, human.output
    assert "Maintenance plan: compact on lake.orders" in human.output
    assert "No changes applied" in human.output
    machine = CliRunner().invoke(maintenance_group, [*args, "--json"])
    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.stdout)
    assert payload["status"] == "planned"
    assert payload["data"]["plan_token"] == "plan-tok-1"


@pytest.mark.parametrize("status", ["blocked", "failed"])
@pytest.mark.parametrize("output_args", [[], ["--json"], ["--format", "json"]])
def test_apply_rejected_provider_result_is_failure(provider, tmp_path, status, output_args):
    from phlo.capabilities.maintenance import MaintenanceOperationResult, MaintenanceOperationState

    evidence = MaintenanceOperationResult(
        operation="compact",
        table_name="lake.orders",
        ref="main",
        dry_run=False,
        status=MaintenanceOperationState(status),
        accepted=False,
        executed=False,
        failure={"reason": "precondition_failed"},
    )
    provider.compact = lambda **kwargs: evidence
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(_plan_json())
    journal_dir = tmp_path / "journal"
    result = CliRunner().invoke(
        maintenance_group,
        ["apply", "--plan", str(plan_path), "--confirmation-token", "plan-tok-1", *output_args],
        env={"PHLO_OPERATIONS_JOURNAL_DIR": str(journal_dir)},
    )
    assert result.exit_code == 1, result.output
    if "--json" in output_args:
        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["reason_code"] == "maintenance_rejected"
        assert payload["data"] == evidence.to_dict()
    elif output_args:
        assert json.loads(result.stdout) == evidence.to_dict()
    else:
        assert status in result.stdout
    entry = json.loads(next(journal_dir.glob("*.json")).read_text())
    assert entry["state"] == "failed"
    assert entry["result"] == evidence.to_dict()


def test_blocked_plan_is_not_reported_as_planned(provider):
    from phlo.capabilities.maintenance import MaintenanceOperationResult, MaintenanceOperationState

    provider.compact = lambda **kwargs: MaintenanceOperationResult(
        operation="compact",
        table_name="lake.orders",
        ref="main",
        dry_run=True,
        status=MaintenanceOperationState.BLOCKED,
        accepted=False,
        executed=False,
        failure={"reason": "active_writer"},
    )
    result = _invoke(["plan", "--operation", "compact", "--table", "lake.orders", "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["data"]["status"] == "blocked"


def test_apply_snapshot_expiry_replays_plan_parameters(provider, tmp_path) -> None:
    """Apply must replay plan-time parameters so the recomputed token matches."""
    captured: dict[str, Any] = {}
    provider.expire_snapshots = lambda **kwargs: (
        captured.update(kwargs)
        or {
            "accepted": True,
            "status": "noop",
            "operation": "expire_snapshots",
        }
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "operation": "snapshot_expiry",
                "table_name": "lake.orders",
                "ref": "main",
                "plan_token": "tok-expiry",
                "before_revision": 42,
                "planned": {
                    "before_snapshot_id": 42,
                    "retention_hours": 720,
                    "retain_last": 3,
                    "catalog": "iceberg",
                },
            }
        )
    )
    result = _invoke(
        ["apply", "--plan", str(plan_path), "--confirmation-token", "tok-expiry"],
        journal_dir=tmp_path / "journal",
    )
    assert result.exit_code == 0, result.output
    assert captured["dry_run"] is False
    assert captured["confirmation_token"] == "tok-expiry"
    assert captured["expected_snapshot_id"] == 42
    assert captured["retention_hours"] == 720
    assert captured["retain_last"] == 3


def test_apply_journals_each_exact_plan_for_the_authorized_principal(provider, tmp_path) -> None:
    """A fresh plan for the same table applies again; the same plan twice is refused."""
    journal_dir = tmp_path / "journal"
    env = {"PHLO_OPERATIONS_JOURNAL_DIR": str(journal_dir), "PHLO_AUTH_SUBJECT": "ops@example.com"}
    plans = {}
    for token in ("plan-tok-1", "plan-tok-2"):
        plans[token] = tmp_path / f"{token}.json"
        plans[token].write_text(_plan_json(token=token))

    def apply(token: str) -> Any:
        return CliRunner().invoke(
            maintenance_group,
            [
                "apply",
                "--plan",
                str(plans[token]),
                "--confirmation-token",
                token,
                "--format",
                "json",
            ],
            env=env,
        )

    first = apply("plan-tok-1")
    assert first.exit_code == 0, first.output
    second = apply("plan-tok-2")
    assert second.exit_code == 0, second.output
    entries = [json.loads(path.read_text()) for path in sorted(journal_dir.glob("*.json"))]
    assert [entry["operation_id"] for entry in entries] == [
        "compact:lake.orders:main:plan-tok-1",
        "compact:lake.orders:main:plan-tok-2",
    ]
    assert {entry["subject"] for entry in entries} == {"ops@example.com"}
    assert {entry["state"] for entry in entries} == {"succeeded"}

    repeat = apply("plan-tok-2")
    assert repeat.exit_code != 0
    assert "conflicting_claim" in repeat.output


@pytest.mark.parametrize("phase", ["preflight", "submission"])
def test_real_iceberg_submission_ambiguity_fences_fresh_plans(
    provider, monkeypatch, tmp_path, phase
):
    from phlo_iceberg.resource import IcebergResource
    from phlo_trino.plugin import TrinoResourceProvider
    from phlo_trino.resource import TrinoResource

    from phlo.capabilities.registry import register_capability
    from phlo.operations.journal_store import FileOperationJournalStore

    resource = IcebergResource(ref="review")
    monkeypatch.setattr(
        resource,
        "_compaction_metadata",
        lambda *_args: (41, {"file_count": 4, "snapshot_count": 2}),
    )
    submissions = []

    def execute(self, sql, **kwargs):
        assert self._resolved_ref() == "review"
        if sql.startswith("SELECT") and phase != "preflight":
            return [(41,)]
        submissions.append(sql)
        raise ConnectionError("reset")

    provider.compact = resource.compact
    register_capability(
        "maintenance_executor", TrinoResourceProvider().get_maintenance_executors()[0]
    )
    monkeypatch.setattr(TrinoResource, "execute", execute)
    journal_dir = tmp_path / "journal"
    plan_path = tmp_path / "plan.json"

    def apply(token):
        plan_path.write_text(
            json.dumps(
                {
                    "operation": "compact",
                    "table_name": "raw.events",
                    "ref": "review",
                    "plan_token": token,
                    "before_revision": 41,
                }
            )
        )
        return _invoke(
            ["apply", "--plan", str(plan_path), "--confirmation-token", token], journal_dir
        )

    first = apply("original")
    assert first.exit_code == 1, first.output
    evidence = json.loads(first.stdout)
    assert evidence["status"] == "failed"
    assert evidence["failure"]["outcome"] == (
        "unknown" if phase == "submission" else "not_submitted"
    )
    entry = FileOperationJournalStore(journal_dir).read("compact:raw.events:review:original")
    assert entry.state == ("unknown" if phase == "submission" else "failed")
    assert entry.result == evidence
    assert apply("original").exit_code == 1
    fresh = apply("fresh")
    assert fresh.exit_code == 1
    if phase == "submission":
        assert "conflicting_claim" in fresh.output
        assert submissions == ['ALTER TABLE "raw"."events" EXECUTE optimize']
    else:
        assert len(submissions) == 2
        assert all(sql.startswith("SELECT") for sql in submissions)


def test_real_iceberg_stale_zero_revision_is_not_dropped(provider, monkeypatch, tmp_path):
    from phlo_iceberg.resource import IcebergResource

    provider.compact = IcebergResource.compact.__get__(provider)
    monkeypatch.setattr(provider, "_compaction_metadata", lambda *_: (0, {"file_count": 4}))
    planned = _invoke(["plan", "--operation", "compact", "--table", "raw.events"])
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    assert plan["plan_token"] == "0"
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(provider, "_compaction_metadata", lambda *_: (1, {"file_count": 4}))
    applied = _invoke(
        ["apply", "--plan", str(path), "--confirmation-token", "0"], tmp_path / "journal"
    )
    assert applied.exit_code == 1, applied.output
    assert json.loads(applied.output)["failure"]["code"] == "concurrent_change_detected"


def test_real_iceberg_expiry_plan_applies_with_nondefault_parameters(
    provider, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from phlo_iceberg.resource import IcebergResource

    snapshot = SimpleNamespace(snapshot_id=42, timestamp_ms=1)
    table = SimpleNamespace(
        snapshots=lambda: [snapshot],
        current_snapshot=lambda: snapshot,
        refs=lambda: {"main": snapshot},
    )
    provider.expire_snapshots = IcebergResource.expire_snapshots.__get__(provider)
    monkeypatch.setattr(
        provider, "get_catalog", lambda **_: SimpleNamespace(load_table=lambda _: table)
    )
    monkeypatch.setattr(
        "phlo_iceberg.resource.get_table_stats", lambda **_: {"current_snapshot_id": 42}
    )
    planned = _invoke(
        [
            "plan",
            "--operation",
            "snapshot_expiry",
            "--table",
            "raw.events",
            "--ref",
            "review",
            "--catalog",
            "review_catalog",
            "--retention-hours",
            "720",
            "--retain-last",
            "3",
            "--max-affected-objects",
            "17",
            "--max-affected-bytes",
            "123456",
        ]
    )
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    assert plan["operation"] == "expire_snapshots"
    assert plan["planned"]["retention_hours"] == 720
    assert plan["planned"]["retain_last"] == 3
    path = tmp_path / "expiry.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    applied = _invoke(
        ["apply", "--plan", str(path), "--confirmation-token", plan["plan_token"]],
        tmp_path / "journal",
    )
    assert applied.exit_code == 0, applied.output
    result = json.loads(applied.output)
    assert result["status"] == "noop"
    assert result["operation_id"] == f"snapshot_expiry:raw.events:review:{plan['plan_token']}"
    for key in (
        "retention_hours",
        "retain_last",
        "catalog",
        "max_affected_objects",
        "max_affected_bytes",
    ):
        assert result["planned"][key] == plan["planned"][key]
