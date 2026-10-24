"""Candidate ingestion preserves actor, immutable review and real run/result identity."""

import sys
import json
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
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
    monkeypatch.setattr(ingestion, "resolve_dagster_url", lambda: "http://dagster.test")
    monkeypatch.setattr(
        ingestion,
        "graphql_request",
        AsyncMock(
            side_effect=lambda url, query, variables, **kwargs: {
                "data": {
                    "reloadRepositoryLocation": {
                        "loadStatus": "LOADED",
                        "locationOrLoadError": {"name": variables["name"]},
                    }
                }
            }
        ),
    )
    monkeypatch.setattr(ingestion.v1_assets, "_assets", AsyncMock(return_value=[]))

    def author(**kwargs):
        result = author_csv_upload(kwargs["project_root"], kwargs["values"])
        return SimpleNamespace(**result)

    monkeypatch.setattr(ingestion, "create_workflow_with_provider", author)
    return client, path, upload, tmp_path


def test_reload_timeout_is_unavailable_and_preserves_incomplete_registration(
    candidate, monkeypatch
):
    client, path, upload, root = candidate
    reload = AsyncMock(side_effect=httpx.ReadTimeout("private transport details"))
    monkeypatch.setattr(ingestion, "graphql_request", reload)
    response = client.post(
        path + "/destination?env=sandbox", json={"expected_version": 2, "table": "reviewed_batch"}
    )
    assert response.status_code == 503, response.text
    assert "retained" in response.text and "private transport" not in response.text
    assert reload.await_count == 1 and reload.await_args.kwargs["timeout"] == 60.0
    receipt = client.get(path + "/candidate?env=sandbox").json()
    assert (
        receipt["status"] == "preparing"
        and receipt["loaded"] is False
        and receipt["run_id"] is None
    )
    contract = root / f"contracts/uploads/{upload['upload_id']}.json"
    source = root / f"workflows/uploads/{upload['upload_id']}.py"
    retained = (contract.read_bytes(), source.read_bytes())
    assert len(list((root / "contracts/uploads").iterdir())) == 1
    reload.side_effect = None
    reload.return_value = {
        "data": {
            "reloadRepositoryLocation": {
                "loadStatus": "LOADED",
                "locationOrLoadError": {"name": "sandbox_jobs"},
            }
        }
    }
    prepared = client.post(
        path + "/destination?env=sandbox", json={"expected_version": 2, "table": "reviewed_batch"}
    )
    assert prepared.status_code == 200, prepared.text
    assert (
        prepared.json()["status"] == "prepared"
        and prepared.json()["asset_id"] == receipt["asset_id"]
    )
    assert (contract.read_bytes(), source.read_bytes()) == retained
    assert reload.await_count == 2


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


def test_operational_openapi_exposes_closed_typed_evidence_review_and_handoff():
    from phlo_api.main import app

    document = app.openapi()
    schemas = document["components"]["schemas"]

    def model(value):
        if "anyOf" in value:
            value = next(item for item in value["anyOf"] if item.get("type") != "null")
        return schemas[value["$ref"].rsplit("/", 1)[-1]] if "$ref" in value else value

    def response(path, method="get"):
        return model(
            document["paths"][path][method]["responses"]["200"]["content"]["application/json"][
                "schema"
            ]
        )

    upload = "/api/v1/uploads/csv/{upload_id}/candidate"
    transform = "/api/v1/transform/models/{model_id}/drafts/{draft_id}/{revision}"
    review = response(upload + "/review")
    assert review == response(upload + "/reconcile", "post")
    assert review["additionalProperties"] is False
    assert set(review["required"]) == {"receipt", "evidence", "preview", "reconciliation"}
    evidence = model(review["properties"]["evidence"])
    assert evidence["additionalProperties"] is False
    assert set(evidence["required"]) == {
        "stale",
        "eligible_for_release",
        "reason",
        "base_hash",
        "current_base_hash",
        "candidate_hash",
        "observed_candidate_hash",
    }
    reconciliation = model(review["properties"]["reconciliation"])
    assert reconciliation["properties"]["resolution"]["enum"] == ["verified_success", "unresolved"]
    assert set(reconciliation["required"]) == {"resolution", "reason"}
    execution = response(transform + "/executions/{execution_id}")
    assert model(execution["properties"]["evidence"]) == evidence
    assert model(execution["properties"]["reconciliation"]) == reconciliation
    handoff = response(upload + "/handoff")
    assert handoff == response(transform + "/handoff")
    assert handoff["additionalProperties"] is False
    assert handoff["properties"]["kind"]["enum"] == ["csv_candidate", "transform_revision"]
    assert handoff["properties"]["automatic_promotion"]["const"] is False
    assert handoff["properties"]["source_review_required"]["const"] is True
    assert set(model(handoff["properties"]["files"]["items"])["required"]) == {
        "path",
        "content",
        "sha256",
    }
    release = model(handoff["properties"]["release"])
    assert release["additionalProperties"] is False
    assert release["properties"]["requires_signature"]["const"] is True


