"""Tests for the upgrade contract/registry (Plan 013 Steps 1-4)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from phlo.capabilities.continuity import RestoreTarget
from phlo.operations.backup import create_backup_set
from phlo.operations.journal import InMemoryOperationJournalStore, claim_operation
from phlo.operations.upgrade import (
    ROLLBACK_SAFE_LAST_STEP,
    SUPPORTED_FROM_VERSION,
    SUPPORTED_TO_VERSION,
    UPGRADE_PIPELINE,
    UpgradeError,
    UpgradeStepPhase,
    UpgradeStepState,
    migration_digest,
    plan_upgrade,
    upgrade_apply,
    validate_upgrade_pair,
)


class UpgradeStub:
    """Provider upgrade step that can be forced to fail at a chosen step."""

    def __init__(self, name: str, fail_step: str | None = None) -> None:
        self.name = name
        self.fail_step = fail_step
        self.upgrade_calls = 0
        self.reconcile_calls = 0

    def upgrade_step(self, defn, target, from_v, to_v, plan_token):
        self.upgrade_calls += 1
        from phlo.operations.upgrade import UpgradeStepResult

        if self.fail_step == defn.name:
            return UpgradeStepResult.fail(defn, UpgradeStepPhase.SUBMISSION, "step failed")
        return UpgradeStepResult.ok(defn, {"version": from_v}, {"version": to_v})

    def upgrade_reconcile(self, target, to_version, plan_token):
        self.reconcile_calls += 1
        return {"ok": True, "reason": ""}


def _providers(fail_step: str | None = None) -> dict[str, UpgradeStub]:
    providers: dict[str, UpgradeStub] = {}
    for defn in UPGRADE_PIPELINE:
        providers[defn.owner] = UpgradeStub(defn.owner, fail_step=fail_step)
    return providers


def _make_set(tmp_path: Path) -> Path:
    from phlo_iceberg.continuity import IcebergBackupContributor
    from phlo_minio.continuity import MinioBackupContributor
    from phlo_nessie.continuity import NessieBackupContributor
    from phlo_postgres.continuity import PostgresBackupContributor

    postgres = PostgresBackupContributor(dump_runner=lambda: "CREATE TABLE t (id int);")
    nessie = NessieBackupContributor(
        client=SimpleNamespace(list_branches=lambda: [SimpleNamespace(name="main", hash="abc")])
    )
    minio = MinioBackupContributor(mc_runner=lambda args: "")
    iceberg = IcebergBackupContributor(inventory_fn=list)
    result = create_backup_set(
        target=tmp_path / "backup",
        contributors=[
            ("postgres", postgres),
            ("nessie", nessie),
            ("minio", minio),
            ("iceberg", iceberg),
        ],
        journal=InMemoryOperationJournalStore(),
        deployment_id="deploy-source",
        versions={"phlo": SUPPORTED_FROM_VERSION},
    )
    assert result.accepted
    return Path(result.target) / result.set_id


def _plan(set_dir: Path, tmp_path: Path):
    return plan_upgrade(
        from_version=SUPPORTED_FROM_VERSION,
        to_version=SUPPORTED_TO_VERSION,
        backup_set_dir=set_dir,
        target=RestoreTarget.of(tmp_path / "deploy"),
    )


def test_pair_is_closed_and_rejects_mutable_pairs() -> None:
    validate_upgrade_pair(SUPPORTED_FROM_VERSION, SUPPORTED_TO_VERSION)
    for bad in (
        (SUPPORTED_TO_VERSION, SUPPORTED_FROM_VERSION),  # reverse
        (SUPPORTED_FROM_VERSION, SUPPORTED_FROM_VERSION),  # equal
        ("0.12.0", SUPPORTED_TO_VERSION),  # skipped
        ("0.14.0", "0.16.0-beta"),  # mutable
    ):
        with pytest.raises(UpgradeError, match="unsupported_pair"):
            validate_upgrade_pair(*bad)


def test_migration_digest_is_deterministic() -> None:
    assert migration_digest() == migration_digest()


def test_plan_requires_verified_backup(tmp_path) -> None:
    with pytest.raises(UpgradeError, match="unverified_backup_set"):
        plan_upgrade(
            from_version=SUPPORTED_FROM_VERSION,
            to_version=SUPPORTED_TO_VERSION,
            backup_set_dir=tmp_path / "missing",
            target=RestoreTarget.of(tmp_path / "deploy"),
        )


def test_plan_is_mutation_free_and_bound(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    assert not (tmp_path / "deploy").exists()
    assert plan.from_version == SUPPORTED_FROM_VERSION
    assert plan.to_version == SUPPORTED_TO_VERSION
    assert plan.backup_digest
    assert plan.migration_digest == migration_digest()


def test_apply_succeeds_and_reconciles(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    providers = _providers()
    result = upgrade_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=InMemoryOperationJournalStore(),
    )
    assert result.accepted is True
    assert result.rollback_action is None
    assert result.forward_repair is None
    assert result.reconciliation is not None and result.reconciliation["ok"] is True
    assert all(step.state is UpgradeStepState.SUCCEEDED for step in result.steps)
    assert all(p.upgrade_calls == 1 for p in providers.values())


def test_apply_token_mismatch_makes_zero_calls(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    providers = _providers()
    with pytest.raises(UpgradeError, match="token_mismatch"):
        upgrade_apply(
            plan=plan,
            confirmation_token="no",
            contributors=providers,
            journal=InMemoryOperationJournalStore(),
        )
    assert all(p.upgrade_calls == 0 for p in providers.values())


def test_apply_rejects_tampered_backup(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    providers = _providers()
    import json

    manifest = json.loads((set_dir / "manifest.json").read_text(encoding="utf-8"))
    (set_dir / manifest["artifacts"][0]["relative_path"]).write_bytes(b"tampered")
    with pytest.raises(UpgradeError):
        upgrade_apply(
            plan=plan,
            confirmation_token=plan.plan_token,
            contributors=providers,
            journal=InMemoryOperationJournalStore(),
        )
    assert all(p.upgrade_calls == 0 for p in providers.values())


def test_failure_at_rollback_boundary_issues_restore(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    # postgres.schema is the rollback-safe step; faulting it restores via Plan 012.
    providers = _providers(fail_step="postgres.schema")
    result = upgrade_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=InMemoryOperationJournalStore(),
    )
    assert result.accepted is False
    assert result.rollback_action == "restore"
    assert result.forward_repair is None
    assert result.reconciliation is None


def test_failure_after_rollback_boundary_emits_forward_repair(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    # nessie, iceberg, and minio are irreversible; faulting minio must not
    # claim a rollback (no false downgrade).
    providers = _providers(fail_step="minio.policy")
    result = upgrade_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=InMemoryOperationJournalStore(),
    )
    assert result.accepted is False
    assert result.forward_repair is not None
    assert result.forward_repair["must_not_rollback"] is True
    assert result.rollback_action is None


def test_failure_at_irreversible_step_does_not_claim_rollback(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    providers = _providers(fail_step="nessie.catalog")
    result = upgrade_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=InMemoryOperationJournalStore(),
    )
    assert result.accepted is False
    assert result.rollback_action is None
    assert result.forward_repair is not None


def test_apply_is_exactly_once_with_replay(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    providers = _providers()
    journal = InMemoryOperationJournalStore()

    first = upgrade_apply(
        plan=plan, confirmation_token=plan.plan_token, contributors=providers, journal=journal
    )
    assert first.accepted is True
    calls = {name: p.upgrade_calls for name, p in providers.items()}

    second = upgrade_apply(
        plan=plan, confirmation_token=plan.plan_token, contributors=providers, journal=journal
    )
    assert second.accepted is True
    assert {name: p.upgrade_calls for name, p in providers.items()} == calls


def test_apply_conflicting_claim_is_rejected(tmp_path) -> None:
    set_dir = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    from phlo.operations import upgrade as upgrade_module

    operation_id = upgrade_module._operation_id(plan)
    journal = InMemoryOperationJournalStore()
    claim_operation(
        journal,
        operation_id=operation_id,
        subject="other",
        action="upgrade.apply",
        target=plan.target.target_id,
        plan_token=plan.plan_token,
    )
    with pytest.raises(UpgradeError, match="conflicting_claim"):
        upgrade_apply(
            plan=plan,
            confirmation_token=plan.plan_token,
            contributors=_providers(),
            journal=journal,
        )


def test_pipeline_boundary_step_is_rollback_safe() -> None:
    names = [defn.name for defn in UPGRADE_PIPELINE]
    assert ROLLBACK_SAFE_LAST_STEP in names
