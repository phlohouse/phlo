"""Incident parity on disposable storage, with real authorization boundaries."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from testcontainers.postgres import PostgresContainer

from phlo.capabilities import (
    AuthPrincipal,
    AuthenticationProviderSpec,
    AuthorizationPolicyBackendSpec,
    AlertSinkSpec,
    get_capability_registry,
    register_capability,
)
from phlo.capabilities.authorization import DefaultAuthorizationPolicyBackend
from phlo_api import incidents
from phlo_api.api import v1_query


@pytest.fixture(scope="module")
def database():
    # Disposable databases must not inherit missing or empty deployment credentials.
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        yield postgres.get_connection_url(driver=None)


@pytest.fixture
def incident_db(database, monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", database)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    incidents.initialize_incidents()
    with incidents._transaction() as connection, connection.cursor() as cur:
        cur.execute("TRUNCATE phlo.incident,phlo.query_execution,phlo.incident_command CASCADE")
    registry = get_capability_registry()
    previous = {
        family: registry.list(family)
        for family in ("authentication_provider", "authorization_policy_backend", "alert_sink")
    }

    class HeaderAuth:
        def current_principal(self, context):
            subject = context.headers.get("x-test-subject")
            return (
                AuthPrincipal(
                    subject=subject,
                    principal_type="user",
                    groups=("operators",),
                    claims={"scope": "lakehouse:operate"},
                )
                if subject
                else None
            )

    policies = [
        {
            "policy_id": action,
            "effect": "allow",
            "principal": {"roles": ["operator"]},
            "action": action,
            "resource": {"type": "*", "id_pattern": "*"},
        }
        for action in ("dataset.query", "asset.manage", "run.manage", "service.manage")
    ]
    register_capability(
        "authentication_provider", AuthenticationProviderSpec(name="parity", provider=HeaderAuth())
    )
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="parity", provider=DefaultAuthorizationPolicyBackend(policies=policies)
        ),
    )
    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "parity")
    monkeypatch.setenv("PHLO_AUTHORIZATION_BACKEND", "parity")
    monkeypatch.setenv("PHLO_AUTHORIZATION_MODE", "required")
    monkeypatch.setenv("PHLO_REGULATED", "false")
    yield policies
    for family, specs in previous.items():
        registry.clear(family)
        for spec in specs:
            register_capability(family, spec)


def request(subject="alice", env="prod"):
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/api/v1/incidents/one",
            "query_string": f"env={env}".encode(),
            "headers": [(b"x-test-subject", subject.encode())],
            "client": ("127.0.0.1", 123),
            "server": ("local", 80),
            "scheme": "http",
        }
    )


def create(subject="alice", env="prod", **fields):
    return incidents.create_incident(
        request(subject, env),
        incidents.IncidentInput(
            asset_id="bronze/orders",
            kind="audit",
            title="Orders audit failed",
            evidence_id=f"manual:{env}:{subject}",
            evidence={"check_id": "orders.pk"},
            **fields,
        ),
        f"create-{subject}-{env}",
        env,
    )


@pytest.mark.integration
def test_versioned_fields_manual_creation_and_simultaneous_updates(incident_db):
    first = create(
        severity="high",
        owner="qa-team",
        asset_ids=["bronze/orders", "gold/report"],
        description="Check the affected report before release.",
    )
    assert first.asset_ids == ["bronze/orders", "gold/report"]
    assert first.layers == []  # Layer filters use authoritative asset metadata, not ID prefixes.
    incidents.initialize_incidents()
    persisted = incidents.incident_detail(request(), first.id, "prod")
    assert (persisted.severity, persisted.owner, persisted.description) == (
        "high",
        "qa-team",
        "Check the affected report before release.",
    )
    second = create(subject="bob", severity="low")
    assert second.id != first.id

    def update(index):
        try:
            return incidents.update_incident(
                request(),
                first.id,
                incidents.IncidentUpdate(
                    owner=f"owner-{index}", severity="low" if index == 1 else "medium"
                ),
                f"update-{index}",
                "prod",
                "1",
            )
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(update, [1, 2]))
    winner = next(item for item in outcomes if isinstance(item, incidents.IncidentView))
    assert outcomes.count(409) == 1
    assert winner.version == 2
    assert winner.severity == ("low" if winner.owner == "owner-1" else "medium")
    assert incidents.incident_detail(request(), first.id, "prod").owner == winner.owner
    with pytest.raises(HTTPException) as error:
        incidents.incident_detail(request(env="staging"), first.id, "staging")
    assert error.value.status_code == 404


@pytest.mark.integration
def test_query_pins_use_durable_owner_environment_evidence_without_disclosing_rows(incident_db):
    incident = create()
    sql = "SELECT secret FROM private_records"
    result = {
        "columns": [{"name": "secret", "type": "varchar"}],
        "rows": [{"secret": "confidential-value"}],
        "has_more": False,
    }
    incidents.persist_query_execution(
        query_id="execution-1",
        env="prod",
        actor="alice",
        nessie_ref="main",
        statement=sql,
        executed_statement=f'SELECT * FROM ({sql}) AS "_phlo_query_result" LIMIT 2',
        result=result,
        provider_query_id="trino-123",
    )
    assert incidents.load_query_execution("execution-1", "staging", "alice") is None
    assert incidents.load_query_execution("execution-1", "prod", "bob") is None
    v1_query._QUERY_SESSIONS.clear()
    restored = v1_query._session_for_actor("execution-1", request(), "prod")
    assert restored.result == result
    assert restored.evidence_available is True
    for subject, env in (("bob", "prod"), ("alice", "staging")):
        with pytest.raises(HTTPException) as error:
            v1_query._session_for_actor("execution-1", request(subject, env), env)
        assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        incidents.update_incident(
            request("bob"),
            incident.id,
            incidents.IncidentUpdate(query_id="execution-1"),
            "bob-pin",
            "prod",
            "1",
        )
    assert error.value.status_code == 404
    pinned = incidents.update_incident(
        request(),
        incident.id,
        incidents.IncidentUpdate(query_id="execution-1"),
        "pin-1",
        "prod",
        "1",
    )
    replay = incidents.update_incident(
        request(),
        incident.id,
        incidents.IncidentUpdate(query_id="execution-1"),
        "pin-1",
        "prod",
        "1",
    )
    duplicate = incidents.update_incident(
        request(),
        incident.id,
        incidents.IncidentUpdate(query_id="execution-1"),
        "pin-2",
        "prod",
        "2",
    )
    assert pinned.version == replay.version == duplicate.version == 2
    events = incidents.incident_timeline(request("bob"), incident.id, "prod").items
    evidence = [event.payload for event in events if event.kind == "query_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["statement_sha256"] == hashlib.sha256(sql.encode()).hexdigest()
    expected_result_hash = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert evidence[0]["result_sha256"] == expected_result_hash
    assert evidence[0]["provider_query_id"] == "trino-123"
    assert "confidential-value" not in json.dumps(
        [event.model_dump(mode="json") for event in events]
    )
    assert sql not in json.dumps([event.model_dump(mode="json") for event in events])


@pytest.mark.integration
def test_effect_authority_failures_recovery_and_concurrent_delivery(incident_db, monkeypatch):
    from phlo_api.observatory_api import settings

    monkeypatch.setattr(
        settings,
        "get_operational_settings",
        lambda: type("Settings", (), {"notify_owners": False, "notify_consumers": False})(),
    )
    deliveries = []

    class Sink:
        fail = True

        def send_alert(self, **payload):
            deliveries.append(payload)
            return not self.fail

    sink = Sink()
    register_capability("alert_sink", AlertSinkSpec(name="qa", provider=sink))
    created = create(notify_qa=True)
    assert created.effects[0]["status"] == "failed"
    assert created.effects[0]["attempts"] == 1
    assert "Orders audit failed" not in json.dumps(deliveries)
    incidents.dispatch_incident_effects(request("bob"), env="prod", incident_id=created.id)
    incidents.dispatch_incident_effects(
        request(env="staging"), env="staging", incident_id=created.id
    )
    assert len(deliveries) == 1
    sink.fail = False
    incidents.recover_incident_notifications()
    assert len(deliveries) == 1  # A fresh failure waits before an automatic retry.
    with incidents._transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "UPDATE phlo.incident_effect SET updated_at=now()-interval '2 minutes' WHERE incident_id=%s",
            (created.id,),
        )
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(
            workers.map(
                lambda _: incidents.recover_incident_notifications(),
                range(2),
            )
        )
    persisted = incidents.incident_detail(request(), created.id, "prod")
    assert persisted.effects[0]["status"] == "delivered"
    assert persisted.effects[0]["attempts"] == 2
    assert len(deliveries) == 2
    assert deliveries[0]["run_id"] == deliveries[1]["run_id"]
    incidents.dispatch_incident_effects(None, env="prod", incident_id=created.id)
    assert len(deliveries) == 2

    # Prove the API router starts recovery without a browser or an HTTP mutation.
    from threading import Event
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    with incidents._transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "UPDATE phlo.incident_effect SET status='failed',updated_at=now()-interval '2 minutes' WHERE incident_id=%s",
            (created.id,),
        )
    recovered = Event()
    recovery = incidents.recover_incident_notifications

    def recover_and_signal():
        recovery()
        recovered.set()

    monkeypatch.setattr(incidents, "recover_incident_notifications", recover_and_signal)
    app = FastAPI()
    app.include_router(incidents.router)
    with TestClient(app):
        assert recovered.wait(5)
    assert len(deliveries) == 3
    assert (
        incidents.incident_detail(request(), created.id, "prod").effects[0]["status"] == "delivered"
    )

    denied = [policy for policy in incident_db if policy["action"] != "service.manage"]
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="parity", provider=DefaultAuthorizationPolicyBackend(policies=denied)
        ),
    )
    with pytest.raises(HTTPException) as error:
        create(subject="mallory", notify_qa=True)
    assert error.value.status_code == 403
    assert len(incidents.list_incidents(request(), "prod").items) == 1
    with incidents._transaction() as connection, connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM phlo.incident_command WHERE actor='mallory'")
        assert cur.fetchone()[0] == 0


@pytest.mark.integration
def test_pause_outbox_uses_scoped_graph_and_real_schedule_consumer(incident_db, monkeypatch):
    from phlo_api.api import v1_assets, v1_jobs

    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod_loc", "nessie_ref": "main"},
                "staging": {"dagster_location": "stage_loc", "nessie_ref": "candidate"},
            }
        ),
    )
    for gate in (
        "PHLO_V1_ACTIONS_SINGLE_REPLICA",
        "PHLO_V1_ACTIONS_SINGLE_PROCESS",
        "PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
    ):
        monkeypatch.setenv(gate, "1")
    state = {"prod_loc": "RUNNING", "stage_loc": "RUNNING"}
    mutations = []

    async def graphql(url, query, variables=None):
        if "V1Jobs" in query:
            return {
                "data": {
                    "repositoriesOrError": {
                        "__typename": "RepositoryConnection",
                        "nodes": [
                            {
                                "name": "repo",
                                "location": {"name": location},
                                "pipelines": [{"name": "report", "description": "report"}],
                                "schedules": [
                                    {
                                        "id": f"{location}:daily",
                                        "name": "daily",
                                        "pipelineName": "report",
                                        "scheduleState": {
                                            "id": f"{location}:daily",
                                            "status": status,
                                        },
                                    }
                                ],
                            }
                            for location, status in state.items()
                        ],
                    }
                }
            }
        if "V1Assets" in query:
            return {
                "data": {
                    "repositoriesOrError": {
                        "__typename": "RepositoryConnection",
                        "nodes": [
                            {
                                "name": "repo",
                                "location": {"name": location},
                                "assetNodes": [
                                    {
                                        "assetKey": {"path": key.split("/")},
                                        "dependencyKeys": [
                                            {"path": dep.split("/")} for dep in deps
                                        ],
                                        "jobNames": [job],
                                        "groupName": "transform",
                                        "metadataEntries": [{"label": "phlo/layer", "text": layer}],
                                        "repository": {
                                            "name": "repo",
                                            "location": {"name": location},
                                        },
                                    }
                                    for key, deps, job, layer in (
                                        ("bronze/orders", [], "ingest", "bronze"),
                                        ("silver/orders", ["bronze/orders"], "transform", "silver"),
                                        ("gold/report", ["silver/orders"], "report", "gold"),
                                        ("gold/unrelated", [], "other", "gold"),
                                    )
                                ],
                            }
                            for location in state
                        ],
                    }
                }
            }
        if "V1AssetJobs" in query:
            selector = variables["pipeline"]
            inventory = await graphql(url, "V1Assets")
            return {
                "data": {
                    "assetNodes": [
                        node
                        for repository in inventory["data"]["repositoriesOrError"]["nodes"]
                        if repository["location"]["name"] == selector["repositoryLocationName"]
                        and repository["name"] == selector["repositoryName"]
                        for node in repository["assetNodes"]
                        if selector["pipelineName"] in node["jobNames"]
                    ]
                }
            }
        assert "V1ScheduleStop" in query
        mutations.append(variables["scheduleId"])
        state["prod_loc"] = "STOPPED"
        return {
            "data": {
                "stopRunningSchedule": {
                    "__typename": "ScheduleStateResult",
                    "scheduleState": {"status": "STOPPED"},
                }
            }
        }

    monkeypatch.setattr(v1_jobs, "graphql_request", graphql)
    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    created = create(pause_downstream=True)
    assert created.effects[0]["status"] == "delivered"
    assert mutations == ["prod_loc:daily"]
    assert state == {"prod_loc": "STOPPED", "stage_loc": "RUNNING"}
    with incidents._transaction() as connection, connection.cursor() as cur:
        cur.execute("SELECT payload FROM phlo.incident_effect WHERE incident_id=%s", (created.id,))
        targets = cur.fetchone()[0]["targets"]
        assert targets[0]["asset_ids"] == ["gold/report"]
        assert targets[0]["subject"] == "alice"
        # Simulate an interrupted worker, then another operator resuming the schedule.
        cur.execute(
            "UPDATE phlo.incident_effect SET status='failed' WHERE incident_id=%s", (created.id,)
        )
    state["prod_loc"] = "RUNNING"
    incidents.dispatch_incident_effects(request("bob"), env="prod", incident_id=created.id)
    incidents.dispatch_incident_effects(None, env="prod", incident_id=created.id)
    assert len(mutations) == 1
    incidents.dispatch_incident_effects(request(), env="prod", incident_id=created.id)
    assert incidents.incident_detail(request(), created.id, "prod").effects[0]["status"] == "failed"
    assert len(mutations) == 1
    denied = [policy for policy in incident_db if policy["action"] != "run.manage"]
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="parity", provider=DefaultAuthorizationPolicyBackend(policies=denied)
        ),
    )
    with pytest.raises(HTTPException) as error:
        create(subject="mallory", pause_downstream=True)
    assert error.value.status_code == 403
    assert len(incidents.list_incidents(request(), "prod").items) == 1


@pytest.mark.integration
def test_unconfigured_notification_fails_before_persistence(incident_db):
    get_capability_registry().clear("alert_sink")
    with pytest.raises(HTTPException) as error:
        create(notify_qa=True)
    assert error.value.status_code == 503
    assert incidents.list_incidents(request(), "prod").items == []