@pytest.mark.parametrize("occupied", ["candidate", "other_environment", "declared_asset"])
def test_upload_scoped_assets_allow_same_destination_without_identity_collisions(
    candidate, monkeypatch, occupied
):
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
    accepted = client.post(
        path + f"/destination?env={env}",
        json={"expected_version": 2, "table": "reviewed_batch"},
    )
    assert accepted.status_code == 200, accepted.text
    receipt = accepted.json()
    assert receipt["asset_id"] == f"dlt_upload_{uploaded['upload_id']}"
    assert len(receipt["asset_id"]) < 63
    assert receipt["relation"] == "bronze.reviewed_batch"
    assert client.get(path + f"/candidate?env={env}").json() == receipt
    contract = json.loads((root / f"contracts/uploads/{uploaded['upload_id']}.json").read_text())
    assert contract["asset_id"] == receipt["asset_id"]
    assert (root / f"workflows/uploads/{uploaded['upload_id']}.py").exists()
    if occupied != "declared_asset":
        assert original["asset_id"] != receipt["asset_id"]
        first_path = f"/api/v1/uploads/csv/{original['upload_id']}/candidate?env=sandbox"
        assert client.get(first_path).json() == original


@pytest.mark.parametrize("legacy", [False, True])
def test_reclaimed_upload_keeps_legacy_receipts_and_source_handoff_checksums(
    candidate, monkeypatch, legacy
):
    client, path, upload, root = candidate
    receipt = ingestion.CandidateReceipt.model_validate(prepare(candidate))
    if legacy:
        contract_path = root / receipt.files[0]
        contract = json.loads(contract_path.read_bytes())
        contract.pop("asset_id")
        content = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        contract_path.write_bytes(content)
        old = receipt.model_copy(
            update={
                "asset_id": f"dlt_{receipt.table}",
                "contract_digest": hashlib.sha256(content).hexdigest(),
            }
        )
        ingestion._save(old, previous=receipt)
        receipt = old
    monkeypatch.setattr(
        ingestion,
        "candidate_evidence",
        AsyncMock(
            return_value={
                "base_hash": "base-head",
                "current_base_hash": "base-head",
                "candidate_hash": "base-head",
                "observed_candidate_hash": None,
                "stale": True,
                "eligible_for_release": False,
                "reason": "Archived",
            }
        ),
    )
    assert client.get(path + "/candidate?env=sandbox").json()["asset_id"] == receipt.asset_id
    archived = client.post(
        path + "/archive?env=sandbox", json={"archived": True, "reason": "Expired"}
    )
    assert archived.status_code == 200, archived.text
    reclaimed = client.post(
        path + "/reclaim?env=sandbox",
        json={"confirmed": True, "expected_checksum": upload["checksum"], "reason": "Expired"},
    )
    assert reclaimed.status_code == 200, reclaimed.text
    assert client.get(path + "/candidate?env=sandbox").json()["asset_id"] == receipt.asset_id
    handoff = client.get(path + "/candidate/handoff?env=sandbox")
    assert handoff.status_code == 200, handoff.text
    for file in handoff.json()["files"]:
        assert hashlib.sha256(file["content"].encode()).hexdigest() == file["sha256"]
        assert (root / file["path"]).read_text() == file["content"]
    assert handoff.json()["candidate_evidence"]["eligible_for_release"] is False
    assert not (root / ".phlo/uploads" / upload["upload_id"] / "source.csv").exists()


