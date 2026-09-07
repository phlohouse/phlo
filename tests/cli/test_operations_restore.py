"""Tests for the guarded restore CLI (Plan 012, ADR 0049 §4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from phlo.capabilities.continuity import (
    BACKUP_PROVIDER_ORDER,
    BackupArtifact,
    BackupContributorResult,
    BackupContributorState,
    RestoreStepResult,
    sha256_file,
)
from phlo.cli.commands.operations import restore_group
from phlo.operations.backup import create_backup_set
from phlo.operations.journal import InMemoryOperationJournalStore


class RestoreStub:
    def __init__(self, name: str) -> None:
        self.name = name
        self.reconcile_ok = True

    def contribute(self, destination: Any, operation_id: str) -> Any:
        destination = destination / f"{self.name}.bin"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.name.encode())
        return BackupContributorResult(
            provider=self.name,
            state=BackupContributorState.SUCCEEDED,
            artifacts=(
                BackupArtifact(
                    provider=self.name,
                    name=f"{self.name}.bin",
                    relative_path=f"{self.name}/{self.name}.bin",
                    size_bytes=destination.stat().st_size,
                    sha256=sha256_file(destination),
                    metadata={},
                ),
            ),
            operation_id=operation_id,
        )

    def restore(self, target: Any, artifacts: Any, plan_token: str, backup_set_dir: str) -> Any:
        return RestoreStepResult.ok(self.name, evidence={"plan_token": plan_token})

    def reconcile(self, target: Any, artifacts: Any, plan_token: str, backup_set_dir: str) -> dict:
        return {"ok": self.reconcile_ok, "reason": "" if self.reconcile_ok else "reconcile_failed"}


@pytest.fixture()
def stubbed(monkeypatch):
    providers = {name: RestoreStub(name) for name in BACKUP_PROVIDER_ORDER}
    monkeypatch.setattr(
        "phlo.operations.backup.default_backup_contributors",
        lambda: [(name, providers[name]) for name in BACKUP_PROVIDER_ORDER],
    )
    return providers


def _make_set(tmp_path: Path) -> Path:
    result = create_backup_set(
        target=tmp_path / "backup",
        contributors=[(name, RestoreStub(name)) for name in BACKUP_PROVIDER_ORDER],
        journal=InMemoryOperationJournalStore(),
        deployment_id="deploy-source",
        versions={"phlo": "0.1"},
    )
    assert result.accepted
    return Path(result.target) / result.set_id


def _invoke(args: list[str], journal_dir: Path | None = None) -> Any:
    return CliRunner().invoke(
        restore_group,
        args if "--format" in args or "--json" in args else [*args, "--format", "json"],
        env={"PHLO_OPERATIONS_JOURNAL_DIR": str(journal_dir)} if journal_dir else {},
    )


def test_plan_apply_round_trip(stubbed, tmp_path) -> None:
    journal_dir = tmp_path / "journal"
    set_dir = _make_set(tmp_path)
    target = tmp_path / "target"

    plan_result = _invoke(
        ["plan", "--backup-set", str(set_dir), "--target", str(target), "--format", "json"]
    )
    assert plan_result.exit_code == 0, plan_result.output
    plan = json.loads(plan_result.output)
    assert plan["target"]["target_id"] == str(target.resolve())
    assert not target.exists()

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    apply_result = _invoke(
        [
            "apply",
            "--plan",
            str(plan_path),
            "--confirmation-token",
            plan["plan_token"],
            "--fixture-substrate",
        ],
        journal_dir=journal_dir,
    )
    assert apply_result.exit_code == 0, apply_result.output
    payload = json.loads(apply_result.output)
    assert payload["accepted"] is True
    assert payload["reconciliation"]["ok"] is True
    assert payload["operational"] is False
    assert payload["substrate"] == "fixture"


def test_apply_requires_fixture_substrate(stubbed, tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan_result = _invoke(["plan", "--backup-set", str(set_dir), "--target", str(tmp_path / "t")])
    plan = json.loads(plan_result.output)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    apply_result = _invoke(
        ["apply", "--plan", str(plan_path), "--confirmation-token", plan["plan_token"]],
        journal_dir=tmp_path / "journal",
    )
    assert apply_result.exit_code == 1
    assert "fixture substrate" in apply_result.output


def test_apply_fails_when_reconciliation_is_corrupt(stubbed, tmp_path) -> None:
    stubbed["minio"].reconcile_ok = False
    set_dir = _make_set(tmp_path)
    target = tmp_path / "target"
    plan_result = _invoke(["plan", "--backup-set", str(set_dir), "--target", str(target)])
    plan = json.loads(plan_result.output)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    apply_result = _invoke(
        [
            "apply",
            "--plan",
            str(plan_path),
            "--confirmation-token",
            plan["plan_token"],
            "--fixture-substrate",
        ],
        journal_dir=tmp_path / "journal",
    )
    assert apply_result.exit_code == 1
    assert "reconciliation" in apply_result.output


def test_apply_rejects_mismatched_token(stubbed, tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan_result = _invoke(["plan", "--backup-set", str(set_dir), "--target", str(tmp_path / "t")])
    plan = json.loads(plan_result.output)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    apply_result = _invoke(
        [
            "apply",
            "--plan",
            str(plan_path),
            "--confirmation-token",
            "nope",
            "--fixture-substrate",
        ],
        journal_dir=tmp_path / "journal",
    )
    assert apply_result.exit_code == 1
    assert "token_mismatch" in apply_result.output
