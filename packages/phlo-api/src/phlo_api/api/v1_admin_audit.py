"""Read-only access to the durable, tamper-evident compliance audit trail."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import psycopg2
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import Field, StringConstraints

from phlo.compliance.audit.sealed import GENESIS_HASH, compute_record_hash
from phlo.compliance.audit.sealed import AuditStore
from phlo.compliance.audit.store import PostgresAuditStore
from phlo_api.errors import BackendUnavailableError
from phlo_api.v1_contract import WireModel

router = APIRouter(tags=["v1 admin audit"])

Surface = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.:-]+$"),
]
Sequence = Annotated[int, Query(ge=0)]
PageLimit = Annotated[int, Query(ge=1, le=500)]
ExportLimit = Annotated[int, Query(ge=1, le=5000)]
FilterValue = Annotated[str | None, Query(min_length=1, max_length=512)]
SearchValue = Annotated[str | None, Query(min_length=1, max_length=200)]

_QUERY_BATCH_SIZE = 1000
_SEARCH_SCAN_LIMIT = 5000


class AuditRecord(WireModel):
    sequence_number: int = Field(ge=1)
    sealed_at: str
    previous_hash: str
    record_hash: str
    event: dict[str, Any]


class AuditRecordPage(WireModel):
    surface: str
    items: list[AuditRecord]
    next_after: int | None
    scan_truncated: bool


class AuditChainVerification(WireModel):
    surface: str
    valid: bool
    total_records: int = Field(ge=0)
    first_invalid_sequence: int | None = None
    error_message: str | None = None


@contextmanager
def _audit_store() -> Iterator[AuditStore]:
    dsn = os.environ.get("PHLO_RUN_EVIDENCE_DB_URL")
    if not dsn:
        raise BackendUnavailableError("Durable audit storage is unavailable.")
    connection = None
    try:
        connection = psycopg2.connect(dsn)
        yield PostgresAuditStore(connection)
    except BackendUnavailableError:
        raise
    except Exception as exc:
        raise BackendUnavailableError("Durable audit storage is unavailable.") from exc
    finally:
        if connection is not None:
            connection.close()


def _matches(
    record: Any,
    *,
    event_type: str | None,
    decision: str | None,
    actor_subject: str | None,
    action: str | None,
    resource_id: str | None,
    search: str | None,
) -> bool:
    event = record.event
    if event_type is not None and event.event_type != event_type:
        return False
    if decision is not None and event.decision != decision:
        return False
    if actor_subject is not None and event.actor_subject != actor_subject:
        return False
    if action is not None and event.action != action:
        return False
    if resource_id is not None and event.resource_id != resource_id:
        return False
    if search is None:
        return True
    return search.casefold() in json.dumps(event.to_dict(), sort_keys=True).casefold()


def _query_records(
    store: AuditStore,
    surface: str,
    *,
    after: int | None,
    before: int | None,
    limit: int,
    event_type: str | None,
    decision: str | None,
    actor_subject: str | None,
    action: str | None,
    resource_id: str | None,
    search: str | None,
) -> tuple[list[Any], int | None, bool]:
    items: list[Any] = []
    cursor = after
    scanned = 0
    exhausted = False
    while len(items) < limit and scanned < _SEARCH_SCAN_LIMIT:
        batch_limit = min(_QUERY_BATCH_SIZE, _SEARCH_SCAN_LIMIT - scanned)
        batch = store.query(surface, after=cursor, before=before, limit=batch_limit)
        if not batch:
            exhausted = True
            break
        scanned += len(batch)
        cursor = batch[-1].sequence_number
        items.extend(
            record
            for record in batch
            if _matches(
                record,
                event_type=event_type,
                decision=decision,
                actor_subject=actor_subject,
                action=action,
                resource_id=resource_id,
                search=search,
            )
        )
        if len(batch) < batch_limit:
            exhausted = True
            break
    page = items[:limit]
    scan_truncated = not exhausted and scanned >= _SEARCH_SCAN_LIMIT
    if len(page) == limit:
        next_after = page[-1].sequence_number
    elif scan_truncated:
        next_after = cursor
    else:
        next_after = None
    return page, next_after, scan_truncated


@router.get("/admin/audit/records", response_model=AuditRecordPage)
def v1_admin_audit_records(
    surface: Surface,
    after: Sequence | None = None,
    before: Sequence | None = None,
    limit: PageLimit = 100,
    event_type: FilterValue = None,
    decision: FilterValue = None,
    actor_subject: FilterValue = None,
    action: FilterValue = None,
    resource_id: FilterValue = None,
    search: SearchValue = None,
) -> AuditRecordPage:
    """Search one audit surface, with bounded scanning and result size."""
    with _audit_store() as store:
        records, next_after, scan_truncated = _query_records(
            store,
            surface,
            after=after,
            before=before,
            limit=limit,
            event_type=event_type,
            decision=decision,
            actor_subject=actor_subject,
            action=action,
            resource_id=resource_id,
            search=search,
        )
        return AuditRecordPage(
            surface=surface,
            items=[AuditRecord.model_validate(record.to_dict()) for record in records],
            next_after=next_after,
            scan_truncated=scan_truncated,
        )


@router.get("/admin/audit/verify", response_model=AuditChainVerification)
def v1_admin_audit_verify(surface: Surface) -> AuditChainVerification:
    """Verify every record in a surface's chain without returning its hashes."""
    expected_previous = GENESIS_HASH
    cursor: int | None = None
    total = 0
    first_invalid_sequence: int | None = None
    error_message: str | None = None
    with _audit_store() as store:
        while True:
            records = store.query(surface, after=cursor, limit=_QUERY_BATCH_SIZE)
            if not records:
                break
            for record in records:
                total += 1
                if first_invalid_sequence is None and record.sequence_number != total:
                    first_invalid_sequence = record.sequence_number
                    error_message = "Sequence gap."
                elif first_invalid_sequence is None and record.previous_hash != expected_previous:
                    first_invalid_sequence = record.sequence_number
                    error_message = "Previous hash mismatch."
                elif first_invalid_sequence is None and record.record_hash != compute_record_hash(
                    record.event, record.sequence_number, record.previous_hash
                ):
                    first_invalid_sequence = record.sequence_number
                    error_message = "Record hash mismatch."
                expected_previous = record.record_hash
                cursor = record.sequence_number
            if len(records) < _QUERY_BATCH_SIZE:
                break
        return AuditChainVerification(
            surface=surface,
            valid=first_invalid_sequence is None,
            total_records=total,
            first_invalid_sequence=first_invalid_sequence,
            error_message=error_message,
        )


@router.get(
    "/admin/audit/export",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {"schema": {"type": "string"}}}}},
)
def v1_admin_audit_export(
    surface: Surface,
    after: Sequence | None = None,
    before: Sequence | None = None,
    limit: ExportLimit = 1000,
) -> StreamingResponse:
    """Export a bounded sequence range as newline-delimited JSON."""
    with _audit_store() as store:
        records = store.query(surface, after=after, before=before, limit=limit)
        body = "".join(
            f"{json.dumps(record.to_dict(), sort_keys=True, separators=(',', ':'))}\n"
            for record in records
        )
    return StreamingResponse(
        iter([body]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="audit-{surface}.jsonl"'},
    )
