"""Tests for the guarded plan-first maintenance CLI (Plan 010)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
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
    store = SimpleNamespace()
    store.compact = _compact_result
    store.expire_snapshots = lambda **kwargs: {
        "accepted": True,
        "status": "noop",
        "operation": "expire_snapshots",
    }

    def _resolve(kind: str, _name: str | None = None):
        if kind == "table_store":
            return SimpleNamespace(name="iceberg", provider=store)
        return None

    monkeypatch.setattr("phlo.capabilities.resolve_capability", _resolve)
    return store


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
    monkeypatch.setattr("phlo.capabilities.resolve_capability", lambda _kind, _name=None: None)
    result = _invoke(["plan", "--operation", "compact", "--table", "x"])
    assert result.exit_code != 0
    assert "no maintenance store" in result.output


def test_apply_succeeds_with_matching_token(provider, tmp_path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(_plan_json())
    result = _invoke(
        ["apply", "--plan", str(p), "--confirmation-token", "plan-tok-1"],
        journal_dir=tmp_path / "journal",
    )
    assert result.exit_code == 0


def test_apply_with_mismatched_token_fails(provider, tmp_path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(_plan_json(token="plan-tok-A"))
    result = _invoke(
        ["apply", "--plan", str(p), "--confirmation-token", "plan-tok-B"],
        journal_dir=tmp_path / "journal",
    )
    assert result.exit_code != 0


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
