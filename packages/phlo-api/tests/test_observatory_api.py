"""Tests for the Observatory provider-neutral API contract.

Covers payload serialization shape (no provider URLs leak), scoped run-report
identity and idempotency on operational routes, read-model caching, service
and docker status resolution, capability inventory gating, bounded log tails,
and the saved-query, branch, and WAP-report contracts.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from phlo.capabilities import ResourceRef
from phlo.capabilities.registry import CapabilityRegistry
from phlo.capabilities.authentication import ServiceTokenAuthenticationProvider
from phlo.capabilities.authorization import DefaultAuthorizationPolicyBackend
from phlo.capabilities.specs import CatalogSpec
from phlo_api.main import app
from security_test_support import _regulated_api_boundary, authenticated_client  # noqa: F401
from phlo.run_evidence import PipelineRun, RunEvent, RunStage, SQLiteRunEvidenceStore
from phlo_api.observatory_api import observatory
from phlo_api.observatory_api import observatory_services
from phlo_api.observatory_api import observatory_runs as observatory_runs_module
from phlo_api.observatory_api.observatory import (
    _execute_action,
    _load_capabilities,
    _load_services,
    _overview_health_from_services,
    _run_read_query,
    _safe_metadata,
)
from phlo_api.observatory_api.observatory_models import (
    ObservatoryActionRequest,
    ObservatoryAsset,
    ObservatoryAssetDetail,
    ObservatoryBranch,
    ObservatoryCapabilities,
    ObservatoryCapabilityPage,
    ObservatoryExtension,
    ObservatoryExternalLink,
    ObservatoryHealth,
    ObservatoryLogEvent,
    ObservatoryOperation,
    ObservatoryOverview,
    ObservatoryQualityCheck,
    ObservatoryQueryRequest,
    ObservatoryResourceRef,
    ObservatoryService,
    ObservatorySettings,
    ObservatoryTable,
    ObservatoryTablePreview,
)
from phlo_api.observatory_api.observatory_operation_journal import append_operation
from phlo_api.observatory_api.observatory_services import fallback_services as _fallback_services
from phlo_api.observatory_api.observatory_services import (
    load_docker_service_statuses as _load_docker_service_statuses,
)
from phlo_api.security_manifest import RUN_REPORT_RESOURCE_ID_ATTRIBUTE

# Lowercase concatenations catch camelCase spellings that `.lower()` would
# otherwise merge past the snake_case names (e.g. "dagsterGraphqlUrl").
_PROVIDER_URL_SETTING_NAMES = (
    "dagster_url",
    "trino_url",
    "nessie_url",
    "dagstergraphqlurl",
    "dagster_graphql_url",
    "trinourl",
    "nessieurl",
)


def test_authenticated_run_report_isolated_by_attempt_and_path_identity(
    monkeypatch, tmp_path, regulated_api_boundary
) -> None:
    database = tmp_path / "run-evidence.sqlite"
    store = SQLiteRunEvidenceStore(database)
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run", attempt=2))
    for attempt in (1, 2):
        store.append_stage(
            RunStage(
                project_id="project",
                run_id="run",
                stage_id=f"stage-{attempt}",
                stage_type="ingest",
                attempt=attempt,
            )
        )
        store.append_event(
            RunEvent(
                project_id="project",
                run_id="run",
                event_id=f"event-{attempt}",
                event_type="run.terminal",
                producer="test",
                payload={"status": "success"},
                attempt=attempt,
            )
        )
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_SQLITE_PATH", str(database))

    response = authenticated_client("viewer").get(
        "/api/observatory/projects/project/runs/run/attempts/1/report"
    )

    assert response.status_code == 200
    assert response.json()["attempt"] == 1
    assert [stage["stage_id"] for stage in response.json()["stages"]] == ["stage-1"]

    assert (
        TestClient(app)
        .get("/api/observatory/projects/project/runs/run/attempts/1/report")
        .status_code
        == 401
    )
    for path in (
        "/api/observatory/projects/other/runs/run/attempts/1/report",
        "/api/observatory/projects/project/runs/other/attempts/1/report",
        "/api/observatory/projects/project/runs/run/attempts/3/report",
    ):
        assert authenticated_client("viewer").get(path).status_code == 404

    class _DenyBackend:
        def explain_decision(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            from phlo.capabilities.interfaces import AuthorizationDecision

            return AuthorizationDecision(
                allowed=False,
                reason_code="default_deny",
                policy_id=None,
                explanation="denied by test",
            )

    monkeypatch.setattr(
        "phlo_api.security_manifest.get_authorization_backend", lambda: _DenyBackend()
    )
    assert (
        authenticated_client("viewer")
        .get("/api/observatory/projects/project/runs/run/attempts/1/report")
        .status_code
        == 403
    )


def test_scoped_service_token_cannot_read_another_run_report(
    monkeypatch, tmp_path, regulated_api_boundary
) -> None:
    database = tmp_path / "run-evidence.sqlite"
    store = SQLiteRunEvidenceStore(database)
    for run_id in ("allowed", "other"):
        store.append_pipeline_run(PipelineRun(project_id="project", run_id=run_id, attempt=1))
        store.append_event(
            RunEvent(
                project_id="project",
                run_id=run_id,
                event_id=f"{run_id}-event",
                event_type="run.terminal",
                producer="dagster",
                payload={"status": "success"},
                attempt=1,
                resource_ref=ResourceRef(
                    resource_type="run",
                    resource_id=run_id,
                    tenant="project",
                    attributes={"attempt": "1"},
                ),
            )
        )
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_SQLITE_PATH", str(database))
    allowed_resource_id = "project_id=project|run_id=allowed|attempt=1"
    provider = ServiceTokenAuthenticationProvider(
        {
            "dagster-report-token": {
                "subject": "dagster:report-reader",
                "attributes": {RUN_REPORT_RESOURCE_ID_ATTRIBUTE: allowed_resource_id},
            }
        }
    )
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "service-report-read",
                "effect": "allow",
                "principal": {"attributes": {RUN_REPORT_RESOURCE_ID_ATTRIBUTE: "*"}},
                "action": "run.read",
                "resource": {"type": "run", "id_pattern": "*"},
            }
        ]
    )
    monkeypatch.setattr("phlo_api.api.authentication.get_authentication_provider", lambda: provider)
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)

    client = TestClient(app)
    allowed = client.get(
        "/api/observatory/projects/project/runs/allowed/attempts/1/report",
        headers={"Authorization": "Bearer dagster-report-token"},
    )
    denied = client.get(
        "/api/observatory/projects/project/runs/other/attempts/1/report",
        headers={"Authorization": "Bearer dagster-report-token"},
    )

    assert allowed.status_code == 200
    assert allowed.json()["run_id"] == "allowed"
    assert allowed.json()["lifecycle"]["events"][0]["resource_identity"] == {
        "project_id": "project",
        "resource_type": "run",
        "resource_id": "allowed",
        "tenant": "project",
        "attributes": {"attempt": "1"},
    }
    assert denied.status_code == 403
    assert denied.json() == {"error": "forbidden", "reason": "run_report_scope_mismatch"}


def test_unregulated_run_report_keeps_anonymous_open_but_enforces_supplied_scope(
    monkeypatch, tmp_path
) -> None:
    database = tmp_path / "run-evidence.sqlite"
    SQLiteRunEvidenceStore(database).append_pipeline_run(
        PipelineRun(project_id="project", run_id="allowed", attempt=1)
    )
    monkeypatch.setenv("PHLO_REGULATED", "false")
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_SQLITE_PATH", str(database))
    provider = ServiceTokenAuthenticationProvider(
        {
            "dagster-report-token": {
                "subject": "dagster:report-reader",
                "attributes": {
                    RUN_REPORT_RESOURCE_ID_ATTRIBUTE: (
                        "project_id=project|run_id=allowed|attempt=1"
                    )
                },
            }
        }
    )
    monkeypatch.setattr("phlo_api.api.authentication.get_authentication_provider", lambda: provider)
    client = TestClient(app)
    path = "/api/observatory/projects/project/runs/allowed/attempts/1/report"
    headers = {"Authorization": "Bearer dagster-report-token"}

    anonymous = client.get(path)
    allowed = client.get(path, headers=headers)
    denied = client.get(path.replace("/allowed/", "/other/"), headers=headers)

    assert anonymous.status_code == 200
    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert denied.json() == {"error": "forbidden", "reason": "run_report_scope_mismatch"}


def test_runs_list_carries_report_identity_only_for_complete_durable_evidence(
    monkeypatch, tmp_path
) -> None:
    database = tmp_path / "run-evidence.sqlite"
    store = SQLiteRunEvidenceStore(database)
    store.append_pipeline_run(
        PipelineRun(
            project_id="finance",
            run_id="daily-orders",
            attempt=2,
            pipeline_name="Daily orders refresh",
            status="success",
        )
    )
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_SQLITE_PATH", str(database))
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    observatory._clear_read_model_cache()

    items = authenticated_client("admin").get("/api/observatory/runs").json()["items"]

    durable = next(run for run in items if run["id"] == "finance/daily-orders")
    assert durable["report_identity"] == {
        "project_id": "finance",
        "run_id": "daily-orders",
        "attempt": 2,
    }
    assert durable["metadata"]["source"] == "durable_run_evidence"

    report = authenticated_client("admin").get(
        "/api/observatory/projects/finance/runs/daily-orders/attempts/2/report"
    )
    assert report.status_code == 200
    assert report.json()["project_id"] == "finance"
    assert report.json()["run_id"] == "daily-orders"
    assert report.json()["attempt"] == 2


def test_manifest_and_recovered_runs_never_carry_report_identity(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    state_dir = tmp_path / ".phlo" / "observatory"
    state_dir.mkdir(parents=True)
    (state_dir / "lakehouse_manifest.json").write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "id": "legacy-run-1",
                        "name": "Legacy manifest run",
                        "status": "succeeded",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    observatory._clear_read_model_cache()

    items = authenticated_client("admin").get("/api/observatory/runs").json()["items"]
    manifest_run = next(run for run in items if run["id"] == "legacy-run-1")
    assert "report_identity" not in manifest_run
    assert manifest_run["metadata"]["evidence_source"] == "lakehouse_manifest"


def test_durable_run_without_complete_identity_omits_report_identity(monkeypatch, tmp_path) -> None:
    database = tmp_path / "run-evidence.sqlite"
    SQLiteRunEvidenceStore(database).append_pipeline_run(
        PipelineRun(project_id="finance", run_id="daily-orders", attempt=2)
    )
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_SQLITE_PATH", str(database))
    monkeypatch.setattr(observatory_runs_module, "_durable_report_identity", lambda row: None)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    observatory._clear_read_model_cache()

    items = authenticated_client("admin").get("/api/observatory/runs").json()["items"]
    assert items == []
    assert all("report_identity" not in run for run in items)


class _FakeOrchestratorOperations:
    def __init__(self, **handlers: Any) -> None:
        self._handlers = handlers

    async def get_run_status(self, run_id: str) -> Any:
        return await self._handlers["get_run_status"](run_id)

    async def retry_run(self, run_id: str, request: dict[str, Any]) -> Any:
        return await self._handlers["retry_run"](run_id, SimpleNamespace(**request))

    async def cancel_run(self, run_id: str, request: dict[str, Any]) -> Any:
        return await self._handlers["cancel_run"](run_id, SimpleNamespace(**request))

    async def get_materialization_history(self, asset_key_path: str, *, limit: int = 10) -> Any:
        return await self._handlers["get_materialization_history"](asset_key_path, limit)

    async def materialize_asset(self, asset_key_path: str, request: dict[str, Any]) -> Any:
        return await self._handlers["materialize_asset"](asset_key_path, SimpleNamespace(**request))

    async def backfill_asset(self, asset_key_path: str, request: dict[str, Any]) -> Any:
        return await self._handlers["backfill_asset"](asset_key_path, SimpleNamespace(**request))

    async def list_partitions(self, asset_key_path: str) -> Any:
        return await self._handlers["list_partitions"](asset_key_path)


def _assert_no_provider_url_settings(payload: object) -> None:
    serialized = json.dumps(payload).lower()
    for setting_name in _PROVIDER_URL_SETTING_NAMES:
        assert setting_name not in serialized


def test_safe_metadata_removes_private_looking_values() -> None:
    metadata = _safe_metadata(
        {
            "location": "http://internal",
            "harmless_name": "postgres://user:pass@host/db",
            "nested": {
                "safe": "ok",
                "callback": "https://internal.example/callback",
                "details": ["visible", "token=secret", {"safe_nested": True, "uri": "http://gone"}],
            },
            "items": [
                {"label": "kept", "location": "http://internal"},
                "safe-item",
                "Bearer secret-token",
            ],
            "safe_name": "orders",
            "safe_count": 3,
            "safe_enabled": True,
        }
    )

    assert "location" not in metadata
    assert "harmless_name" not in metadata
    assert metadata["nested"] == {"safe": "ok", "details": ["visible", {"safe_nested": True}]}
    assert metadata["items"] == [{"label": "kept"}, "safe-item"]
    assert metadata["safe_name"] == "orders"
    assert metadata["safe_count"] == 3
    assert metadata["safe_enabled"] is True


def test_observatory_service_serializes_provider_neutral_shape() -> None:
    service = ObservatoryService(
        id="phlo-api",
        name="phlo-api",
        kind="api",
        status="running",
        health=ObservatoryHealth(state="ok", message="healthy"),
        depends_on=["postgres"],
        impacts=["observatory"],
        links=[ObservatoryExternalLink(label="Open", url="http://localhost:4000")],
    )

    payload = service.model_dump()

    assert payload == {
        "id": "phlo-api",
        "name": "phlo-api",
        "kind": "api",
        "status": "running",
        "health": {"state": "ok", "message": "healthy"},
        "definition_state": "available",
        "runtime_state": "unknown",
        "in_stack": False,
        "disabled": False,
        "profile": None,
        "backend": "unknown",
        "depends_on": ["postgres"],
        "impacts": ["observatory"],
        "links": [{"label": "Open", "url": "http://localhost:4000", "kind": "external"}],
        "metadata": {},
    }
    _assert_no_provider_url_settings(payload)


def test_observatory_overview_contains_resource_refs_not_provider_urls() -> None:
    overview = ObservatoryOverview(
        health=ObservatoryHealth(state="warning", message="1 service needs attention"),
        counters={"services": 12, "incidents": 1},
        recent=[ObservatoryResourceRef(kind="service", id="phlo-api", label="phlo-api")],
    )

    payload = overview.model_dump()

    assert payload["health"]["state"] == "warning"
    assert payload["recent"][0]["kind"] == "service"
    _assert_no_provider_url_settings(payload)


def test_observatory_resource_models_serialize_provider_neutral_shapes() -> None:
    asset = ObservatoryAsset(
        id="raw.orders",
        name="raw.orders",
        group="raw",
        kinds=["table"],
        dependencies=["source.orders"],
        resources=["object-store"],
        checks=["not_null_order_id"],
    )
    check = ObservatoryQualityCheck(
        id="raw.orders:not_null_order_id",
        name="not_null_order_id",
        asset_id="raw.orders",
        status="unknown",
    )
    branch = ObservatoryBranch(id="main", name="main", current=True, protected=True)
    settings = ObservatorySettings(defaults={"branch": "main"}, features={"assets": True})

    payload = {
        "asset": asset.model_dump(),
        "check": check.model_dump(),
        "branch": branch.model_dump(),
        "settings": settings.model_dump(),
    }

    assert payload["asset"]["id"] == "raw.orders"
    assert payload["check"]["asset_id"] == "raw.orders"
    assert payload["branch"]["current"] is True
    assert payload["settings"]["version"] == 2
    _assert_no_provider_url_settings(payload)


def test_observatory_datasets_endpoint_returns_profile_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setattr(
        observatory,
        "_load_assets",
        lambda: [
            ObservatoryAsset(
                id="gold.orders",
                name="gold.orders",
                group="gold",
                description="Curated orders",
                kinds=["table"],
                metadata={
                    "owner": "analytics",
                    "classification": "internal",
                    "published": True,
                },
            )
        ],
    )
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [
            ObservatoryTable(
                id="orders",
                name="orders",
                namespace="gold",
                asset_id="gold.orders",
            )
        ],
    )
    monkeypatch.setattr(
        observatory,
        "_load_quality",
        lambda: [
            ObservatoryQualityCheck(
                id="gold.orders:not_null_order_id",
                name="not_null_order_id",
                asset_id="gold.orders",
                status="passing",
            )
        ],
    )

    response = authenticated_client("admin").get("/api/observatory/datasets")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["id"] == "gold.orders"
    assert payload["items"][0]["owner"] == "analytics"
    assert payload["items"][0]["classifications"] == ["internal"]
    assert payload["items"][0]["publication_state"] == "published"
    assert payload["items"][0]["readiness_state"] == "ok"
    assert payload["items"][0]["candidate"] is False


def test_observatory_publishing_readiness_loads_shared_sources_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observatory._clear_read_model_cache()
    calls: list[str] = []

    def assets() -> list[ObservatoryAsset]:
        calls.append("assets")
        return [
            ObservatoryAsset(
                id=f"gold.{name}",
                name=name,
                metadata={"owner": "analytics", "classification": "internal"},
            )
            for name in ("orders", "customers", "payments")
        ]

    monkeypatch.setattr(observatory, "_load_assets", assets)
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: calls.append("tables") or [],
    )
    monkeypatch.setattr(
        observatory,
        "_load_quality",
        lambda: calls.append("quality") or [],
    )

    response = authenticated_client("admin").get("/api/observatory/datasets/publishing-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert [item["dataset_id"] for item in payload["items"]] == [
        "gold.customers",
        "gold.orders",
        "gold.payments",
    ]
    assert all(item["publishing"]["state"] == "unknown" for item in payload["items"])
    assert calls == ["assets", "tables", "quality"]


def test_observatory_datasets_endpoint_uses_project_read_model_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    observatory._clear_read_model_cache()
    calls: list[str] = []

    def load_assets() -> list[ObservatoryAsset]:
        calls.append("assets")
        return [ObservatoryAsset(id="gold.orders", name="Gold Orders", group="gold")]

    monkeypatch.setattr(observatory, "_load_assets", load_assets)
    monkeypatch.setattr(observatory, "_load_tables_without_catalog", lambda: [])
    monkeypatch.setattr(observatory, "_load_quality", lambda: [])

    first = authenticated_client("admin").get("/api/observatory/datasets")
    observatory._READ_MODEL_CACHE._values.clear()
    second = authenticated_client("admin").get("/api/observatory/datasets")

    assert first.status_code == 200
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == ["gold.orders"]
    assert calls == ["assets"]
    assert (tmp_path / ".phlo" / "observatory" / "read_models.sqlite").exists()


def test_observatory_datasets_endpoint_returns_table_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setattr(observatory, "_load_assets", lambda: [])
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [
            ObservatoryTable(
                id="raw_orders",
                name="raw_orders",
                namespace="raw",
                format="iceberg",
            )
        ],
    )
    monkeypatch.setattr(observatory, "_load_quality", lambda: [])

    response = authenticated_client("admin").get("/api/observatory/datasets")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["id"] == "candidate:raw_orders"
    assert payload["items"][0]["name"] == "raw_orders"
    assert payload["items"][0]["candidate"] is True
    assert payload["items"][0]["source_refs"] == [
        {"kind": "table", "id": "raw_orders", "label": "raw_orders"}
    ]


def test_observatory_dataset_profile_returns_table_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("USER", "data-team")
    monkeypatch.setattr(observatory, "_load_assets", lambda: [])
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [
            ObservatoryTable(
                id="raw_orders",
                name="raw_orders",
                namespace="raw",
                format="iceberg",
            )
        ],
    )
    monkeypatch.setattr(observatory, "_load_quality", lambda: [])
    monkeypatch.setattr(observatory, "_load_logs", lambda: [])
    monkeypatch.setattr(observatory, "_load_operations", lambda: [])

    response = authenticated_client("admin").get("/api/observatory/datasets/candidate:raw_orders")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset"]["id"] == "candidate:raw_orders"
    assert payload["dataset"]["candidate"] is True
    assert payload["asset"] is None
    assert payload["tables"][0]["id"] == "raw_orders"
    assert payload["governance"][2]["status"] == "not_applicable"


def test_observatory_candidate_actions_persist_workflow_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("USER", "data-team")
    monkeypatch.setattr(observatory, "_load_assets", lambda: [])
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [ObservatoryTable(id="raw_orders", name="raw_orders", namespace="raw")],
    )
    monkeypatch.setattr(observatory, "_load_quality", lambda: [])
    monkeypatch.setattr(observatory, "_load_logs", lambda: [])
    monkeypatch.setattr(observatory, "_load_operations", lambda: [])
    client = authenticated_client("admin")

    claim = client.post(
        "/api/observatory/actions",
        json={"action_id": "candidate:raw_orders:claim"},
    )
    assert claim.status_code == 200
    assert claim.json()["status"] == "succeeded"
    candidate = client.get("/api/observatory/datasets/candidate:raw_orders").json()
    assert candidate["dataset"]["owner"] == "data-team"
    assert candidate["dataset"]["metadata"]["approval_state"] == "claimed"

    promote = client.post(
        "/api/observatory/actions",
        json={"action_id": "candidate:raw_orders:promote"},
    )
    assert promote.status_code == 200
    datasets = client.get("/api/observatory/datasets").json()["items"]
    assert datasets[0]["id"] == "raw_orders"
    assert datasets[0]["candidate"] is False
    assert datasets[0]["owner"] == "data-team"
    profile = client.get("/api/observatory/datasets/raw_orders").json()
    assert profile["dataset"]["metadata"]["promoted_from_candidate"] is True
    telemetry = tmp_path / ".phlo" / "telemetry" / "events.jsonl"
    assert "observatory.candidate.promote" in telemetry.read_text(encoding="utf-8")


def test_observatory_publication_action_persists_dataset_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(
        observatory,
        "_load_assets",
        lambda: [
            ObservatoryAsset(
                id="gold.orders",
                name="gold.orders",
                metadata={"owner": "analytics", "classification": "internal"},
            )
        ],
    )
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [ObservatoryTable(id="orders", name="orders", asset_id="gold.orders")],
    )
    monkeypatch.setattr(
        observatory,
        "_load_quality",
        lambda: [
            ObservatoryQualityCheck(
                id="gold.orders:not_null_order_id",
                name="not_null_order_id",
                asset_id="gold.orders",
                status="passing",
                blocking=True,
            )
        ],
    )
    monkeypatch.setattr(observatory, "_load_logs", lambda: [])
    monkeypatch.setattr(observatory, "_load_operations", lambda: [])
    client = authenticated_client("admin")

    result = client.post(
        "/api/observatory/actions",
        json={"action_id": "dataset:gold.orders:publish"},
    )

    assert result.status_code == 200
    assert result.json()["status"] == "succeeded"
    profile = client.get("/api/observatory/datasets/gold.orders").json()
    assert profile["dataset"]["publication_state"] == "published"
    assert profile["dataset"]["metadata"]["approval_state"] == "approved"
    assert profile["publishing"]["actions"][0]["enabled"] is False
    telemetry = tmp_path / ".phlo" / "telemetry" / "events.jsonl"
    assert "observatory.dataset.publish" in telemetry.read_text(encoding="utf-8")


def test_observatory_dataset_workflow_config_round_trips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    client = authenticated_client("admin")

    saved = client.put(
        "/api/observatory/dataset-workflow/config",
        json={
            "default_owner": "platform-team",
            "approval_states": ["draft", "review", "approved"],
        },
    )

    assert saved.status_code == 200
    loaded = client.get("/api/observatory/dataset-workflow/config")
    assert loaded.json() == {
        "default_owner": "platform-team",
        "approval_states": ["draft", "review", "approved"],
    }


def test_observatory_governance_endpoint_returns_dataset_control_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setattr(
        observatory,
        "_load_assets",
        lambda: [
            ObservatoryAsset(
                id="gold.orders",
                name="gold.orders",
                group="gold",
                metadata={"owner": "analytics", "classification": "internal"},
            ),
            ObservatoryAsset(id="gold.customers", name="gold.customers", group="gold"),
        ],
    )
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [
            ObservatoryTable(id="orders", name="orders", asset_id="gold.orders"),
            ObservatoryTable(id="customers", name="customers", asset_id="gold.customers"),
            ObservatoryTable(id="raw_events", name="raw_events"),
        ],
    )
    monkeypatch.setattr(
        observatory,
        "_load_quality",
        lambda: [
            ObservatoryQualityCheck(
                id="gold.orders:not_null_order_id",
                name="not_null_order_id",
                asset_id="gold.orders",
                status="failing",
                blocking=True,
            )
        ],
    )

    response = authenticated_client("admin").get("/api/observatory/governance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["controls"] == ["owner", "classification", "blocking_quality"]
    order_row = next(row for row in payload["rows"] if row["dataset"]["id"] == "gold.orders")
    assert order_row["owner"] == "analytics"
    assert order_row["classifications"] == ["internal"]
    assert order_row["status"] == "fail"
    quality_control = next(
        control for control in order_row["controls"] if control["id"] == "blocking_quality"
    )
    assert quality_control["status"] == "fail"
    assert quality_control["evidence"][0]["kind"] == "quality_check"

    customer_row = next(row for row in payload["rows"] if row["dataset"]["id"] == "gold.customers")
    assert (
        next(control for control in customer_row["controls"] if control["id"] == "owner")["status"]
        == "fail"
    )
    assert (
        next(
            control for control in customer_row["controls"] if control["id"] == "blocking_quality"
        )["status"]
        == "unknown"
    )

    candidate_row = next(
        row for row in payload["rows"] if row["dataset"]["id"] == "candidate:raw_events"
    )
    assert (
        next(
            control for control in candidate_row["controls"] if control["id"] == "blocking_quality"
        )["status"]
        == "not_applicable"
    )


def test_observatory_dataset_profile_returns_privacy_shaped_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setattr(
        observatory,
        "_load_lakehouse_manifest",
        lambda: {
            "usage": {
                "privacy_policy": {
                    "identity_detail": "audit_only",
                    "retention_days": 30,
                    "audit_drilldown": True,
                },
                "access_activity": [
                    {
                        "id": "access-1",
                        "dataset_id": "gold.orders",
                        "actor": "alice@example.com",
                        "actor_kind": "person",
                        "requester_id": "employee-123",
                        "action": "query",
                        "count": 12,
                        "last_seen_at": "2026-07-01T12:00:00Z",
                    }
                ],
                "dependency_activity": [
                    {
                        "id": "dep-1",
                        "source": {"kind": "pipeline", "id": "orders-refresh"},
                        "target": {"kind": "dataset", "id": "gold.orders"},
                        "kind": "pipeline_read",
                        "count": 3,
                    }
                ],
                "consumer_adoption": [
                    {
                        "id": "consumer-1",
                        "dataset_id": "gold.orders",
                        "consumer": "finance",
                        "kind": "team",
                        "owner": "morgan",
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(
        observatory,
        "_load_assets",
        lambda: [
            ObservatoryAsset(
                id="gold.orders",
                name="gold.orders",
                metadata={"owner": "analytics", "classification": "internal"},
            )
        ],
    )
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [ObservatoryTable(id="orders", name="orders", asset_id="gold.orders")],
    )
    monkeypatch.setattr(observatory, "_load_quality", lambda: [])
    monkeypatch.setattr(observatory, "_load_logs", lambda: [])
    monkeypatch.setattr(observatory, "_load_operations", lambda: [])

    response = authenticated_client("admin").get("/api/observatory/datasets/gold.orders")

    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["privacy_policy"]["identity_detail"] == "audit_only"
    assert usage["access_activity"][0]["actor_label"] == "audit only"
    assert "alice@example.com" not in json.dumps(usage)
    assert "employee-123" not in json.dumps(usage)
    assert usage["access_activity"][0]["metadata"]["audit_drilldown"] is True
    assert usage["dependency_activity"][0]["source"]["kind"] == "pipeline"
    assert usage["consumer_adoption"][0]["consumer"] == "finance"


def test_observatory_pipelines_endpoint_returns_flow_and_action_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setattr(
        observatory,
        "_load_assets",
        lambda: [ObservatoryAsset(id="gold.orders", name="gold.orders")],
    )
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [ObservatoryTable(id="orders", name="orders", asset_id="gold.orders")],
    )
    monkeypatch.setattr(observatory, "_load_quality", lambda: [])
    monkeypatch.setattr(
        observatory,
        "_load_operations",
        lambda: [
            ObservatoryOperation(
                id="run-1",
                name="orders refresh",
                kind="pipeline",
                status="failed",
                health=ObservatoryHealth(state="error"),
                target=ObservatoryResourceRef(kind="asset", id="gold.orders", label="gold.orders"),
                completed_at="2026-07-01T12:00:00Z",
            )
        ],
    )

    response = authenticated_client("admin").get("/api/observatory/pipelines")

    assert response.status_code == 200
    pipeline = response.json()["items"][0]
    assert pipeline["dataset"]["id"] == "gold.orders"
    assert pipeline["last_run"]["id"] == "run-1"
    assert [stage["id"] for stage in pipeline["stages"]] == [
        "ingest",
        "transform",
        "checks",
        "publish",
    ]
    retry = next(action for action in pipeline["actions"] if action["id"] == "retry")
    cancel = next(action for action in pipeline["actions"] if action["id"] == "cancel")
    assert retry["enabled"] is True
    assert cancel["enabled"] is False


def test_observatory_dataset_profile_collects_related_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observatory._clear_read_model_cache()
    monkeypatch.setattr(
        observatory,
        "_load_assets",
        lambda: [
            ObservatoryAsset(
                id="silver.orders",
                name="silver.orders",
                group="silver",
            ),
            ObservatoryAsset(
                id="gold.orders",
                name="gold.orders",
                group="gold",
                dependencies=["silver.orders"],
                metadata={"owner": "analytics"},
            ),
        ],
    )
    monkeypatch.setattr(
        observatory,
        "_load_tables_without_catalog",
        lambda: [
            ObservatoryTable(
                id="orders",
                name="orders",
                namespace="gold",
                asset_id="gold.orders",
            )
        ],
    )
    monkeypatch.setattr(
        observatory,
        "_load_quality",
        lambda: [
            ObservatoryQualityCheck(
                id="gold.orders:not_null_order_id",
                name="not_null_order_id",
                asset_id="gold.orders",
                status="warning",
            )
        ],
    )
    monkeypatch.setattr(observatory, "_load_logs", lambda: [])
    monkeypatch.setattr(observatory, "_load_operations", lambda: [])

    response = authenticated_client("admin").get("/api/observatory/datasets/gold.orders")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset"]["id"] == "gold.orders"
    assert payload["dataset"]["owner"] == "analytics"
    assert payload["dataset"]["readiness_state"] == "warning"
    assert payload["tables"][0]["id"] == "orders"
    assert payload["quality"][0]["name"] == "not_null_order_id"
    assert payload["upstream"][0] == {
        "kind": "asset",
        "id": "silver.orders",
        "label": "silver.orders",
    }
    assert payload["sections"]["overview"] is True
    assert payload["sections"]["quality"] is True
    assert payload["sections"]["governance"] is True
    assert payload["sections"]["usage"] is True
    assert payload["publishing"]["internal_only"] is True
    assert payload["publishing"]["state"] == "error"
    assert payload["publishing"]["actions"][0]["id"] == "publish"
    assert payload["publishing"]["actions"][0]["enabled"] is False
    assert "external sharing" in payload["publishing"]["actions"][0]["consequences"][2]
    assert [control["id"] for control in payload["governance"]] == [
        "owner",
        "classification",
        "blocking_quality",
    ]
    assert payload["usage"]["dependency_activity"][0]["source"]["id"] == "silver.orders"


def test_observatory_capability_page_serializes_provider_neutral_shape() -> None:
    page = ObservatoryCapabilityPage(
        id="branches",
        label="Changes",
        path="/branches",
        available=False,
        nav=False,
        reason="Install a branching provider to compare catalog branches.",
        providers=[],
    )

    payload = page.model_dump()

    assert payload == {
        "id": "branches",
        "label": "Changes",
        "path": "/branches",
        "available": False,
        "nav": False,
        "reason": "Install a branching provider to compare catalog branches.",
        "providers": [],
        "metadata": {},
    }
    _assert_no_provider_url_settings(payload)


def test_observatory_fallback_services_are_deterministic_and_provider_neutral() -> None:
    payload = [service.model_dump() for service in _fallback_services()]

    assert [service["id"] for service in payload] == ["phlo-api", "observatory"]
    assert payload[1]["depends_on"] == ["phlo-api"]
    _assert_no_provider_url_settings(payload)


def test_observatory_docker_statuses_match_services_without_provider_imports(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        assert args[0][1:] == [
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=phlo",
            "--format",
            "{{json .}}",
        ]
        assert args[0][0].endswith("docker")
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="\n".join(
                [
                    json.dumps(
                        {
                            "Names": "phlo-trino-1",
                            "State": "running",
                            "Status": "Up 3 minutes (healthy)",
                            "Labels": "com.docker.compose.project=phlo,com.docker.compose.service=trino",
                        }
                    ),
                    json.dumps(
                        {
                            "Names": "old-trino-1",
                            "State": "exited",
                            "Status": "Exited (0) yesterday",
                            "Labels": "com.docker.compose.project=old,com.docker.compose.service=trino",
                        }
                    ),
                    json.dumps(
                        {
                            "Names": "phlo-dagster-1",
                            "State": "created",
                            "Status": "Created",
                            "Labels": "com.docker.compose.project=phlo,com.docker.compose.service=dagster",
                        }
                    ),
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("PHLO_COMPOSE_PROJECT", "phlo")

    statuses = _load_docker_service_statuses({"trino", "dagster"})

    assert statuses["trino"][0] == "running"
    assert statuses["trino"][1].state == "ok"
    assert statuses["dagster"][0] == "starting"
    _assert_no_provider_url_settings(
        {
            service: {"status": status, "health": health.model_dump()}
            for service, (status, health) in statuses.items()
        }
    )


def test_observatory_docker_statuses_fall_back_to_socket_and_scope_project(
    monkeypatch, tmp_path
) -> None:
    def fake_run(*args, **kwargs):
        raise OSError("docker cli missing")

    payload = [
        {
            "Id": "abc123",
            "Names": ["/phlo-postgres-1"],
            "State": "running",
            "Status": "Up 3 minutes (healthy)",
            "Labels": {
                "com.docker.compose.project": "phlo",
                "com.docker.compose.service": "postgres",
            },
        },
        {
            "Id": "def456",
            "Names": ["/other-postgres-1"],
            "State": "running",
            "Status": "Up 3 minutes (healthy)",
            "Labels": {
                "com.docker.compose.project": "other",
                "com.docker.compose.service": "postgres",
            },
        },
    ]

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory_services.DOCKER_SOCKET",
        str(tmp_path / "docker.sock"),
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory_services.Path.exists", lambda self: True
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory_services.docker_socket_json",
        lambda path, socket_path=str(tmp_path / "docker.sock"): payload,
    )
    monkeypatch.setenv("PHLO_COMPOSE_PROJECT", "phlo")

    statuses = _load_docker_service_statuses({"postgres"})

    assert statuses["postgres"][0] == "running"
    assert statuses["postgres"][1].state == "ok"


def test_observatory_docker_statuses_warn_on_recent_container_kill(monkeypatch) -> None:
    containers = [
        {
            "ID": "trino123",
            "Names": "phlo-trino-1",
            "State": "running",
            "Status": "Up 10 seconds (healthy)",
            "Labels": "com.docker.compose.project=phlo,com.docker.compose.service=trino",
        }
    ]

    monkeypatch.setenv("PHLO_COMPOSE_PROJECT", "phlo")
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory_services.docker_inspect_container",
        lambda _container_id: {
            "RestartCount": 3,
            "State": {
                "ExitCode": 0,
                "OOMKilled": False,
                "StartedAt": "2026-07-04T08:50:43Z",
                "FinishedAt": "2026-07-04T08:50:42Z",
                "Health": {
                    "Status": "healthy",
                    "Log": [{"ExitCode": 137}],
                },
            },
        },
    )

    statuses = _load_docker_service_statuses({"trino"}, containers)

    assert statuses["trino"][0] == "running"
    assert statuses["trino"][1].state == "warning"
    assert "recent container kill" in (statuses["trino"][1].message or "")


def test_observatory_load_services_includes_runtime_containers_missing_from_discovery(
    monkeypatch,
) -> None:
    containers = [
        {
            "ID": "abc123",
            "Names": "phlo-postgres-1",
            "State": "running",
            "Status": "Up 3 minutes (healthy)",
            "Labels": ("com.docker.compose.project=phlo,com.docker.compose.service=postgres"),
        }
    ]

    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory.load_project_docker_containers",
        lambda _project_root: containers,
    )
    monkeypatch.setenv("PHLO_COMPOSE_PROJECT", "phlo")

    services = _load_services()

    postgres = next(service for service in services if service.id == "postgres")
    assert postgres.in_stack is True
    assert postgres.backend == "docker"
    assert postgres.status == "running"


def test_observatory_load_services_uses_project_scoped_container_loader(
    monkeypatch, tmp_path
) -> None:
    containers = [{"Names": "phlo-postgres-1"}]
    observed: dict[str, object] = {}

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    def fake_project_containers(project_root: Path) -> list[dict[str, str]]:
        observed["project_root"] = project_root
        return containers

    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory.load_project_docker_containers",
        fake_project_containers,
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_services_impl",
        lambda project_root, containers: (
            observed.update(
                loader_project_root=project_root,
                containers=containers,
            )
            or []
        ),
    )

    assert _load_services() == []
    assert observed["project_root"] == tmp_path.resolve()
    assert observed["loader_project_root"] == tmp_path.resolve()
    assert observed["containers"] is containers


def test_observatory_load_services_marks_registry_service_runtime_container_in_stack(
    monkeypatch,
    tmp_path: Path,
) -> None:
    containers = [
        {
            "ID": "abc123",
            "Names": "phlo-postgres-1",
            "State": "running",
            "Status": "Up 3 minutes (healthy)",
            "Labels": ("com.docker.compose.project=phlo,com.docker.compose.service=postgres"),
        }
    ]

    monkeypatch.setenv("PHLO_COMPOSE_PROJECT", "phlo")
    monkeypatch.setattr(observatory_services, "load_docker_containers", lambda: containers)
    monkeypatch.setattr(
        observatory_services, "load_project_docker_containers", lambda project_root: containers
    )
    monkeypatch.setattr(
        observatory_services,
        "get_registry_data",
        lambda: {
            "plugins": {
                "postgres": {
                    "type": "service",
                    "package": "phlo-postgres",
                    "version": "0.1.0",
                    "description": "PostgreSQL database",
                    "tags": ["core", "database"],
                }
            }
        },
    )

    services = observatory_services.load_services(tmp_path, containers=containers)

    postgres = next(service for service in services if service.id == "postgres")
    assert postgres.in_stack is True
    assert postgres.backend == "docker"
    assert postgres.status == "running"
    assert postgres.definition_state == "configured"


def test_observatory_service_registry_metadata_matches_helper_services(monkeypatch) -> None:
    monkeypatch.setattr(
        observatory_services,
        "get_registry_data",
        lambda: {
            "plugins": {
                "openmetadata": {
                    "type": "hooks",
                    "package": "phlo-openmetadata",
                    "version": "0.1.0",
                    "description": "OpenMetadata integration",
                    "verified": True,
                    "tags": ["metadata"],
                }
            }
        },
    )
    monkeypatch.setattr(observatory_services, "_package_installed", lambda package: False)

    entries = observatory_services._registry_package_entries()
    metadata = observatory_services._registry_metadata("openmetadata-mysql", entries)

    assert metadata["registry_name"] == "openmetadata"
    assert metadata["package"] == "phlo-openmetadata"
    assert metadata["installable"] is True


def test_observatory_docker_statuses_ignore_containers_without_project_scope(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(
                {
                    "Names": "pokehunt-postgres",
                    "State": "running",
                    "Status": "Up 3 minutes (healthy)",
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("PHLO_COMPOSE_PROJECT", raising=False)
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)

    assert _load_docker_service_statuses({"postgres"}) == {}


def test_observatory_capabilities_include_running_service_backed_providers(monkeypatch) -> None:
    trino = ObservatoryService(
        id="trino",
        name="trino",
        kind="service",
        status="running",
        health=ObservatoryHealth(state="ok", message="healthy"),
        in_stack=True,
    )
    loki = ObservatoryService(
        id="loki",
        name="loki",
        kind="service",
        status="running",
        health=ObservatoryHealth(state="ok", message="healthy"),
        in_stack=True,
    )

    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_capability_registry", lambda: None
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_services", lambda: [trino, loki]
    )

    capabilities = _load_capabilities()

    assert capabilities.features["tables"] is True
    assert capabilities.features["logs"] is True
    assert "trino" in capabilities.providers["tables"]
    assert "loki" in capabilities.providers["logs"]


def test_observatory_disabled_service_action_skips_without_subprocess(monkeypatch) -> None:
    service = ObservatoryService(
        id="phlo-api",
        name="phlo-api",
        kind="api",
        status="running",
        health=ObservatoryHealth(state="ok"),
        in_stack=True,
    )
    subprocess_calls: list[object] = []

    def fake_run(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        raise AssertionError("disabled actions must not spawn subprocesses")

    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_services", lambda: [service])
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _execute_action(ObservatoryActionRequest(action_id="phlo-api:start"))

    assert result.status == "skipped"
    assert result.action.id == "phlo-api:start"
    assert result.message == result.action.reason
    assert subprocess_calls == []
    assert result.operation is None


def test_observatory_available_installed_service_can_be_added_to_stack(monkeypatch) -> None:
    service = ObservatoryService(
        id="alloy",
        name="alloy",
        kind="observability",
        status="unknown",
        health=ObservatoryHealth(state="unknown"),
        in_stack=False,
        metadata={"package": "phlo-alloy", "package_installed": True},
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="Services added.")

    monkeypatch.setattr(observatory, "_load_services", lambda: [service])
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _execute_action(ObservatoryActionRequest(action_id="alloy:add"))

    assert result.status == "succeeded"
    assert result.action.label == "Add to stack"
    assert commands == [["phlo", "services", "add", "alloy"]]


def test_observatory_actions_endpoint_routes_add_service_action(
    monkeypatch, tmp_path: Path
) -> None:
    service = ObservatoryService(
        id="pgweb",
        name="pgweb",
        kind="admin",
        status="unknown",
        health=ObservatoryHealth(state="unknown"),
        in_stack=False,
        metadata={"package": "phlo-pgweb", "package_installed": True},
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="Services added.")

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_services", lambda: [service])
    monkeypatch.setattr(subprocess, "run", fake_run)
    observatory._clear_read_model_cache()

    response = authenticated_client("admin").post(
        "/api/observatory/actions",
        json={"action_id": "pgweb:add"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["action"]["label"] == "Add to stack"
    assert payload["action"]["kind"] == "service.add"
    assert commands == [["phlo", "services", "add", "pgweb"]]


def test_observatory_asset_operational_routes_use_observatory_paths(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"operate-token":{"subject":"agent","scopes":["lakehouse:operate"]}}',
    )
    calls: list[tuple[str, str, object]] = []

    async def fake_history(asset_id: str, limit: int = 10, dagster_url: str | None = None):
        calls.append(("history", asset_id, limit))
        return [{"run_id": "run-123", "timestamp": "2026-01-01T00:00:00Z"}]

    async def fake_materialize(asset_id: str, payload, dagster_url: str | None = None):
        calls.append(("materialize", asset_id, payload.partition_key))
        return {
            "operation": "materialize_asset",
            "dry_run": payload.dry_run,
            "accepted": True,
            "asset_key_path": asset_id,
            "partition_key": payload.partition_key,
            "status": "DRY_RUN",
            "message": "Materialization request is valid.",
            "details": {},
        }

    async def fake_backfill(asset_id: str, payload, dagster_url: str | None = None):
        calls.append(("backfill", asset_id, payload.partitions))
        return {
            "operation": "backfill_asset",
            "dry_run": payload.dry_run,
            "accepted": True,
            "asset_key_path": asset_id,
            "status": "DRY_RUN",
            "message": "Backfill request is valid.",
            "details": {"partitions": payload.partitions},
        }

    async def fake_partitions(asset_id: str, dagster_url: str | None = None):
        calls.append(("partitions", asset_id, None))
        return [{"partition_key": "2026-04-26", "status": "UNKNOWN"}]

    provider = _FakeOrchestratorOperations(
        get_materialization_history=fake_history,
        materialize_asset=fake_materialize,
        backfill_asset=fake_backfill,
        list_partitions=fake_partitions,
    )
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)

    client = authenticated_client("admin")
    headers = {"Authorization": "Bearer operate-token"}
    history = client.get("/api/observatory/assets/silver/orders/materializations?limit=3")
    materialize = client.post(
        "/api/observatory/assets/silver/orders/materialize",
        json={"dry_run": True, "partition": "2026-04-26"},
        headers=headers,
    )
    backfill = client.post(
        "/api/observatory/assets/silver/orders/backfill",
        json={"dry_run": True, "partitions": ["2026-04-26"]},
        headers=headers,
    )
    partitions = client.get("/api/observatory/assets/silver/orders/partitions")

    assert history.status_code == 200
    assert history.json()[0]["run_id"] == "run-123"
    assert materialize.status_code == 200
    assert materialize.json()["asset_key_path"] == "silver/orders"
    assert backfill.status_code == 200
    assert backfill.json()["operation"] == "backfill_asset"
    assert partitions.status_code == 200
    assert partitions.json()[0]["partition_key"] == "2026-04-26"
    assert calls == [
        ("history", "silver/orders", 3),
        ("materialize", "silver/orders", "2026-04-26"),
        ("backfill", "silver/orders", ["2026-04-26"]),
        ("partitions", "silver/orders", None),
    ]


def test_observatory_asset_materializations_clamps_limit(monkeypatch) -> None:
    calls: list[int] = []

    async def fake_history(asset_id: str, limit: int = 10, dagster_url: str | None = None):
        calls.append(limit)
        return []

    provider = _FakeOrchestratorOperations(get_materialization_history=fake_history)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)

    client = authenticated_client("admin")
    too_low = client.get("/api/observatory/assets/silver/orders/materializations?limit=0")
    too_high = client.get("/api/observatory/assets/silver/orders/materializations?limit=999")

    assert too_low.status_code == 200
    assert too_high.status_code == 200
    assert calls == [1, 200]


def test_observatory_run_operational_routes_use_observatory_paths(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"operate-token":{"subject":"agent","scopes":["lakehouse:operate"]}}',
    )
    calls: list[tuple[str, str, object]] = []

    async def fake_status(run_id: str, dagster_url: str | None = None):
        calls.append(("status", run_id, None))
        return {"run_id": run_id, "status": "FAILURE"}

    async def fake_retry(run_id: str, payload, dagster_url: str | None = None):
        calls.append(("retry", run_id, payload.dry_run))
        return {
            "operation": "retry_failed_run",
            "dry_run": payload.dry_run,
            "accepted": True,
            "run_id": run_id,
            "status": "DRY_RUN",
            "message": "Run retry request is valid.",
            "details": {"run_status": "FAILURE"},
        }

    async def fake_cancel(run_id: str, payload, dagster_url: str | None = None):
        calls.append(("cancel", run_id, payload.reason, payload.idempotency_key))
        return {
            "operation": "cancel_run",
            "dry_run": False,
            "accepted": True,
            "run_id": run_id,
            "status": "CANCELING",
            "message": "Dagster accepted run cancellation.",
            "details": {},
        }

    provider = _FakeOrchestratorOperations(
        get_run_status=fake_status,
        retry_run=fake_retry,
        cancel_run=fake_cancel,
    )
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)

    client = authenticated_client("admin")
    headers = {"Authorization": "Bearer operate-token"}
    status = client.get("/api/observatory/runs/run-123/status")
    retry = client.post(
        "/api/observatory/runs/run-123/retry", json={"dry_run": False}, headers=headers
    )
    cancel = client.post(
        "/api/observatory/runs/run-123/cancel",
        json={"reason": "stuck", "idempotency_key": "cancel-key"},
        headers=headers,
    )

    assert status.status_code == 200
    assert status.json()["status"] == "FAILURE"
    assert retry.status_code == 200
    assert retry.json()["run_id"] == "run-123"
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELING"
    assert calls == [
        ("status", "run-123", None),
        ("retry", "run-123", False),
        ("cancel", "run-123", "stuck", "cancel-key"),
    ]


def test_observatory_operation_routes_enforce_scope_idempotency_audit_and_rate_limit(
    monkeypatch, tmp_path: Path, regulated_api_boundary
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_API_RATE_LIMIT_MATERIALIZE", "2")
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        json.dumps(
            {
                "read-token": {"subject": "reader", "scopes": ["lakehouse:read"]},
                "operate-token": {"subject": "operator-idem", "scopes": ["lakehouse:operate"]},
                "limited-token": {"subject": "operator-limited", "scopes": ["lakehouse:operate"]},
            }
        ),
    )
    calls: list[str] = []

    async def fake_materialize(asset_id: str, payload, dagster_url: str | None = None):
        calls.append(asset_id)
        return {
            "operation": "materialize_asset",
            "dry_run": payload.dry_run,
            "accepted": True,
            "run_id": f"run-{len(calls)}",
            "asset_key_path": asset_id,
            "partition_key": payload.partition_key,
            "status": "STARTED",
            "message": "Dagster accepted materialize_asset.",
            "details": {},
        }

    provider = _FakeOrchestratorOperations(materialize_asset=fake_materialize)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)
    client = authenticated_client("admin")

    missing = TestClient(app).post(
        "/api/observatory/assets/silver/orders/materialize",
        json={"dry_run": False},
    )
    forbidden = client.post(
        "/api/observatory/assets/silver/orders/materialize",
        json={"dry_run": False},
        headers={
            "Authorization": "Bearer read-token",
            "X-Test-Principal": "viewer",
        },
    )
    first = client.post(
        "/api/observatory/assets/silver/orders/materialize",
        json={"dry_run": False, "idempotency_key": "same-key"},
        headers={
            "Authorization": "Bearer operate-token",
            "X-Test-Principal": "operator",
            "X-Test-Subject": "operator-idem",
        },
    )
    replay = client.post(
        "/api/observatory/assets/silver/orders/materialize",
        json={"dry_run": False, "idempotency_key": "same-key"},
        headers={
            "Authorization": "Bearer operate-token",
            "X-Test-Principal": "operator",
            "X-Test-Subject": "operator-idem",
        },
    )
    limited_first = client.post(
        "/api/observatory/assets/silver/orders/materialize",
        json={"dry_run": True},
        headers={
            "Authorization": "Bearer limited-token",
            "X-Test-Principal": "operator",
            "X-Test-Subject": "operator-limited",
        },
    )
    limited_second = client.post(
        "/api/observatory/assets/silver/orders/materialize",
        json={"dry_run": True},
        headers={
            "Authorization": "Bearer limited-token",
            "X-Test-Principal": "operator",
            "X-Test-Subject": "operator-limited",
        },
    )
    limited_third = client.post(
        "/api/observatory/assets/silver/orders/materialize",
        json={"dry_run": True},
        headers={
            "Authorization": "Bearer limited-token",
            "X-Test-Principal": "operator",
            "X-Test-Subject": "operator-limited",
        },
    )

    assert missing.status_code == 401
    assert forbidden.status_code == 403
    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["run_id"] == "run-1"
    assert replay.json()["run_id"] == "run-1"
    assert limited_first.status_code == 200
    assert limited_second.status_code == 200
    assert limited_third.status_code == 429
    assert calls == ["silver/orders", "silver/orders", "silver/orders"]

    audit_path = tmp_path / ".phlo" / "audit" / "operations.jsonl"
    assert audit_path.exists()
    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert any(record["operation"] == "materialize_asset" for record in records)
    assert all(record["subject"] != "read-token" for record in records)


def test_observatory_concurrent_idempotency_endpoint_executes_provider_once(
    monkeypatch, tmp_path: Path, regulated_api_boundary
) -> None:
    """Two overlapping identical mutation requests execute the provider exactly once."""
    import httpx
    from phlo.capabilities import (
        AuthenticationProviderSpec,
        AuthorizationPolicyBackendSpec,
        clear_capabilities,
        register_capability,
    )
    from security_test_support import _HeaderAuthenticationProvider, _backend

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        json.dumps(
            {"operate-token": {"subject": "operator-idem", "scopes": ["lakehouse:operate"]}}
        ),
    )
    provider_call_count = 0
    provider_lock = threading.Lock()

    async def fake_materialize(asset_id: str, payload, dagster_url: str | None = None):
        nonlocal provider_call_count
        with provider_lock:
            provider_call_count += 1
        # Hold the claim open so the contender observes the pending state.
        await asyncio.sleep(0.4)
        return {
            "operation": "materialize_asset",
            "dry_run": payload.dry_run,
            "accepted": True,
            "run_id": "run-only",
            "asset_key_path": asset_id,
            "partition_key": payload.partition_key,
            "status": "STARTED",
            "message": "Dagster accepted materialize_asset.",
            "details": {},
        }

    provider = _FakeOrchestratorOperations(materialize_asset=fake_materialize)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)

    # Register the test auth provider once for the duration of the test so both
    # concurrent requests resolve a principal without per-request registration.
    register_capability(
        "authentication_provider",
        AuthenticationProviderSpec(
            name="test-concurrent", provider=_HeaderAuthenticationProvider()
        ),
    )
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(name="test-concurrent", provider=_backend()),
    )
    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "test-concurrent")
    monkeypatch.setenv("PHLO_AUTHORIZATION_BACKEND", "test-concurrent")

    headers = {
        "Authorization": "Bearer operate-token",
        "X-Test-Principal": "operator",
        "X-Test-Subject": "operator-idem",
    }
    body = {"dry_run": False, "idempotency_key": "endpoint-same-key"}

    async def call_once() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/observatory/assets/silver/orders/materialize",
                json=body,
                headers=headers,
            )

    async def main() -> tuple[httpx.Response, httpx.Response]:
        return await asyncio.gather(call_once(), call_once())

    try:
        r1, r2 = asyncio.run(main())
    finally:
        clear_capabilities("authentication_provider")
        clear_capabilities("authorization_policy_backend")

    assert provider_call_count == 1
    responses = (r1, r2)
    assert all(response.status_code in {200, 409} for response in responses)
    winners = [response for response in responses if response.status_code == 200]
    contenders = [response for response in responses if response.status_code == 409]
    assert winners
    assert all(winner.json()["run_id"] == "run-only" for winner in winners)
    assert all(
        contender.json()["detail"] == {"error": "idempotency_in_progress"}
        for contender in contenders
    )
    assert all(contender.headers.get("retry-after") is not None for contender in contenders)


def test_observatory_idempotency_outcome_unknown_endpoint_does_not_retry(
    monkeypatch, tmp_path: Path, regulated_api_boundary
) -> None:
    """A provider exception leaves an unknown claim; retrying returns 409 without execution."""
    from phlo.capabilities import (
        AuthenticationProviderSpec,
        AuthorizationPolicyBackendSpec,
        clear_capabilities,
        register_capability,
    )
    from security_test_support import _HeaderAuthenticationProvider, _backend

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        json.dumps(
            {"operate-token": {"subject": "operator-idem", "scopes": ["lakehouse:operate"]}}
        ),
    )
    provider_call_count = 0
    provider_lock = threading.Lock()

    async def fake_materialize(asset_id: str, payload, dagster_url: str | None = None):
        nonlocal provider_call_count
        with provider_lock:
            provider_call_count += 1
        raise RuntimeError("provider exploded")

    provider = _FakeOrchestratorOperations(materialize_asset=fake_materialize)
    monkeypatch.setattr(observatory, "resolve_orchestrator_operations", lambda: provider)

    register_capability(
        "authentication_provider",
        AuthenticationProviderSpec(name="test-unknown", provider=_HeaderAuthenticationProvider()),
    )
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(name="test-unknown", provider=_backend()),
    )
    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "test-unknown")
    monkeypatch.setenv("PHLO_AUTHORIZATION_BACKEND", "test-unknown")

    headers = {
        "Authorization": "Bearer operate-token",
        "X-Test-Principal": "operator",
        "X-Test-Subject": "operator-idem",
    }
    body = {"dry_run": False, "idempotency_key": "endpoint-unknown-key"}
    client = TestClient(app, raise_server_exceptions=False)
    try:
        first = client.post(
            "/api/observatory/assets/silver/orders/materialize",
            json=body,
            headers=headers,
        )
        retry = client.post(
            "/api/observatory/assets/silver/orders/materialize",
            json=body,
            headers=headers,
        )
    finally:
        clear_capabilities("authentication_provider")
        clear_capabilities("authorization_policy_backend")

    assert first.status_code == 500
    assert retry.status_code == 409
    assert retry.json()["detail"] == {"error": "idempotency_outcome_unknown"}
    assert provider_call_count == 1


def test_observatory_available_missing_package_service_add_is_disabled(monkeypatch) -> None:
    service = ObservatoryService(
        id="plugin-example",
        name="plugin-example",
        kind="service",
        status="unknown",
        health=ObservatoryHealth(state="unknown"),
        in_stack=False,
        metadata={"package": "phlo-plugin-example", "package_installed": False},
    )

    monkeypatch.setattr(observatory, "_load_services", lambda: [service])

    result = _execute_action(ObservatoryActionRequest(action_id="plugin-example:add"))

    assert result.status == "skipped"
    assert result.action.label == "Add to stack"
    assert result.action.enabled is False
    assert "Install phlo-plugin-example" in result.message


def test_observatory_operations_endpoint_includes_journal_records(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    observatory._clear_read_model_cache()
    append_operation(
        tmp_path,
        ObservatoryOperation(
            id="phlo-api:restart",
            name="Restart",
            kind="service.restart",
            status="succeeded",
            health=ObservatoryHealth(state="ok", message="Restarted"),
            target=ObservatoryResourceRef(kind="service", id="phlo-api", label="phlo-api"),
        ),
        record_id="op-restart",
        recorded_at="2026-05-16T12:00:00+00:00",
    )

    response = authenticated_client("admin").get("/api/observatory/operations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["id"] == "op-restart"
    assert payload["items"][0]["kind"] == "service.restart"
    assert payload["items"][0]["target"]["id"] == "phlo-api"


def test_observatory_manifest_records_enrich_lakehouse_surfaces(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_SQLITE_PATH", str(tmp_path / "empty.sqlite"))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    state_dir = tmp_path / ".phlo" / "observatory"
    state_dir.mkdir(parents=True)
    (state_dir / "lakehouse_manifest.json").write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "id": "gold/keystone_release_metrics",
                        "name": "gold/keystone_release_metrics",
                        "group": "gold",
                        "description": "Release analytics generated from Keystone runs.",
                        "kinds": ["table", "analytics"],
                    }
                ],
                "tables": [
                    {
                        "id": "gold.keystone_release_metrics",
                        "name": "keystone_release_metrics",
                        "namespace": "gold",
                        "asset_id": "gold/keystone_release_metrics",
                        "format": "duckdb",
                        "metadata": {
                            "records": 2,
                            "columns": [
                                {"name": "experiment_id", "type": "varchar"},
                                {"name": "package_size_mb", "type": "double"},
                            ],
                            "preview_rows": [
                                {
                                    "experiment_id": "EXP-0041",
                                    "package_size_mb": 2.35,
                                },
                                {
                                    "experiment_id": "EXP-0048",
                                    "package_size_mb": 3.03,
                                },
                            ],
                        },
                    }
                ],
                "quality": [
                    {
                        "id": "gold/keystone_release_metrics:freshness",
                        "name": "freshness",
                        "asset_id": "gold/keystone_release_metrics",
                        "status": "passing",
                        "blocking": True,
                    }
                ],
                "operations": [
                    {
                        "id": "keystone:package:EXP-0041",
                        "name": "Package EXP-0041",
                        "kind": "pipeline.package",
                        "status": "succeeded",
                        "health": {"state": "ok"},
                        "target": {
                            "kind": "asset",
                            "id": "gold/keystone_release_metrics",
                            "label": "gold/keystone_release_metrics",
                        },
                    }
                ],
                "runs": [
                    {
                        "id": "keystone-run-0041",
                        "name": "Keystone export catalog",
                        "status": "succeeded",
                        "started_at": "2026-05-17T20:00:25+01:00",
                        "completed_at": "2026-05-17T20:03:02+01:00",
                        "duration_seconds": 157,
                        "assets": [
                            {
                                "kind": "asset",
                                "id": "gold/keystone_release_metrics",
                                "label": "gold/keystone_release_metrics",
                            }
                        ],
                    }
                ],
                "logs": [
                    {
                        "id": "keystone-log-1",
                        "timestamp": "2026-05-17T20:03:02+01:00",
                        "level": "info",
                        "message": "Experiment package created",
                        "source": "keystone_pipeline",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    observatory._clear_read_model_cache()

    client = authenticated_client("admin")
    assets = client.get("/api/observatory/assets").json()["items"]
    tables = client.get("/api/observatory/tables").json()["items"]
    quality = client.get("/api/observatory/quality").json()["items"]
    operations = client.get("/api/observatory/operations").json()["items"]
    runs = client.get("/api/observatory/runs").json()["items"]
    logs = client.get("/api/observatory/logs").json()["items"]
    preview = client.get("/api/observatory/table-preview/gold.keystone_release_metrics").json()
    capabilities = client.get("/api/observatory/capabilities").json()

    assert assets[0]["id"] == "gold/keystone_release_metrics"
    assert tables[0]["metadata"]["records"] == 2
    assert quality[0]["status"] == "passing"
    assert operations[0]["kind"] == "pipeline.package"
    assert runs[0]["id"] == "keystone-run-0041"
    assert logs[0]["source"] == "keystone_pipeline"
    assert preview["rows"][0]["experiment_id"] == "EXP-0041"
    assert capabilities["features"]["tables"] is True
    assert capabilities["features"]["lineage"] is True
    assert "issues" not in capabilities["features"]
    assert capabilities["features"]["quality"] is True
    assert capabilities["features"]["runs"] is True
    assert capabilities["features"]["datasets"] is True
    assert capabilities["features"]["governance"] is True
    assert capabilities["features"]["publishing"] is True
    assert capabilities["features"]["pipelines"] is True
    pages = {page["id"]: page for page in capabilities["pages"]}
    assert "issues" not in pages
    assert pages["quality"]["available"] is True
    assert pages["quality"]["nav"] is True
    for page_id in ("datasets", "governance", "publishing", "pipelines"):
        assert pages[page_id]["nav"] is True
        assert pages[page_id]["metadata"]["domain"] == "datasets"
        assert pages[page_id]["metadata"]["contribution_policy"] == "shared_surface"
    assert pages["datasets"]["metadata"]["read_models"] == [
        "datasets",
        "dataset-profile",
    ]
    assert pages["governance"]["metadata"]["profile_sections"] == ["governance"]
    assert pages["publishing"]["metadata"]["actions"] == ["publish", "retire"]
    assert pages["pipelines"]["metadata"]["actions"] == [
        "retry",
        "cancel",
        "materialize",
        "backfill",
    ]


def test_observatory_generic_skipped_action_records_operation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_services", lambda: [])
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    observatory._clear_read_model_cache()

    response = authenticated_client("admin").post(
        "/api/observatory/actions",
        json={"action_id": "quality:raw.orders:rerun"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["operation"]["status"] == "skipped"
    assert payload["operation"]["kind"] == "quality.rerun"

    operations = authenticated_client("admin").get("/api/observatory/operations").json()["items"]
    assert operations[0]["id"] == payload["operation"]["id"]
    assert operations[0]["metadata"]["action_id"] == "quality:raw.orders:rerun"


def test_observatory_service_action_records_subprocess_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    observatory._clear_read_model_cache()
    service = ObservatoryService(
        id="phlo-api",
        name="phlo-api",
        kind="api",
        status="stopped",
        health=ObservatoryHealth(state="warning", message="stopped"),
        in_stack=True,
    )

    def fake_run(*args, **kwargs):
        assert args[0] == ["phlo", "services", "start", "--service", "phlo-api"]
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="phlo-api start requested\n",
        )

    monkeypatch.setattr(observatory, "_load_services", lambda: [service])
    monkeypatch.setattr(subprocess, "run", fake_run)

    response = authenticated_client("admin").post(
        "/api/observatory/actions",
        json={"action_id": "phlo-api:start"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["operation"]["kind"] == "service.start"
    assert payload["operation"]["target"]["id"] == "phlo-api"

    operations = authenticated_client("admin").get("/api/observatory/operations").json()["items"]
    assert operations[0]["id"] == payload["operation"]["id"]
    assert operations[0]["status"] == "succeeded"


def test_observatory_overview_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/overview")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"health", "counters", "attention", "events", "recent"}
    assert {
        "services",
        "operations",
        "assets",
        "tables",
        "quality",
        "incidents",
    } == set(payload["counters"])
    _assert_no_provider_url_settings(payload)


def test_observatory_overview_endpoint_returns_canonical_home_rows(monkeypatch) -> None:
    observatory._clear_read_model_cache()
    service = ObservatoryService(
        id="dagster",
        name="Dagster",
        kind="orchestrator",
        status="running",
        health=ObservatoryHealth(state="ok", message="running"),
        in_stack=True,
    )
    operation = ObservatoryOperation(
        id="revenue-refresh-20260702",
        name="Refresh Revenue Draft",
        kind="pipeline_run",
        status="failed",
        health=ObservatoryHealth(state="error", message="Reconciliation check failed."),
        target=ObservatoryResourceRef(kind="dataset", id="gold.revenue", label="Revenue Draft"),
        completed_at="2026-07-02T08:29:11Z",
        metadata={"failure_reason": "Reconciliation check failed."},
    )
    quality_checks = [
        ObservatoryQualityCheck(
            id="silver.orders:late_arrivals",
            name="Late arrivals monitored",
            asset_id="silver.orders",
            status="warning",
            severity="medium",
        ),
        ObservatoryQualityCheck(
            id="gold.revenue:reconciliation",
            name="Revenue reconciles to billing",
            asset_id="gold.revenue",
            status="failing",
            severity="critical",
        ),
    ]
    logs = [
        ObservatoryLogEvent(
            id="dataset-log",
            message="Revenue reconciliation is outside tolerance.",
            level="error",
            source="observatory-fixture",
            resource=ObservatoryResourceRef(
                kind="dataset", id="gold.revenue", label="Revenue Draft"
            ),
        ),
        ObservatoryLogEvent(
            id="plugin-noise",
            message="plugin_load_failed",
            level="error",
            source="phlo.plugins.discovery._plugin_loading",
            resource=ObservatoryResourceRef(kind="service", id="phlo-api", label="Phlo API"),
        ),
    ]

    monkeypatch.setattr(observatory, "load_project_docker_containers", lambda _root: [])
    monkeypatch.setattr(
        observatory,
        "_runtime_services_from_containers",
        lambda _containers, _disabled, _root: [service],
    )
    monkeypatch.setattr(observatory, "_load_operations", lambda: [operation])
    monkeypatch.setattr(observatory, "_load_logs", lambda: logs)
    monkeypatch.setattr(
        observatory,
        "_manifest_records",
        lambda key, model: quality_checks if key == "quality" else [],
    )

    response = authenticated_client("admin").get("/api/observatory/overview")

    assert response.status_code == 200
    payload = response.json()
    attention = payload["attention"]
    events = payload["events"]

    assert [
        (row["kind"], row["href"]) for row in attention if row["kind"] in {"quality", "operation"}
    ] == [
        ("quality", "/quality?checkId=gold.revenue%3Areconciliation"),
        ("quality", "/quality?checkId=silver.orders%3Alate_arrivals"),
        ("operation", "/operations?operationId=revenue-refresh-20260702"),
    ]
    assert not any(row["label"] == "plugin_load_failed" for row in attention)
    assert ("operation", "/operations?operationId=revenue-refresh-20260702") in [
        (row["kind"], row["href"]) for row in events
    ]
    assert not any(row["label"] == "plugin_load_failed" for row in events)
    observatory._clear_read_model_cache()


def test_observatory_capabilities_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"version", "pages", "features", "providers"}
    pages = {page["id"]: page for page in payload["pages"]}
    assert pages["overview"]["available"] is True
    assert pages["services"]["available"] is True
    assert pages["settings"]["available"] is True
    assert set(pages) == {
        "overview",
        "workflows",
        "tables",
        "lineage",
        "quality",
        "logs",
        "branches",
        "operations",
        "runs",
        "storage",
        "observability",
        "governance",
        "datasets",
        "publishing",
        "pipelines",
        "apis",
        "bi",
        "extensions",
        "services",
        "settings",
    }
    assert pages["workflows"]["available"] is True
    assert pages["tables"]["metadata"]["required_any"] == ["query_engine", "table_store"]
    assert "issues" not in pages
    assert pages["quality"]["nav"] is pages["quality"]["available"]
    assert pages["storage"]["nav"] is False
    assert pages["observability"]["nav"] is False
    for page_id in ("datasets", "governance", "publishing", "pipelines"):
        assert pages[page_id]["nav"] is pages[page_id]["available"]
    assert pages["publishing"]["metadata"]["read_models"] == [
        "datasets",
        "dataset-profile",
    ]
    assert pages["pipelines"]["metadata"]["read_models"] == [
        "pipelines",
        "dataset-profile",
    ]
    assert pages["apis"]["nav"] is False
    assert pages["bi"]["nav"] is False
    assert pages["extensions"]["nav"] is False
    _assert_no_provider_url_settings(payload)


def test_observatory_capabilities_endpoint_reloads_project_capabilities(monkeypatch) -> None:
    """Route gating must reflect package/service inventory changes immediately."""
    calls = 0

    def load_capabilities() -> ObservatoryCapabilities:
        nonlocal calls
        calls += 1
        return ObservatoryCapabilities(features={"tables": calls == 2})

    monkeypatch.setattr(observatory, "_load_capabilities", load_capabilities)

    first = authenticated_client("admin").get("/api/observatory/capabilities")
    second = authenticated_client("admin").get("/api/observatory/capabilities")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["features"] == {"tables": False}
    assert second.json()["features"] == {"tables": True}


def test_observatory_capabilities_enable_manifest_backed_operations(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_capability_registry", lambda: None
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_services", lambda: [])
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_lakehouse_manifest",
        lambda: {
            "operations": [
                {
                    "id": "refresh-orders",
                    "name": "Refresh Orders",
                    "kind": "pipeline_run",
                    "status": "succeeded",
                }
            ]
        },
    )

    capabilities = _load_capabilities().model_dump()
    settings = observatory._load_settings().model_dump()

    pages = {page["id"]: page for page in capabilities["pages"]}
    assert pages["operations"]["available"] is True
    assert pages["operations"]["nav"] is True
    assert capabilities["features"]["operations"] is True
    assert capabilities["providers"]["operations"] == ["lakehouse-manifest"]
    assert settings["features"]["operations"] is True
    assert settings["metadata"]["providers"]["operations"] == ["lakehouse-manifest"]


def test_observatory_capabilities_gate_provider_pages_when_providers_are_absent(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_capability_registry", lambda: None
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_services", lambda: [])

    payload = _load_capabilities().model_dump()

    pages = {page["id"]: page for page in payload["pages"]}
    assert pages["overview"]["available"] is True
    assert pages["services"]["available"] is True
    assert pages["settings"]["available"] is True
    assert pages["branches"]["available"] is False
    assert pages["branches"]["nav"] is False
    assert "issues" not in pages
    assert pages["quality"]["available"] is False
    assert pages["quality"]["nav"] is False
    assert pages["logs"]["available"] is True
    assert pages["logs"]["nav"] is True
    assert pages["extensions"]["available"] is True
    assert pages["extensions"]["nav"] is False
    _assert_no_provider_url_settings(payload)


def test_observatory_logs_include_project_phlo_logs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    observatory._clear_read_model_cache()
    log_dir = tmp_path / ".phlo" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "20260517.log").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-17T10:00:00Z",
                "level": "warning",
                "logger": "phlo.test",
                "event": "project_log_seen",
                "path": "/api/observatory/logs",
            }
        )
        + "\n"
    )

    response = authenticated_client("admin").get("/api/observatory/logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["message"] == "project_log_seen"
    assert payload["items"][0]["source"] == "phlo.test"
    assert payload["items"][0]["level"] == "warning"


def test_project_log_tail_reads_only_bounded_suffix_of_100_mib_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_dir = tmp_path / ".phlo" / "logs"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "history.log"
    historical_chunk = b'{"message":"historical"}\n' * 2048
    with log_path.open("wb") as handle:
        while handle.tell() < 100 * 1024 * 1024:
            handle.write(historical_chunk)
        for index in range(100):
            handle.write(
                (
                    json.dumps(
                        {
                            "timestamp": f"2026-05-17T10:00:{index:02d}Z",
                            "message": f"tail-{index}",
                        }
                    )
                    + "\n"
                ).encode()
            )

    original_open = Path.open
    bytes_read = 0

    class CountingReader:
        def __init__(self, handle) -> None:
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args) -> None:
            self._handle.__exit__(*args)

        def read(self, *args, **kwargs):
            nonlocal bytes_read
            data = self._handle.read(*args, **kwargs)
            bytes_read += len(data)
            return data

        def __getattr__(self, name):
            return getattr(self._handle, name)

    def counting_open(path: Path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return CountingReader(handle) if path == log_path and args[0] == "rb" else handle

    monkeypatch.setattr(Path, "open", counting_open)

    events = observatory._load_project_log_events(tmp_path)

    assert [event.message for event in events] == [f"tail-{index}" for index in range(99, -1, -1)]
    assert bytes_read <= observatory.LOG_TAIL_CHUNK_BYTES
    assert len(events) == observatory.FILE_LOG_EVENT_LIMIT


def test_project_log_tail_merges_newest_events_and_preserves_small_file_ids(tmp_path: Path) -> None:
    log_dir = tmp_path / ".phlo" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "a.log").write_text(
        "\n".join(
            json.dumps({"timestamp": timestamp, "message": message})
            for timestamp, message in (
                ("2026-05-17T10:00:00Z", "a-old"),
                ("2026-05-17T10:03:00Z", "a-new"),
            )
        )
        + "\n"
    )
    (log_dir / "b.log").write_text(
        "\n".join(
            json.dumps({"timestamp": timestamp, "message": message})
            for timestamp, message in (
                ("2026-05-17T10:01:00Z", "b-old"),
                ("2026-05-17T10:02:00Z", "b-new"),
            )
        )
        + "\n"
    )

    events = observatory._load_project_log_events(tmp_path)

    assert [event.message for event in events] == ["a-new", "b-new", "b-old", "a-old"]
    assert [event.id for event in events] == [
        "phlo:a.log:2",
        "phlo:b.log:2",
        "phlo:b.log:1",
        "phlo:a.log:1",
    ]


def test_project_log_tail_keeps_small_malformed_lines(tmp_path: Path) -> None:
    log_dir = tmp_path / ".phlo" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "malformed.log").write_text("not-json\n42\n\n")

    events = observatory._load_project_log_events(tmp_path)

    assert [(event.message, event.level, event.id) for event in events] == [
        ("not-json", "info", "phlo:malformed.log:1")
    ]


def test_project_log_tail_marks_oversized_malformed_line(tmp_path: Path) -> None:
    log_dir = tmp_path / ".phlo" / "logs"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "oversized.log"
    log_path.write_bytes(
        b"before\n" + b"x" * (observatory.MAX_LOG_EVENT_BYTES + 1) + b"\n" + b"after\n"
    )

    events = observatory._load_project_log_events(tmp_path)

    assert [event.message for event in events if event.message != "before"][-1].endswith(
        observatory.TRUNCATED_LOG_EVENT_MARKER
    )
    assert "before" in [event.message for event in events]
    assert "after" in [event.message for event in events]
    oversized_event = next(
        event for event in events if event.message.endswith(observatory.TRUNCATED_LOG_EVENT_MARKER)
    )
    assert (
        len(oversized_event.message)
        <= len(observatory.TRUNCATED_LOG_EVENT_MARKER) + observatory.MAX_LOG_EVENT_BYTES
    )


def test_observatory_log_telemetry_keeps_only_the_latest_50_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from phlo.capabilities import telemetry

    monkeypatch.setattr(observatory, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(observatory, "_manifest_records", lambda *_args: [])
    monkeypatch.setattr(observatory, "_load_project_log_events", lambda _root: [])
    monkeypatch.setattr(
        telemetry,
        "iter_telemetry_events",
        lambda _path: (
            {"id": str(index), "timestamp": str(index), "name": f"telemetry-{index}"}
            for index in range(75)
        ),
    )

    events = observatory._load_logs()

    assert [event.message for event in events] == [
        f"telemetry-{index}" for index in range(74, 24, -1)
    ]


def test_observatory_overview_health_describes_unavailable_runtime_state() -> None:
    health = _overview_health_from_services(_fallback_services())

    assert health.state == "unknown"
    assert health.message == "Runtime service state unavailable"


def test_observatory_services_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/services")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)
    assert payload["items"]
    assert {
        "id",
        "name",
        "kind",
        "status",
        "health",
        "depends_on",
        "impacts",
        "links",
        "metadata",
    } <= set(payload["items"][0])
    _assert_no_provider_url_settings(payload)


def test_observatory_service_detail_endpoint_returns_provider_neutral_payload() -> None:
    client = authenticated_client("admin")
    services = client.get("/api/observatory/services").json()["items"]
    response = client.get(f"/api/observatory/services/{services[0]['id']}")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "service",
        "dependencies",
        "dependents",
        "actions",
        "logs",
        "ports",
        "config",
    }
    assert payload["service"]["id"] == services[0]["id"]
    assert {"id", "label", "kind", "enabled", "requires_confirmation", "reason"} <= set(
        payload["actions"][0]
    )
    _assert_no_provider_url_settings(payload)


def test_observatory_operations_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/operations")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)
    _assert_no_provider_url_settings(payload)


def test_observatory_operations_endpoint_filters_before_returning_context_candidates(
    monkeypatch,
) -> None:
    operations = [
        ObservatoryOperation(
            id="op-orders-failed",
            name="Apply orders workflow",
            kind="workflow.apply",
            status="failed",
            health=ObservatoryHealth(state="error", message="Validation failed"),
            target=ObservatoryResourceRef(kind="workflow", id="orders", label="orders"),
        ),
        ObservatoryOperation(
            id="op-customers-failed",
            name="Apply customers workflow",
            kind="workflow.apply",
            status="failed",
            health=ObservatoryHealth(state="error", message="Validation failed"),
            target=ObservatoryResourceRef(kind="workflow", id="customers", label="customers"),
        ),
        ObservatoryOperation(
            id="op-orders-restart",
            name="Restart API",
            kind="service.restart",
            status="succeeded",
            health=ObservatoryHealth(state="ok"),
            target=ObservatoryResourceRef(kind="service", id="phlo-api", label="phlo-api"),
        ),
    ]
    monkeypatch.setattr(observatory, "_load_operations", lambda: operations)
    observatory._clear_read_model_cache()

    response = authenticated_client("admin").get(
        "/api/observatory/operations",
        params={"status": "failed", "kind": "workflow.apply", "q": "orders", "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["items"]] == ["op-orders-failed"]
    _assert_no_provider_url_settings(payload)


def test_observatory_operation_detail_endpoint_returns_provider_neutral_payload(
    monkeypatch,
) -> None:
    client = authenticated_client("admin")
    operation = ObservatoryOperation(
        id="compact:main:main",
        name="compact",
        kind="maintenance",
        status="succeeded",
        health=ObservatoryHealth(state="ok"),
        target=ObservatoryResourceRef(kind="branch", id="main", label="main"),
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_operations", lambda: [operation]
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_logs", lambda: [])

    response = client.get("/api/observatory/operations/compact:main:main")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"operation", "related", "logs", "actions"}
    assert payload["operation"]["id"] == operation.id
    _assert_no_provider_url_settings(payload)


def test_observatory_operation_agent_context_endpoint_returns_stable_contract(monkeypatch) -> None:
    client = authenticated_client("admin")
    operation = ObservatoryOperation(
        id="op-recorded",
        name="Apply workflow proposal",
        kind="workflow.apply",
        status="failed",
        health=ObservatoryHealth(state="error", message="Validation failed"),
        metadata={
            "observability_contract": {
                "operation_id": "op-recorded",
                "trace_ids": ["trace-123"],
                "log_ids": ["log-456"],
                "metric_ids": ["metric-789"],
                "incident_ids": ["incident-001"],
            }
        },
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_operations", lambda: [operation]
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_logs", lambda: [])

    response = client.get("/api/observatory/operations/op-recorded/agent-context")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "phlo.operation_observability.v1"
    assert payload["operation"]["id"] == "op-recorded"
    assert payload["identifiers"]["trace_ids"] == ["trace-123"]
    assert payload["incident"]["incident_ids"] == ["incident-001"]
    assert payload["retention"]["history_limit"] == 200
    _assert_no_provider_url_settings(payload)


def test_observatory_assets_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/assets")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)
    _assert_no_provider_url_settings(payload)


def test_table_catalog_state_is_explicit_when_catalog_read_is_unavailable() -> None:
    table = ObservatoryTable(id="orders", name="orders", namespace="gold")

    enriched = observatory._enrich_tables_with_catalog([table], None)

    assert enriched[0].metadata["catalog_state"] == "unknown"


def test_asset_derived_tables_include_unknown_catalog_state_when_catalog_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SimpleNamespace(
        list=lambda kind: (
            [
                SimpleNamespace(
                    key="gold.orders",
                    group="gold",
                    kinds=["table"],
                    metadata={"table": "orders", "schema": "gold", "format": "iceberg"},
                )
            ]
            if kind == "asset"
            else []
        )
    )
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: registry)
    monkeypatch.setattr(observatory, "_manifest_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(observatory, "_catalog_tables", lambda: None)

    tables = observatory._load_tables()

    assert tables[0].metadata["catalog_state"] == "unknown"


def test_observatory_asset_detail_endpoint_returns_related_provider_neutral_payload(
    monkeypatch,
) -> None:
    client = authenticated_client("admin")
    asset = ObservatoryAsset(id="raw.orders", name="raw.orders", group="raw", kinds=["table"])
    table = ObservatoryTable(
        id="orders",
        name="orders",
        namespace="raw",
        asset_id=asset.id,
        metadata={"columns": ["order_id", "customer_id"]},
    )
    check = ObservatoryQualityCheck(
        id="raw.orders:order_id_present",
        name="order_id_present",
        asset_id=asset.id,
        status="unknown",
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_assets", lambda: [asset])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_tables", lambda: [table])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_quality", lambda: [check])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_logs", lambda: [])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_operations", lambda: [])

    response = client.get("/api/observatory/assets/raw.orders")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "asset",
        "upstream",
        "downstream",
        "tables",
        "quality",
        "logs",
        "operations",
        "lineage",
        "materializations",
        "column_lineage",
    }
    assert payload["asset"]["id"] == asset.id
    _assert_no_provider_url_settings(payload)


def test_observatory_asset_graph_and_impact_are_first_class(monkeypatch) -> None:
    monkeypatch.setattr(
        observatory,
        "_load_assets",
        lambda: [
            ObservatoryAsset(id="silver.stg_orders", name="stg_orders", group="silver"),
            ObservatoryAsset(
                id="gold.fct_orders",
                name="fct_orders",
                group="gold",
                dependencies=["silver.stg_orders"],
            ),
        ],
    )

    client = authenticated_client("admin")
    graph_response = client.get("/api/observatory/asset-graph")
    impact_response = client.get(
        "/api/observatory/asset-graph/impact",
        params={"asset_key": "silver.stg_orders", "max_depth": 2},
    )

    assert graph_response.status_code == 200
    graph = graph_response.json()
    assert [node["key_path"] for node in graph["nodes"]] == [
        "silver.stg_orders",
        "gold.fct_orders",
    ]
    assert graph["edges"] == [{"source": "silver.stg_orders", "target": "gold.fct_orders"}]

    assert impact_response.status_code == 200
    assert impact_response.json() == [
        {
            "key_path": "gold.fct_orders",
            "label": "fct_orders",
            "layer": "gold",
            "depth": 1,
        }
    ]


def test_observatory_tables_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/tables")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)
    _assert_no_provider_url_settings(payload)


def test_observatory_table_preview_endpoint_returns_provider_neutral_payload(monkeypatch) -> None:
    client = authenticated_client("admin")
    table = ObservatoryTable(
        id="orders",
        name="orders",
        namespace="raw",
        metadata={
            "records": 12,
            "columns": ["order_id"],
            "column_types": {"order_id": "integer"},
        },
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_tables", lambda: [table])
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._preview_from_query_engine",
        lambda *_args, **_kwargs: None,
    )

    response = client.get("/api/observatory/table-preview/orders?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "table",
        "columns",
        "column_types",
        "rows",
        "row_count",
        "limit",
        "offset",
        "has_more",
        "state",
        "message",
    }
    assert payload["table"]["id"] == table.id
    assert payload["rows"] == [
        {"_phlo_row_id": "orders:1", "order_id": "order-0001"},
        {"_phlo_row_id": "orders:2", "order_id": "order-0002"},
        {"_phlo_row_id": "orders:3", "order_id": "order-0003"},
        {"_phlo_row_id": "orders:4", "order_id": "order-0004"},
        {"_phlo_row_id": "orders:5", "order_id": "order-0005"},
    ]
    assert payload["row_count"] == 12
    assert payload["has_more"] is True
    assert payload["state"] == "relation_missing"
    assert "unambiguous query relation" in payload["message"]
    assert len(payload["column_types"]) == len(payload["columns"])
    _assert_no_provider_url_settings(payload)


def test_observatory_query_endpoint_returns_provider_neutral_payload(monkeypatch) -> None:
    client = authenticated_client("admin")
    table = ObservatoryTable(
        id="orders",
        name="orders",
        namespace="raw",
        metadata={"columns": ["order_id"], "column_types": {"order_id": "integer"}},
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_tables", lambda: [table])
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._preview_from_query_engine",
        lambda *_args, **_kwargs: None,
    )

    response = client.post(
        "/api/observatory/query",
        json={"sql": "select * from orders limit 5"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "columns",
        "rows",
        "row_count",
        "effective_sql",
        "limit",
        "offset",
        "warnings",
    }
    assert payload["limit"] == 5
    _assert_no_provider_url_settings(payload)


def test_observatory_query_endpoint_rejects_unknown_tables(monkeypatch) -> None:
    client = authenticated_client("admin")
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_tables", lambda: [])

    response = client.post(
        "/api/observatory/query",
        json={"sql": "select * from arbitrary_table limit 5"},
    )

    assert response.status_code == 404
    assert "table not found" in response.json()["detail"].lower()


def test_observatory_query_engine_preserves_known_table_request_offset(monkeypatch) -> None:
    table = ObservatoryTable(id="orders", name="orders", namespace="raw", schema_name="silver")
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_tables", lambda: [table])
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._preview_from_query_engine",
        lambda table, limit, offset, **_: ObservatoryTablePreview(
            table=table,
            columns=["order_id"],
            rows=[{"order_id": 3}],
            limit=limit,
            offset=offset,
        ),
    )

    result = _run_read_query(
        ObservatoryQueryRequest(sql="select * from orders limit 1", limit=1, offset=25)
    )

    assert result.offset == 25


def test_observatory_saved_queries_contract_persists_provider_neutral_payload(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    from phlo.plugins.observatory_settings import _reset_memory_service

    _reset_memory_service()
    client = authenticated_client("admin")

    create_response = client.post(
        "/api/observatory/saved-queries",
        json={
            "name": "Recent orders",
            "sql": "select * from orders limit 10",
            "branch": "main",
        },
    )
    duplicate_response = client.post(
        "/api/observatory/saved-queries",
        json={
            "name": "Recent orders",
            "sql": "select   *  from   orders   limit 10",
            "branch": "main",
        },
    )
    list_response = client.get("/api/observatory/saved-queries")

    assert create_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert list_response.status_code == 200
    created = duplicate_response.json()
    payload = list_response.json()
    assert {"id", "name", "sql", "branch", "created_at", "updated_at", "metadata"} <= set(created)
    assert any(item["id"] == created["id"] for item in payload["items"])
    assert len(payload["items"]) == 1
    _assert_no_provider_url_settings(payload)


def test_observatory_stage_diff_endpoint_returns_provider_neutral_payload(monkeypatch) -> None:
    source_table = ObservatoryTable(id="orders", name="orders", namespace="raw")
    target_table = ObservatoryTable(id="orders_clean", name="orders_clean", namespace="silver")
    previews = {
        source_table.id: ObservatoryTablePreview(
            table=source_table,
            columns=["order_id", "amount"],
            column_types=["integer", "number"],
            rows=[{"_phlo_row_id": "orders:1", "order_id": 1, "amount": 12.5}],
            row_count=1,
            limit=20,
            offset=0,
        ),
        target_table.id: ObservatoryTablePreview(
            table=target_table,
            columns=["order_id", "amount", "status"],
            column_types=["integer", "number", "string"],
            rows=[
                {
                    "_phlo_row_id": "orders_clean:1",
                    "order_id": 1,
                    "amount": 12.5,
                    "status": "clean",
                }
            ],
            row_count=1,
            limit=20,
            offset=0,
        ),
    }
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_table_preview",
        lambda table_id, *, limit, offset: previews[table_id],
    )

    response = authenticated_client("admin").get(
        "/api/observatory/stage-diff",
        params={"source_table_id": "orders", "target_table_id": "orders_clean"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "source",
        "target",
        "columns",
        "rows",
        "summary",
        "metadata",
    }
    assert {"added", "removed", "changed", "unchanged"} <= set(payload["summary"])
    _assert_no_provider_url_settings(payload)


def test_observatory_row_journey_endpoint_returns_provider_neutral_payload(monkeypatch) -> None:
    client = authenticated_client("admin")
    table = ObservatoryTable(
        id="orders",
        name="orders",
        namespace="raw",
        asset_id="raw.orders",
        metadata={"columns": ["order_id"]},
    )
    asset = ObservatoryAsset(id="raw.orders", name="raw.orders", group="raw", kinds=["table"])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_tables", lambda: [table])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_assets", lambda: [asset])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_logs", lambda: [])
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._preview_from_query_engine",
        lambda *_args, **_kwargs: None,
    )

    response = client.get("/api/observatory/row-journey/orders/orders:1")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "table",
        "row_id",
        "row",
        "upstream",
        "downstream",
        "stages",
        "logs",
        "diff",
    }
    _assert_no_provider_url_settings(payload)


def test_observatory_contributing_rows_query_and_page(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo_api.observatory_api.lineage._resolve_lineage_sink",
        lambda: type(
            "LineageSink",
            (),
            {"get_asset_graph": lambda self: {"edges": {"silver/stg_orders": ["gold/fct_orders"]}}},
        )(),
    )

    async def fake_execute_trino_query(
        query: str,
        catalog: str | None = None,
        schema: str | None = None,
        trino_url: str | None = None,
        timeout_ms: int | None = None,
    ):
        del catalog, schema, trino_url, timeout_ms
        if "information_schema.tables" in query:
            return {
                "columns": ["table_schema"],
                "column_types": ["varchar"],
                "rows": [{"table_schema": "silver"}],
            }
        if "information_schema.columns" in query:
            return {
                "columns": ["column_name", "data_type"],
                "column_types": ["varchar", "varchar"],
                "rows": [{"column_name": "_phlo_row_id", "data_type": "varchar"}],
            }
        return {
            "columns": ["_phlo_row_id"],
            "column_types": ["varchar"],
            "rows": [{"_phlo_row_id": "abc123"}],
        }

    monkeypatch.setattr(
        "phlo_api.observatory_api.contributing.execute_trino_query",
        fake_execute_trino_query,
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.contributing.resolve_default_catalog",
        lambda: "iceberg",
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.contributing.resolve_default_ref",
        lambda: "main",
    )

    client = authenticated_client("admin")
    payload = {
        "downstream_asset_key": "gold/fct_orders",
        "upstream_asset_key": "silver/stg_orders",
        "row_data": {"_phlo_row_id": "abc123"},
    }

    query_response = client.post(
        "/api/observatory/contributing-rows/query",
        json={**payload, "limit": 25},
    )
    page_response = client.post(
        "/api/observatory/contributing-rows/page",
        json={**payload, "page": 0, "page_size": 25},
    )

    assert query_response.status_code == 200
    assert query_response.json() == {
        "query": 'SELECT * FROM "iceberg"."silver"."stg_orders" WHERE "_phlo_row_id" = \'abc123\' ORDER BY "_phlo_row_id" LIMIT 25',
        "upstream": {"schema": "silver", "table": "stg_orders"},
    }

    assert page_response.status_code == 200
    assert page_response.json() == {
        "mode": "entity",
        "page": 0,
        "page_size": 25,
        "has_more": False,
        "query": 'SELECT * FROM "iceberg"."silver"."stg_orders" WHERE "_phlo_row_id" = \'abc123\' ORDER BY "_phlo_row_id" OFFSET 0 LIMIT 26',
        "upstream": {"schema": "silver", "table": "stg_orders"},
        "columns": ["_phlo_row_id"],
        "column_types": ["varchar"],
        "rows": [{"_phlo_row_id": "abc123"}],
    }


def test_contributing_rows_reject_client_provider_routing(monkeypatch) -> None:
    called = False

    async def forbidden_resolver(*_args, **_kwargs):  # noqa: ANN002, ANN003
        nonlocal called
        called = True
        raise AssertionError("client-controlled provider routing reached the resolver")

    monkeypatch.setattr(
        "phlo_api.observatory_api.contributing.resolve_iceberg_table",
        forbidden_resolver,
    )
    response = authenticated_client("analyst").post(
        "/api/observatory/contributing-rows/query",
        json={
            "downstream_asset_key": "gold/fct_orders",
            "upstream_asset_key": "silver/stg_orders",
            "row_data": {"_phlo_row_id": "abc123"},
            "catalog": "other_catalog",
            "trino_url": "http://169.254.169.254/",
        },
    )

    assert response.status_code == 422
    assert called is False


def test_observatory_branch_action_contract_skips_until_provider_write_contract_exists(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    observatory._clear_read_model_cache()
    response = authenticated_client("admin").post(
        "/api/observatory/branches/actions",
        json={"action_id": "branch:create:review/demo"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"action", "status", "message", "operation"}
    assert payload["status"] == "skipped"
    assert payload["action"]["enabled"] is False
    assert payload["operation"]["status"] == "skipped"
    _assert_no_provider_url_settings(payload)


def test_observatory_branch_action_skips_when_branches_capability_is_unavailable(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_capabilities",
        lambda: ObservatoryCapabilities(features={"branches": False}),
    )

    def fail_write(_branches):
        raise AssertionError("branch action must not write without a catalog provider")

    monkeypatch.setattr("phlo_api.observatory_api.observatory._write_branches", fail_write)

    response = authenticated_client("admin").post(
        "/api/observatory/branches/actions",
        json={"action_id": "branch:create:review/demo"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["action"]["kind"] == "branch.create"
    assert payload["action"]["enabled"] is False
    assert "catalog provider" in payload["message"]
    assert payload["operation"]["status"] == "skipped"


def test_observatory_branch_action_skip_is_recorded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: None)
    observatory._clear_read_model_cache()

    response = authenticated_client("admin").post(
        "/api/observatory/branches/actions",
        json={"action_id": "branch:create:experiment"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["operation"]["status"] == "skipped"
    assert payload["operation"]["kind"] == "branch.create"

    operations = authenticated_client("admin").get("/api/observatory/operations").json()["items"]
    assert operations[0]["id"] == payload["operation"]["id"]
    assert operations[0]["metadata"]["action_id"] == "branch:create:experiment"


def test_observatory_operations_include_wap_reports(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    reports_dir = tmp_path / ".phlo" / "wap-reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "run-1.json").write_text(
        json.dumps(
            {
                "schema_version": "phlo.wap_report.v1",
                "run_id": "run-1",
                "status": "promoted",
                "branch": "pipeline-run-1",
                "source_hash": "source",
                "target_branch": "main",
                "target_hash_before": "before",
                "target_hash_after": "after",
                "updated_at": "2026-06-17T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    observatory._clear_read_model_cache()

    payload = authenticated_client("admin").get("/api/observatory/operations").json()

    wap = next(item for item in payload["items"] if item["id"] == "wap:run-1")
    assert wap["status"] == "succeeded"
    assert wap["target"]["id"] == "pipeline-run-1"
    assert wap["metadata"]["target_hash_after"] == "after"


@pytest.mark.parametrize(
    ("report_status", "operation_status", "health_state"),
    [
        ("failed", "failed", "error"),
        ("cancelled", "failed", "error"),
        ("promotion_blocked", "failed", "error"),
        ("cleanup_complete", "succeeded", "ok"),
    ],
)
def test_observatory_operations_classify_terminal_wap_reports(
    monkeypatch,
    tmp_path: Path,
    report_status: str,
    operation_status: str,
    health_state: str,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    reports_dir = tmp_path / ".phlo" / "wap-reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "run-terminal.json").write_text(
        json.dumps(
            {
                "run_id": "run-terminal",
                "status": report_status,
                "branch": "pipeline-run-terminal",
                "updated_at": "2026-06-17T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    observatory._clear_read_model_cache()

    payload = authenticated_client("admin").get("/api/observatory/operations").json()

    wap = next(item for item in payload["items"] if item["id"] == "wap:run-terminal")
    assert wap["status"] == operation_status
    assert wap["health"]["state"] == health_state


def test_observatory_branch_detail_uses_wap_report_tables_when_catalog_tables_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    reports_dir = tmp_path / ".phlo" / "wap-reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "run-1.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "status": "promoted",
                "branch": "pipeline-run-1",
                "tables": [
                    {
                        "id": "analytics.orders",
                        "name": "orders",
                        "namespace": "analytics",
                        "format": "iceberg",
                        "records": 128,
                    }
                ],
                "updated_at": "2026-06-17T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_branches",
        lambda: [
            ObservatoryBranch(
                id="pipeline-run-1",
                name="pipeline-run-1",
                metadata={"changed": 1, "tables": 1},
            )
        ],
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_tables", lambda: [])
    observatory._clear_read_model_cache()

    payload = authenticated_client("admin").get("/api/observatory/branches/pipeline-run-1").json()

    assert payload["tables"][0]["id"] == "analytics.orders"
    assert payload["contents"][0]["label"] == "orders"


def test_observatory_branch_actions_use_registered_catalog_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class CatalogProvider:
        def __init__(self) -> None:
            self.branches: dict[str, str] = {}
            self.promoted: list[tuple[str, str]] = []

        def list_branches(self):
            return [
                {"name": name, "hash": hash_value} for name, hash_value in self.branches.items()
            ]

        def create_branch(self, name: str, from_ref: str = "main") -> str | None:
            self.branches[name] = f"{from_ref}-hash"
            return self.branches[name]

        def merge_branch(self, source: str, target: str = "main") -> bool:
            self.promoted.append((source, target))
            return source in self.branches

        def delete_branch(self, name: str) -> bool:
            return self.branches.pop(name, None) is not None

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    provider = CatalogProvider()
    registry = CapabilityRegistry()
    registry.register("catalog", CatalogSpec(name="catalog", provider=provider))
    monkeypatch.setattr(observatory, "_load_capability_registry", lambda: registry)
    observatory._clear_read_model_cache()

    client = authenticated_client("admin")
    create_response = client.post(
        "/api/observatory/branches/actions",
        json={"action_id": "branch:create:experiment"},
    )
    promote_response = client.post(
        "/api/observatory/branches/actions",
        json={"action_id": "branch:promote:experiment"},
    )
    delete_response = client.post(
        "/api/observatory/branches/actions",
        json={"action_id": "branch:delete:experiment"},
    )

    assert create_response.status_code == 200
    assert create_response.json()["status"] == "succeeded"
    assert create_response.json()["operation"]["status"] == "succeeded"
    assert promote_response.status_code == 200
    assert promote_response.json()["status"] == "succeeded"
    assert provider.promoted == [("experiment", "main")]
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "succeeded"
    assert provider.branches == {}


def test_observatory_quality_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/quality")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)
    _assert_no_provider_url_settings(payload)


def test_observatory_quality_detail_endpoint_returns_provider_neutral_payload(monkeypatch) -> None:
    client = authenticated_client("admin")
    asset = ObservatoryAsset(id="raw.orders", name="raw.orders", group="raw", kinds=["table"])
    check = ObservatoryQualityCheck(
        id="raw.orders:order_id_present",
        name="order_id_present",
        asset_id=asset.id,
        status="unknown",
    )
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_assets", lambda: [asset])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_quality", lambda: [check])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_logs", lambda: [])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_operations", lambda: [])

    response = client.get("/api/observatory/quality/raw.orders:order_id_present")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"check", "asset", "history", "logs", "actions"}
    assert payload["check"]["id"] == check.id
    _assert_no_provider_url_settings(payload)


def test_observatory_logs_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/logs")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)
    _assert_no_provider_url_settings(payload)


def test_observatory_log_facets_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/logs/facets")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"sources", "levels", "resources"}
    _assert_no_provider_url_settings(payload)


def test_observatory_branches_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/branches")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert {
        "id": "main",
        "name": "main",
        "current": True,
        "protected": True,
        "metadata": {},
    } in payload["items"]
    _assert_no_provider_url_settings(payload)


def test_observatory_branch_detail_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/branches/main")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"branch", "contents", "commits", "compare", "tables"}
    assert payload["branch"]["name"] == "main"
    _assert_no_provider_url_settings(payload)


def test_observatory_extensions_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/extensions")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)
    _assert_no_provider_url_settings(payload)


def test_observatory_extension_detail_endpoint_returns_provider_neutral_payload(
    monkeypatch,
) -> None:
    client = authenticated_client("admin")
    extension = ObservatoryExtension(
        id="observatory-demo",
        name="Observatory Demo",
        version="0.1.0",
        routes=["/demo"],
        nav=["/demo"],
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_extensions", lambda: [extension]
    )

    response = client.get("/api/observatory/extensions/observatory-demo")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"extension", "routes", "nav", "capabilities"}
    assert payload["extension"]["id"] == extension.id
    _assert_no_provider_url_settings(payload)


def test_observatory_extension_settings_put_reads_json_body() -> None:
    response = authenticated_client("admin").put(
        "/api/observatory/extensions/not-installed/settings",
        json={"settings": {"theme": "dark"}},
    )
    invalid = authenticated_client("admin").put(
        "/api/observatory/extensions/not-installed/settings",
        json={"theme": "dark"},
    )

    assert response.status_code == 404
    assert invalid.status_code == 422


def test_observatory_settings_endpoint_returns_provider_neutral_payload(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_COMPOSE_PROJECT", "observatory-real-stack")

    response = authenticated_client("admin").get("/api/observatory/settings")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"version", "defaults", "features", "storage", "metadata"}
    assert payload["version"] == 2
    assert payload["defaults"]["branch"] == "main"
    assert payload["metadata"]["runtime"] == {
        "project_path": str(tmp_path.resolve()),
        "compose_project": "observatory-real-stack",
        "api_source": "phlo-api",
    }
    _assert_no_provider_url_settings(payload)


def test_observatory_search_endpoint_returns_provider_neutral_payload() -> None:
    response = authenticated_client("admin").get("/api/observatory/search", params={"q": "gold"})

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)
    _assert_no_provider_url_settings(payload)


def test_observatory_search_endpoint_url_encodes_resource_href_segments(monkeypatch) -> None:
    client = authenticated_client("admin")
    asset = ObservatoryAsset(id="silver/demo", name="silver/demo", group="silver", kinds=["table"])
    table = ObservatoryTable(id="analytics/demo", name="demo", namespace="analytics")
    check = ObservatoryQualityCheck(
        id="silver/demo:row-count",
        name="demo row count",
        asset_id="silver/demo",
        status="failing",
    )
    extension = ObservatoryExtension(id="demo/ext", name="Demo Extension", version="0.1.0")
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_services", lambda: [])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_assets", lambda: [asset])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_tables", lambda: [table])
    monkeypatch.setattr("phlo_api.observatory_api.observatory._load_quality", lambda: [check])
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._load_extensions", lambda: [extension]
    )

    response = client.get("/api/observatory/search", params={"q": "demo"})

    assert response.status_code == 200
    hrefs = {item["kind"]: item["href"] for item in response.json()["items"]}
    assert hrefs["asset"] == "/lineage?assetId=silver%2Fdemo"
    assert hrefs["table"] == "/tables?tableId=analytics%2Fdemo"
    assert hrefs["quality"] == "/quality?checkId=silver%2Fdemo%3Arow-count"
    assert hrefs["extension"] == "/extensions/demo%2Fext"


def test_observatory_schema_diff_returns_stable_agent_envelope(monkeypatch) -> None:
    detail = ObservatoryAssetDetail(
        asset=ObservatoryAsset(id="raw.orders", name="raw.orders"),
        tables=[
            ObservatoryTable(id="orders", name="orders", metadata={"columns": ["id", "amount"]})
        ],
    )
    monkeypatch.setattr(observatory, "_load_asset_detail", lambda asset_key: detail)

    response = authenticated_client("admin").post(
        "/api/observatory/schemas/diff",
        json={"asset_key": "raw.orders", "from_run": "run-a", "to_run": "run-b"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_key"] == "raw.orders"
    assert payload["from_run"] == "run-a"
    assert payload["to_run"] == "run-b"
    assert payload["changes"] == []
    assert payload["snapshot_available"] is False
    assert payload["current_columns"] == ["id", "amount"]


def test_observatory_all_endpoints_do_not_leak_provider_url_setting_names() -> None:
    client = authenticated_client("admin")

    for path in (
        "/api/observatory/overview",
        "/api/observatory/capabilities",
        "/api/observatory/services",
        "/api/observatory/services/phlo-api",
        "/api/observatory/operations",
        "/api/observatory/operations/compact:main:main",
        "/api/observatory/assets",
        "/api/observatory/assets/raw.orders",
        "/api/observatory/tables",
        "/api/observatory/table-preview/orders",
        "/api/observatory/saved-queries",
        "/api/observatory/stage-diff?source_table_id=orders&target_table_id=orders_clean",
        "/api/observatory/row-journey/orders/orders:1",
        "/api/observatory/quality",
        "/api/observatory/quality/raw.orders:order_id_present",
        "/api/observatory/logs",
        "/api/observatory/logs/facets",
        "/api/observatory/branches",
        "/api/observatory/branches/main",
        "/api/observatory/extensions",
        "/api/observatory/settings",
        "/api/observatory/search?q=gold",
    ):
        response = client.get(path)

        if response.status_code == 404:
            continue
        assert response.status_code == 200, path
        _assert_no_provider_url_settings(response.json())


def test_preview_uses_one_bounded_neutral_query_without_a_count(monkeypatch) -> None:
    """A preview delegates one bounded request to the configured QueryEngine."""
    from phlo.capabilities import QueryPreviewResult

    calls: list[tuple[str, int, int, str | None]] = []

    class FakeQueryEngine:
        def preview(self, relation, *, limit, offset=0, schema=None):
            calls.append((relation, limit, offset, schema))
            return QueryPreviewResult(
                columns=["order_id"],
                column_types=["varchar"],
                rows=[{"order_id": "o-1"}],
                has_more=True,
            )

    monkeypatch.setattr("phlo.capabilities.discovery.discover_capabilities", lambda: None)
    monkeypatch.setattr(
        "phlo.capabilities.resolve_capability",
        lambda *_: SimpleNamespace(provider=FakeQueryEngine()),
    )
    table = ObservatoryTable(
        id="raw.orders",
        name="orders",
        namespace="raw",
        metadata={"catalog": "hive", "schema": "raw", "table": "orders"},
    )

    preview = observatory._preview_from_query_engine(table, limit=10, offset=20)

    assert preview is not None
    assert preview.row_count is None
    assert preview.has_more is True
    assert calls == [('"hive"."raw"."orders"', 10, 20, "raw")]


def test_preview_with_missing_relation_does_not_discover_catalog() -> None:
    table = ObservatoryTable(id="orders", name="orders", metadata={})

    assert observatory._query_relation_for_table(table) is None
