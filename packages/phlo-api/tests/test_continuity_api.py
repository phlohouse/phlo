"""Guarded continuity action API tests.

Proves the HTTP projection of the landed neutral continuity contracts:
one supported operation per landed family, immutable target-bound dry-run
plans, pre-invocation rejection of every guard, exactly-once apply, restart-
safe canonical verification, and unreplayable unknown outcomes — without
expanding provider behavior or importing any provider/CLI code in the adapter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

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
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    return authenticated_client("admin")


@pytest.fixture()
def maintenance_backend(monkeypatch):
    """Shipped Iceberg/Trino registration, with metadata and SQL transport controlled."""
    from phlo_iceberg.plugin import IcebergResourceProvider
    from phlo_iceberg.resource import IcebergResource
    from phlo_trino.plugin import TrinoResourceProvider
    from phlo_trino.resource import TrinoResource

    state = SimpleNamespace(revision=41, expired=False, failure=None, sql=[])
    store_spec = IcebergResourceProvider().get_table_stores()[0]
    engine_spec = TrinoResourceProvider().get_maintenance_executors()[0]
    clear_capabilities("table_store")
    clear_capabilities("maintenance_executor")
    register_capability("table_store", store_spec)
    register_capability("maintenance_executor", engine_spec)
    monkeypatch.setattr("phlo.operations.maintenance.discover_capabilities", lambda: None)

    def snapshots():
        ids = [state.revision] if state.expired else [1, 2, 3, 4, state.revision]
        return [
            SimpleNamespace(
                snapshot_id=value,
                timestamp_ms=int(datetime.now(UTC).timestamp() * 1000)
                if value == state.revision
                else 1,
                summary=SimpleNamespace(
                    operation=SimpleNamespace(value="append"), additional_properties={}
                ),
                manifests=lambda _io: [],
            )
            for value in ids
        ]

    table = SimpleNamespace(
        snapshots=snapshots,
        current_snapshot=lambda: snapshots()[-1],
        refs=lambda: {"main": SimpleNamespace(snapshot_id=state.revision)},
        io=None,
    )
    monkeypatch.setattr(
        IcebergResource,
        "get_catalog",
        lambda self, override_ref=None: SimpleNamespace(load_table=lambda _name: table),
    )

    def stats(**_):
        return {"current_snapshot_id": state.revision, "file_count": 4, "snapshot_count": 5}

    monkeypatch.setattr("phlo_iceberg.resource.get_table_stats", stats)
    monkeypatch.setattr(
        IcebergResource, "_compaction_metadata", lambda self, *_: (state.revision, stats())
    )

    def execute(self, sql, **_kwargs):
        state.sql.append((self._resolved_ref(), sql))
        if sql.startswith("SELECT"):
            if state.failure == "preflight":
                raise ConnectionError("preflight disconnected")
            return [(state.revision,)]
        if state.failure == "submission":
            raise ConnectionError("submission disconnected")
        if "EXECUTE optimize" in sql:
            state.revision += 1
        else:
            state.expired = True
        return []

    monkeypatch.setattr(TrinoResource, "execute", execute)
    yield state
    clear_capabilities("table_store")
    clear_capabilities("maintenance_executor")


def _create_backup(client: TestClient, tmp_path: Path, key: str) -> tuple[Path, dict[str, Any]]:
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


def test_inventory_lists_every_supported_operation_and_family(client: TestClient) -> None:
    response = client.get("/api/continuity/operations")

    assert response.status_code == 200
    payload = response.json()
    operations = payload["operations"]
    assert {entry["operation"] for entry in operations} == {
        "maintenance.plan",
        "maintenance.apply",
        "backup.create",
        "restore.plan",
        "restore.apply",
        "upgrade.plan",
        "upgrade.apply",
    }
    assert {entry["family"] for entry in operations} == {
        "maintenance",
        "backup",
        "restore",
        "upgrade",
    }
    unsupported = {entry["operation"] for entry in payload["unsupported"]}
    assert unsupported == {"orphan_delete"}


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


def _maintenance_plan(client, operation="compact", **options):
    response = client.post(
        "/api/continuity/plan",
        json={
            "operation": "maintenance.plan",
            "maintenance_operation": operation,
            "table": "raw.events",
            **options,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _maintenance_apply(client, plan, key="first", **options):
    return client.post(
        "/api/continuity/apply",
        json={
            "operation": "maintenance.apply",
            "plan": plan["plan"],
            "confirmation_token": plan["plan_token"],
            "idempotency_key": key,
            **options,
        },
    )


def test_maintenance_compact_uses_shipped_providers_and_plan_owned_target(
    client, maintenance_backend, journal_dir
):
    backend = maintenance_backend
    backend.revision = 0
    planned = _maintenance_plan(client, ref="feature")
    assert planned["operation_id"] == "compact:raw.events:feature:0"
    assert planned["plan_token"] == "0"
    assert backend.sql == []  # Planning never invokes the engine.
    for options, error in [
        ({"ref": "main"}, "ref_mismatch"),
        ({"table": "raw.other"}, "table_mismatch"),
        ({"confirmation_token": "wrong"}, "token_mismatch"),
    ]:
        refused = _maintenance_apply(client, planned, **options)
        assert refused.status_code == 400, refused.text
        assert refused.json()["detail"]["error"] == error
    assert backend.sql == []

    applied = _maintenance_apply(client, planned)
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "succeeded"
    assert applied.json()["operation"] == "maintenance.apply"
    assert applied.json()["operation_id"] == planned["operation_id"]
    assert applied.json()["before_revision"] == 0
    assert backend.revision == 1
    assert {ref for ref, _sql in backend.sql} == {"feature"}
    assert backend.sql[-1][1] == 'ALTER TABLE "raw"."events" EXECUTE optimize'
    count = len(backend.sql)
    assert _maintenance_apply(client, planned).json() == applied.json()
    assert _maintenance_apply(client, planned, "another-key").status_code == 409
    assert len(backend.sql) == count
    fresh = _maintenance_plan(client, ref="feature")
    assert fresh["operation_id"] == "compact:raw.events:feature:1"
    assert _maintenance_apply(client, fresh, "fresh").status_code == 200
    assert backend.revision == 2


@pytest.mark.parametrize("phase,state", [("preflight", "failed"), ("submission", "unknown")])
def test_maintenance_real_provider_failure_evidence_owns_replay_fence(
    client, maintenance_backend, journal_dir, phase, state
):
    backend = maintenance_backend
    planned = _maintenance_plan(client)
    backend.failure = phase
    first = _maintenance_apply(client, planned)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "failed"
    assert first.json()["failure"]["outcome"] == (
        "unknown" if state == "unknown" else "not_submitted"
    )
    entry = FileOperationJournalStore(journal_dir).read(planned["operation_id"])
    assert entry.state == state
    assert entry.result["failure"] == first.json()["failure"]
    verified = client.get(f"/api/continuity/verifications/{planned['operation_id']}")
    assert verified.json()["replay_blocked"] is (state == "unknown")
    count = len(backend.sql)
    assert _maintenance_apply(client, planned).json() == first.json()
    assert _maintenance_apply(client, planned, "other-key").status_code == 409
    assert len(backend.sql) == count
    backend.revision += 1
    fresh = _maintenance_plan(client)
    result = _maintenance_apply(client, fresh, "fresh-key")
    assert result.status_code == (409 if state == "unknown" else 200), result.text
    if state == "unknown":
        assert len(backend.sql) == count


@pytest.mark.parametrize("custom", [False, True])
def test_maintenance_expiry_replays_real_plan_parameters(client, maintenance_backend, custom):
    options = (
        {
            "catalog": "iceberg_review",
            "retention_hours": 720,
            "retain_last": 2,
            "max_affected_objects": 17,
            "max_affected_bytes": 123456,
        }
        if custom
        else {}
    )
    planned = _maintenance_plan(client, "snapshot_expiry", ref="review", **options)
    assert planned["plan"]["operation"] == "expire_snapshots"
    assert planned["operation_id"].startswith("snapshot_expiry:raw.events:review:")
    parameters = planned["plan"]["planned"]
    assert parameters["retention_hours"] == (720 if custom else 168)
    assert parameters["retain_last"] == (2 if custom else 5)
    assert parameters["max_affected_objects"] == (17 if custom else 1000)
    assert parameters["max_affected_bytes"] == (123456 if custom else 1 << 30)
    assert maintenance_backend.sql == []
    applied = _maintenance_apply(client, planned)
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == ("succeeded" if custom else "noop"), applied.text
    assert applied.json()["operation_id"] == planned["operation_id"]
    assert applied.json()["planned"]["retention_hours"] == parameters["retention_hours"]
    assert applied.json()["planned"]["retain_last"] == parameters["retain_last"]
    if custom:
        assert maintenance_backend.sql[-1] == (
            "review",
            'ALTER TABLE "raw"."events" EXECUTE expire_snapshots(retention_threshold => \'720h\')',
        )
    else:
        assert maintenance_backend.sql == []


@pytest.mark.parametrize("hours", [0, 1])
def test_maintenance_real_provider_rejects_stale_revision_and_unsafe_retention(
    client, maintenance_backend, hours
):
    maintenance_backend.revision = 0
    stale = _maintenance_plan(client)
    maintenance_backend.revision += 1
    refused = _maintenance_apply(client, stale)
    assert refused.status_code == 200, refused.text
    assert refused.json()["status"] == "blocked"
    assert refused.json()["failure"]["code"] == "concurrent_change_detected"
    unsafe = _maintenance_plan(client, "snapshot_expiry", retention_hours=hours)
    assert unsafe["plan"]["accepted"] is False
    assert unsafe["plan"]["failure"]["code"] == "retention_floor_violation"
    applied = _maintenance_apply(client, unsafe, "unsafe")
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "blocked"
    assert applied.json()["planned"]["retention_hours"] == hours
    assert maintenance_backend.sql == []


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
