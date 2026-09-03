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


def _invoke(args: list[str]) -> Any:
    return CliRunner().invoke(maintenance_group, args)


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


def test_apply_succeeds_with_matching_token(provider) -> None:
    p = Path("plan.json")
    p.write_text(_plan_json())
    result = _invoke(["apply", "--plan", str(p), "--confirmation-token", "plan-tok-1"])
    assert result.exit_code == 0


def test_apply_with_mismatched_token_fails(provider) -> None:
    p = Path("plan.json")
    p.write_text(_plan_json(token="plan-tok-A"))
    result = _invoke(["apply", "--plan", str(p), "--confirmation-token", "plan-tok-B"])
    assert result.exit_code != 0


def test_apply_rejects_orphan_deletion(provider) -> None:
    p = Path("orphan-plan.json")
    p.write_text(
        json.dumps(
            {"operation": "orphan_delete", "table_name": "t", "ref": "main", "plan_token": "tok"}
        )
    )
    result = _invoke(["apply", "--plan", str(p), "--confirmation-token", "tok"])
    assert result.exit_code != 0
