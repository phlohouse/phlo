"""Guarded continuity action API tests.

Proves the HTTP projection of the landed neutral continuity contracts:
one supported operation per landed family, immutable target-bound dry-run
plans, pre-invocation rejection of every guard, exactly-once apply, restart-
safe canonical verification, and unreplayable unknown outcomes — without
expanding provider behavior or importing any provider/CLI code in the adapter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from phlo.capabilities.continuity import (
    BACKUP_PROVIDER_ORDER,
    BackupArtifact,
    BackupContributorResult,
    BackupContributorState,
    RestoreStepPhase,
    RestoreStepResult,
    sha256_file,
)
from phlo.capabilities.registry import clear_capabilities, register_capability
from phlo.capabilities.interfaces import Principal
from phlo.capabilities.specs import MaintenanceExecutorSpec
from phlo.operations.journal import claim_operation, mark_unknown
from phlo.operations.journal_store import FileOperationJournalStore
from phlo.operations.upgrade import (
    SUPPORTED_FROM_VERSION,
    SUPPORTED_TO_VERSION,
    UpgradeStepDef,
    UpgradeStepResult,
)
from phlo_api.api.continuity import JOURNAL_DIR_ENV
from security_test_support import authenticated_client

SET_DIR_NAME = "set"


@pytest.fixture(name="regulated_api_boundary")
def _regulated_api_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run API authorization expectations through an explicit regulated boundary."""
    from phlo.security.adapters import EnforcementResult
    from phlo_api import security_manifest

    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.setattr(security_manifest, "is_regulated", lambda: True)

    def enforce_with_test_backend(*, principal, action, resource, context, **_kwargs):  # noqa: ANN001
        backend = security_manifest.get_authorization_backend()
        if backend is None:
            return EnforcementResult.error(reason_code="backend_unavailable")
        test_role = principal.attributes.get("test_principal")
        decision = backend.explain_decision(
            Principal(
                subject=principal.subject,
                principal_type=principal.principal_type,
                roles=(test_role,) if isinstance(test_role, str) else principal.groups,
                attributes=principal.attributes,
            ),
            action,
            resource,
            context,
        )
        if decision.allowed:
            return EnforcementResult.allow()
        return EnforcementResult.deny(reason_code=decision.reason_code)

    monkeypatch.setattr(security_manifest, "enforce", enforce_with_test_backend)


class FakeContinuityContributor:
    """One fake provider backed by real files, covering backup/restore/upgrade."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.restore_calls = 0

    def contribute(self, destination: Path, operation_id: str) -> BackupContributorResult:
        destination.mkdir(parents=True, exist_ok=True)
        artifact = destination / "state.txt"
        artifact.write_text(f"{self.name}:ok", encoding="utf-8")
        return BackupContributorResult(
            provider=self.name,
            state=BackupContributorState.SUCCEEDED,
            artifacts=(
                BackupArtifact(
                    provider=self.name,
                    name="state",
                    relative_path=f"{self.name}/state.txt",
                    size_bytes=artifact.stat().st_size,
                    sha256=sha256_file(artifact),
                ),
            ),
            operation_id=operation_id,
        )

    def restore(
        self, target: Any, artifacts: Any, plan_token: str, backup_set_dir: Path
    ) -> RestoreStepResult:
        self.restore_calls += 1
        return RestoreStepResult(
            provider=self.name,
            state=BackupContributorState.SUCCEEDED,
            phase=RestoreStepPhase.SUBMISSION,
            retry_safe=True,
        )

    def reconcile(
        self, target: Any, artifacts: Any, plan_token: str, backup_set_dir: Path
    ) -> dict[str, Any]:
        return {"ok": True, "reasons": []}

    def upgrade_step(
        self,
        defn: UpgradeStepDef,
        target: Any,
        from_version: str,
        to_version: str,
        plan_token: str,
    ) -> UpgradeStepResult:
        return UpgradeStepResult.not_applicable(defn)

    def upgrade_reconcile(self, target: Any, to_version: str, plan_token: str) -> dict[str, Any]:
        return {"ok": True, "reasons": []}


@pytest.fixture()
def contributors(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeContinuityContributor]:
    """Route the adapter's contributor resolution to instrumented fakes."""
    from phlo_api.api import continuity

    roster = {name: FakeContinuityContributor(name) for name in BACKUP_PROVIDER_ORDER}
    ordered = [(name, roster[name]) for name in BACKUP_PROVIDER_ORDER]
    monkeypatch.setattr(continuity, "default_backup_contributors", lambda: ordered)
    return roster


@pytest.fixture(autouse=True)
def journal_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "operation-journal"
    monkeypatch.setenv(JOURNAL_DIR_ENV, str(directory))
    return directory


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    return authenticated_client("admin")


