"""Tests for the guarded run-action contract (retry/cancel).

Covers the one provider-neutral run-action result: mandatory idempotency keys
rejected before invocation, distinct safe outcomes (accepted, pending,
rejected, skipped, unauthorized, duplicate/pending key, missing/ambiguous
capability, response loss), identical durable verification handles on replay,
and canonical report identity resolved only from durable evidence.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from phlo.run_evidence import PipelineRun, SQLiteRunEvidenceStore
from phlo_api.main import app
from security_test_support import (  # noqa: F401
    _regulated_api_boundary,
    authenticated_client,
)
from phlo_api.observatory_api import observatory
from phlo_api.observatory_api.observatory_models import ObservatoryAction
from phlo_api.observatory_api.run_action_contract import (
    CANCEL_RUN_ACTION,
    RETRY_RUN_ACTION,
    RunActionResult,
    normalize_run_action_result,
    resolve_run_action_reconciliation,
)

RETRY_URL = "/api/observatory/runs/run-123/retry"
CANCEL_URL = "/api/observatory/runs/run-123/cancel"
OPERATE_HEADERS = {"Authorization": "Bearer operate-token"}


def _retry_provider_result(run_id: str | None, *, accepted: bool = True) -> dict[str, Any]:
    return {
        "operation": "retry_failed_run",
        "dry_run": False,
        "accepted": accepted,
        "run_id": run_id,
        "status": "STARTED" if accepted else "FAILURE",
        "message": "Dagster accepted retry_failed_run." if accepted else "Run is not retryable.",
        "details": {},
    }


class _FakeProvider:
    """Minimal async provider seam for retry/cancel handlers."""

    def __init__(self, **handlers: Any) -> None:
        self._handlers = handlers

    async def retry_run(self, run_id: str, request: dict[str, Any]) -> Any:
        result = self._handlers["retry_run"](run_id, SimpleNamespace(**request))
        return await result if inspect.isawaitable(result) else result

    async def cancel_run(self, run_id: str, request: dict[str, Any]) -> Any:
        result = self._handlers["cancel_run"](run_id, SimpleNamespace(**request))
        return await result if inspect.isawaitable(result) else result


def _fake_provider(**handlers: Any) -> Any:
    return _FakeProvider(**handlers)


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        json.dumps(
            {
                "operate-token": {"subject": "operator", "scopes": ["lakehouse:operate"]},
                "read-token": {"subject": "reader", "scopes": ["lakehouse:read"]},
            }
        ),
    )
    return TestClient(app)


def test_run_action_descriptors_name_full_guard_metadata() -> None:
    """Retry/cancel descriptors name capability, permission, risk, confirmation, target, evidence."""
    for contract in (RETRY_RUN_ACTION, CANCEL_RUN_ACTION):
        assert contract.required_capability == "orchestrator_operations"
        assert contract.required_permission == "lakehouse:operate"
        assert contract.requires_confirmation is True
        assert contract.risk_level in {"medium", "high", "critical"}
        assert any("verification_handle" in evidence for evidence in contract.expected_evidence)
        assert any(
            "project_id/run_id/attempt" in evidence for evidence in contract.expected_evidence
        )


def test_pipeline_read_model_uses_contract_descriptors(observatory_loaders) -> None:
    """The read model renders retry/cancel actions straight from the contract."""
    observatory._clear_read_model_cache()
    observatory_loaders(capability_registry=None)
    action = observatory._pipeline_actions(
        SimpleNamespace(id="run-1", status="failed", name="run", kind="pipeline")
    )
    retry = next(item for item in action if item.id == "retry")
    cancel = next(item for item in action if item.id == "cancel")
    assert isinstance(retry, ObservatoryAction)
    assert retry.required_capability == "orchestrator_operations"
    assert retry.required_permission == "lakehouse:operate"
    assert retry.risk_level == RETRY_RUN_ACTION.risk_level
    assert retry.requires_confirmation is True
    assert retry.background_operation_id == "run-1"
    assert retry.expected_evidence == list(RETRY_RUN_ACTION.expected_evidence)
    assert cancel.enabled is False
    assert cancel.risk_level == CANCEL_RUN_ACTION.risk_level


def test_authorized_retry_is_accepted_with_durable_verification_handle(
    monkeypatch, tmp_path: Path
) -> None:
    """An authorized retry returns accepted plus a handle that replays identically."""
    calls: list[str] = []

    def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        return _retry_provider_result("run-new-456")

    provider = _fake_provider(retry_run=retry_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = _client(monkeypatch, tmp_path)

    first = client.post(
        RETRY_URL,
        json={"dry_run": False, "idempotency_key": "retry-once"},
        headers=OPERATE_HEADERS,
    )
    replay = client.post(
        RETRY_URL,
        json={"dry_run": False, "idempotency_key": "retry-once"},
        headers=OPERATE_HEADERS,
    )

    assert first.status_code == 200
    body = first.json()
    assert body["contract_version"] == 1
    assert body["action_kind"] == "run.retry"
    assert body["status"] == "accepted"
    assert body["resulting_run"] == {"run_id": "run-new-456"}
    assert body["verification_handle"].startswith("vh-")
    assert "canonical_report" not in body
    # Replay returns the identical result without re-invoking the provider.
    assert replay.status_code == 200
    assert replay.json() == body
    assert calls == ["run-123"]


def test_missing_or_blank_idempotency_key_is_rejected_before_invocation(
    monkeypatch, tmp_path: Path
) -> None:
    """Missing and blank keys fail fast; the provider is never invoked."""
    calls: list[str] = []

    def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        return _retry_provider_result("run-new-456")

    provider = _fake_provider(retry_run=retry_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = _client(monkeypatch, tmp_path)

    missing = client.post(RETRY_URL, json={"dry_run": False}, headers=OPERATE_HEADERS)
    blank = client.post(
        RETRY_URL, json={"dry_run": False, "idempotency_key": "   "}, headers=OPERATE_HEADERS
    )
    blank_cancel = client.post(CANCEL_URL, json={"idempotency_key": ""}, headers=OPERATE_HEADERS)

    assert missing.status_code == 422
    assert missing.json()["detail"]["error"] == "idempotency_key_required"
    assert blank.status_code == 422
    assert blank_cancel.status_code == 422
    assert calls == []


def test_unauthorized_run_action_is_forbidden(
    monkeypatch, tmp_path: Path, regulated_api_boundary
) -> None:
    """A read-scoped principal is rejected before the provider runs."""
    calls: list[str] = []

    def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        return _retry_provider_result("run-new-456")

    provider = _fake_provider(retry_run=retry_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    _client(monkeypatch, tmp_path)

    forbidden = authenticated_client("viewer").post(
        RETRY_URL,
        json={"dry_run": False, "idempotency_key": "retry-key"},
    )

    assert forbidden.status_code == 403
    assert calls == []


def test_ambiguous_capability_is_a_distinct_safe_result(monkeypatch, tmp_path: Path) -> None:
    """Two installed orchestrator providers resolve to a stable 422, no execution."""
    from phlo.capabilities import (
        OrchestratorOperationsSpec,
        clear_capabilities,
        register_capability,
    )

    calls: list[str] = []

    def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        return _retry_provider_result("run-new-456")

    provider = _fake_provider(retry_run=retry_run)
    clear_capabilities("orchestrator_operations")
    register_capability(
        "orchestrator_operations",
        OrchestratorOperationsSpec(name="one", provider=provider),
    )
    register_capability(
        "orchestrator_operations",
        OrchestratorOperationsSpec(name="two", provider=provider),
    )
    client = _client(monkeypatch, tmp_path)
    try:
        response = client.post(
            RETRY_URL,
            json={"dry_run": False, "idempotency_key": "retry-key"},
            headers=OPERATE_HEADERS,
        )
    finally:
        clear_capabilities("orchestrator_operations")

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "orchestrator_operations_ambiguous"
    assert calls == []


def test_rejected_provider_result_is_distinct_and_never_reexecuted(
    monkeypatch, tmp_path: Path
) -> None:
    """A provider refusal is surfaced as rejected; replay returns it verbatim."""
    calls: list[str] = []

    def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        return _retry_provider_result(run_id, accepted=False)

    provider = _fake_provider(retry_run=retry_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = _client(monkeypatch, tmp_path)

    first = client.post(
        RETRY_URL,
        json={"dry_run": False, "idempotency_key": "rejected-key"},
        headers=OPERATE_HEADERS,
    )
    replay = client.post(
        RETRY_URL,
        json={"dry_run": False, "idempotency_key": "rejected-key"},
        headers=OPERATE_HEADERS,
    )

    assert first.status_code == 200
    assert first.json()["status"] == "rejected"
    assert "resulting_run" not in first.json()
    assert replay.json() == first.json()
    assert calls == ["run-123"]


def test_dry_run_retry_is_skipped_without_execution(monkeypatch, tmp_path: Path) -> None:
    """A provider dry run is a distinct skipped result, not an execution."""
    calls: list[str] = []

    def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        return {
            "operation": "retry_failed_run",
            "dry_run": True,
            "accepted": True,
            "run_id": run_id,
            "status": "DRY_RUN",
            "message": "Run retry request is valid.",
            "details": {},
        }

    provider = _fake_provider(retry_run=retry_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        RETRY_URL, json={"dry_run": True, "idempotency_key": "dry-key"}, headers=OPERATE_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert "resulting_run" not in response.json()
    assert calls == ["run-123"]


def test_pending_key_conflicts_without_duplicate_execution(monkeypatch, tmp_path: Path) -> None:
    """A concurrent duplicate of a live claim sees 409 while the provider runs once."""
    import httpx

    calls: list[str] = []

    async def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        await asyncio.sleep(0.4)
        return _retry_provider_result("run-new-456")

    provider = _fake_provider(retry_run=retry_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    _client(monkeypatch, tmp_path)
    body = {"dry_run": False, "idempotency_key": "pending-key"}

    async def call_once() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(RETRY_URL, json=body, headers=OPERATE_HEADERS)

    async def main() -> tuple[httpx.Response, httpx.Response]:
        first, second = await asyncio.gather(call_once(), call_once())
        return first, second

    r1, r2 = asyncio.run(main())

    statuses = sorted(response.status_code for response in (r1, r2))
    assert statuses == [200, 409]
    conflict = r1 if r1.status_code == 409 else r2
    assert conflict.json()["detail"] == {"error": "idempotency_in_progress"}
    assert conflict.headers.get("retry-after") is not None
    assert calls == ["run-123"]


def test_response_loss_leaves_unknown_outcome_and_never_reexecutes(
    monkeypatch, tmp_path: Path
) -> None:
    """A provider failure after the claim surfaces 500 then a stable 409."""
    calls: list[str] = []

    def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        raise RuntimeError("provider exploded after acceptance")

    provider = _fake_provider(retry_run=retry_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    body = {"dry_run": False, "idempotency_key": "lossy-key"}

    first = client.post(RETRY_URL, json=body, headers=OPERATE_HEADERS)
    retry = client.post(RETRY_URL, json=body, headers=OPERATE_HEADERS)

    assert first.status_code == 500
    assert retry.status_code == 409
    assert retry.json()["detail"] == {"error": "idempotency_outcome_unknown"}
    assert calls == ["run-123"]


def test_cancel_accepted_names_target_run_and_replays_identically(
    monkeypatch, tmp_path: Path
) -> None:
    """Cancel acceptance targets the exact run and replays the same handle."""
    calls: list[str] = []

    def cancel_run(run_id: str, payload: Any) -> dict[str, Any]:
        calls.append(run_id)
        return {
            "operation": "cancel_run",
            "dry_run": False,
            "accepted": True,
            "run_id": run_id,
            "status": "CANCELING",
            "message": "Dagster accepted run cancellation.",
            "details": {},
        }

    provider = _fake_provider(cancel_run=cancel_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = _client(monkeypatch, tmp_path)

    first = client.post(
        CANCEL_URL,
        json={"reason": "stuck", "idempotency_key": "cancel-once"},
        headers=OPERATE_HEADERS,
    )
    replay = client.post(
        CANCEL_URL,
        json={"reason": "stuck", "idempotency_key": "cancel-once"},
        headers=OPERATE_HEADERS,
    )

    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "accepted"
    assert body["target"] == {"run_id": "run-123"}
    assert body["resulting_run"] == body["target"]
    assert replay.json() == body
    assert calls == ["run-123"]


def test_cancel_of_finished_run_is_rejected(monkeypatch, tmp_path: Path) -> None:
    """Cancelling a non-running run is a distinct rejected result."""

    def cancel_run(run_id: str, payload: Any) -> dict[str, Any]:
        return {
            "operation": "cancel_run",
            "dry_run": False,
            "accepted": False,
            "run_id": run_id,
            "status": "SUCCEEDED",
            "message": "Run already finished; nothing to cancel.",
            "details": {"typename": "TerminateRunError"},
        }

    provider = _fake_provider(cancel_run=cancel_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        CANCEL_URL, json={"idempotency_key": "late-cancel"}, headers=OPERATE_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_ambiguous_provider_outcome_stays_pending_with_handle(monkeypatch, tmp_path: Path) -> None:
    """Accepted without a distinct new run identity is pending, never fabricated."""

    def retry_run(run_id: str, payload: Any) -> dict[str, Any]:
        return _retry_provider_result(run_id)

    provider = _fake_provider(retry_run=retry_run)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        RETRY_URL,
        json={"dry_run": False, "idempotency_key": "ambiguous-key"},
        headers=OPERATE_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert "resulting_run" not in body
    assert "canonical_report" not in body
    assert body["verification_handle"].startswith("vh-")


def test_reconciliation_resolves_canonical_identity_only_from_durable_evidence(
    tmp_path: Path,
) -> None:
    """Canonical report identity comes only from durable evidence."""
    database = tmp_path / "run-evidence.sqlite"
    store = SQLiteRunEvidenceStore(database)

    pending = normalize_run_action_result(
        action_kind="run.retry",
        target_run_id="run-123",
        provider_result=_retry_provider_result("run-new-456"),
        idempotency_key="reconcile-key",
    )

    # No evidence yet: the result stays accepted with no canonical identity.
    unresolved = resolve_run_action_reconciliation(pending, store, project_id="finance")
    assert unresolved.status == "accepted"
    assert unresolved.canonical_report is None

    # No project named: no canonical identity either.
    unscoped = resolve_run_action_reconciliation(pending, store, project_id=None)
    assert unscoped.canonical_report is None

    store.append_pipeline_run(PipelineRun(project_id="finance", run_id="run-new-456", attempt=1))
    reconciled = resolve_run_action_reconciliation(pending, store, project_id="finance")

    assert reconciled.status == "reconciled"
    assert reconciled.canonical_report is not None
    assert reconciled.canonical_report.model_dump() == {
        "project_id": "finance",
        "run_id": "run-new-456",
        "attempt": 1,
    }
    assert (
        reconciled.canonical_report_path
        == "/api/observatory/projects/finance/runs/run-new-456/attempts/1/report"
    )
    # The handle is unchanged by reconciliation.
    assert reconciled.verification_handle == pending.verification_handle


def test_normalization_is_provider_model_agnostic() -> None:
    """Dagster-style pydantic payloads normalize exactly like raw dicts."""
    from phlo_dagster.operations import DagsterOperationResult

    result = DagsterOperationResult(
        operation="retry_failed_run",
        dry_run=False,
        accepted=True,
        run_id="run-new-456",
        status="STARTED",
        message="Dagster accepted retry_failed_run.",
    )
    from_dict = normalize_run_action_result(
        action_kind="run.retry",
        target_run_id="run-123",
        provider_result={
            "operation": "retry_failed_run",
            "dry_run": False,
            "accepted": True,
            "run_id": "run-new-456",
            "asset_key_path": None,
            "partition_key": None,
            "status": "STARTED",
            "message": "Dagster accepted retry_failed_run.",
            "details": {},
        },
        idempotency_key="k",
    )
    from_model = normalize_run_action_result(
        action_kind="run.retry",
        target_run_id="run-123",
        provider_result=result,
        idempotency_key="k",
    )
    assert isinstance(from_dict, RunActionResult)
    assert from_model.model_dump() == from_dict.model_dump()
