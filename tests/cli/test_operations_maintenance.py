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
            "thresholds": {"target_size_mb": 512},
        }
    )


@pytest.fixture()
def provider(monkeypatch):

    executor = SimpleNamespace()
    executor.plan = lambda table_name, ref: {
        "operation": "compact",
        "table_name": table_name,
        "ref": ref,
        "plan_token": "plan-tok-1",
        "thresholds": {"target_size_mb": 512},
    }
    execute_result = {"accepted": True, "status": "succeeded", "operation": "compact"}
    executor.execute = lambda **_kwargs: execute_result
    monkeypatch.setattr(
        "phlo.capabilities.resolve_capability",
        lambda _kind, _name=None: SimpleNamespace(name="iceberg", provider=executor),
    )
    return executor


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


def test_plan_fails_without_executor(monkeypatch) -> None:
    monkeypatch.setattr("phlo.capabilities.resolve_capability", lambda _kind, _name=None: None)
    result = _invoke(["plan", "--operation", "compact", "--table", "x"])
    assert result.exit_code != 0
    assert "no maintenance executor" in result.output


def test_apply_succeeds_with_matching_token(provider, tmp_path) -> None:
    p = Path("plan.json")
    p.write_text(_plan_json())
    result = _invoke(
        ["apply", "--plan", str(p), "--confirmation-token", "plan-tok-1"],
        journal_dir=tmp_path / "journal",
    )
    assert result.exit_code == 0


def test_apply_with_mismatched_token_fails(provider, tmp_path) -> None:
    p = Path("plan.json")
    p.write_text(_plan_json(token="plan-tok-A"))
    result = _invoke(
        ["apply", "--plan", str(p), "--confirmation-token", "plan-tok-B"],
        journal_dir=tmp_path / "journal",
    )
    assert result.exit_code != 0


def test_apply_fails_closed_without_a_durable_journal(provider, tmp_path) -> None:
    p = Path("plan.json")
    p.write_text(_plan_json())
    result = _invoke(["apply", "--plan", str(p), "--confirmation-token", "plan-tok-1"])
    assert result.exit_code != 0
    assert "PHLO_OPERATIONS_JOURNAL_DIR" in result.output


def test_apply_rejects_orphan_deletion(provider, tmp_path) -> None:
    p = Path("orphan-plan.json")
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
    provider.execute = lambda **kwargs: evidence
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

    provider.plan = lambda **kwargs: MaintenanceOperationResult(
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
