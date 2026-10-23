"""Candidate ingestion preserves actor, immutable review and real run/result identity."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from graphql import parse, validate
from dagster_graphql.schema import create_schema

from phlo_api.api import v1_upload_ingestion as ingestion
from phlo_dlt.csv_upload import author_csv_upload
from security_test_support import authenticated_client
import security_test_support
from phlo.rbac.models import CanonicalAction


@pytest.fixture
def candidate(monkeypatch, tmp_path):
    monkeypatch.setitem(
        security_test_support._ACTIONS_BY_PRINCIPAL,
        "operator",
        {
            *security_test_support._ACTIONS_BY_PRINCIPAL["operator"],
            CanonicalAction.ASSET_MANAGE.value,
        },
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        '{"sandbox":{"dagster_location":"sandbox_jobs","nessie_ref":"main"},"prod":{"dagster_location":"prod_jobs","nessie_ref":"prod"}}',
    )
    client = authenticated_client("operator")
    upload = client.post(
        "/api/v1/uploads/csv?env=sandbox",
        content=b"id,result\n1,ND\n2,<LOQ\n",
        headers={"X-Phlo-Upload-Name": "batch.csv"},
    ).json()
    path = f"/api/v1/uploads/csv/{upload['upload_id']}"
    review = client.put(
        path + "/columns?env=sandbox", json={"expected_version": 1, "columns": upload["columns"]}
    )
    assert review.status_code == 200, review.text
    upload = review.json()
    monkeypatch.setattr(ingestion, "_head", AsyncMock(return_value="base-head"))
    monkeypatch.setattr(ingestion, "_new_relation", AsyncMock())
    binding = {
        "ref": f"sandbox-upload-{upload['upload_id']}",
        "actor": upload["actor"],
        "env": "sandbox",
    }
    monkeypatch.setattr(ingestion, "provision_candidate", AsyncMock(return_value=binding))
    monkeypatch.setattr(ingestion, "verify_candidate", AsyncMock(return_value="base-head"))
    monkeypatch.setattr(ingestion, "_reload", AsyncMock())
    monkeypatch.setattr(ingestion.v1_assets, "_assets", AsyncMock(return_value=[]))

    def author(**kwargs):
        result = author_csv_upload(kwargs["project_root"], kwargs["values"])
        return SimpleNamespace(**result)

    monkeypatch.setattr(ingestion, "create_workflow_with_provider", author)
    return client, path, upload, tmp_path


def prepare(candidate):
    client, path, upload, _ = candidate
    response = client.post(
        path + "/destination?env=sandbox",
        json={"expected_version": upload["version"], "table": "reviewed_batch"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def plan(candidate, monkeypatch):
    prepare(candidate)
    monkeypatch.setattr(
        ingestion.v1_assets,
        "v1_materialization_estimate",
        AsyncMock(
            return_value=SimpleNamespace(
                plan_hash="verified-plan",
                job_snapshot_id="job-snapshot",
                job_name="one_off",
                ref_hash="base-head",
            )
        ),
    )
    monkeypatch.setattr(
        ingestion.v1_assets,
        "_action_context",
        AsyncMock(return_value=(None, "sandbox_repo", ["one_off"])),
    )
    client, path, _, _ = candidate
    response = client.post(path + "/candidate/plan?env=sandbox", json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_destination_is_real_immutable_actor_private_and_requires_current_review(candidate):
    client, path, upload, root = candidate
    stale = client.post(
        path + "/destination?env=sandbox", json={"expected_version": 1, "table": "reviewed_batch"}
    )
    assert stale.status_code == 409
    receipt = prepare(candidate)
    assert receipt["loaded"] is False and receipt["status"] == "prepared"
    assert receipt["base_ref"] == "main" and receipt["candidate_ref"].startswith("sandbox-upload-")
    assert (root / receipt["files"][0]).exists() and (root / receipt["files"][1]).exists()
    assert (
        client.post(
            path + "/destination?env=sandbox",
            json={"expected_version": 2, "table": "reviewed_batch"},
        ).json()
        == receipt
    )
    assert (
        client.post(
            path + "/destination?env=sandbox", json={"expected_version": 2, "table": "other"}
        ).status_code
        == 409
    )
    assert (
        client.put(
            path + "/columns?env=sandbox",
            json={"expected_version": 2, "columns": upload["columns"]},
        ).status_code
        == 409
    )
    other = authenticated_client("operator")
    other.headers["x-test-subject"] = "other"
    assert other.get(path + "/candidate?env=sandbox").status_code == 404
    assert (
        other.post(
            path + "/destination?env=sandbox", json={"expected_version": 2, "table": "other"}
        ).status_code
        == 404
    )
    assert client.get(path + "/candidate?env=prod").status_code == 404
    assert (
        authenticated_client("viewer")
        .post(path + "/destination?env=sandbox", json={"expected_version": 2, "table": "other"})
        .status_code
        == 403
    )


def test_candidate_head_drift_and_review_checksum_tampering_fail_closed(candidate, monkeypatch):
    prepare(candidate)
    client, path, upload, root = candidate
    monkeypatch.setattr(ingestion, "verify_candidate", AsyncMock(return_value="unexpected-head"))
    assert client.post(path + "/candidate/plan?env=sandbox", json={}).status_code == 409
    (root / ".phlo/uploads" / upload["upload_id"] / "source.csv").write_bytes(b"changed")
    assert client.get(path + "/candidate?env=sandbox").status_code == 409


@pytest.mark.parametrize("occupied", ["candidate", "other_environment", "declared_asset"])
def test_destination_asset_collision_is_rejected_before_artifacts(candidate, monkeypatch, occupied):
    client, _, _, root = candidate
    env = "prod" if occupied == "other_environment" else "sandbox"
    if occupied != "declared_asset":
        original = prepare(candidate)
    else:
        monkeypatch.setattr(
            ingestion.v1_assets,
            "_assets",
            AsyncMock(
                return_value=[
                    SimpleNamespace(id="dlt_reviewed_batch", relation="raw.reviewed_batch")
                ]
            ),
        )
    uploaded = client.post(
        f"/api/v1/uploads/csv?env={env}",
        content=b"id\n3\n",
        headers={"X-Phlo-Upload-Name": "another.csv"},
    ).json()
    path = f"/api/v1/uploads/csv/{uploaded['upload_id']}"
    reviewed = client.put(
        path + f"/columns?env={env}",
        json={"expected_version": 1, "columns": uploaded["columns"]},
    )
    assert reviewed.status_code == 200
    rejected = client.post(
        path + f"/destination?env={env}",
        json={"expected_version": 2, "table": "reviewed_batch"},
    )
    assert rejected.status_code == 409, rejected.text
    assert client.get(path + f"/candidate?env={env}").status_code == 404
    assert not (root / f"contracts/uploads/{uploaded['upload_id']}.json").exists()
    assert not (root / f"workflows/uploads/{uploaded['upload_id']}.py").exists()
    if occupied != "declared_asset":
        first_path = f"/api/v1/uploads/csv/{original['upload_id']}/candidate?env=sandbox"
        assert client.get(first_path).json() == original


def test_confirmed_load_is_idempotent_and_readback_pins_written_snapshot(candidate, monkeypatch):
    receipt = plan(candidate, monkeypatch)
    client, path, upload, _ = candidate
    materialize = AsyncMock(
        return_value=SimpleNamespace(result={"accepted": True, "run_ids": ["actual-run"]})
    )
    monkeypatch.setattr(ingestion.v1_assets, "v1_asset_materialize", materialize)
    payload = {
        "expected_version": 2,
        "recipe_digest": receipt["recipe_digest"],
        "plan_hash": "verified-plan",
        "confirmed": True,
        "idempotency_key": "candidate-test",
    }
    assert (
        client.post(
            path + "/candidate/load?env=sandbox", json={**payload, "expected_version": 1}
        ).status_code
        == 409
    )
    with monkeypatch.context() as missing_dagster:
        missing_dagster.setitem(sys.modules, "phlo_dagster", None)
        unavailable = client.post(path + "/candidate/load?env=sandbox", json=payload)
        assert unavailable.status_code == 503, unavailable.text
        assert "phlo-api[dagster]" in unavailable.text
        assert client.get(path + "/candidate?env=sandbox").json()["status"] == "planned"
    launched = client.post(path + "/candidate/load?env=sandbox", json=payload)
    assert launched.status_code == 200, launched.text
    assert launched.json()["status"] == "queued" and not launched.json()["loaded"]
    assert client.post(path + "/candidate/load?env=sandbox", json=payload).json() == launched.json()
    assert materialize.await_count == 1
    run = {
        "__typename": "Run",
        "runId": "actual-run",
        "status": "SUCCESS",
        "pipelineName": "one_off",
        "pipelineSnapshotId": "subset-snapshot",
        "parentPipelineSnapshotId": "job-snapshot",
        "assetSelection": [{"path": [receipt["asset_id"]]}],
        "repositoryOrigin": {
            "repositoryName": "sandbox_repo",
            "repositoryLocationName": "sandbox_jobs",
        },
        "tags": [
            {"key": key, "value": value}
            for key, value in {
                "environment": "sandbox",
                "phlo/ref": receipt["candidate_ref"],
                "phlo/plan": "verified-plan",
                "phlo/job_snapshot_id": "job-snapshot",
                "phlo/ref_hash": "base-head",
                "phlo/actor": receipt["actor"],
            }.items()
        ],
        "assetMaterializations": [
            {
                "runId": "actual-run",
                "assetKey": {"path": [receipt["asset_id"]]},
                "metadataEntries": [
                    {"label": key, "text": value}
                    for key, value in {
                        "table_name": receipt["relation"],
                        "write_branch": receipt["candidate_ref"],
                        "snapshot_id": "123",
                    }.items()
                ],
            }
        ],
    }
    monkeypatch.setattr(
        ingestion, "graphql_request", AsyncMock(return_value={"data": {"runOrError": run}})
    )
    preview = AsyncMock(
        return_value={
            "columns": ["id", "result"],
            "column_types": ["varchar", "varchar"],
            "rows": [{"id": "1", "result": "ND"}, {"id": "2", "result": "<LOQ"}],
            "has_more": False,
            "row_count": 2,
            "snapshot_id": 123,
            "ref_hash": "written-head",
            "candidate_ref": receipt["candidate_ref"],
            "relation": "bronze.reviewed_batch",
            "query_id": "query-1",
        }
    )
    monkeypatch.setattr(ingestion, "preview_candidate", preview)
    first = client.get(path + "/candidate/preview?env=sandbox")
    assert first.status_code == 200, first.text
    assert first.json()["receipt"]["loaded"] is True and first.json()["rows"][0]["result"] == "ND"
    assert preview.call_args.kwargs["expected_hash"] is None
    second = client.get(path + "/candidate/preview?env=sandbox")
    assert second.status_code == 200, second.text
    assert preview.call_args.kwargs["expected_hash"] == "written-head"
    assert preview.call_args.kwargs["expected_snapshot_id"] == "123"
    assert second.json()["receipt"]["launch_ref_hash"] == "base-head"
    run["repositoryOrigin"]["repositoryLocationName"] = "other_jobs"
    assert client.get(path + "/candidate/preview?env=sandbox").status_code == 409


def test_candidate_run_query_matches_installed_dagster_schema():
    import inspect
    import re

    source = inspect.getsource(ingestion._run)
    query = re.search(r'"""(query CsvCandidateRun.*?)"""', source, re.S).group(1)
    assert validate(create_schema().graphql_schema, parse(query)) == []


def test_failed_before_write_requires_verified_retry_plan_and_explicit_failed_run_confirmation(
    candidate, monkeypatch
):
    receipt = plan(candidate, monkeypatch)
    client, path, _, _ = candidate
    launches = AsyncMock(
        side_effect=[
            SimpleNamespace(result={"accepted": True, "run_ids": ["failed-run"]}),
            SimpleNamespace(result={"accepted": True, "run_ids": ["retry-run"]}),
        ]
    )
    monkeypatch.setattr(ingestion.v1_assets, "v1_asset_materialize", launches)
    body = {
        "expected_version": 2,
        "recipe_digest": receipt["recipe_digest"],
        "plan_hash": "verified-plan",
        "confirmed": True,
        "idempotency_key": "confirmed-load",
    }
    assert client.post(path + "/candidate/load?env=sandbox", json=body).status_code == 200
    run = {
        "__typename": "Run",
        "runId": "failed-run",
        "status": "FAILURE",
        "pipelineName": "one_off",
        "pipelineSnapshotId": "subset-job",
        "parentPipelineSnapshotId": "job-snapshot",
        "assetSelection": [{"path": [receipt["asset_id"]]}],
        "repositoryOrigin": {
            "repositoryName": "sandbox_repo",
            "repositoryLocationName": "sandbox_jobs",
        },
        "tags": [
            {"key": key, "value": value}
            for key, value in {
                "environment": "sandbox",
                "phlo/ref": receipt["candidate_ref"],
                "phlo/plan": "verified-plan",
                "phlo/job_snapshot_id": "job-snapshot",
                "phlo/ref_hash": "base-head",
                "phlo/actor": receipt["actor"],
            }.items()
        ],
        "assetMaterializations": [],
    }
    monkeypatch.setattr(
        ingestion, "graphql_request", AsyncMock(return_value={"data": {"runOrError": run}})
    )
    assert client.get(path + "/candidate?env=sandbox").json()["status"] == "failed"
    assert client.get(path + "/candidate/preview?env=sandbox").status_code == 409
    from phlo_api.errors import ConflictError

    monkeypatch.setattr(
        ingestion,
        "_new_relation",
        AsyncMock(side_effect=ConflictError("A candidate table already exists.")),
    )
    assert client.post(path + "/candidate/plan?env=sandbox", json={}).status_code == 409
    assert client.get(path + "/candidate?env=sandbox").json()["run_id"] == "failed-run"
    monkeypatch.setattr(ingestion, "_new_relation", AsyncMock())
    replanned = client.post(path + "/candidate/plan?env=sandbox", json={})
    assert replanned.status_code == 200, replanned.text
    assert (
        replanned.json()["run_id"] is None
        and replanned.json()["attempts"][0]["run_id"] == "failed-run"
    )
    assert client.post(path + "/candidate/load?env=sandbox", json=body).status_code == 409
    retry = {**body, "retry_of_run_id": "failed-run"}
    queued = client.post(path + "/candidate/load?env=sandbox", json=retry)
    assert queued.status_code == 200, queued.text
    assert (
        queued.json()["run_id"] == "retry-run"
        and queued.json()["attempts"][0]["status"] == "FAILURE"
    )
    assert client.post(path + "/candidate/load?env=sandbox", json=retry).json() == queued.json()
    assert launches.await_count == 2
    assert (
        launches.call_args_list[0].args[2].idempotency_key
        != launches.call_args_list[1].args[2].idempotency_key
    )


def test_receipt_cas_normalizes_new_default_fields_without_replacing_identity(candidate):
    prepare(candidate)
    receipt = ingestion._all()[0]
    ingestion.mutate_collection(
        ingestion.project_root(),
        ingestion._COLLECTION,
        ingestion._legacy(),
        lambda items: [
            {key: value for key, value in item.items() if key != "attempts"} for item in items
        ],
    )
    updated = receipt.model_copy(update={"status": "planned"})
    assert ingestion._save(updated, previous=receipt).attempts == []
    assert ingestion._all()[0].recipe_digest == receipt.recipe_digest
