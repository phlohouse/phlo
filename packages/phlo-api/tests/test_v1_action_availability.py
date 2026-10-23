"""Operation availability reflects manifest policy and local gates, never grants mutations."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from phlo.capabilities import AuthorizationDecision
from phlo.exceptions import PhloCapabilitySetupError
from phlo_api.api import v1_action_availability as availability
from phlo_api.errors import BackendUnavailableError
from phlo_api.main import app
import security_test_support
from security_test_support import authenticated_client


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod", "nessie_ref": "release"},
                "staging": {"dagster_location": "staging", "nessie_ref": "review"},
            }
        ),
    )
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "prod": {"catalog": "preview_prod", "nessie_ref": "release"},
                "staging": {"catalog": "preview_staging", "nessie_ref": "review"},
            }
        ),
    )
    for name in (
        "PHLO_V1_QUERY_SINGLE_REPLICA",
        "PHLO_V1_QUERY_SINGLE_PROCESS",
        "PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED",
        "PHLO_V1_ACTIONS_SINGLE_REPLICA",
        "PHLO_V1_ACTIONS_SINGLE_PROCESS",
        "PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
    ):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD", "test-secret")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING", "other-secret")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_URL", "https://preview:8443")


@pytest.mark.parametrize(
    "role,can_query,can_write",
    [("viewer", False, False), ("analyst", True, False), ("operator", True, True)],
)
def test_availability_distinguishes_read_query_and_write_policy(
    configured, role, can_query, can_write
):
    response = authenticated_client(role).get("/api/v1/action-availability?env=prod")
    assert response.status_code == 200, response.text
    actions = response.json()["actions"]
    assert actions["v1_query_submit"]["allowed"] is can_query
    assert actions["v1_saved_query_create"]["allowed"] is can_write
    assert actions["v1_metric_model_create"]["allowed"] is can_write
    assert actions["v1_data_product_create"]["allowed"] is can_write
    assert actions["v1_branch_create"]["allowed"] is can_write
    assert actions["v1_job_launch"]["allowed"] is False
    assert all(item["reason"] for item in actions.values() if not item["allowed"])
    assert "test-secret" not in response.text


def test_availability_rechecks_configuration_and_never_overrides_mutation_policy(
    configured, monkeypatch
):
    client = authenticated_client("operator")
    path = "/api/v1/action-availability?env=prod&job_id=ingest&run_id=run-1&branch_name=prod-review"
    first = client.get(path)
    assert first.status_code == 200, first.text
    assert first.json()["actions"]["v1_job_launch"]["allowed"] is True
    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_PROCESS", "0")
    monkeypatch.setenv("PHLO_V1_ACTIONS_REF_TAG_CONTRACT", "0")
    actions = client.get(path).json()["actions"]
    assert actions["v1_query_submit"]["allowed"] is False
    assert "one explicitly asserted API process" in actions["v1_query_submit"]["reason"]
    assert actions["v1_job_launch"]["allowed"] is False
    assert actions["v1_branch_create"]["allowed"] is False
    assert actions["v1_metric_model_create"]["allowed"] is True
    viewer = authenticated_client("viewer")
    assert viewer.post("/api/v1/metric-models?env=prod", json={}).status_code == 403
    assert TestClient(app).get("/api/v1/action-availability?env=prod").status_code == 401
    assert client.get("/api/v1/action-availability?env=unknown").status_code == 422
    assert client.get("/api/v1/action-availability?env=prod&env=staging").status_code == 422


def test_contextual_availability_uses_exact_manifest_resource_and_environment(
    configured, monkeypatch
):
    default_backend = security_test_support._backend

    class ScopedPolicy:
        def explain_decision(self, principal, action, resource, context=None):
            if action == "run.manage" and resource.resource_id != "env=prod|job_id=permitted":
                return AuthorizationDecision(allowed=False, reason_code="denied")
            return default_backend().explain_decision(principal, action, resource, context)

    monkeypatch.setattr(security_test_support, "_backend", ScopedPolicy)
    client = authenticated_client("operator")
    allowed = client.get("/api/v1/action-availability?env=prod&job_id=permitted").json()["actions"]
    denied = client.get("/api/v1/action-availability?env=prod&job_id=other").json()["actions"]
    other_env = client.get("/api/v1/action-availability?env=staging&job_id=permitted").json()[
        "actions"
    ]
    assert allowed["v1_job_launch"]["allowed"] is True
    assert denied["v1_job_launch"]["allowed"] is False
    assert other_env["v1_job_launch"]["allowed"] is False


def test_availability_fails_closed_without_exposing_backend_errors(configured, monkeypatch):
    async def unavailable(*args):
        raise HTTPException(503, "private backend detail")

    monkeypatch.setattr(availability, "enforce_http_operation", unavailable)
    response = authenticated_client("operator").get("/api/v1/action-availability?env=prod")
    assert response.status_code == 200
    assert all(not item["allowed"] for item in response.json()["actions"].values())
    assert "private backend detail" not in response.text


def test_native_and_csv_preflights_require_exact_preview_binding_and_candidate_runtime(
    configured, monkeypatch, tmp_path
):
    missing = {"native": True, "candidate": True}

    def preview_settings(
        root, *, ref, required_ref, required_catalog, required_endpoint, required_user
    ):
        if missing["native"]:
            raise PhloCapabilitySetupError("transform", "Private setup detail", required=True)
        if (root, ref, required_ref, required_catalog, required_endpoint, required_user) != (
            tmp_path,
            "release",
            "release",
            "preview_prod",
            "https://preview:8443",
            "phlo_api_preview_prod",
        ):
            raise RuntimeError("Binding mismatch")

    def candidate():
        if missing["candidate"]:
            raise BackendUnavailableError("Writable candidate catalog is not configured.")

    modules = {
        "phlo_transform.editor": SimpleNamespace(preview_settings=preview_settings),
        "phlo_transform.editor_execution": SimpleNamespace(),
        "phlo_api.candidate_runtime": SimpleNamespace(preflight_candidate=candidate),
        "phlo_dlt.csv_upload": SimpleNamespace(),
    }
    monkeypatch.setattr(availability, "import_module", modules.__getitem__)
    client = authenticated_client("operator")
    path = "/api/v1/action-availability?env=prod"
    actions = client.get(path).json()["actions"]
    assert actions["v1_transform_model_compile"]["allowed"] is False
    assert "Private setup detail" not in actions["v1_transform_model_compile"]["reason"]
    assert actions["v1_csv_candidate_load"]["allowed"] is False
    assert actions["v1_csv_upload"]["allowed"] is True
    missing["native"] = False
    actions = client.get(path).json()["actions"]
    assert actions["v1_transform_model_compile"]["allowed"] is True
    assert actions["v1_transform_model_execute"]["allowed"] is False
    missing["candidate"] = False
    actions = client.get(path).json()["actions"]
    assert actions["v1_transform_model_execute"]["allowed"] is True
    assert actions["v1_csv_candidate_load"]["allowed"] is True