def _create_backup(client, tmp_path: Path, key: str) -> tuple[Path, dict[str, Any]]:  # noqa: ANN001
    response = client.post(
        "/api/continuity/apply",
        json={
            "operation": "backup.create",
            "idempotency_key": key,
            "target": str(tmp_path / "backups"),
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["accepted"] is True
    return Path(payload["target"]) / payload["set_id"], payload


def _plan_restore(client, set_dir: Path, target: Path) -> dict[str, Any]:  # noqa: ANN001
    response = client.post(
        "/api/continuity/plan",
        json={
            "operation": "restore.plan",
            "backup_set": str(set_dir),
            "target": str(target),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- inventory --------------------------------------------------------------


def test_inventory_lists_one_supported_operation_per_landed_family(client) -> None:  # noqa: ANN001
    response = client.get("/api/continuity/operations")

    assert response.status_code == 200
    payload = response.json()
    families = {entry["family"] for entry in payload["operations"]}
    assert {"maintenance", "backup", "restore", "upgrade"} <= families
    unsupported = {entry["operation"] for entry in payload["unsupported"]}
    assert "orphan_delete" in unsupported


# --- planning ---------------------------------------------------------------


def test_plan_is_immutable_target_bound_and_mutation_free(
    client,
    contributors,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    set_dir, _ = _create_backup(client, tmp_path, "bk-plan")
    target = tmp_path / "restore-target"

    first = _plan_restore(client, set_dir, target)
    second = _plan_restore(client, set_dir, target)

    # Deterministic token and identical binding; only the per-issuance TTL
    # window (created_at/expires_at) differs between two plan requests.
    assert first["plan_token"] == second["plan_token"]
    assert {
        key: value
        for key, value in first["plan"].items()
        if key not in {"created_at", "expires_at"}
    } == {
        key: value
        for key, value in second["plan"].items()
        if key not in {"created_at", "expires_at"}
    }
    assert first["plan"]["target"]["target_id"] == str(target.resolve())
    assert first["plan"]["set_digest"]
    # Planning is a dry run: the target is never created.
    assert not target.exists()


def test_plan_rejects_unsupported_pair_before_mutation(
    client,
    contributors,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    set_dir, _ = _create_backup(client, tmp_path, "bk-pair")
    response = client.post(
        "/api/continuity/plan",
        json={
            "operation": "upgrade.plan",
            "backup_set": str(set_dir),
            "target": str(tmp_path / "upgrade-target"),
            "from_version": "9.0.0",
            "to_version": "9.0.1",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "unsupported_pair"


def test_plan_rejects_unknown_and_orphan_operations(client) -> None:  # noqa: ANN001
    orphan = client.post(
        "/api/continuity/plan", json={"operation": "orphan_delete", "table": "lake.t"}
    )
    unknown = client.post("/api/continuity/plan", json={"operation": "catalog.rewind"})

    assert orphan.status_code == 400
    assert orphan.json()["detail"]["error"] == "orphan_delete_unsupported"
    assert unknown.status_code == 400
    assert unknown.json()["detail"]["error"] == "unsupported_operation"


# --- guarded apply ----------------------------------------------------------


def test_apply_rejects_missing_permission_before_provider_invocation(
    client,  # noqa: ANN001
    contributors,  # noqa: ANN001
    journal_dir: Path,
    tmp_path: Path,
    regulated_api_boundary,  # noqa: ANN002
) -> None:
    viewer = authenticated_client("viewer")
    response = viewer.post(
        "/api/continuity/apply",
        json={
            "operation": "backup.create",
            "idempotency_key": "bk-denied",
            "target": str(tmp_path / "denied"),
        },
    )

    assert response.status_code == 403
    assert not (tmp_path / "denied").exists()


def test_apply_rejects_wrong_confirmation_before_provider_invocation(
    client,
    contributors,
    journal_dir: Path,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    set_dir, _ = _create_backup(client, tmp_path, "bk-confirm")
    planned = _plan_restore(client, set_dir, tmp_path / "confirm-target")

    response = client.post(
        "/api/continuity/apply",
        json={
            "operation": "restore.apply",
            "idempotency_key": "rs-wrong",
            "plan": planned["plan"],
            "confirmation_token": "not-the-plan-token",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "token_mismatch"
    assert all(contributor.restore_calls == 0 for contributor in contributors.values())


def test_apply_rejects_changed_set_after_planning(
    client,
    contributors,
    journal_dir: Path,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    set_dir, _ = _create_backup(client, tmp_path, "bk-stale")
    planned = _plan_restore(client, set_dir, tmp_path / "stale-target")

    # Change the set after the plan was issued: the plan is now stale.
    artifact = next(set_dir.rglob("state.txt"))
    artifact.write_text("tampered", encoding="utf-8")

    response = client.post(
        "/api/continuity/apply",
        json={
            "operation": "restore.apply",
            "idempotency_key": "rs-stale",
            "plan": planned["plan"],
            "confirmation_token": planned["plan_token"],
        },
    )

    assert response.status_code == 400
    # Reverification fails before any provider invocation: the stale plan can
    # never reach a provider against a changed set.
    assert response.json()["detail"]["error"] == "unverified_backup_set"
    assert all(contributor.restore_calls == 0 for contributor in contributors.values())


def test_apply_is_exactly_once_and_reused_conflicting_keys_are_rejected(
    client,
    contributors,
    journal_dir: Path,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    set_dir, _ = _create_backup(client, tmp_path, "bk-once")
    planned = _plan_restore(client, set_dir, tmp_path / "once-target")
    body = {
        "operation": "restore.apply",
        "idempotency_key": "rs-once",
        "plan": planned["plan"],
        "confirmation_token": planned["plan_token"],
    }

    first = client.post("/api/continuity/apply", json=body)
    second = client.post("/api/continuity/apply", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["operation_id"] == second.json()["operation_id"]
    assert first.json()["state"] == second.json()["state"] == "succeeded"
    total_calls = sum(contributor.restore_calls for contributor in contributors.values())
    assert total_calls == len(BACKUP_PROVIDER_ORDER)  # providers ran exactly once

    # The same key bound to a different plan is a conflicting reuse.
    other = _plan_restore(client, set_dir, tmp_path / "other-target")
    conflict = client.post(
        "/api/continuity/apply",
        json={
            "operation": "restore.apply",
            "idempotency_key": "rs-once",
            "plan": other["plan"],
            "confirmation_token": other["plan_token"],
        },
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "idempotency_key_conflict"


def test_unknown_outcome_blocks_new_key_replay(
    client,
    contributors,
    journal_dir: Path,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    set_dir, _ = _create_backup(client, tmp_path, "bk-unknown")
    planned = _plan_restore(client, set_dir, tmp_path / "unknown-target")
    operation_id = planned["operation_id"]

    # Simulate a submission whose outcome was never observed.
    journal = FileOperationJournalStore(journal_dir)
    claim_operation(
        journal,
        operation_id=operation_id,
        subject="operator",
        action="restore.apply",
        target=planned["plan"]["target"]["target_id"],
        plan_token=planned["plan_token"],
    )
    mark_unknown(journal, operation_id)

    response = client.post(
        "/api/continuity/apply",
        json={
            "operation": "restore.apply",
            "idempotency_key": "rs-unknown-new-key",
            "plan": planned["plan"],
            "confirmation_token": planned["plan_token"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "unknown_outcome_blocks_replay"
    assert all(contributor.restore_calls == 0 for contributor in contributors.values())


def test_apply_fails_closed_without_durable_journal(
    client,
    contributors,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    monkeypatch.delenv(JOURNAL_DIR_ENV, raising=False)
    response = client.post(
        "/api/continuity/apply",
        json={
            "operation": "backup.create",
            "idempotency_key": "bk-nojournal",
            "target": str(tmp_path / "nojournal"),
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "journal_unconfigured"


def test_maintenance_plan_apply_and_unknown_outcome_is_unreplayable(
    client,
    journal_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    executed: list[str] = []

    class FakeExecutor:
        def plan(self, *, table_name: str, ref: str) -> dict[str, Any]:
            return {
                "operation": "compact",
                "table_name": table_name,
                "ref": ref,
                "plan_token": "maintenance-token-1",
            }

        def execute(self, *, table_name: str, ref: str, plan_token: str) -> dict[str, Any]:
            if plan_token == "explode":
                raise RuntimeError("provider died after submission")
            executed.append(f"{table_name}:{ref}:{plan_token}")
            return {"status": "completed", "operation": "compact", "table": table_name}

    def apply_body(key: str, table: str, token: str) -> dict[str, Any]:
        return {
            "operation": "maintenance.apply",
            "idempotency_key": key,
            "plan": {
                "operation": "compact",
                "table_name": table,
                "ref": "main",
                "plan_token": token,
            },
            "confirmation_token": token,
            "table": table,
            "ref": "main",
        }

    register_capability(
        "maintenance_executor",
        MaintenanceExecutorSpec(name="compact", provider=FakeExecutor()),
    )
    try:
        planned = client.post(
            "/api/continuity/plan",
            json={
                "operation": "maintenance.plan",
                "maintenance_operation": "compact",
                "table": "silver.orders",
                "ref": "main",
            },
        )
        assert planned.status_code == 200, planned.text
        plan = planned.json()["plan"]

        applied = client.post(
            "/api/continuity/apply", json=apply_body("mt-1", "silver.orders", plan["plan_token"])
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["status"] == "completed"
        assert executed == ["silver.orders:main:maintenance-token-1"]

        # A provider crash after submission records UNKNOWN; the verification
        # handle shows it and no new key can replay the operation.
        crashing = client.post(
            "/api/continuity/apply", json=apply_body("mt-2", "silver.customers", "explode")
        )
        assert crashing.status_code == 502
        assert crashing.json()["detail"]["error"] == "apply_outcome_unknown"

        operation_id = "compact:silver.customers:main"
        verification = client.get(f"/api/continuity/verifications/{operation_id}")
        assert verification.status_code == 200
        assert verification.json()["state"] == "unknown"
        assert verification.json()["replay_blocked"] is True

        replay = client.post(
            "/api/continuity/apply", json=apply_body("mt-3", "silver.customers", "explode")
        )
        assert replay.status_code == 409
        assert replay.json()["detail"]["error"] == "conflicting_claim"
    finally:
        clear_capabilities("maintenance_executor")


def test_upgrade_plan_and_apply_prove_the_supported_pair(
    client,
    contributors,
    journal_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    # The backup belongs to the source deployment, regardless of the version
    # installed in this test runner. Restore normal discovery for the upgrade.
    with monkeypatch.context() as source_deployment:
        source_deployment.setattr(
            "phlo.operations.backup.get_package_versions",
            lambda: {"phlo": SUPPORTED_FROM_VERSION, "backup_set_schema": "1"},
        )
        set_dir, _ = _create_backup(client, tmp_path, "bk-upgrade")
    target = tmp_path / "upgrade-target"

    planned = client.post(
        "/api/continuity/plan",
        json={
            "operation": "upgrade.plan",
            "backup_set": str(set_dir),
            "target": str(target),
            "from_version": SUPPORTED_FROM_VERSION,
            "to_version": SUPPORTED_TO_VERSION,
        },
    )
    assert planned.status_code == 200, planned.text
    payload = planned.json()
    assert payload["plan"]["from_version"] == SUPPORTED_FROM_VERSION

    applied = client.post(
        "/api/continuity/apply",
        json={
            "operation": "upgrade.apply",
            "idempotency_key": "up-1",
            "plan": payload["plan"],
            "confirmation_token": payload["plan_token"],
        },
    )

    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["accepted"] is True
    assert body["state"] == "succeeded"

    verification = client.get(f"/api/continuity/verifications/{body['operation_id']}")
    assert verification.status_code == 200
    assert verification.json()["state"] == "succeeded"
    assert verification.json()["replay_blocked"] is False


# --- canonical verification -------------------------------------------------


def test_verification_is_canonical_and_restart_safe(
    client,
    contributors,
    journal_dir: Path,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    set_dir, _ = _create_backup(client, tmp_path, "bk-verify")
    planned = _plan_restore(client, set_dir, tmp_path / "verify-target")
    applied = client.post(
        "/api/continuity/apply",
        json={
            "operation": "restore.apply",
            "idempotency_key": "rs-verify",
            "plan": planned["plan"],
            "confirmation_token": planned["plan_token"],
        },
    )
    assert applied.status_code == 200
    operation_id = applied.json()["operation_id"]

    resolved = client.get(f"/api/continuity/verifications/{operation_id}")
    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["operation_id"] == operation_id
    assert payload["state"] == "succeeded"
    assert payload["result"]["accepted"] is True

    # Restart safety: a brand-new durable store instance resolves the same
    # canonical evidence for the same handle.
    reopened = FileOperationJournalStore(journal_dir).read(operation_id)
    assert reopened is not None
    assert reopened.state.value == "succeeded"
    assert json.dumps(reopened.result, sort_keys=True) == json.dumps(
        payload["result"], sort_keys=True
    )

    missing = client.get("/api/continuity/verifications/restore.apply:nowhere:000000")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "unknown_operation"


# --- adapter boundary -------------------------------------------------------


def test_adapter_has_no_provider_cli_or_subprocess_code() -> None:
    from phlo_api.api import continuity

    source = Path(continuity.__file__).read_text(encoding="utf-8")
    forbidden = (
        "phlo_postgres",
        "phlo_nessie",
        "phlo_iceberg",
        "phlo_minio",
        "phlo_trino",
        "phlo_dagster",
        "phlo.cli",
        "subprocess",
        "import click",
        "from click",
    )
    assert not [needle for needle in forbidden if needle in source]
