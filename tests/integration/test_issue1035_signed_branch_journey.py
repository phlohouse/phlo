"""Signed branch merge through real Nessie, Iceberg, Dagster and PostgreSQL.

Authentication and Dagster's HTTP transport are injected. The transport
executes the actual GraphQL schema and launches real disposable job processes.
GitHub transport uses stock Git in a disposable repo; its draft PR envelope
is injected, so this does not prove GitHub's own PR creation behavior.
No check result, catalog operation, Nessie response or signature is fabricated.
"""

import asyncio
import hashlib
import json
import socket
import time
from types import SimpleNamespace
from uuid import uuid4

import dagster as dg
import pyarrow as pa
import pytest
import requests
from dagster._core.workspace.context import WorkspaceProcessContext
from dagster._core.workspace.load_target import PythonFileTarget
from dagster_graphql.test.utils import execute_dagster_graphql
from fastapi import FastAPI
from fastapi.testclient import TestClient
from phlo_api import incidents
from phlo_api.api import operation_controls, v1_admin_audit, v1_admin_identity, v1_assets, v1_jobs
from phlo_api.api import v1_branch_workflows as branches
from phlo_iceberg.catalog import get_catalog, reset_catalog_cache
from phlo_iceberg.settings import get_settings as iceberg_settings
from phlo_postgres.settings_store import PostgresSettingsStore
from pyiceberg.schema import Schema
from pyiceberg.types import IntegerType, LongType, NestedField, StringType
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer

from phlo.capabilities.interfaces import AuthenticatedSession, AuthPrincipal, AuthResult
from phlo.identity.authority import IdentityAuthority
from tests.integration.issue1035_git_rest import LocalGitRest

pytestmark = pytest.mark.integration


def wait_ready(url):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=2)
            response.raise_for_status()
            return
        except requests.RequestException:
            time.sleep(0.5)
    pytest.fail(f"Disposable service not ready: {url}")


