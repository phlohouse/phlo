"""PostgreSQL-backed incident persistence, replay and environment tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer

from phlo.capabilities import AuthPrincipal, AuthorizationDecision, Principal
from phlo_api import incidents, usage
from phlo_api import security_manifest
from phlo_api.main import app


@pytest.mark.integration
def test_api_startup_initializes_incident_and_usage_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        dsn = postgres.get_connection_url(driver=None)
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", dsn)
        monkeypatch.setenv("PHLO_ENVIRONMENT", "dev")
        monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
        principal = AuthPrincipal(subject="operator", principal_type="user", groups=("operator",))
        monkeypatch.setattr(incidents, "get_request_principal", lambda _request: principal)
        monkeypatch.setattr(security_manifest, "get_request_principal", lambda _request: principal)
        monkeypatch.setattr(
            security_manifest,
            "resolve_request_principal",
            lambda *_args, **_kwargs: Principal(
                subject="operator", principal_type="user", roles=("operator",)
            ),
        )
        monkeypatch.setattr(security_manifest, "is_regulated", lambda: False)

        class AllowAll:
            def explain_decision(self, *_args, **_kwargs):
                return AuthorizationDecision(allowed=True, reason_code="explicit_allow")

        monkeypatch.setattr(security_manifest, "get_authorization_backend", AllowAll)

        with TestClient(app) as client:
            response = client.get("/api/v1/incidents?env=prod")
            assert response.status_code == 200, response.text
            assert response.json()["items"] == []

        incidents.initialize_incidents()
        with incidents._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO phlo.incident (incident_id, env, asset_id, kind, title, status)
                VALUES ('retained', 'prod', 'orders', 'audit', 'Existing incident', 'open')
                """
            )

        with TestClient(app) as client:
            response = client.get("/api/v1/incidents?env=prod")
            assert response.status_code == 200, response.text
            assert [item["id"] for item in response.json()["items"]] == ["retained"]

        import psycopg2

        with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('phlo.incident'), to_regclass('phlo.query_usage_event')"
            )
            assert cursor.fetchone() == ("phlo.incident", "phlo.query_usage_event")