@pytest.mark.parametrize("status", ["preparing", "queued", "running", "failed"])
def test_reclamation_cannot_remove_inputs_for_unresolved_candidates(candidate, status):
    client, path, upload, root = candidate
    receipt = ingestion.CandidateReceipt.model_validate(prepare(candidate))
    ingestion._save(receipt.model_copy(update={"status": status}), previous=receipt)
    assert (
        client.post(
            path + "/archive?env=sandbox", json={"archived": True, "reason": "Hide"}
        ).status_code
        == 200
    )
    response = client.post(
        path + "/reclaim?env=sandbox",
        json={"confirmed": True, "expected_checksum": upload["checksum"], "reason": "Expired"},
    )
    assert response.status_code == 409, response.text
    assert (root / ".phlo/uploads" / upload["upload_id"] / "source.csv").exists()
    assert client.get(path + "/candidate?env=sandbox").json()["status"] == status


def test_reclamation_checks_collection_fences_inside_atomic_update(candidate, monkeypatch):
    from phlo.plugins.observatory_settings import SettingsScope, get_settings_service
    from phlo_api.observatory_api.observatory_durable_state import state_namespace

    client, path, upload, root = candidate
    prepare(candidate)
    client.post(path + "/archive?env=sandbox", json={"archived": True, "reason": "Expired"})
    original = ingestion.uploads.mutate_collection
    namespace = state_namespace(root, ingestion.uploads._COLLECTION)
    service = get_settings_service()

    def race(*args, **kwargs):
        service.mutate(
            SettingsScope.GLOBAL, namespace, lambda state: {**state, "future_fence": True}
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(ingestion.uploads, "mutate_collection", race)
    response = client.post(
        path + "/reclaim?env=sandbox",
        json={"confirmed": True, "expected_checksum": upload["checksum"], "reason": "Expired"},
    )
    assert response.status_code == 409, response.text
    state = service.get(SettingsScope.GLOBAL, namespace).settings
    assert state["future_fence"] is True and not state["items"][0].get("reclaim_started_at")
    assert (root / ".phlo/uploads" / upload["upload_id"] / "source.csv").exists()


def test_interrupted_local_reclamation_resumes_without_freeing_quota_early(candidate, monkeypatch):
    from pathlib import Path

    client, path, upload, root = candidate
    prepare(candidate)
    monkeypatch.setattr(ingestion.uploads, "MAX_UPLOADS", 1)
    client.post(path + "/archive?env=sandbox", json={"archived": True, "reason": "Expired"})
    source = root / ".phlo/uploads" / upload["upload_id"] / "source.csv"
    body = {"confirmed": True, "expected_checksum": upload["checksum"], "reason": "Expired"}
    unlink = Path.unlink

    def fail(path, *args, **kwargs):
        if path == source:
            raise OSError("temporary local disposal failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        with pytest.raises(OSError, match="disposal failure"):
            client.post(path + "/reclaim?env=sandbox", json=body)
    tombstone = client.get("/api/v1/uploads/csv?env=sandbox&include_archived=true").json()["items"][
        0
    ]
    assert tombstone["reclaim_started_at"] and tombstone["reclaimed_at"] is None
    assert source.exists()
    assert client.post("/api/v1/uploads/csv?env=sandbox", content=b"id\n9\n").status_code == 422
    assert (
        client.post(
            path + "/archive?env=sandbox", json={"archived": False, "reason": "Restore"}
        ).status_code
        == 409
    )
    assert client.post(path + "/reclaim?env=sandbox", json=body).status_code == 200
    assert not source.exists()
    assert client.post("/api/v1/uploads/csv?env=sandbox", content=b"id\n9\n").status_code == 201


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


def test_csv_reconciliation_historical_review_and_handoff_never_launch_or_promote(
    candidate, monkeypatch
):
    import hashlib

    receipt = prepare(candidate)
    client, path, _, root = candidate
    monkeypatch.setattr(
        ingestion,
        "candidate_evidence",
        AsyncMock(
            return_value={
                "stale": True,
                "eligible_for_release": False,
                "reason": "Base changed.",
                "base_hash": "base-head",
                "current_base_hash": "later-base",
                "candidate_hash": "result-head",
                "observed_candidate_hash": "result-head",
            }
        ),
    )
    monkeypatch.setattr(
        ingestion.v1_assets,
        "v1_asset_materialize",
        AsyncMock(side_effect=AssertionError("must not launch")),
    )
    unresolved = client.post(path + "/candidate/reconcile?env=sandbox")
    assert unresolved.status_code == 200, unresolved.text
    assert unresolved.json()["reconciliation"]["resolution"] == "unresolved"
    assert ingestion._all()[0].status == "prepared"
    current = ingestion._all()[0]
    ingestion._save(
        current.model_copy(update={"run_id": "confirmed-run", "status": "running"}),
        previous=current,
    )
    monkeypatch.setattr(ingestion, "_run", AsyncMock(return_value=("SUCCESS", "9007199254740993")))
    monkeypatch.setattr(ingestion, "_head", AsyncMock(return_value="later-base"))

    async def preview(binding, relation, **kwargs):
        assert kwargs["expected_snapshot_id"] == "9007199254740993"
        if kwargs["expected_hash"] is not None:
            assert kwargs["expected_hash"] == "result-head"
        return {
            "columns": ["id"],
            "column_types": ["bigint"],
            "rows": [{"id": 1}, {"id": 2}],
            "has_more": False,
            "row_count": 2,
            "snapshot_id": "9007199254740993",
            "ref_hash": "result-head",
            "candidate_ref": receipt["candidate_ref"],
            "relation": receipt["relation"],
            "query_id": "review-query",
        }

    monkeypatch.setattr(ingestion, "preview_candidate", preview)
    reconciled = client.post(path + "/candidate/reconcile?env=sandbox")
    assert reconciled.status_code == 200, reconciled.text
    result = reconciled.json()
    assert result["reconciliation"]["resolution"] == "verified_success"
    assert result["receipt"]["loaded"] is True and result["receipt"]["main_unchanged"] is False
    assert (
        result["evidence"]["stale"] is True and result["evidence"]["eligible_for_release"] is False
    )
    assert result["preview"]["snapshot_id"] == "9007199254740993"
    handoff = client.get(path + "/candidate/handoff?env=sandbox").json()
    assert handoff["source_review_required"] is True and handoff["automatic_promotion"] is False
    assert handoff["release"]["requires_signature"] is True
    assert handoff["release"]["source_ref"] == receipt["candidate_ref"]
    for file in handoff["files"]:
        assert file["content"].encode() == (root / file["path"]).read_bytes()
        assert file["sha256"] == hashlib.sha256(file["content"].encode()).hexdigest()
    assert len(handoff["files"]) == 2
    assert (
        client.post(
            path + "/archive?env=sandbox", json={"archived": True, "reason": "Retain evidence"}
        ).status_code
        == 200
    )
    assert (
        client.get(path + "/candidate/review?env=sandbox").json()["receipt"]["snapshot_id"]
        == "9007199254740993"
    )
    assert client.post(path + "/candidate/plan?env=sandbox", json={}).status_code == 409
