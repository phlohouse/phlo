"""Personal inbox reads survive connections while remaining actor- and environment-bound."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request
from testcontainers.postgres import PostgresContainer
from phlo_api import incidents, notifications


@pytest.mark.integration
def test_notification_filters_and_read_receipts_are_durable_and_actor_scoped(monkeypatch):
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", postgres.get_connection_url(driver=None))
        subject = {"value": "alice"}
        monkeypatch.setattr(
            incidents,
            "get_request_principal",
            lambda request: SimpleNamespace(subject=subject["value"]),
        )
        monkeypatch.setattr(notifications, "_visible", lambda request, env, incident_id: True)
        incidents.initialize_incidents()
        incidents.initialize_incidents()
        with incidents._transaction() as connection, connection.cursor() as cur:
            for identity, env, owner in [
                ("assigned", "prod", "alice"),
                ("watched", "prod", "bob"),
                ("other", "staging", "alice"),
            ]:
                cur.execute(
                    "INSERT INTO phlo.incident(incident_id,env,asset_id,kind,title,status,owner) VALUES (%s,%s,%s,'audit','Real incident','open',%s)",
                    (identity, env, identity, owner),
                )
                cur.execute(
                    "INSERT INTO phlo.incident_event(event_id,incident_id,env,actor,kind,payload) VALUES (%s,%s,%s,'sensor','signal','{}')",
                    (f"event-{identity}", identity, env),
                )
            cur.execute(
                "INSERT INTO phlo.incident_subscription(env,incident_id,subject) VALUES ('prod','watched','alice')"
            )
        request = Request(
            {
                "type": "http",
                "method": "PUT",
                "scheme": "http",
                "server": ("testserver", 80),
                "path": "/api/v1/notifications/read-state",
                "query_string": b"env=prod",
                "headers": [
                    (b"x-phlo-intent", b"notifications.mark-read"),
                    (b"content-type", b"application/json"),
                ],
            }
        )
        assigned = notifications.v1_notifications(request, "prod", "for_you", 100)
        watching = notifications.v1_notifications(request, "prod", "watching", 100)
        assert [item.id for item in assigned.items] == ["event-assigned"]
        assert [item.id for item in watching.items] == ["event-watched"]
        notifications.v1_notifications_read(
            request, notifications.NotificationReadInput(event_ids=["event-assigned"]), "prod"
        )
        incidents.initialize_incidents()
        assert notifications.v1_notifications(request, "prod", "for_you", 100).items[0].read
        subject["value"] = "bob"
        bob = notifications.v1_notifications(request, "prod", "all", 100)
        assert all(not item.read for item in bob.items)
        assert (
            notifications.v1_incident_subscription(request, "watched", "prod").subscribed is False
        )
        subject["value"] = "alice"
        assert notifications.v1_incident_subscription(request, "watched", "prod").subscribed is True
        with pytest.raises(Exception) as denied:
            notifications.v1_notifications_read(
                request, notifications.NotificationReadInput(event_ids=["event-other"]), "prod"
            )
        assert denied.value.status_code == 404
