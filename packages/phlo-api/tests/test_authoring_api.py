"""Tests for the agent-facing authoring API routes.

Exercises template creation, listing, validation scoping, and project-write
authorization against fake workflow scaffolds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from phlo_api.main import app
from security_test_support import authenticated_client


@dataclass(frozen=True)
class _FakeWorkflowResult:
    workflow_type: str
    provider: str
    domain: str
    table: str
    files: list[str]
    next_steps: list[str]
    metadata: dict[str, object] | None = None


def test_authoring_routes_create_and_list_templates(monkeypatch, tmp_path) -> None:
    from phlo_api.api import authoring

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"agent","scopes":["project:write"]}}',
    )

    def fake_create_workflow_with_provider(**kwargs):  # noqa: ANN003, ANN202
        return _FakeWorkflowResult(
            workflow_type=kwargs["workflow_type"],
            provider=kwargs["provider"] or "fake",
            domain=kwargs["domain"],
            table=kwargs["table"],
            files=["workflows/ingestion/demo/orders.py"],
            next_steps=["phlo materialize dlt_orders"],
            metadata={},
        )

    monkeypatch.setattr(
        authoring, "create_workflow_with_provider", fake_create_workflow_with_provider
    )

    client = authenticated_client("operator")
    headers = {"Authorization": "Bearer project-token"}
    created = client.post(
        "/api/authoring/workflows",
        json={"domain": "demo", "table": "orders", "unique_key": "id"},
        headers=headers,
    )
    templates = client.get("/api/authoring/templates")

    assert created.status_code == 200
    assert created.json()["provider"] == "fake"
    assert created.json()["files"] == ["workflows/ingestion/demo/orders.py"]
    assert templates.status_code == 200
    assert any(item["name"] == "csv-batch" for item in templates.json()["items"])


def test_authoring_create_workflow_returns_conflict_for_existing_files(
    monkeypatch, tmp_path
) -> None:
    from phlo_api.api import authoring

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"agent","scopes":["project:write"]}}',
    )

    def fake_create_workflow_with_provider(**kwargs):  # noqa: ANN003, ANN202
        raise FileExistsError("Files already exist:\n  - workflows/ingestion/demo/orders.py")

    monkeypatch.setattr(
        authoring, "create_workflow_with_provider", fake_create_workflow_with_provider
    )

    response = authenticated_client("operator").post(
        "/api/authoring/workflows",
        json={"domain": "demo", "table": "orders", "unique_key": "id"},
        headers={"Authorization": "Bearer project-token"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error": "workflow_already_exists",
        "message": "Files already exist:\n  - workflows/ingestion/demo/orders.py",
        "target": "demo/orders",
    }
    audit_path = tmp_path / ".phlo" / "audit" / "operations.jsonl"
    assert "workflow_already_exists" in audit_path.read_text(encoding="utf-8")


def test_authoring_create_workflow_uses_project_root_and_provider(monkeypatch, tmp_path) -> None:
    from phlo_api.api import authoring

    service_cwd = tmp_path / "service-cwd"
    service_cwd.mkdir()
    monkeypatch.chdir(service_cwd)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(project_root))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"agent","scopes":["project:write"]}}',
    )
    captured: dict[str, object] = {}

    def fake_create_workflow_with_provider(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return _FakeWorkflowResult(
            workflow_type=kwargs["workflow_type"],
            provider=kwargs["provider"],
            domain=kwargs["domain"],
            table=kwargs["table"],
            files=["workflows/ingestion/demo/orders.py"],
            next_steps=[],
            metadata={},
        )

    monkeypatch.setattr(
        authoring, "create_workflow_with_provider", fake_create_workflow_with_provider
    )

    response = authenticated_client("operator").post(
        "/api/authoring/workflows",
        json={"provider": "sling", "domain": "demo", "table": "orders", "unique_key": "id"},
        headers={"Authorization": "Bearer project-token"},
    )

    assert response.status_code == 200
    assert captured["project_root"] == project_root
    assert captured["provider"] == "sling"
    assert response.json()["provider"] == "sling"


def test_authoring_write_routes_require_project_write_scope(monkeypatch, tmp_path) -> None:
    from phlo_api.api import authoring

    monkeypatch.setattr("phlo_api.api.operation_controls.is_regulated", lambda: True)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"read-token":{"subject":"reader","scopes":["lakehouse:read"]},'
        '"project-token":{"subject":"agent","scopes":["project:write"]}}',
    )
    workflow_file = tmp_path / "workflow.py"
    workflow_file.write_text("# workflow\n", encoding="utf-8")
    monkeypatch.setattr(authoring, "_validate_workflow_file", lambda path: None)

    client = authenticated_client("operator")
    missing = TestClient(app).post(
        "/api/authoring/workflows/validate", json={"workflow_path": str(workflow_file)}
    )
    forbidden = client.post(
        "/api/authoring/workflows/validate",
        json={"workflow_path": str(workflow_file)},
        headers={
            "Authorization": "Bearer read-token",
            "X-Test-Principal": "viewer",
        },
    )
    allowed = client.post(
        "/api/authoring/workflows/validate",
        json={"workflow_path": str(workflow_file)},
        headers={"Authorization": "Bearer project-token"},
    )

    assert missing.status_code == 401
    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["valid"] is True
    audit_path = tmp_path / ".phlo" / "audit" / "operations.jsonl"
    assert "validate_workflow" in audit_path.read_text(encoding="utf-8")


def test_unregulated_authoring_validation_uses_development_identity(monkeypatch, tmp_path) -> None:
    from phlo_api.api import authoring

    workflow_file = tmp_path / "workflow.py"
    workflow_file.write_text("# workflow\n", encoding="utf-8")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr("phlo_api.api.operation_controls.is_regulated", lambda: False)
    monkeypatch.setattr(authoring, "_validate_workflow_file", lambda path: None)

    response = TestClient(app).post(
        "/api/authoring/workflows/validate", json={"workflow_path": str(workflow_file)}
    )

    assert response.status_code == 200
    audit_path = tmp_path / ".phlo" / "audit" / "operations.jsonl"
    assert '"subject": "development:anonymous"' in audit_path.read_text(encoding="utf-8")


def test_authoring_validation_rejects_paths_outside_project(monkeypatch, tmp_path) -> None:
    from phlo_api.api import authoring

    project_root = tmp_path / "project"
    outside_file = tmp_path / "outside.py"
    project_root.mkdir()
    outside_file.write_text("# not in project\n", encoding="utf-8")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(project_root))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"agent","scopes":["project:write"]}}',
    )
    monkeypatch.setattr(authoring, "_validate_workflow_file", lambda path: None)

    response = authenticated_client("operator").post(
        "/api/authoring/workflows/validate",
        json={"workflow_path": str(outside_file)},
        headers={"Authorization": "Bearer project-token"},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "path_outside_project" in response.json()["errors"][0]


def test_authoring_doctor_route_returns_json(monkeypatch) -> None:
    from phlo_api.api import authoring

    monkeypatch.setattr(authoring, "_run_diagnostics_quietly", lambda verbose=False: [])

    response = authenticated_client("operator").get("/api/authoring/doctor")

    assert response.status_code == 200
    assert response.json() == {"checks": [], "summary": {"fail": 0, "ok": 0, "skip": 0, "warn": 0}}


def test_authoring_doctor_uses_configured_project_root(monkeypatch, tmp_path) -> None:
    from phlo.cli.commands.doctor import DiagnosticResult, DiagnosticStatus
    from phlo_api.api import authoring

    service_cwd = tmp_path / "service-cwd"
    project_root = tmp_path / "project"
    service_cwd.mkdir()
    project_root.mkdir()
    monkeypatch.chdir(service_cwd)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(project_root))
    captured: dict[str, object] = {}

    def fake_run_diagnostics_quietly(verbose=False):  # noqa: ANN001, ANN202
        captured["cwd"] = Path.cwd()
        captured["verbose"] = verbose
        return [
            DiagnosticResult(
                "project.config",
                "Project",
                DiagnosticStatus.OK,
                "phlo.yaml parsed",
            )
        ]

    monkeypatch.setattr(authoring, "_run_diagnostics_quietly", fake_run_diagnostics_quietly)

    response = authenticated_client("operator").get("/api/authoring/doctor?verbose=true")

    assert response.status_code == 200
    assert captured == {"cwd": project_root, "verbose": True}
    assert Path.cwd() == service_cwd
    assert response.json()["checks"][0]["message"] == "phlo.yaml parsed"