@pytest.mark.integration
def test_incident_and_usage_initializers_are_concurrent_and_preserve_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", postgres.get_connection_url(driver=None))

        initializers = [incidents.initialize_incidents, usage.initialize_usage] * 2
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda initialize: initialize(), initializers))

        with incidents._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO phlo.incident (incident_id, env, asset_id, kind, title, status)
                VALUES ('kept', 'prod', 'orders', 'audit', 'Existing incident', 'open')
                """
            )
            cursor.execute(
                """
                INSERT INTO phlo.query_usage_event (source_id, query_id, event_digest)
                VALUES ('listener', 'query-1', 'digest')
                """
            )

        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda initialize: initialize(), initializers))

        with incidents._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT incident_id FROM phlo.incident WHERE incident_id = 'kept'")
            assert cursor.fetchone() == ("kept",)
            cursor.execute("SELECT query_id FROM phlo.query_usage_event WHERE query_id = 'query-1'")
            assert cursor.fetchone() == ("query-1",)


@pytest.mark.integration
def test_incident_mutation_openapi_matches_nullable_and_replayed_http_results(monkeypatch):
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", postgres.get_connection_url(driver=None))
        monkeypatch.setattr(
            incidents, "get_request_principal", lambda _request: SimpleNamespace(subject="operator")
        )
        incidents.initialize_incidents()
        app = FastAPI()
        app.include_router(incidents.router, prefix="/api/v1")
        client = TestClient(app)
        created = client.post(
            "/api/v1/incidents?env=prod",
            headers={"Idempotency-Key": "incident"},
            json={
                "asset_id": "orders",
                "kind": "schema_drift",
                "title": "Schema changed",
                "evidence": {"column": "order_id"},
                "evidence_id": "schema:orders:order_id",
            },
        )
        assert created.status_code == 201, created.text
        incident_id = created.json()["id"]
        paths = app.openapi()["paths"]
        models = app.openapi()["components"]["schemas"]

        def mutation(method, path, declared_path, payload, key, status):
            response = client.request(
                method, f"{path}?env=prod", json=payload, headers={"Idempotency-Key": key}
            )
            assert response.status_code == status, response.text
            schema = paths[declared_path][method.lower()]["responses"][str(status)]["content"][
                "application/json"
            ]["schema"]
            contract = models[schema["$ref"].rsplit("/", 1)[-1]] if "$ref" in schema else schema
            assert set(contract.get("required", [])) == set(response.json())
            replay = client.request(
                method, f"{path}?env=prod", json=payload, headers={"Idempotency-Key": key}
            )
            assert replay.status_code == status
            assert replay.json() == response.json()
            return response.json()

        base = f"/api/v1/incidents/{incident_id}"
        subscription = mutation(
            "PUT",
            f"{base}/subscriptions",
            "/api/v1/incidents/{incident_id}/subscriptions",
            None,
            "subscription",
            200,
        )
        assert subscription == {"incident_id": incident_id, "subscribed": True}
        unsubscribed = client.put(
            f"{base}/subscriptions?env=prod&subscribed=false",
            headers={"Idempotency-Key": "unsubscribe"},
        )
        assert unsubscribed.status_code == 200
        assert unsubscribed.json() == {"incident_id": incident_id, "subscribed": False}
        follow_up = mutation(
            "POST",
            f"{base}/follow-ups",
            "/api/v1/incidents/{incident_id}/follow-ups",
            {"description": "Review contract", "due_at": None},
            "follow-up",
            201,
        )
        assert follow_up["incident_id"] == incident_id
        assert follow_up["due_at"] is None and follow_up["completed_at"] is None
        completed = mutation(
            "PATCH",
            f"{base}/follow-ups/{follow_up['id']}",
            "/api/v1/incidents/{incident_id}/follow-ups/{follow_up_id}",
            {"completed": True},
            "complete",
            200,
        )
        assert completed["id"] == follow_up["id"]
        assert completed["due_at"] is None and completed["completed_at"] is not None


@pytest.mark.integration
def test_incident_transactions_group_concurrent_signals_and_isolate_environments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PostgreSQL uniqueness and idempotency hold under overlapping writers."""
    # Disposable databases must not inherit missing or empty deployment credentials.
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", postgres.get_connection_url(driver=None))
        monkeypatch.setattr(
            incidents,
            "get_request_principal",
            lambda _request: SimpleNamespace(subject="operator"),
        )
        incidents.initialize_incidents()
        request = SimpleNamespace()
        payload = incidents.IncidentInput(
            asset_id="warehouse/orders",
            kind="failed_check",
            title="Orders check failed",
            evidence={"check_id": "orders.pk", "passed": False},
            evidence_id="dagster:run-1:orders.pk",
        )

        def write_signal(key: str):
            return incidents.create_incident(request, payload, key, "prod")

        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(write_signal, [f"retry-{i}" for i in range(8)]))
        assert len({item.id for item in results}) == 1

        first = incidents.create_incident(request, payload, "retry-0", "prod")
        assert first.id == results[0].id
        with pytest.raises(HTTPException) as conflict:
            incidents.create_incident(
                request,
                payload.model_copy(update={"title": "Different payload"}),
                "retry-0",
                "prod",
            )
        assert conflict.value.status_code == 409

        staging = incidents.create_incident(request, payload, "staging-key", "staging")
        assert staging.id != first.id
        prod_stats = incidents.incident_stats(request, "prod")
        staging_stats = incidents.incident_stats(request, "staging")
        assert prod_stats.counts == {"open": 1}
        assert staging_stats.counts == {"open": 1}

        incidents.create_incident(
            request,
            payload.model_copy(
                update={
                    "asset_id": "warehouse/invoices",
                    "evidence_id": "dagster:run-2:invoices.check",
                    "title": "Invoice check failed",
                }
            ),
            "invoice-key",
            "prod",
        )
        first_page = incidents.list_incidents(request, "prod", 1)
        second_page = incidents.list_incidents(request, "prod", 1, first_page.next_cursor)
        assert first_page.next_cursor is not None
        assert first_page.items[0].id != second_page.items[0].id
        with pytest.raises(HTTPException) as cursor_error:
            incidents.list_incidents(request, "staging", 1, first_page.next_cursor)
        assert cursor_error.value.status_code == 400

        updated = incidents.update_incident(
            request,
            first.id,
            incidents.IncidentUpdate(status="acknowledged", owner="alice"),
            "ack-1",
            "prod",
            "1",
        )
        replay = incidents.update_incident(
            request,
            first.id,
            incidents.IncidentUpdate(status="acknowledged", owner="alice"),
            "ack-1",
            "prod",
            "1",
        )
        assert updated.version == replay.version == 2
        with pytest.raises(HTTPException) as stale:
            incidents.update_incident(
                request,
                first.id,
                incidents.IncidentUpdate(status="open"),
                "stale-1",
                "prod",
                "1",
            )
        assert stale.value.status_code == 409

        subscription = incidents.subscribe_incident(request, first.id, "sub-1", "prod", True)
        assert subscription == {"incident_id": first.id, "subscribed": True}
        assert (
            incidents.subscribe_incident(request, first.id, "sub-1", "prod", True) == subscription
        )
        with pytest.raises(HTTPException) as subscription_conflict:
            incidents.subscribe_incident(request, first.id, "sub-1", "prod", False)
        assert subscription_conflict.value.status_code == 409

        policy_input = incidents.AssetIncidentPolicyInput(
            owner="data-team", freshness_sla_seconds=3600
        )
        policy = incidents.put_asset_incident_policy(
            request, "warehouse/orders", policy_input, "policy-1", "prod", "0"
        )
        assert policy["version"] == 1
        assert (
            incidents.put_asset_incident_policy(
                request, "warehouse/orders", policy_input, "policy-1", "prod", "0"
            )
            == policy
        )
        assert (
            incidents.get_asset_incident_policy(request, "warehouse/orders", "staging")["version"]
            == 0
        )
        with pytest.raises(HTTPException) as policy_conflict:
            incidents.put_asset_incident_policy(
                request,
                "warehouse/orders",
                incidents.AssetIncidentPolicyInput(owner="other", freshness_sla_seconds=3600),
                "policy-2",
                "prod",
                "0",
            )
        assert policy_conflict.value.status_code == 409
        incidents.put_asset_incident_policy(
            request,
            "warehouse/invoices",
            incidents.AssetIncidentPolicyInput(owner="finance", freshness_sla_seconds=7200),
            "policy-3",
            "prod",
            "0",
        )
        policy_page = incidents.list_asset_incident_policies(request, "prod", 1)
        policy_page_2 = incidents.list_asset_incident_policies(
            request, "prod", 1, policy_page.next_cursor
        )
        assert policy_page.next_cursor is not None
        assert policy_page.items[0]["asset_id"] == "warehouse/invoices"
        assert policy_page_2.items[0]["asset_id"] == "warehouse/orders"
        with pytest.raises(HTTPException) as cross_environment:
            incidents.list_asset_incident_policies(request, "staging", 1, policy_page.next_cursor)
        assert cross_environment.value.status_code == 400

        decision_input = incidents.SchemaDecisionInput(
            source_ref="feature/orders",
            target_ref="main",
            source_hash="source-abc",
            target_hash="target-def",
            table_key="warehouse.orders",
            columns={"customer_id": "source", "status": "target"},
            justification="Keep the widened identifier and canonical status.",
        )
        decision = incidents.create_schema_decision(
            request, first.id, decision_input, "decision-1", "prod"
        )
        replayed_decision = incidents.create_schema_decision(
            request, first.id, decision_input, "decision-1", "prod"
        )
        assert replayed_decision == decision
        assert decision.actor == "operator"

        # Reapplying the additive migration simulates a new process using the durable records.
        incidents.initialize_incidents()
        page = incidents.list_schema_decisions(request, first.id, "prod")
        assert page.items == [decision]
        assert incidents.load_schema_decisions(
            first.id,
            "prod",
            "feature/orders",
            "main",
            "source-abc",
            "target-def",
        ) == [decision]
        assert (
            incidents.load_schema_decisions(
                first.id,
                "prod",
                "feature/orders",
                "main",
                "stale-source",
                "target-def",
            )
            == []
        )
        assert (
            incidents.load_schema_decisions(
                staging.id,
                "staging",
                "feature/orders",
                "main",
                "source-abc",
                "target-def",
            )
            == []
        )

        with pytest.raises(HTTPException) as decision_replay_conflict:
            incidents.create_schema_decision(
                request,
                first.id,
                decision_input.model_copy(update={"justification": "Different choice."}),
                "decision-1",
                "prod",
            )
        assert decision_replay_conflict.value.status_code == 409
        with pytest.raises(HTTPException) as immutable_conflict:
            incidents.create_schema_decision(
                request, first.id, decision_input, "decision-2", "prod"
            )
        assert immutable_conflict.value.status_code == 409
        with pytest.raises(HTTPException) as wrong_environment:
            incidents.create_schema_decision(
                request, first.id, decision_input, "decision-staging", "staging"
            )
        assert wrong_environment.value.status_code == 404

        timeline = incidents.incident_timeline(request, first.id, "prod")
        decision_events = [event for event in timeline.items if event.kind == "schema_decision"]
        assert len(decision_events) == 1
        assert decision_events[0].payload["id"] == decision.id


