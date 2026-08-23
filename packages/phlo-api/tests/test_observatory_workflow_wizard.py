"""Tests the Observatory workflow wizard HTTP surface.

Exercises the FastAPI app with an authenticated project:write token: listing
package contributions, proposal validation (graph nodes, stages), file
previews, and the write paths guarded behind project-write scope.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from importlib import import_module
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from phlo.capabilities import WorkflowFilePreview, WorkflowProposal
from phlo_api.main import app
from security_test_support import _regulated_api_boundary, authenticated_client  # noqa: F401
from phlo_api.observatory_api import observatory
from phlo_api.observatory_api import observatory_workflow_wizard as wizard


client = authenticated_client("admin")


@pytest.fixture(autouse=True)
def _workflow_project_write_auth(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"operator","scopes":["project:write"]}}',
    )
    client.headers.update({"Authorization": "Bearer project-token"})
    yield
    client.headers.pop("Authorization", None)


def _can_load_workflow_plugin(module_name: str) -> bool:
    try:
        module = import_module(module_name)
    except Exception:
        return False
    return callable(getattr(module, "get_workflow_wizard_contributions", None))


def test_workflow_wizard_lists_package_contributions() -> None:
    response = client.get("/api/observatory/workflow-wizard")

    assert response.status_code == 200
    payload = response.json()
    ids = [item["id"] for item in payload["contributions"]]
    optional_contributions = (
        ("phlo_dlt.plugin", "dlt.rest-api-source"),
        ("phlo_sling.plugin", "sling.replication-source"),
        ("phlo_dbt.plugin", "dbt.transform"),
        ("phlo_pandera.plugin", "pandera.quality-checks"),
        ("phlo_dagster.plugin", "dagster.orchestration"),
        ("phlo_openmetadata.plugin", "openmetadata.catalog"),
    )
    for module_name, contribution_id in optional_contributions:
        if _can_load_workflow_plugin(module_name):
            assert contribution_id in ids
    assert payload["stages"] == ["source", "transform", "quality", "publish"]


def test_workflow_wizard_proposal_requires_graph_nodes() -> None:
    response = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {"nodes": [], "edges": []},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["graph"] == ["Add at least one workflow node."]


def test_workflow_wizard_proposal_requires_project_write(
    monkeypatch: pytest.MonkeyPatch, regulated_api_boundary
) -> None:
    request = {
        "workflow_name": "customer_health",
        "domain": "customers",
        "graph": {
            "nodes": [
                {
                    "id": "source",
                    "contribution_id": "dlt.rest-api-source",
                    "stage": "source",
                    "values": {"table_name": "orders"},
                }
            ],
            "edges": [],
        },
    }
    anonymous = TestClient(app).post("/api/observatory/workflow-wizard/proposals", json=request)
    assert anonymous.status_code == 401

    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"viewer-token":{"subject":"viewer","scopes":["project:read"]}}',
    )
    viewer = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json=request,
        headers={
            "Authorization": "Bearer viewer-token",
            "X-Test-Principal": "viewer",
        },
    )
    assert viewer.status_code == 403


def test_workflow_wizard_builds_dlt_dbt_graph_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    response = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {
                            "domain": "customers",
                            "table_name": "orders",
                            "unique_key": "order_id",
                            "api_base_url": "https://api.example.test",
                            "fields": ["total:float", "created_at:datetime"],
                        },
                    },
                    {
                        "id": "model",
                        "contribution_id": "dbt.transform",
                        "stage": "transform",
                        "values": {
                            "project_name": "customer_health",
                            "source_name": "raw",
                            "source_table": "orders",
                            "staging_model_name": "stg_orders",
                            "staging_source_relation": "raw.orders",
                            "dedupe_model_name": "clean_orders",
                            "partition_by": "order_id",
                            "order_by": "created_at",
                            "test_model_name": "clean_orders",
                            "unique_key": "order_id",
                        },
                    },
                ],
                "edges": [{"id": "source-model", "source": "source", "target": "model"}],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["planned_assets"] == ["dlt_orders"]
    assert payload["planned_models"] == ["stg_orders", "clean_orders"]
    assert "workflows/ingestion/customers/orders.py" in [item["path"] for item in payload["files"]]
    assert payload["actions"][0]["enabled"] is True


def test_workflow_wizard_builds_composed_transform_graph_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    response = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "recipe_catalog",
            "domain": "recipes",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {
                            "domain": "recipes",
                            "table_name": "recipes",
                            "unique_key": "id",
                            "api_base_url": "https://dummyjson.com/recipes",
                            "response_path": "recipes",
                            "fields": ["name:str", "rating:float"],
                        },
                    },
                    {
                        "id": "transform",
                        "contribution_id": "dbt.transform",
                        "stage": "transform",
                        "values": {
                            "project_name": "recipe_catalog",
                            "source_name": "raw",
                            "source_table": "recipes",
                            "staging_model_name": "stg_recipes",
                            "staging_source_relation": "raw.recipes",
                            "filter_model_name": "filtered_recipes",
                            "where": "rating >= 4.5",
                            "dedupe_model_name": "clean_recipes",
                            "partition_by": "id",
                            "order_by": "reviewCount",
                            "test_model_name": "clean_recipes",
                            "unique_key": "id",
                        },
                    },
                ],
                "edges": [
                    {"id": "source-transform", "source": "source", "target": "transform"},
                ],
            },
        },
    )

    assert response.status_code == 200
    paths = [item["path"] for item in response.json()["files"]]
    assert "workflows/transforms/dbt/dbt_project.yml" in paths
    assert "workflows/transforms/dbt/models/sources/raw.yml" in paths
    assert "workflows/transforms/dbt/models/stg_recipes.sql" in paths
    assert "workflows/transforms/dbt/models/filtered_recipes.sql" in paths
    assert "workflows/transforms/dbt/models/clean_recipes.sql" in paths
    assert "workflows/transforms/dbt/models/clean_recipes.yml" in paths


def test_workflow_wizard_builds_quality_and_publish_graph_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    response = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "recipe_catalog",
            "domain": "recipes",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "sling.replication-source",
                        "stage": "source",
                        "values": {
                            "domain": "recipes",
                            "source_name": "POSTGRES",
                            "source_stream": "public.recipes",
                            "target_table": "recipes",
                            "primary_key": "id",
                            "replication_mode": "incremental",
                            "update_key": "updated_at",
                        },
                    },
                    {
                        "id": "quality",
                        "contribution_id": "pandera.quality-checks",
                        "stage": "quality",
                        "values": {
                            "target_table": "recipes.clean_recipes",
                            "check_name": "clean_recipes_quality",
                            "unique_key": "id",
                            "not_null_columns": "id\nname",
                            "range_checks": "rating:0:5",
                        },
                    },
                    {
                        "id": "orchestration",
                        "contribution_id": "dagster.orchestration",
                        "stage": "publish",
                        "values": {
                            "job_name": "recipe_catalog_job",
                            "asset_group": "recipes",
                            "schedule": "0 2 * * *",
                        },
                    },
                    {
                        "id": "catalog",
                        "contribution_id": "openmetadata.catalog",
                        "stage": "publish",
                        "values": {
                            "service_name": "phlo",
                            "database": "warehouse",
                            "schema": "recipes",
                            "owner": "data-platform",
                            "tags": "domain.recipes\nsource.postgres",
                        },
                    },
                ],
                "edges": [
                    {"id": "source-quality", "source": "source", "target": "quality"},
                    {"id": "quality-orchestration", "source": "quality", "target": "orchestration"},
                    {"id": "orchestration-catalog", "source": "orchestration", "target": "catalog"},
                ],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    paths = [item["path"] for item in payload["files"]]
    assert payload["planned_assets"] == ["sling_recipes"]
    assert payload["disabled_stages"] == {}
    assert "workflows/ingestion/recipes/recipes_sling.py" in paths
    assert "workflows/ingestion/recipes/recipes_sling.yml" in paths
    assert "workflows/quality/recipes/recipes_quality.py" in paths
    assert "workflows/orchestration/recipe_catalog.py" in paths
    assert "workflows/catalog/recipes/recipe_catalog.yml" in paths


def test_workflow_wizard_apply_fails_on_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"operator","scopes":["project:write"]}}',
    )
    existing = tmp_path / "workflows" / "ingestion" / "customers"
    existing.mkdir(parents=True)
    (existing / "orders.py").write_text("# existing\n", encoding="utf-8")

    response = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {
                            "domain": "customers",
                            "table_name": "orders",
                            "unique_key": "order_id",
                        },
                    }
                ],
                "edges": [],
            },
        },
    )
    proposal = response.json()

    assert proposal["actions"][0]["enabled"] is False
    apply_response = client.post(
        "/api/observatory/workflow-wizard/actions",
        json={"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]},
        headers={"Authorization": "Bearer project-token"},
    )

    assert apply_response.status_code == 409
    assert "File conflicts" in apply_response.json()["detail"]


def test_workflow_wizard_apply_records_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"operator","scopes":["project:write"]}}',
    )
    observatory._clear_read_model_cache()
    proposal_response = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {
                            "domain": "customers",
                            "table_name": "orders",
                            "unique_key": "order_id",
                        },
                    }
                ],
                "edges": [],
            },
        },
    )
    proposal = proposal_response.json()

    apply_response = client.post(
        "/api/observatory/workflow-wizard/actions",
        json={"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]},
        headers={"Authorization": "Bearer project-token"},
    )

    assert apply_response.status_code == 200
    operations = client.get("/api/observatory/operations").json()["items"]
    assert operations[0]["kind"] == "workflow.apply"
    assert operations[0]["status"] == "succeeded"
    assert operations[0]["metadata"]["action_id"] == proposal["actions"][0]["id"]
    assert "workflows/ingestion/customers/orders.py" in operations[0]["metadata"]["files"]
    assert (
        stat.S_IMODE((tmp_path / "workflows/ingestion/customers/orders.py").stat().st_mode) == 0o644
    )


def test_workflow_wizard_conflict_records_failed_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"operator","scopes":["project:write"]}}',
    )
    observatory._clear_read_model_cache()
    existing = tmp_path / "workflows" / "ingestion" / "customers"
    existing.mkdir(parents=True)
    (existing / "orders.py").write_text("# existing\n", encoding="utf-8")
    proposal_response = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {
                            "domain": "customers",
                            "table_name": "orders",
                            "unique_key": "order_id",
                        },
                    }
                ],
                "edges": [],
            },
        },
    )
    proposal = proposal_response.json()

    apply_response = client.post(
        "/api/observatory/workflow-wizard/actions",
        json={"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]},
        headers={"Authorization": "Bearer project-token"},
    )

    assert apply_response.status_code == 409
    operations = client.get("/api/observatory/operations").json()["items"]
    assert operations[0]["kind"] == "workflow.apply"
    assert operations[0]["status"] == "failed"
    assert "File conflicts" in operations[0]["health"]["message"]


def test_workflow_wizard_apply_requires_project_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, regulated_api_boundary
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    proposal = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {"table_name": "orders"},
                    }
                ],
                "edges": [],
            },
        },
    ).json()
    body = {"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]}

    anonymous = TestClient(app).post("/api/observatory/workflow-wizard/actions", json=body)
    assert anonymous.status_code == 401

    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"viewer-token":{"subject":"viewer","scopes":["project:read"]}}',
    )
    viewer = client.post(
        "/api/observatory/workflow-wizard/actions",
        json=body,
        headers={
            "Authorization": "Bearer viewer-token",
            "X-Test-Principal": "viewer",
        },
    )
    assert viewer.status_code == 403
    assert not (tmp_path / "workflows").exists()


def test_workflow_wizard_proposal_is_bound_to_issuing_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, regulated_api_boundary
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"writer-a":{"subject":"writer-a","scopes":["project:write"]},'
        '"writer-b":{"subject":"writer-b","scopes":["project:write"]}}',
    )
    request = {
        "workflow_name": "customer_health",
        "domain": "customers",
        "graph": {
            "nodes": [
                {
                    "id": "source",
                    "contribution_id": "dlt.rest-api-source",
                    "stage": "source",
                    "values": {"table_name": "orders"},
                }
            ],
            "edges": [],
        },
    }
    proposal = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json=request,
        headers={
            "Authorization": "Bearer writer-a",
            "X-Test-Principal": "operator",
            "X-Test-Subject": "writer-a",
        },
    ).json()
    body = {"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]}

    other_writer = client.post(
        "/api/observatory/workflow-wizard/actions",
        json=body,
        headers={
            "Authorization": "Bearer writer-b",
            "X-Test-Principal": "operator",
            "X-Test-Subject": "writer-b",
        },
    )
    issuing_writer = client.post(
        "/api/observatory/workflow-wizard/actions",
        json=body,
        headers={
            "Authorization": "Bearer writer-a",
            "X-Test-Principal": "operator",
            "X-Test-Subject": "writer-a",
        },
    )

    assert other_writer.status_code == 404
    assert issuing_writer.status_code == 200


def test_workflow_wizard_rejects_target_directory_swap_before_safe_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"operator","scopes":["project:write"]}}',
    )
    workflow_root = tmp_path / "workflows"
    workflow_root.mkdir()
    proposal = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {"table_name": "orders"},
                    }
                ],
                "edges": [],
            },
        },
        headers={"Authorization": "Bearer project-token"},
    ).json()
    target = next(item for item in proposal["files"] if item["path"].endswith("orders.py"))
    outside = tmp_path / "outside-workflow"
    outside_target = outside / Path(target["path"]).relative_to("workflows")
    outside_target.parent.mkdir(parents=True)
    outside_target.write_text(target["content"], encoding="utf-8")

    original_validate = wizard._validated_workflow_targets

    def swap_after_validation(
        project_root: Path, workflow_proposal: WorkflowProposal
    ) -> list[tuple[WorkflowFilePreview, Path]]:
        targets = original_validate(project_root, workflow_proposal)
        safe_root = project_root / "workflows-safe"
        (project_root / "workflows").rename(safe_root)
        (project_root / "workflows").symlink_to(outside, target_is_directory=True)
        return targets

    monkeypatch.setattr(wizard, "_validated_workflow_targets", swap_after_validation)
    body = {"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]}
    applied = client.post(
        "/api/observatory/workflow-wizard/actions",
        json=body,
        headers={"Authorization": "Bearer project-token"},
    )

    assert applied.status_code == 409
    assert outside_target.read_text(encoding="utf-8") == target["content"]
    assert not (
        tmp_path / ".phlo" / "workflow-wizard" / "applied" / f"{proposal['proposal_id']}.json"
    ).exists()


def test_workflow_wizard_publish_does_not_replace_competing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    original_link = wizard.os.link
    injected: set[str] = set()

    def inject_competing_destination(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if destination not in injected:
            injected.add(destination)
            assert dst_dir_fd is not None
            descriptor = wizard.os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=dst_dir_fd,
            )
            try:
                wizard.os.write(descriptor, b"competing content")
            finally:
                wizard.os.close(descriptor)
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(wizard.os, "link", inject_competing_destination)
    fail_preview = WorkflowFilePreview(path="workflows/race-fail.py", content="generated")
    with pytest.raises(HTTPException) as error:
        wizard._apply_workflow_file(
            tmp_path,
            fail_preview,
            conflict_policy="fail-on-conflict",
        )

    skip_preview = WorkflowFilePreview(path="workflows/race-skip.py", content="generated")
    outcome = wizard._apply_workflow_file(
        tmp_path,
        skip_preview,
        conflict_policy="skip-if-exists",
    )

    assert error.value.status_code == 409
    assert outcome == "skipped"
    assert (workflows / "race-fail.py").read_text(encoding="utf-8") == "competing content"
    assert (workflows / "race-skip.py").read_text(encoding="utf-8") == "competing content"


def test_workflow_wizard_apply_rejects_tampered_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"operator","scopes":["project:write"]}}',
    )
    response = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {"table_name": "orders"},
                    }
                ],
                "edges": [],
            },
        },
    )
    proposal = response.json()
    stored_path = (
        tmp_path / ".phlo" / "workflow-wizard" / "proposals" / f"{proposal['proposal_id']}.json"
    )
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    stored["proposal"]["files"][0]["path"] = str(tmp_path.parent / "sentinel.py")
    stored_path.write_text(json.dumps(stored), encoding="utf-8")

    applied = client.post(
        "/api/observatory/workflow-wizard/actions",
        json={"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]},
        headers={"Authorization": "Bearer project-token"},
    )

    assert applied.status_code == 409
    assert not (tmp_path.parent / "sentinel.py").exists()
    assert not (tmp_path / "workflows").exists()


def test_workflow_wizard_apply_is_idempotent_for_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"operator","scopes":["project:write"]}}',
    )
    proposal = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {"table_name": "orders"},
                    }
                ],
                "edges": [],
            },
        },
    ).json()
    body = {"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]}
    headers = {"Authorization": "Bearer project-token"}

    first = client.post("/api/observatory/workflow-wizard/actions", json=body, headers=headers)
    second = client.post("/api/observatory/workflow-wizard/actions", json=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()


def test_workflow_wizard_replay_detects_changed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"project-token":{"subject":"operator","scopes":["project:write"]}}',
    )
    proposal = client.post(
        "/api/observatory/workflow-wizard/proposals",
        json={
            "workflow_name": "customer_health",
            "domain": "customers",
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "contribution_id": "dlt.rest-api-source",
                        "stage": "source",
                        "values": {"table_name": "orders"},
                    }
                ],
                "edges": [],
            },
        },
    ).json()
    body = {"action_id": proposal["actions"][0]["id"], "proposal_id": proposal["proposal_id"]}
    headers = {"Authorization": "Bearer project-token"}
    first = client.post("/api/observatory/workflow-wizard/actions", json=body, headers=headers)
    changed = tmp_path / first.json()["files"][0]
    changed.unlink()

    replay = client.post("/api/observatory/workflow-wizard/actions", json=body, headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 409
    assert "changed after completion" in replay.json()["detail"]


def test_workflow_wizard_rejects_absolute_traversal_and_symlink_paths(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-workflow-sentinel.py"
    cases = [
        "/tmp/absolute.py",
        "workflows/../outside.py",
        "workflows/../../outside.py",
    ]
    for path in cases:
        with pytest.raises(HTTPException) as error:
            wizard._validated_workflow_targets(
                tmp_path,
                WorkflowProposal(
                    workflow_name="unsafe",
                    domain="unsafe",
                    files=[WorkflowFilePreview(path=path, content="x")],
                ),
            )
        assert error.value.status_code == 400

    outside.mkdir(exist_ok=True)
    workflow_root = tmp_path / "workflows"
    workflow_root.mkdir()
    (workflow_root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(HTTPException) as error:
        wizard._validated_workflow_targets(
            tmp_path,
            WorkflowProposal(
                workflow_name="unsafe",
                domain="unsafe",
                files=[WorkflowFilePreview(path="workflows/escape/file.py", content="x")],
            ),
        )
    assert error.value.status_code == 400


def test_workflow_wizard_integrity_key_is_stable_across_calls(tmp_path: Path) -> None:
    first = wizard._workflow_integrity_key(tmp_path)
    second = wizard._workflow_integrity_key(tmp_path)

    assert first == second


def test_workflow_wizard_apply_retries_after_completion_record_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = wizard.build_workflow_proposal(
        tmp_path,
        wizard.ObservatoryWorkflowProposalRequest(
            workflow_name="customer_health",
            domain="customers",
            graph=wizard.ObservatoryWorkflowGraph(
                nodes=[
                    wizard.ObservatoryWorkflowGraphNode(
                        id="source",
                        contribution_id="dlt.rest-api-source",
                        stage="source",
                        values={"table_name": "orders"},
                    )
                ]
            ),
        ),
        issuer_subject="operator",
    )
    request = wizard.ObservatoryWorkflowActionRequest(
        action_id=proposal["actions"][0]["id"],
        proposal_id=proposal["proposal_id"],
    )
    original_write = wizard._write_state_json
    failed = False

    def fail_completion(
        project_root: Path,
        storage_name: str,
        filename: str,
        payload: dict[str, object],
    ) -> None:
        nonlocal failed
        if storage_name == "applied" and payload.get("status") == "succeeded" and not failed:
            failed = True
            raise OSError("simulated completion-record failure")
        original_write(project_root, storage_name, filename, payload)

    monkeypatch.setattr(wizard, "_write_state_json", fail_completion)
    with pytest.raises(OSError):
        wizard.apply_workflow_action(tmp_path, request, "operator")

    monkeypatch.setattr(wizard, "_write_state_json", original_write)
    result = wizard.apply_workflow_action(tmp_path, request, "operator")

    assert result.status == "succeeded"
    assert len(result.files) == len(proposal["files"])


def test_workflow_wizard_integrity_key_rejects_symlink(tmp_path: Path) -> None:
    state_dir = tmp_path / ".phlo" / "workflow-wizard"
    state_dir.mkdir(parents=True)
    outside = tmp_path / "outside.key"
    outside.write_bytes(b"attacker-controlled")
    (state_dir / "integrity.key").symlink_to(outside)

    with pytest.raises(HTTPException) as error:
        wizard._workflow_integrity_key(tmp_path)

    assert error.value.status_code == 503


def test_workflow_wizard_state_directory_rejects_symlink(tmp_path: Path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    outside = tmp_path / "outside-state"
    outside.mkdir()
    (phlo_dir / "workflow-wizard").symlink_to(outside, target_is_directory=True)

    with pytest.raises(HTTPException) as error:
        wizard._open_workflow_state_fd(tmp_path)

    assert error.value.status_code == 503


def test_workflow_wizard_state_writes_remain_anchored_after_directory_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def swap_storage(
        project_root: Path,
        storage_name: str,
        outside: Path,
        original_open: Callable[..., int],
    ) -> int:
        fd = original_open(project_root, storage_name, create=True)
        storage = project_root / ".phlo" / "workflow-wizard" / storage_name
        backup = storage.with_name(f"{storage_name}-safe")
        storage.rename(backup)
        storage.symlink_to(outside, target_is_directory=True)
        return fd

    proposal_root = tmp_path / "issuance"
    proposal_root.mkdir()
    proposal_outside = tmp_path / "proposal-outside"
    proposal_outside.mkdir()
    original_open = wizard._open_state_storage_fd

    def swap_proposals(project_root: Path, storage_name: str, *, create: bool) -> int:
        if storage_name == "proposals":
            return swap_storage(project_root, storage_name, proposal_outside, original_open)
        return original_open(project_root, storage_name, create=create)

    monkeypatch.setattr(wizard, "_open_state_storage_fd", swap_proposals)
    proposal = wizard.build_workflow_proposal(
        proposal_root,
        wizard.ObservatoryWorkflowProposalRequest(
            workflow_name="customer_health",
            domain="customers",
            graph=wizard.ObservatoryWorkflowGraph(
                nodes=[
                    wizard.ObservatoryWorkflowGraphNode(
                        id="source",
                        contribution_id="dlt.rest-api-source",
                        stage="source",
                        values={"table_name": "orders"},
                    )
                ]
            ),
        ),
        issuer_subject="operator",
    )
    proposal_files = list(
        (proposal_root / ".phlo" / "workflow-wizard" / "proposals-safe").glob("*.json")
    )
    assert proposal_files
    assert not list(proposal_outside.iterdir())
    assert proposal["proposal_id"] in proposal_files[0].name

    applied_root = tmp_path / "applied-record"
    applied_dir = applied_root / ".phlo" / "workflow-wizard" / "applied"
    applied_dir.mkdir(parents=True)
    applied_outside = tmp_path / "applied-outside"
    applied_outside.mkdir()
    swapped = False

    def swap_applied(project_root: Path, storage_name: str, *, create: bool) -> int:
        nonlocal swapped
        fd = original_open(project_root, storage_name, create=create)
        if storage_name == "applied" and not swapped:
            swapped = True
            storage = project_root / ".phlo" / "workflow-wizard" / storage_name
            backup = storage.with_name("applied-safe")
            storage.rename(backup)
            storage.symlink_to(applied_outside, target_is_directory=True)
        return fd

    monkeypatch.setattr(wizard, "_open_state_storage_fd", swap_applied)
    wizard._write_state_json(applied_root, "applied", "claim.json", {"status": "applying"})

    assert (applied_root / ".phlo" / "workflow-wizard" / "applied-safe" / "claim.json").exists()
    assert not list(applied_outside.iterdir())


def test_workflow_wizard_escapes_caller_values_in_generated_python(tmp_path: Path) -> None:
    marker = tmp_path / "injected-marker"
    payload = f'" or __import__("os").system("touch {marker}") or "'
    proposal = wizard.build_workflow_proposal(
        tmp_path,
        wizard.ObservatoryWorkflowProposalRequest(
            workflow_name="customer_health",
            domain="customers",
            graph=wizard.ObservatoryWorkflowGraph(
                nodes=[
                    wizard.ObservatoryWorkflowGraphNode(
                        id="source",
                        contribution_id="sling.replication-source",
                        stage="source",
                        values={
                            "table_name": "orders",
                            "source_stream": payload,
                            "source_name": payload,
                            "schedule": payload,
                        },
                    )
                ]
            ),
        ),
        issuer_subject="operator",
    )

    generated = next(
        item["content"] for item in proposal["files"] if item["path"].endswith("_sling.py")
    )
    compile(generated, "generated_sling.py", "exec")
    assert not marker.exists()
