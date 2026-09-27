"""Focused tests for the durable v1 admin audit read API."""

from __future__ import annotations

from contextlib import contextmanager

import psycopg2
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from phlo.audit.events import CanonicalAuditEvent
from phlo.compliance.audit.sealed import GENESIS_HASH, SealedAuditRecord
from phlo.compliance.audit.store import InMemoryAuditStore
from phlo_api.api import v1_admin_audit
from phlo_api.errors import PhloApiError, error_envelope

_KEY = b"audit-test-key"


def _record(sequence: int, previous: str, *, actor: str = "alice") -> SealedAuditRecord:
    return SealedAuditRecord.seal(
        CanonicalAuditEvent(
            surface="phlo-api",
            event_type="authorization",
            actor_subject=actor,
            action="dataset.read",
            resource_id=f"dataset:{sequence}",
            decision="allow",
        ),
        sequence,
        previous,
        hmac_key=_KEY,
    )


def _store() -> InMemoryAuditStore:
    store = InMemoryAuditStore()
    previous = GENESIS_HASH
    for sequence, actor in ((1, "alice"), (2, "bob"), (3, "alice")):
        record = _record(sequence, previous, actor=actor)
        store.append(record)
        previous = record.record_hash
    return store


def _client(monkeypatch, store=None) -> TestClient:
    audit_store = store or _store()

    @contextmanager
    def supplied_store():
        yield audit_store

    monkeypatch.setattr(v1_admin_audit, "_audit_store", supplied_store)
    monkeypatch.setattr(
        v1_admin_audit,
        "compute_record_hash",
        lambda event, sequence, previous: (
            SealedAuditRecord.seal(event, sequence, previous, hmac_key=_KEY).record_hash
        ),
    )
    app = FastAPI()
    app.include_router(v1_admin_audit.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def handle_error(request: Request, exc: PhloApiError) -> JSONResponse:
        del request
        return JSONResponse(status_code=exc.status_code, content=error_envelope(exc))

    return TestClient(app)


def test_search_filters_and_pages_records(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/v1/admin/audit/records",
        params={"surface": "phlo-api", "actor_subject": "alice", "limit": 1},
    )

    assert response.status_code == 200
    assert [item["sequence_number"] for item in response.json()["items"]] == [1]
    assert response.json()["next_after"] == 1
    assert response.json()["scan_truncated"] is False


def test_search_rejects_unbounded_limit_and_invalid_surface(monkeypatch) -> None:
    client = _client(monkeypatch)

    assert (
        client.get(
            "/api/v1/admin/audit/records", params={"surface": "phlo-api", "limit": 501}
        ).status_code
        == 422
    )
    assert (
        client.get("/api/v1/admin/audit/records", params={"surface": "bad surface"}).status_code
        == 422
    )


def test_verify_reads_the_full_chain_in_batches(monkeypatch) -> None:
    store = InMemoryAuditStore()
    previous = GENESIS_HASH
    for sequence in range(1, 1002):
        record = _record(sequence, previous)
        store.append(record)
        previous = record.record_hash
    calls: list[int] = []
    original_query = store.query

    def query(surface, after=None, before=None, limit=1000):
        calls.append(limit)
        return original_query(surface, after=after, before=before, limit=limit)

    store.query = query
    response = _client(monkeypatch, store).get(
        "/api/v1/admin/audit/verify", params={"surface": "phlo-api"}
    )

    assert response.json() == {
        "surface": "phlo-api",
        "valid": True,
        "total_records": 1001,
        "first_invalid_sequence": None,
        "error_message": None,
    }
    assert calls == [1000, 1000]


def test_verify_reports_tampering_without_hash_list(monkeypatch) -> None:
    store = _store()
    store._records["phlo-api"][1] = _record(2, GENESIS_HASH, actor="bob")

    response = _client(monkeypatch, store).get(
        "/api/v1/admin/audit/verify", params={"surface": "phlo-api"}
    )

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["total_records"] == 3
    assert response.json()["first_invalid_sequence"] == 2
    assert "verified_hashes" not in response.json()


def test_export_is_bounded_jsonl(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/v1/admin/audit/export",
        params={"surface": "phlo-api", "after": 1, "limit": 1},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["content-disposition"] == 'attachment; filename="audit-phlo-api.jsonl"'
    assert len(response.text.rstrip().splitlines()) == 1
    assert '"sequence_number":2' in response.text


def test_missing_durable_storage_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_RUN_EVIDENCE_DB_URL", raising=False)
    app = FastAPI()
    app.include_router(v1_admin_audit.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def handle_error(request: Request, exc: PhloApiError) -> JSONResponse:
        del request
        return JSONResponse(status_code=exc.status_code, content=error_envelope(exc))

    response = TestClient(app).get("/api/v1/admin/audit/records", params={"surface": "phlo-api"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"


def test_unavailable_durable_storage_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", "postgresql://unavailable")

    def unavailable(_dsn):
        raise psycopg2.OperationalError("connection refused")

    monkeypatch.setattr(v1_admin_audit.psycopg2, "connect", unavailable)
    app = FastAPI()
    app.include_router(v1_admin_audit.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def handle_error(request: Request, exc: PhloApiError) -> JSONResponse:
        del request
        return JSONResponse(status_code=exc.status_code, content=error_envelope(exc))

    response = TestClient(app).get("/api/v1/admin/audit/verify", params={"surface": "phlo-api"})

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Durable audit storage is unavailable."