@pytest.fixture
def real_branch_services(monkeypatch, tmp_path):
    """Own all containers and credentials; never connect to deployed providers."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (
        DockerContainer("ghcr.io/phlohouse/phlo-minio:0.17.1")
        .with_env("MINIO_ROOT_USER", "localtest")
        .with_env("MINIO_ROOT_PASSWORD", "localpass123")
        .with_command("server /bitnami/minio/data")
        .with_exposed_ports(9000) as minio,
        PostgresContainer("postgres:18-alpine", password="test") as postgres,
    ):
        endpoint = f"http://{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}"
        wait_ready(endpoint + "/minio/health/ready")
        bucket = minio.exec(
            [
                "sh",
                "-c",
                "mc alias set test http://127.0.0.1:9000 localtest localpass123 >/dev/null "
                "&& mc mb test/lake",
            ]
        )
        assert bucket.exit_code == 0
        nessie = DockerContainer("ghcr.io/projectnessie/nessie:0.108.3").with_kwargs(
            network_mode="host"
        )
        for key, value in {
            "QUARKUS_HTTP_PORT": str(port),
            "QUARKUS_MANAGEMENT_PORT": "0",
            "NESSIE_VERSION_STORE_TYPE": "IN_MEMORY",
            "nessie.catalog.default-warehouse": "warehouse",
            "nessie.catalog.warehouses.warehouse.location": "s3://lake/warehouse",
            "nessie.catalog.service.s3.default-options.endpoint": endpoint,
            "nessie.catalog.service.s3.default-options.path-style-access": "true",
            "nessie.catalog.service.s3.default-options.region": "us-east-1",
            "nessie.catalog.service.s3.default-options.access-key": "urn:nessie-secret:quarkus:nessie.catalog.secrets.access-key",
            "nessie.catalog.secrets.access-key.name": "localtest",
            "nessie.catalog.secrets.access-key.secret": "localpass123",
        }.items():
            nessie.with_env(key, value)
        with nessie:
            url = f"http://127.0.0.1:{port}"
            wait_ready(url + "/api/v2/config")
            dsn = postgres.get_connection_url(driver=None)
            jobs = dict.fromkeys(("tests", "contracts", "audits"), "branch_evidence")
            for key, value in {
                "NESSIE_URL": url,
                "ICEBERG_CATALOG_URI": url + "/iceberg",
                "ICEBERG_S3_ENDPOINT": endpoint,
                "ICEBERG_S3_ACCESS_KEY": "localtest",
                "ICEBERG_S3_SECRET_KEY": "localpass123",
                "PHLO_RUN_EVIDENCE_DB_URL": dsn,
                "PHLO_OBSERVATORY_SETTINGS_DB_URL": dsn,
                "PHLO_OBSERVATORY_SETTINGS_BACKEND": "postgres",
                "PHLO_API_OPERATION_CONTROLS_DB_URL": dsn,
                "PHLO_API_OPERATION_CONTROLS_NAMESPACE": "issue1035-" + uuid4().hex,
                "PHLO_IDENTITY_AUTHORITY_ENABLED": "1",
                "PHLO_SIGNATURE_HMAC_KEY": "disposable-signature-key",
                "PHLO_AUDIT_HMAC_KEY": "disposable-audit-key",
                "PHLO_V1_ACTIONS_REF_TAG_CONTRACT": "1",
                "PHLO_V1_ACTIONS_SINGLE_REPLICA": "1",
                "PHLO_V1_ACTIONS_SINGLE_PROCESS": "1",
                "PHLO_V1_GIT_REVIEW_REPOSITORY": "local/journey",
                "PHLO_V1_GIT_REVIEW_BASE_BRANCH": "main",
                "PHLO_V1_GIT_REVIEW_TOKEN": "disposable",
                "PHLO_V1_BRANCH_CHECK_JOBS": json.dumps({"prod": jobs, "staging": jobs}),
                "PHLO_V1_ENVIRONMENTS": json.dumps(
                    {
                        "prod": {"dagster_location": "unused-prod", "nessie_ref": "main"},
                        "staging": {"dagster_location": "journey", "nessie_ref": "staging-target"},
                    }
                ),
            }.items():
                monkeypatch.setenv(key, value)
            monkeypatch.setattr(operation_controls, "project_root", lambda: tmp_path)
            iceberg_settings.cache_clear()
            reset_catalog_cache()
            operation_controls.initialize_operation_controls()
            incidents.initialize_incidents()
            yield SimpleNamespace(url=url, root=tmp_path, store=PostgresSettingsStore())
            reset_catalog_cache()
            iceberg_settings.cache_clear()
            operation_controls._postgres_controls.cache_clear()


@pytest.fixture
def signed_branch_api(real_branch_services, monkeypatch, tmp_path):
    services = real_branch_services
    actor = AuthPrincipal(
        subject="operator",
        principal_type="user",
        issuer="test-idp",
        groups=("operator",),
        claims={"auth_time": time.time(), "amr": ["pwd", "mfa"]},
    )
    session = AuthenticatedSession(
        principal=actor,
        auth_method="bearer_token",
        provider_name="jwt",
        attributes={
            "jwt_issuer": "https://issuer.example",
            "jwt_audience": "phlo-api",
            "jwt_issuer_validated": "true",
            "jwt_audience_validated": "true",
        },
    )
    monkeypatch.setattr("phlo.identity.authority.get_settings_service", lambda: services.store)
    monkeypatch.setattr(
        "phlo.plugins.observatory_settings.get_settings_service", lambda: services.store
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory_durable_state.get_settings_service",
        lambda: services.store,
    )
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: IdentityAuthority(services.store))
    monkeypatch.setattr(
        v1_admin_identity,
        "authenticate_request",
        lambda _request: AuthResult(authenticated=True, principal=actor, session=session),
    )
    monkeypatch.setattr(incidents, "get_request_principal", lambda _request: actor)
    monkeypatch.setattr(branches, "get_request_principal", lambda _request: actor)
    monkeypatch.setattr(v1_assets, "get_request_principal", lambda _request: actor)
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda _request, scope: {"subject": actor.subject, "scopes": [scope]},
    )
    git = LocalGitRest(tmp_path)
    monkeypatch.setattr(v1_assets, "project_git_review_client", git.client)
    monkeypatch.setattr(
        branches,
        "require_scope",
        lambda _request, scope: {"subject": actor.subject, "scopes": [scope]},
    )
    definitions = tmp_path / "checks.py"
    definitions.write_text("""
