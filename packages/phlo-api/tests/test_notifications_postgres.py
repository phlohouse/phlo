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


@pytest.mark.integration
def test_notification_backlog_keysets_unread_filter_and_denied_scan_continuation(monkeypatch):
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", postgres.get_connection_url(driver=None))
        actor = SimpleNamespace(subject="alice")
        monkeypatch.setattr(incidents, "get_request_principal", lambda request: actor)
        monkeypatch.setattr(
            notifications, "_visible", lambda request, env, incident_id: incident_id != "hidden"
        )
        incidents.initialize_incidents()
        with incidents._transaction() as connection, connection.cursor() as cur:
            cur.executemany(
                "INSERT INTO phlo.incident(incident_id,env,asset_id,kind,title,status,owner) VALUES (%s,'prod',%s,'audit',%s,'open','alice')",
                [("visible", "asset", "Visible"), ("hidden", "secret", "Private")],
            )
            cur.executemany(
                "INSERT INTO phlo.incident_event(event_id,incident_id,env,actor,kind,payload,occurred_at) VALUES (%s,'visible','prod','sensor','signal','{}',now())",
                [(f"event-{index:03d}",) for index in range(150)],
            )
            cur.execute(
                "INSERT INTO phlo.incident_notification_read(env,subject,event_id) SELECT 'prod','alice',event_id FROM phlo.incident_event ORDER BY event_id DESC LIMIT 100"
            )
        request = Request(
            {
                "type": "http",
                "method": "PUT",
                "scheme": "http",
                "server": ("testserver", 80),
                "path": "/api/v1/notifications",
                "query_string": b"env=prod",
                "headers": [
                    (b"x-phlo-intent", b"notifications.mark-read"),
                    (b"content-type", b"application/json"),
                ],
            }
        )
        first = notifications.v1_notifications(request, "prod", "all", 100)
        assert len(first.items) == 100 and first.unread == 0 and first.next_cursor
        second = notifications.v1_notifications(request, "prod", "all", 100, first.next_cursor)
        assert [item.id for item in second.items] == [
            f"event-{index:03d}" for index in range(49, -1, -1)
        ]
        assert second.unread == 50 and second.next_cursor is None and not second.truncated

        unread = notifications.v1_notifications(request, "prod", "all", 10, unread_only=True)
        assert [item.id for item in unread.items] == [
            f"event-{index:03d}" for index in range(49, 39, -1)
        ]
        notifications.v1_notifications_read(
            request,
            notifications.NotificationReadInput(event_ids=[item.id for item in unread.items]),
            "prod",
        )
        continued = notifications.v1_notifications(
            request, "prod", "all", 10, unread.next_cursor, unread_only=True
        )
        assert [item.id for item in continued.items] == [
            f"event-{index:03d}" for index in range(39, 29, -1)
        ]
        for env, view, unread_only in [
            ("staging", "all", True),
            ("prod", "watching", True),
            ("prod", "all", False),
        ]:
            with pytest.raises(Exception) as invalid:
                notifications.v1_notifications(
                    request, env, view, 10, unread.next_cursor, unread_only
                )
            assert invalid.value.status_code == 400
        actor.subject = "bob"
        with pytest.raises(Exception) as invalid_actor:
            notifications.v1_notifications(request, "prod", "all", 10, unread.next_cursor, True)
        assert invalid_actor.value.status_code == 400
        actor.subject = "alice"

        with incidents._transaction() as connection, connection.cursor() as cur:
            cur.executemany(
                "INSERT INTO phlo.incident_event(event_id,incident_id,env,actor,kind,payload,occurred_at) VALUES (%s,'hidden','prod','sensor','signal','{}',now()+interval '1 minute')",
                [(f"hidden-{index:03d}",) for index in range(201)],
            )
        denied_page = notifications.v1_notifications(request, "prod", "all", 100)
        assert denied_page.items == [] and denied_page.next_cursor and denied_page.truncated
        visible_page = notifications.v1_notifications(
            request, "prod", "all", 100, denied_page.next_cursor
        )
        assert len(visible_page.items) == 100
        assert all(item.incident_id == "visible" for item in visible_page.items)
