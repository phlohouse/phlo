"""Tests for restore planning/apply coordination (Plan 012 Steps 1-3)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from phlo.capabilities.continuity import (
    RESTORE_PROVIDER_ORDER,
    BackupContributorState,
    RestoreTarget,
)
from phlo.operations.backup import create_backup_set
from phlo.operations.journal import (
    InMemoryOperationJournalStore,
    claim_operation,
)
from phlo.operations.restore import (
    RestoreError,
    plan_restore,
    restore_apply,
)


def _providers() -> dict[str, Any]:
    from phlo_iceberg.continuity import IcebergBackupContributor
    from phlo_minio.continuity import MinioBackupContributor
    from phlo_nessie.continuity import NessieBackupContributor
    from phlo_postgres.continuity import PostgresBackupContributor

    objects = {
        ("lake", "warehouse/db/t.parquet"): b"parquet-bytes",
        ("evidence", "runs/1.json"): b'{"ok": true}',
    }
    buckets = ["evidence", "lake"]

    def mc(args: list[str]) -> str:
        if args[:2] == ["ls", "--json"]:
            return "\n".join(json.dumps({"key": f"{bucket}/"}) for bucket in buckets)
        if args[:1] == ["cat"]:
            target = args[1].removeprefix("local/")
            bucket, key = target.split("/", 1)
            return objects[(bucket, key)].decode("utf-8")
        bucket = args[3].removeprefix("local/")
        return "\n".join(
            json.dumps({"key": key})
            for (obj_bucket, key) in sorted(objects)
            if obj_bucket == bucket
        )

    return {
        "postgres": PostgresBackupContributor(dump_runner=lambda: "CREATE TABLE t (id int);"),
        "nessie": NessieBackupContributor(
            client=SimpleNamespace(
                list_branches=lambda: [SimpleNamespace(name="main", hash="abc123")]
            )
        ),
        "minio": MinioBackupContributor(mc_runner=mc),
        "iceberg": IcebergBackupContributor(
            inventory_fn=lambda: [{"table_name": "lake.t", "snapshot_id": 1}]
        ),
    }


def _make_set(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    providers = _providers()
    journal = InMemoryOperationJournalStore()
    contributors = [(name, providers[name]) for name in ("postgres", "nessie", "minio", "iceberg")]
    result = create_backup_set(
        target=tmp_path / "backup",
        contributors=contributors,
        journal=journal,
        deployment_id="deploy-source",
        versions={"phlo": "0.1"},
    )
    assert result.accepted
    set_dir = Path(result.target) / result.set_id
    return set_dir, providers


def _plan(set_dir: Path, tmp_path: Path, target_name: str = "target"):
    target = RestoreTarget.of(tmp_path / target_name)
    return plan_restore(backup_set_dir=set_dir, target=target)


def _wrap_calls(provider: Any) -> dict[str, int]:
    original_restore = provider.restore
    state = {"calls": 0}

    def tracker(*_args: Any, **_kwargs: Any) -> Any:
        state["calls"] += 1
        return original_restore(*_args, **_kwargs)

    provider.restore = tracker
    return state


# --- planning --------------------------------------------------------------


def test_plan_is_mutation_free_and_bound(tmp_path) -> None:
    set_dir, _ = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)

    # target never created by planning
    assert not (tmp_path / "target").exists()
    assert plan.set_digest
    assert plan.target.target_id == str((tmp_path / "target").resolve())
    assert tuple(plan.provider_order) == RESTORE_PROVIDER_ORDER
    assert plan.backup_set_id


def test_plan_rejects_unverified_set(tmp_path) -> None:
    set_dir, _ = _make_set(tmp_path)
    manifest = json.loads((set_dir / "manifest.json").read_text(encoding="utf-8"))
    (set_dir / manifest["artifacts"][0]["relative_path"]).unlink()
    with pytest.raises(RestoreError, match="unverified_backup_set"):
        _plan(set_dir, tmp_path)


def test_plan_rejects_source_as_target(tmp_path) -> None:
    set_dir, _ = _make_set(tmp_path)
    with pytest.raises(RestoreError, match="source_as_target"):
        plan_restore(backup_set_dir=set_dir, target=RestoreTarget.of(set_dir))


def test_plan_rejects_nonempty_target(tmp_path) -> None:
    set_dir, _ = _make_set(tmp_path)
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "anything").write_text("x")
    with pytest.raises(RestoreError, match="target_not_empty"):
        plan_restore(backup_set_dir=set_dir, target=RestoreTarget.of(target))


# --- apply ------------------------------------------------------------------


def test_apply_succeeds_and_reconciles(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    journal = InMemoryOperationJournalStore()
    before = (set_dir / "manifest.json").read_bytes()

    result = restore_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=journal,
    )

    assert result.accepted is True
    assert result.state == "succeeded"
    assert all(step.state is BackupContributorState.SUCCEEDED for step in result.steps)
    assert result.reconciliation is not None and result.reconciliation.ok is True
    target_root = Path(plan.target.location)
    assert (target_root / "postgres" / "restored.sql").is_file()
    assert (target_root / "nessie" / "catalog.json").is_file()
    assert (target_root / "iceberg" / "inventory.json").is_file()
    # source deployment unchanged
    assert (set_dir / "manifest.json").read_bytes() == before


def test_apply_token_mismatch_does_not_call_providers(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    counters = {name: _wrap_calls(provider) for name, provider in providers.items()}

    with pytest.raises(RestoreError, match="token_mismatch"):
        restore_apply(
            plan=plan,
            confirmation_token="wrong",
            contributors=providers,
            journal=InMemoryOperationJournalStore(),
        )
    assert all(state["calls"] == 0 for state in counters.values())


def test_apply_rejects_tampered_set_between_plan_and_apply(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    counters = {name: _wrap_calls(provider) for name, provider in providers.items()}
    manifest = json.loads((set_dir / "manifest.json").read_text(encoding="utf-8"))
    target_file = set_dir / manifest["artifacts"][0]["relative_path"]
    target_file.write_bytes(b"tampered")

    with pytest.raises(RestoreError):
        restore_apply(
            plan=plan,
            confirmation_token=plan.plan_token,
            contributors=providers,
            journal=InMemoryOperationJournalStore(),
        )
    assert all(state["calls"] == 0 for state in counters.values())


def test_apply_submission_failure_stops_and_is_not_retry_safe(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)

    # force minio (index 1 in restore order) to fail after submission
    def failing_restore(*args: Any, **kwargs: Any) -> Any:
        from phlo.capabilities.continuity import RestoreStepPhase, RestoreStepResult

        return RestoreStepResult.fail_step(
            "minio", RestoreStepPhase.SUBMISSION, "object restore failed", retry_safe=False
        )

    original = providers["minio"].restore
    providers["minio"].restore = failing_restore
    counts = {
        "nessie": _wrap_calls(providers["nessie"]),
        "postgres": _wrap_calls(providers["postgres"]),
    }

    plan = _plan(set_dir, tmp_path)
    result = restore_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=InMemoryOperationJournalStore(),
    )

    assert result.accepted is False
    assert result.reconciliation is None
    failed = next(step for step in result.steps if step.provider == "minio")
    assert failed.state is BackupContributorState.FAILED
    assert failed.phase.value == "submission"
    assert failed.retry_safe is False
    # providers after the failing minio never ran
    assert counts["nessie"]["calls"] == 0
    assert counts["postgres"]["calls"] == 0
    providers["minio"].restore = original


def test_apply_is_exactly_once_with_replay(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    journal = InMemoryOperationJournalStore()
    counts = {name: _wrap_calls(provider) for name, provider in providers.items()}

    first = restore_apply(
        plan=plan, confirmation_token=plan.plan_token, contributors=providers, journal=journal
    )
    assert first.accepted is True
    first_calls = {name: state["calls"] for name, state in counts.items()}

    second = restore_apply(
        plan=plan, confirmation_token=plan.plan_token, contributors=providers, journal=journal
    )
    assert second.accepted is True
    assert {name: state["calls"] for name, state in counts.items()} == first_calls


def test_apply_conflicting_claim_is_rejected(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    plan = _plan(set_dir, tmp_path)
    from phlo.operations import restore as restore_module

    operation_id = restore_module._operation_id(plan)
    journal = InMemoryOperationJournalStore()
    claim_operation(
        journal,
        operation_id=operation_id,
        subject="other",
        action="restore.apply",
        target=plan.target.target_id,
        plan_token=plan.plan_token,
    )
    with pytest.raises(RestoreError, match="conflicting_claim"):
        restore_apply(
            plan=plan,
            confirmation_token=plan.plan_token,
            contributors=providers,
            journal=journal,
        )