import json
from pathlib import Path
import dagster as dg
import requests
@dg.op
def inspect_pinned_table(context):
    tags = context.run.tags
    assert tags["environment"] == "staging"
    assert tags["phlo/branch_check"] in {"tests", "contracts", "audits"}
    import os
    url = os.environ["NESSIE_URL"]
    response = requests.get(f"{url}/api/v2/trees/{tags['phlo/ref']}@{tags['phlo/branch_hash']}/contents/silver.journey", timeout=10)
    response.raise_for_status()
    content = response.json()["content"]
    assert content["type"] == "ICEBERG_TABLE"
    assert content["schemaId"] >= 0
    Path(__file__).with_name(context.run_id + ".json").write_text(json.dumps(tags))
@dg.job
def branch_evidence():
    inspect_pinned_table()
@dg.asset(key=["silver", "journey"], metadata={"phlo/relation": "silver.journey"})
def journey_table():
    from phlo_iceberg.catalog import get_catalog
    return get_catalog("staging-target").load_table("silver.journey").scan().to_arrow()
defs = dg.Definitions(jobs=[branch_evidence], assets=[journey_table])
""")
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    overrides = {
        "run_coordinator": {
            "module": "dagster._core.run_coordinator.default_run_coordinator",
            "class": "DefaultRunCoordinator",
        }
    }
    with dg.instance_for_test(temp_dir=str(instance_dir), overrides=overrides) as instance:
        target = PythonFileTarget(
            python_file=str(definitions),
            attribute="defs",
            working_directory=str(tmp_path),
            location_name="journey",
        )
        with WorkspaceProcessContext(instance, target) as workspace:
            context = workspace.create_request_context()

            async def graphql(query, variables=None):
                result = await asyncio.to_thread(execute_dagster_graphql, context, query, variables)
                assert not result.errors, result.errors
                return {"data": result.data}

            monkeypatch.setattr(v1_jobs, "_graphql", graphql)
            monkeypatch.setattr(v1_assets, "_graphql", graphql)
            app = FastAPI()
            for router in (
                incidents.router,
                branches.router,
                v1_assets.router,
                v1_admin_identity.router,
                v1_admin_audit.router,
            ):
                app.include_router(router, prefix="/api/v1")
            with TestClient(app) as client:
                yield SimpleNamespace(client=client, instance=instance, services=services, git=git)


def test_signed_per_column_merge_is_head_and_decision_bound(signed_branch_api):
    journey = signed_branch_api
    client, url = journey.client, journey.services.url

    def nessie(method, path, **kwargs):
        response = requests.request(method, url + path, timeout=20, **kwargs)
        response.raise_for_status()
        return response.json()

    def head(ref):
        return nessie("GET", f"/api/v1/trees/tree/{ref}")["hash"]

    catalog = get_catalog("main")
    catalog.create_namespace("silver")
    table = catalog.create_table(
        "silver.journey",
        Schema(
            NestedField(1, "id", LongType(), required=False),
            NestedField(2, "status", IntegerType(), required=False, doc="base status"),
            NestedField(3, "note", StringType(), required=False, doc="base note"),
        ),
    )
    table.append(
        pa.table(
            {"id": [1], "status": [7], "note": ["preserved"]},
            schema=pa.schema([("id", pa.int64()), ("status", pa.int32()), ("note", pa.string())]),
        )
    )
    assert table.current_snapshot() is not None
    ancestor = head("main")
    for ref in ("staging-source", "staging-target"):
        nessie("POST", "/api/v1/trees/tree", json={"name": ref, "type": "BRANCH", "hash": ancestor})
        branch_table = get_catalog(ref).load_table("silver.journey")
        with branch_table.update_schema() as update:
            if ref == "staging-source":
                update.update_column("status", field_type=LongType())
            update.update_column("status", doc=ref + " status")
            update.update_column("note", doc=ref + " note")
    heads = {ref: head(ref) for ref in ("staging-source", "staging-target")}
    before_location = get_catalog("staging-target").load_table("silver.journey").metadata_location
    message = "Resolve status from source and preserve target note"
    base_version = heads["staging-source"] + ":" + heads["staging-target"]
    payload = {
        "target": "staging-target",
        "expected_source_hash": heads["staging-source"],
        "expected_target_hash": heads["staging-target"],
        "message": message,
    }

    def post(path, body, key=None):
        return client.post(
            "/api/v1" + path,
            params={"env": "staging"},
            headers={"Idempotency-Key": key or uuid4().hex},
            json=body,
        )

    def sign(version, record_id="staging:staging-source->staging-target"):
        response = client.post(
            "/api/v1/signatures",
            json={
                "action": "branch.merge",
                "target_type": "branch",
                "target_id": record_id,
                "target_version": version,
                "meaning": "approved",
                "justification": message,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["signature_id"]

    def merge(signature_id, key=None, **overrides):
        return post(
            "/branches/staging-source/merge",
            {
                **payload,
                "signature_id": signature_id,
                **overrides,
            },
            key=key,
        )

    # A valid approval cannot bypass an unresolved real Iceberg VALUE_DIFFERS conflict.
    blocked_signature = sign(base_version)
    blocked = merge(blocked_signature)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "conflict"
    assert blocked.json()["details"]["conflicts"] == ["silver.journey"]
    assert all(check["status"] == "passed" for check in blocked.json()["details"]["checks"])
    assert {ref: head(ref) for ref in heads} == heads
    created = post(
        "/incidents",
        {
            "asset_id": "silver.journey",
            "kind": "schema_conflict",
            "title": "Real Iceberg conflict",
            "evidence": blocked.json(),
            "evidence_id": heads["staging-source"],
        },
    )
    assert created.status_code == 201, created.text
    incident_id = created.json()["id"]
    columns = {"status": "source", "note": "target"}
    decision = post(
        f"/incidents/{incident_id}/schema-decisions",
        {
            "source_ref": "staging-source",
            "target_ref": "staging-target",
            "source_hash": heads["staging-source"],
            "target_hash": heads["staging-target"],
            "table_key": "silver.journey",
            "columns": columns,
            "justification": message,
        },
    )
    assert decision.status_code == 201, decision.text
    decision_id = decision.json()["id"]
    bound = [{"id": decision_id, "table_key": "silver.journey", "columns": columns}]
    digest = hashlib.sha256(
        json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    version = base_version + ":schema:" + digest
    payload["incident_id"] = incident_id
    trial = post("/branches/staging-source/trial-merge", payload)
    assert trial.status_code == 200, trial.text
    assert trial.json()["status"] == "succeeded", trial.text
    assert trial.json()["details"]["signature_target_version"] == version
    assert {ref: head(ref) for ref in heads} == heads
    for invalid_signature in (
        blocked_signature,
        sign(version, "prod:staging-source->staging-target"),
        sign("old-source:" + heads["staging-target"] + ":schema:" + digest),
        sign(heads["staging-source"] + ":old-target:schema:" + digest),
        sign(base_version + ":schema:" + "0" * 64),
    ):
        denied_key = uuid4().hex
        rejected = merge(invalid_signature, key=denied_key)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"] == "Signature is missing, stale, reused, or mismatched."
        assert {ref: head(ref) for ref in heads} == heads
        replay = merge(invalid_signature, key=denied_key)
        assert replay.status_code == 409, replay.text
        assert replay.json()["detail"] == {"error": "idempotency_outcome_failed"}
        # A different valid approval is not fenced by a known rejection.
        # No operator reconciliation or unknown-outcome override is needed.
    signature_id = sign(version)
    merge_body = {**payload, "signature_id": signature_id}
    merge_key = uuid4().hex
    response = client.post(
        "/api/v1/branches/staging-source/merge",
        params={"env": "staging"},
        headers={"Idempotency-Key": merge_key},
        json=merge_body,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "succeeded", result
    assert result["source_hash"] == heads["staging-source"]
    assert result["target_hash"] == heads["staging-target"]
    assert result["resulting_hash"] == head("staging-target") != heads["staging-target"]
    assert head("staging-source") == heads["staging-source"]
    assert result["details"]["signature_target_version"] == version
    assert result["details"]["schema_decision_ids"] == [decision_id]
    assert result["details"]["merge_result"]["wasApplied"] is True
    merged = get_catalog("staging-target").load_table("silver.journey")
    assert merged.metadata_location != before_location
    assert {field.name: field.doc for field in merged.schema().fields} == {
        "id": None,
        "status": "staging-source status",
        "note": "staging-target note",
    }
    assert merged.current_snapshot() == table.current_snapshot()
    assert [field.field_id for field in merged.schema().fields] == [1, 2, 3]
    assert str(merged.schema().find_field("status").field_type) == "long"
    assert merged.scan().to_arrow().to_pylist() == [{"id": 1, "status": 7, "note": "preserved"}]
    replay = client.post(
        "/api/v1/branches/staging-source/merge",
        params={"env": "staging"},
        headers={"Idempotency-Key": merge_key},
        json=merge_body,
    )
    assert replay.json() == result
    assert head("staging-target") == result["resulting_hash"]
    refs = nessie("GET", "/api/v1/trees")["references"]
    assert not any(ref["name"].startswith("phlo-schema-resolution-") for ref in refs)
    runs = journey.instance.get_runs()
    assert len(runs) == 24  # conflict, trial, five denials, success; three real jobs each
    for run in runs:
        assert run.status == dg.DagsterRunStatus.SUCCESS
        assert run.tags["phlo/branch_hash"] == heads["staging-source"]
        assert json.loads((journey.services.root / (run.run_id + ".json")).read_text()) == run.tags
    verified = client.get("/api/v1/admin/audit/verify", params={"surface": "phlo-api"})
    assert verified.status_code == 200 and verified.json()["valid"] is True, verified.text
    records = client.get(
        "/api/v1/admin/audit/records",
        params={
            "surface": "phlo-api",
            "action": "branch.merge",
        },
    )
    assert records.status_code == 200, records.text
    events = [item["event"] for item in records.json()["items"]]
    assert sum(event["decision"] == "deny" for event in events) == 5
    assert sum(event["decision"] == "allow" for event in events) == 1
    assert all(
        event["resource_id"] == "staging:staging-source->staging-target"
        for event in events
        if event["decision"] == "allow"
    )
    approved_event = next(event for event in events if event["decision"] == "allow")
    assert approved_event["attributes"]["signature_id"] == signature_id
    assert all(
        event["attributes"]["expected_version"] == version
        for event in events
        if event["decision"] == "deny"
    )
    stored_signatures = IdentityAuthority(journey.services.store).signatures("operator")
    consumed = [item for item in stored_signatures if item.consumed_at is not None]
    assert len(consumed) == 1
    assert consumed[0].record.signature_id == signature_id
    assert consumed[0].record.record_version == version
    assert consumed[0].record.authentication_assurance == "mfa"
    assert consumed[0].record.action == "branch.merge"
    operation_events = client.get(
        "/api/v1/admin/audit/records",
        params={
            "surface": "phlo-api",
            "action": "v1_branch_merge",
        },
    ).json()["items"]
    assert sum(item["event"]["outcome"] == "success" for item in operation_events) == 1
    assert sum(item["event"]["outcome"] == "failure" for item in operation_events) == 1
    merge_event = next(
        item["event"] for item in operation_events if item["event"]["outcome"] == "success"
    )
    assert merge_event["attributes"]["source_hash"] == heads["staging-source"]
    assert merge_event["attributes"]["target_hash"] == heads["staging-target"]
    assert merge_event["attributes"]["resulting_hash"] == result["resulting_hash"]
    prove_contract_replacement(journey)
    print(
        "ISSUE1035 SIGNED BRANCH",
        json.dumps(
            {
                "heads_before": heads,
                "resulting_hash": result["resulting_hash"],
                "signature_id": signature_id,
                "signature_target_version": version,
                "decision_id": decision_id,
                "metadata_before": before_location,
                "metadata_after": merged.metadata_location,
                "fields": {field.name: field.doc for field in merged.schema().fields},
                "types": {field.name: str(field.field_type) for field in merged.schema().fields},
                "snapshot_id": merged.current_snapshot().snapshot_id,
                "rows": merged.scan().to_arrow().to_pylist(),
                "dagster_runs": len(runs),
                "audit_valid": verified.json()["valid"],
                "branch_merge_denials": 5,
                "branch_merge_allow": 1,
            },
            sort_keys=True,
        ),
    )


def prove_contract_replacement(journey):
    client, git = journey.client, journey.git
    route = "/api/v1/assets/silver/journey/audits"
    detail = client.get("/api/v1/assets/silver/journey", params={"env": "staging"})
    assert detail.status_code == 200, detail.text
    assert detail.json()["schema_source"] == "catalog"
    assert {col["name"]: col["type"] for col in detail.json()["columns"]} == {
        "id": "long",
        "status": "long",
        "note": "string",
    }

    def propose(**extra):
        return client.post(
            route,
            params={"env": "staging"},
            json={
                "check_name": "resolved_contract",
                "rules": [{"kind": "not_null", "column": "status"}],
                "idempotency_key": uuid4().hex,
                **extra,
            },
        )

    initial = propose()
    assert initial.status_code == 202, initial.text
    old = initial.json()
    path = journey.services.root / old["file_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(old["source"])
    base = git.commit()
    stale = propose(previous_source_digest="0" * 64)
    assert stale.status_code == 409, stale.text
    replacement = propose(
        previous_source_digest=old["source_digest"],
        rules=[{"kind": "range", "column": "status", "minimum": 0, "maximum": 10}],
    )
    assert replacement.status_code == 202, replacement.text
    proposal = replacement.json()
    assert proposal["previous_source"] == old["source"]
    assert proposal["previous_source_digest"] == hashlib.sha256(old["source"].encode()).hexdigest()
    assert proposal["source_digest"] == hashlib.sha256(proposal["source"].encode()).hexdigest()
    assert proposal["source_digest"] != old["source_digest"]
    assert "-        NullCheck" in proposal["patch"] and "+        RangeCheck" in proposal["patch"]
    version = old["source_digest"] + ":" + proposal["source_digest"]
    record_id = "staging:silver/journey:" + proposal["file_path"]

    def sign(target_version=version, target_id=record_id):
        response = client.post(
            "/api/v1/signatures",
            json={
                "action": "asset.audit.publish",
                "target_type": "asset_contract",
                "target_id": target_id,
                "target_version": target_version,
                "meaning": "approved",
                "justification": "Reviewed resolved Iceberg status contract",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["signature_id"]

    def publish(signature, key=None):
        return client.post(
            route + "/" + proposal["proposal_id"] + "/pull-request",
            params={"env": "staging"},
            json={
                "idempotency_key": key or uuid4().hex,
                "signature_id": signature,
                "expected_source_digest": proposal["source_digest"],
            },
        )

    for signature in (
        sign("0" * 64 + ":" + proposal["source_digest"]),
        sign(target_id="prod:silver/journey:" + proposal["file_path"]),
    ):
        denied = publish(signature)
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"] == "Signature is missing, stale, reused, or mismatched."
        assert git.writes == [] and git.git("rev-parse", "main") == base
    # A valid approval cannot publish over independently changed base content.
    path.write_text(old["source"] + "\n# concurrent review\n")
    changed = git.commit()
    stale_approval = sign()
    rejected = publish(stale_approval)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == {"error": "audit_proposal_branch_conflict"}
    assert git.writes == [] and git.git("rev-parse", "main") == changed
    stored = IdentityAuthority(journey.services.store).signatures("operator")
    assert next(s for s in stored if s.record.signature_id == stale_approval).consumed_at is None
    git.git("reset", "--hard", base)
    signature, key = sign(), uuid4().hex
    published = publish(signature, key)
    assert published.status_code == 202, published.text
    branch = published.json()["head_branch"]
    commit = git.git("rev-parse", branch)
    assert git.git("rev-parse", commit + "^") == base
    assert git.git("show", branch + ":" + proposal["file_path"]) + "\n" == proposal["source"]
    assert git.git("show", "main:" + proposal["file_path"]) + "\n" == old["source"]
    assert path.read_text() == old["source"]
    assert git.git("diff", "--name-only", "main", branch) == proposal["file_path"]
    writes = list(git.writes)
    consumed_before = [
        s.record.signature_id
        for s in IdentityAuthority(journey.services.store).signatures("operator")
        if s.consumed_at is not None
    ]
    assert publish(signature, key).json() == published.json()
    assert git.writes == writes and git.git("rev-parse", branch) == commit
    consumed_after = [
        s.record.signature_id
        for s in IdentityAuthority(journey.services.store).signatures("operator")
        if s.consumed_at is not None
    ]
    assert consumed_before == consumed_after and consumed_after.count(signature) == 1
    assert publish("missing-signature-with-new-key").status_code == 409
    assert git.writes == writes and git.git("rev-parse", branch) == commit
    events = client.get(
        "/api/v1/admin/audit/records",
        params={"surface": "phlo-api", "action": "asset.audit.publish"},
    ).json()["items"]
    assert not any(
        item["event"]["attributes"].get("signature_id") == "missing-signature-with-new-key"
        for item in events
    )
    approved = [
        i["event"] for i in events if i["event"]["attributes"].get("signature_id") == signature
    ]
    assert len(approved) == 1 and approved[0]["resource_id"] == record_id
    assert approved[0]["attributes"]["expected_version"] == version
    operations = operation_controls.read_operation_audit("v1_publish_asset_audit_proposal")
    assert len(operations) == 1
    assert operations[0]["result"]["status"] == "pending_review"
    assert operations[0]["result"]["pull_request_url"] == published.json()["pull_request_url"]
    assert proposal["source_digest"] in operations[0]["target"]
    verified = client.get("/api/v1/admin/audit/verify", params={"surface": "phlo-api"})
    assert verified.status_code == 200 and verified.json()["valid"] is True
    print(
        "ISSUE1035 SIGNED CONTRACT",
        json.dumps(
            {
                "proposal_id": proposal["proposal_id"],
                "previous_digest": old["source_digest"],
                "new_digest": proposal["source_digest"],
                "signature_id": signature,
                "base": base,
                "proposal_commit": commit,
                "git_writes": writes,
                "audit_valid": True,
                "draft_pr_envelope": "injected; Git objects and refs are real",
            },
            sort_keys=True,
        ),
    )
