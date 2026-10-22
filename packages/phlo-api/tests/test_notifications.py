"""Notification disclosure and read receipts require the current actor and incident visibility."""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from phlo.capabilities import AuthPrincipal, AuthorizationDecision, Principal
from phlo_api import incidents, notifications, security_manifest
from phlo_api.main import app


@pytest.fixture
def inbox(monkeypatch):
    actor = {"subject": "alice"}
    calls = []
    monkeypatch.setattr(
        incidents,
        "get_request_principal",
        lambda request: (
            AuthPrincipal(subject=actor["subject"], principal_type="user", groups=("reader",))
            if actor["subject"]
            else None
        ),
    )
    monkeypatch.setattr(security_manifest, "get_request_principal", incidents.get_request_principal)
    monkeypatch.setattr(
        security_manifest,
        "resolve_request_principal",
        lambda *args, **kwargs: Principal(
            subject=actor["subject"], principal_type="user", roles=("reader",)
        ),
    )
    monkeypatch.setattr(security_manifest, "is_regulated", lambda: False)

    class Policy:
        def explain_decision(self, principal, action, resource, context=None):
            calls.append((principal.subject, action, resource.resource_id))
            return AuthorizationDecision(
                allowed="incident_id=hidden" not in resource.resource_id, reason_code="test_policy"
            )

    monkeypatch.setattr(security_manifest, "get_authorization_backend", Policy)
    rows = [
        (
            "event-1",
            "visible",
            "Actual incident",
            "silver/qc_results",
            "sensor",
            "signal",
            datetime.now(UTC),
            False,
        ),
        (
            "event-hidden",
            "hidden",
            "Private incident",
            "private/asset",
            "sensor",
            "signal",
            datetime.now(UTC),
            False,
        ),
    ]
    monkeypatch.setattr(notifications, "_notification_candidates", lambda *args: rows)
    return TestClient(app), actor, calls


def test_notifications_authorize_each_incident_before_disclosure(inbox):
    client, _, calls = inbox
    response = client.get("/api/v1/notifications?env=prod&view=all")
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == ["event-1"]
    assert response.json()["unread"] == 1
    assert ("alice", "run.read", "env=prod|incident_id=hidden") in calls
    assert "Private incident" not in response.text


def test_notification_reads_require_authentication(inbox):
    client, actor, _ = inbox
    actor["subject"] = None
    assert client.get("/api/v1/notifications?env=prod").status_code == 401


@pytest.mark.parametrize(
    "headers,status",
    [
        ({}, 403),
        ({"X-Phlo-Intent": "notifications.mark-read", "Origin": "https://untrusted.invalid"}, 403),
        ({"X-Phlo-Intent": "notifications.mark-read", "Sec-Fetch-Site": "cross-site"}, 403),
    ],
)
def test_read_receipts_reject_missing_intent_and_cross_site_requests(inbox, headers, status):
    client, _, _ = inbox
    response = client.put(
        "/api/v1/notifications/read-state?env=prod",
        json={"event_ids": ["event-1"]},
        headers=headers,
    )
    assert response.status_code == status, response.text


def test_read_receipts_bind_subject_to_authentication_and_hide_unauthorized_events(
    inbox, monkeypatch
):
    client, actor, _ = inbox
    writes = []
    requested = []

    class Cursor:
        def execute(self, sql, params):
            requested.append(params)

        def fetchall(self):
            return [("event-1", "visible")]

        def executemany(self, sql, params):
            writes.extend(params)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    @contextmanager
    def transaction():
        yield SimpleNamespace(cursor=Cursor)

    monkeypatch.setattr(incidents, "_transaction", transaction)
    headers = {"X-Phlo-Intent": "notifications.mark-read"}
    response = client.put(
        "/api/v1/notifications/read-state?env=prod",
        json={"event_ids": ["event-1"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert writes == [("prod", "alice", "event-1")]
    actor["subject"] = "bob"
    response = client.put(
        "/api/v1/notifications/read-state?env=prod",
        json={"event_ids": ["event-1"]},
        headers=headers,
    )
    assert response.status_code == 200
    assert writes[-1] == ("prod", "bob", "event-1")
    assert all(params[0] == "prod" for params in requested)
    spoofed = client.put(
        "/api/v1/notifications/read-state?env=prod",
        json={"event_ids": ["event-1"], "subject": "alice"},
        headers=headers,
    )
    assert spoofed.status_code == 422


def test_hidden_incident_events_cannot_be_marked_read(inbox, monkeypatch):
    client, _, _ = inbox

    class Cursor:
        def execute(self, *args):
            pass

        def fetchall(self):
            return [("event-hidden", "hidden")]

        def executemany(self, *args):
            pytest.fail("Denied incident events must never produce a receipt")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    @contextmanager
    def transaction():
        yield SimpleNamespace(cursor=Cursor)

    monkeypatch.setattr(incidents, "_transaction", transaction)
    response = client.put(
        "/api/v1/notifications/read-state?env=prod",
        json={"event_ids": ["event-hidden"]},
        headers={"X-Phlo-Intent": "notifications.mark-read"},
    )
    assert response.status_code == 404, response.text
