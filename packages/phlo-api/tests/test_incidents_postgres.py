"""PostgreSQL-backed incident persistence, replay and environment tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from testcontainers.postgres import PostgresContainer

from phlo_api import incidents


@pytest.mark.integration
def test_incident_transactions_group_concurrent_signals_and_isolate_environments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PostgreSQL uniqueness and idempotency hold under overlapping writers."""
    with PostgresContainer("postgres:18-alpine") as postgres:
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
        assert prod_stats["counts"] == {"open": 1}
        assert staging_stats["counts"] == {"open": 1}

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