@pytest.mark.integration
def test_explicit_environment_upgrade_checks_real_constraints_and_preserves_rows(monkeypatch):
    import json

    import psycopg2
    from click.testing import CliRunner

    from phlo_api import migrations, usage

    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        dsn = postgres.get_connection_url(driver=None)
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", dsn)
        incidents.initialize_incidents()
        usage.initialize_usage()
        runner = CliRunner()
        fresh = runner.invoke(migrations.main, ["--check", "--json"])
        assert fresh.exit_code == 0, fresh.output
        assert json.loads(fresh.output)["state"] == "compatible"
        with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO phlo.incident (incident_id, env, asset_id, kind, title, status) VALUES ('kept', 'prod', 'orders', 'quality', 'Existing incident', 'open')"
            )
            cursor.execute("ALTER TABLE phlo.incident DROP CONSTRAINT incident_env_check")
            cursor.execute(
                "ALTER TABLE phlo.incident ADD CONSTRAINT incident_env_check CHECK (env IN ('prod', 'staging'))"
            )
        required = runner.invoke(migrations.main, ["--check", "--json"])
        assert required.exit_code == 1, required.output
        assert json.loads(required.output)["state"] == "required"
        assert runner.invoke(migrations.main, ["--apply"]).exit_code != 0
        applied = runner.invoke(migrations.main, ["--apply", "--backup-confirmed", "--json"])
        assert applied.exit_code == 0, applied.output
        assert json.loads(applied.output)["state"] == "compatible"
        with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT env FROM phlo.incident WHERE incident_id = 'kept'")
            assert cursor.fetchone() == ("prod",)
            cursor.execute(
                "INSERT INTO phlo.incident (incident_id, env, asset_id, kind, title, status) VALUES ('new', 'sandbox', 'orders', 'quality', 'New incident', 'open')"
            )
        again = runner.invoke(migrations.main, ["--apply", "--backup-confirmed", "--json"])
        assert again.exit_code == 0, again.output
