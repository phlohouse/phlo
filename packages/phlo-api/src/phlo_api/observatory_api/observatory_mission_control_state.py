"""Durable state and seed data for the Mission Control read models.

Reads are pure: they never create ``.phlo`` directories, import legacy files or
write records. Legacy import happens once during explicit startup
initialization (``initialize_collections``). In ``live`` data mode an absent
collection is reported absent — seeds are only served in ``demo`` mode.

Seeds mirror ``web/src/data/demo.ts`` so the UI renders identically whether it is
reading fixtures or the API in demo mode.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel

from phlo.logging import get_logger
from phlo_api.observatory_api.observatory_durable_state import (
    mutate_collection,
    read_collection,
)
from phlo_api.observatory_api.observatory_mission_control_models import (
    ReadEnvelope,
    ReasonCode,
)
from phlo_api.observatory_api.observatory_mission_control_mode import (
    SourceOutcome,
    data_mode,
    demo_envelope,
    envelope,
    evidence,
    live_envelope,
    outcome_data,
    project_root,
)

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _legacy_path(collection: str) -> Path:
    return project_root() / ".phlo" / "observatory" / f"mission_control_{collection}.json"


def legacy_paths() -> dict[str, Path]:
    """Map every legacy-backed collection to its file for startup init.

    Seed collections import from ``mission_control_<name>.json``; the
    operation journal and saved queries keep their historic filenames.
    """
    paths = {collection: _legacy_path(collection) for collection in SEED}
    state_dir = project_root() / ".phlo" / "observatory"
    paths["operation_journal"] = state_dir / "operation_journal.json"
    paths["saved_queries"] = state_dir / "saved_queries.json"
    return paths


@dataclass(slots=True)
class CollectionResult(Generic[ModelT]):
    """Validated records plus the provenance a route needs for read evidence."""

    records: list[ModelT]
    dropped: int
    present: bool
    source: str


def load_records(collection: str, model: type[ModelT]) -> CollectionResult[ModelT]:
    """Load a collection as validated models.

    Live mode reads only the durable store: an absent collection comes back
    ``present=False`` so the route can report it, and corrupt state propagates
    as ``StorageCorruptionError``/``StorageUnavailableError`` instead of being
    silently emptied. Demo mode serves the reference seed when no durable
    record exists. Malformed stored records are skipped and counted so the
    response can be marked partial.
    """
    raw = _read_state(collection)
    if raw is None:
        if data_mode() == "demo":
            return _validate(list(SEED.get(collection, [])), model, present=True, source="seed")
        return CollectionResult(records=[], dropped=0, present=False, source="durable-state")
    return _validate(raw, model, present=True, source="durable-state")


def _validate(
    raw: list[Any], model: type[ModelT], *, present: bool, source: str
) -> CollectionResult[ModelT]:
    records: list[ModelT] = []
    dropped = 0
    for item in raw:
        if not isinstance(item, Mapping):
            dropped += 1
            continue
        try:
            records.append(model.model_validate(dict(item)))
        except Exception:
            dropped += 1
    return CollectionResult(records=records, dropped=dropped, present=present, source=source)


def _read_state(collection: str) -> list[dict[str, Any]] | None:
    """Read a collection from durable state. Absent returns None; failures raise."""
    return read_collection(project_root(), collection)


def replace_records(collection: str, items: Iterable[Mapping[str, Any]]) -> None:
    """Replace a collection under the durable store's transaction."""
    payload = [dict(item) for item in items]

    def _mutation(_existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return payload

    mutate_collection(project_root(), collection, _legacy_path(collection), _mutation)


def find_record(collection: str, model: type[ModelT], key: str) -> ModelT | None:
    """Return the record whose ``id`` matches, or None."""
    return find_by(collection, model, id=key)


def find_by(collection: str, model: type[ModelT], **equals: Any) -> ModelT | None:
    """Return the first record whose fields equal every given value."""
    for record in load_records(collection, model).records:
        if all(getattr(record, field, None) == value for field, value in equals.items()):
            return record
    return None


# ------------------------------------------------------------- route helpers
#
# Each mission endpoint resolves through one of these so the mode split and
# error mapping live in exactly one place: demo mode serves the stored/seed
# fixture labelled demo; live mode serves the typed outcome or the durable
# collection with its provenance — and never fabricates.


def _load_for_route(collection: str, model: type[ModelT]) -> CollectionResult[ModelT]:
    """Load a collection, mapping storage failure to a sanitized 503."""
    try:
        return load_records(collection, model)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="Observatory durable state is unavailable"
        ) from exc


def _collection_envelope(
    collection: str, result: CollectionResult[ModelT]
) -> ReadEnvelope[list[ModelT]]:
    """Wrap a loaded collection with its provenance evidence.

    Demo mode labels the payload demo regardless of where it was stored. Live
    mode reports the durable source: an absent collection answers ``[]`` with
    ``reason_code=absent`` so "nothing recorded" stays distinct from "recorded
    empty", and dropped malformed records mark the payload partial.
    """
    if data_mode() == "demo":
        return demo_envelope(result.records, source=result.source)
    if result.dropped:
        logger.warning(
            "mission_collection_partial",
            collection=collection,
            dropped=result.dropped,
        )
    reason_code: ReasonCode | None
    if result.dropped:
        reason_code = "partial"
    elif not result.present:
        reason_code = "absent"
    else:
        reason_code = None
    return envelope(
        result.records,
        evidence(
            "live",
            source=result.source,
            reason_code=reason_code,
            detail=None if result.present else "No records recorded for this project",
            dropped_records=result.dropped,
        ),
    )


def serve_collection(collection: str, model: type[ModelT]) -> ReadEnvelope[list[ModelT]]:
    """Return a collection's records with read evidence for the current mode.

    Live mode returns the durable store's contents; an absent collection is a
    truthful empty list, never seeded. Demo mode serves the reference seed when
    nothing is stored. Malformed stored records are dropped and marked partial
    so a partially corrupt collection still answers with what validated.
    Storage failures surface as HTTP 503 rather than an empty list.
    """
    return _collection_envelope(collection, _load_for_route(collection, model))


def serve_record(
    collection: str, model: type[ModelT], detail: str, **equals: Any
) -> ReadEnvelope[ModelT]:
    """Return the first matching record; 404 when absent, 503 on storage failure."""
    result = _load_for_route(collection, model)
    record = next(
        (
            record
            for record in result.records
            if all(getattr(record, field, None) == value for field, value in equals.items())
        ),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=detail)
    if data_mode() == "demo":
        return demo_envelope(record, source=result.source)
    return envelope(
        record,
        evidence(
            "live",
            source=result.source,
            reason_code="partial" if result.dropped else None,
            dropped_records=result.dropped,
        ),
    )


def serve_sourced(
    derive: Callable[[], SourceOutcome[list[ModelT]]],
    collection: str,
    model: type[ModelT],
) -> ReadEnvelope[list[ModelT]]:
    """Demo mode serves the stored/seed fixture; live mode serves the outcome."""
    if data_mode() == "demo":
        return serve_collection(collection, model)
    outcome = derive()
    data = outcome_data(outcome)
    return live_envelope(
        data,
        source=outcome.source or "provider",
        stale=outcome.stale,
        last_confirmed_at=outcome.last_confirmed_at,
    )


def serve_sourced_record(
    derive: Callable[[], SourceOutcome[ModelT]],
    collection: str,
    model: type[ModelT],
    detail: str,
    **equals: Any,
) -> ReadEnvelope[ModelT]:
    """Demo mode serves a stored/seed record; live mode serves the outcome."""
    if data_mode() == "demo":
        return serve_record(collection, model, detail, **equals)
    outcome = derive()
    data = outcome_data(outcome)
    return live_envelope(
        data,
        source=outcome.source or "provider",
        stale=outcome.stale,
        last_confirmed_at=outcome.last_confirmed_at,
    )


# ------------------------------------------------------------------ seeds
#
# Keys are collection names. Values are the demo fixture records served only
# when PHLO_OBSERVATORY_DATA_MODE=demo and durable state holds nothing for the
# collection; they are never written into durable state by a read. Live mode
# never sees them.

SEED: dict[str, list[dict[str, Any]]] = {
    "environments": [
        {"id": "production", "name": "Production", "state": "ready"},
        {"id": "staging", "name": "Staging", "state": "degraded"},
        {"id": "development", "name": "Development", "state": "idle"},
    ],
    "alerts": [
        {
            "id": "alert-release-blocked",
            "severity": "danger",
            "title": "Release blocked",
            "detail": "rel-0193 held at the evidence gate — Pandera uniqueness",
            "action": "Open release →",
            "raised_at": "09:31",
        },
        {
            "id": "alert-evidence-degraded",
            "severity": "danger",
            "title": "Evidence degraded",
            "detail": "Polaris unreachable — figures shown as last confirmed 09:21",
            "action": "Inspect provider →",
            "raised_at": "09:22",
        },
        {
            "id": "alert-run-failed",
            "severity": "danger",
            "title": "Run failed",
            "detail": "orders_daily r7e42b — uniqueness check on order_id",
            "action": "Open run →",
            "raised_at": "09:14",
        },
        {
            "id": "alert-unknown-outcome",
            "severity": "accent",
            "title": "Unknown outcome",
            "detail": "op-7c41 applied but unconfirmed — reconcile, don't retry",
            "action": "Reconcile →",
            "raised_at": "08:47",
        },
        {
            "id": "alert-digest",
            "severity": "muted",
            "title": "Digest delivered",
            "detail": "Nightly summary sent to #phlo-ops — recorded in Audit",
            "action": None,
            "raised_at": "08:02",
        },
    ],
    "attention": [
        {
            "id": "attn-orders",
            "severity": "danger",
            "title": "Orders delivery blocked",
            "detail": "42 duplicate IDs · Revenue dashboard + 2 consumers · 8m",
            "action": "Inspect run",
            "target": "/runs/orders-daily",
        },
        {
            "id": "attn-customers",
            "severity": "warning",
            "title": "Customer profiles are 35m late",
            "detail": "09:00 delivery missed · CRM sync · Data platform",
            "action": "View dataset",
            "target": "/datasets/orders",
        },
        {
            "id": "attn-inventory",
            "severity": "muted",
            "title": "Inventory release lacks evidence",
            "detail": "Write succeeded · Validation missing · Released data unchanged",
            "action": "Review",
            "target": "/releases",
        },
    ],
    "execution": [
        {
            "id": "exec-orders",
            "workflow": "orders_incremental",
            "stage": "Ingest",
            "progress": "1.2m rows staged",
            "elapsed": "04:12",
            "run_id": "r7e42b",
        },
        {
            "id": "exec-sales",
            "workflow": "sales_marts",
            "stage": "Transform",
            "progress": "18 / 24 models complete",
            "elapsed": "02:48",
            "run_id": "r7e42b",
        },
        {
            "id": "exec-inventory",
            "workflow": "inventory_stream",
            "stage": "Ingest",
            "progress": "558 events · checkpoint open",
            "elapsed": "00:36",
            "run_id": "r7e42b",
        },
        {
            "id": "exec-events",
            "workflow": "events_backfill",
            "stage": "Backfill",
            "progress": "42 / 60 partitions complete",
            "elapsed": "38:05",
            "run_id": "r7e42b",
        },
    ],
    "run_stages": [
        {
            "id": "s1",
            "run_id": "r7e42b",
            "name": "Ingest orders",
            "provider": "dlt",
            "outcome": "Succeeded",
            "offset_percent": 0,
            "width_percent": 25,
            "duration": "34s",
            "note": None,
            "flagged": False,
        },
        {
            "id": "s2",
            "run_id": "r7e42b",
            "name": "Build orders mart",
            "provider": "dbt",
            "outcome": "Succeeded",
            "offset_percent": 25,
            "width_percent": 54,
            "duration": "1m 12s",
            "note": None,
            "flagged": False,
        },
        {
            "id": "s3",
            "run_id": "r7e42b",
            "name": "Validate candidate",
            "provider": "Pandera",
            "outcome": "Failed",
            "offset_percent": 79,
            "width_percent": 21,
            "duration": "28s",
            "note": None,
            "flagged": True,
        },
        {
            "id": "s4",
            "run_id": "r7e42b",
            "name": "Promote branch",
            "provider": "Nessie",
            "outcome": "Blocked",
            "offset_percent": 0,
            "width_percent": 0,
            "duration": "—",
            "note": "No provider mutation",
            "flagged": False,
        },
        {
            "id": "s5",
            "run_id": "r7e42b",
            "name": "Deliver to target",
            "provider": "Postgres",
            "outcome": "Not started",
            "offset_percent": 0,
            "width_percent": 0,
            "duration": "—",
            "note": "No provider mutation",
            "flagged": False,
        },
    ],
    "run_events": [
        {
            "id": "e1",
            "run_id": "r7e42b",
            "at": "09:26:46",
            "level": "INFO",
            "message": "Candidate snapshot 938106 created on wap/r7e42b.",
        },
        {
            "id": "e2",
            "run_id": "r7e42b",
            "at": "09:27:14",
            "level": "ERROR",
            "message": "unique_order_id failed: 42 rows. Release withheld.",
        },
        {
            "id": "e3",
            "run_id": "r7e42b",
            "at": "09:27:14",
            "level": "INFO",
            "message": "Evidence complete. Released snapshot 938105 unchanged.",
        },
    ],
    "run_spans": [
        {
            "id": "sp1",
            "run_id": "r7e42b",
            "name": "run.orders_daily",
            "duration": "134,000ms",
            "width_percent": 100,
            "tone": "danger",
        },
        {
            "id": "sp2",
            "run_id": "r7e42b",
            "name": "stage.ingest_orders",
            "duration": "34,000ms",
            "width_percent": 25,
            "tone": "success",
        },
        {
            "id": "sp3",
            "run_id": "r7e42b",
            "name": "stage.build_mart",
            "duration": "72,000ms",
            "width_percent": 54,
            "tone": "success",
        },
        {
            "id": "sp4",
            "run_id": "r7e42b",
            "name": "stage.validate",
            "duration": "28,000ms",
            "width_percent": 21,
            "tone": "danger",
        },
    ],
    "run_artifacts": [
        {
            "id": "a1",
            "run_id": "r7e42b",
            "name": "quality-results.json",
            "size": "4 KB",
            "checksum": "sha256:1c9f…",
        },
        {
            "id": "a2",
            "run_id": "r7e42b",
            "name": "failed-rows.parquet",
            "size": "18 KB",
            "checksum": "sha256:7ab2…",
        },
        {
            "id": "a3",
            "run_id": "r7e42b",
            "name": "dbt-run-results.json",
            "size": "96 KB",
            "checksum": "sha256:e310…",
        },
    ],
    "run_consumers": [
        {
            "id": "c1",
            "run_id": "r7e42b",
            "name": "Revenue dashboard",
            "role": "Superset · Analytics",
            "current_snapshot": "938105",
        },
        {
            "id": "c2",
            "run_id": "r7e42b",
            "name": "Orders API",
            "role": "PostgREST · Commerce",
            "current_snapshot": "938105",
        },
        {
            "id": "c3",
            "run_id": "r7e42b",
            "name": "Finance reconciliation",
            "role": "dbt · Finance",
            "current_snapshot": "938105",
        },
    ],
    "run_config": [
        {
            "id": "cfg1",
            "run_id": "r7e42b",
            "label": "Schedule",
            "value": "orders_hourly · 25 * * * *",
        },
        {"id": "cfg2", "run_id": "r7e42b", "label": "Timeout", "value": "30m per stage"},
        {
            "id": "cfg3",
            "run_id": "r7e42b",
            "label": "Retries",
            "value": "0 · manual review required",
        },
        {"id": "cfg4", "run_id": "r7e42b", "label": "Quality gate", "value": "Pandera · blocking"},
        {
            "id": "cfg5",
            "run_id": "r7e42b",
            "label": "Promotion",
            "value": "Nessie branch wap/{run_id}",
        },
    ],
    "run_quality": [
        {
            "id": "r7e42b",
            "run_id": "r7e42b",
            "check": "order_id must be unique",
            "verdict": "Blocking · Pandera",
            "detail": "42 of 1,204,000 candidate rows share an order_id. The candidate is retained for inspection.",
            "sample_total": 42,
            "sample": [
                {
                    "key": "ORD-10482",
                    "record_id": "rec_7a8e01",
                    "observed_at": "2026-09-13 09:14:08",
                    "occurrences": 2,
                },
                {
                    "key": "ORD-10482",
                    "record_id": "rec_7a8e02",
                    "observed_at": "2026-09-13 09:14:09",
                    "occurrences": 2,
                },
                {
                    "key": "ORD-10517",
                    "record_id": "rec_7a8f14",
                    "observed_at": "2026-09-13 09:18:42",
                    "occurrences": 2,
                },
            ],
        }
    ],
    "dataset_governance": [
        {
            "id": "orders",
            "dataset_id": "marts.orders",
            "ownership": {
                "owner": "Data platform",
                "domain": "Commerce",
                "freshness_target": "2 hours",
                "schedule": "Hourly",
                "classification": "Internal",
                "contract_version": "v3 · Approved",
                "retention": "7 years · cold after 1",
            },
            "access": [
                {"principal": "Analytics", "kind": "read", "scope": "Supabase · marts.orders"},
                {"principal": "Finance", "kind": "read", "scope": "Supabase · marts.orders"},
                {"principal": "Orders API", "kind": "service", "scope": "PostgREST · /orders"},
            ],
            "runs": [
                {
                    "run_id": "r7e42b",
                    "finished_at": "09:27",
                    "outcome": "Failed validation",
                    "release": "Not promoted",
                    "duration": "2m 14s",
                },
                {
                    "run_id": "r6b190",
                    "finished_at": "08:00",
                    "outcome": "Succeeded",
                    "release": "938105",
                    "duration": "1m 58s",
                },
                {
                    "run_id": "r5d871",
                    "finished_at": "07:00",
                    "outcome": "Succeeded",
                    "release": "938104",
                    "duration": "2m 03s",
                },
            ],
            "stale": False,
            "last_confirmed_at": "2026-09-13T09:35:00Z",
        }
    ],
    "data_products": [
        {
            "id": "dp-orders",
            "name": "Orders",
            "freshness": "Fresh",
            "quality": "1 failed",
            "released": "08:00",
            "consumers": 3,
            "target": "/datasets/marts.orders",
        },
        {
            "id": "dp-customers",
            "name": "Customer profiles",
            "freshness": "35m late",
            "quality": "Passed",
            "released": "08:00",
            "consumers": 2,
            "target": "/datasets/marts.orders",
        },
        {
            "id": "dp-inventory",
            "name": "Product inventory",
            "freshness": "Fresh",
            "quality": "Unknown",
            "released": "08:00",
            "consumers": 4,
            "target": "/datasets/marts.orders",
        },
        {
            "id": "dp-payments",
            "name": "Payments",
            "freshness": "Fresh",
            "quality": "Passed",
            "released": "09:20",
            "consumers": 2,
            "target": "/datasets/marts.orders",
        },
        {
            "id": "dp-sessions",
            "name": "Session events",
            "freshness": "Fresh",
            "quality": "3 warnings",
            "released": "09:15",
            "consumers": 1,
            "target": "/datasets/marts.orders",
        },
    ],
    "overview_rail": [
        {
            "id": "rail",
            "ready_count": 11,
            "total_services": 12,
            "services": [
                {"name": "Dagster", "role": "Orchestration", "state": "Ready"},
                {"name": "Nessie", "role": "Catalog", "state": "Ready"},
                {"name": "MinIO", "role": "Object storage", "state": "Ready"},
                {"name": "Trino", "role": "Query engine", "state": "Ready"},
                {"name": "Postgres", "role": "Serving & state", "state": "Ready"},
                {"name": "OTel collector", "role": "Telemetry", "state": "Delayed"},
            ],
            "release_queue": [
                {
                    "name": "inventory_daily",
                    "state": "Blocked",
                    "detail": "Missing validation · main unchanged",
                },
                {
                    "name": "product_catalog",
                    "state": "Ready",
                    "detail": "12 checks passed · awaiting promotion",
                },
            ],
            "governance": [
                {"label": "Dataset ownership", "value": "124 / 128 assigned", "tone": "warning"},
                {"label": "Access policies", "value": "In sync", "tone": "success"},
                {"label": "Publication reviews", "value": "2 awaiting review", "tone": "accent"},
            ],
            "recovery": [
                {"label": "Last backup", "value": "06:00 · Verified", "tone": "success"},
                {"label": "Restore rehearsal", "value": "3 days ago · Passed", "tone": "success"},
                {"label": "Table maintenance", "value": "2 optimizations due", "tone": "warning"},
            ],
        }
    ],
    "run_logs": [
        {
            "id": "l1",
            "run_id": "r7e42b",
            "at": "09:25:00",
            "level": "INFO",
            "message": "dlt pipeline orders_incremental start \u00b7 partition 2026-09-13",
        },
        {
            "id": "l2",
            "run_id": "r7e42b",
            "at": "09:25:34",
            "level": "INFO",
            "message": "ingest complete \u00b7 1,204,000 rows staged in 34s",
        },
        {
            "id": "l3",
            "run_id": "r7e42b",
            "at": "09:26:46",
            "level": "INFO",
            "message": "candidate snapshot 938106 created on wap/r7e42b",
        },
        {
            "id": "l4",
            "run_id": "r7e42b",
            "at": "09:27:14",
            "level": "ERROR",
            "message": "unique_order_id failed: 42 rows. Release withheld.",
        },
        {
            "id": "l5",
            "run_id": "r7e42b",
            "at": "09:27:14",
            "level": "INFO",
            "message": "evidence complete \u00b7 released snapshot 938105 unchanged",
        },
        {
            "id": "l6",
            "run_id": "r7e42b",
            "at": "09:27:15",
            "level": "INFO",
            "message": "evidence reconciled \u00b7 terminal run",
        },
    ],
    "run_detail": [
        {
            "id": "r7e42b",
            "workflow": "orders_daily",
            "run_id": "r7e42b",
            "status": "Failed validation",
            "summary": "Run r7e42b \u00b7 13 Sep 2026, 09:25 UTC \u00b7 Scheduled \u00b7 Partition 2026-09-13",
            "metrics": [
                {
                    "label": "Execution",
                    "value": "Failed",
                    "hint": "Blocking quality check",
                    "tone": "danger",
                },
                {
                    "label": "Evidence",
                    "value": "Complete",
                    "hint": "All required stages recorded",
                    "tone": "success",
                },
                {
                    "label": "Release",
                    "value": "Not promoted",
                    "hint": "Consumers remain on 08:00",
                    "tone": "warning",
                },
                {
                    "label": "Duration",
                    "value": "2m 14s",
                    "hint": "09:25:00 \u2013 09:27:14",
                    "tone": "muted",
                },
                {
                    "label": "Attempt",
                    "value": "1 of 1",
                    "hint": "Previous success at 08:00",
                    "tone": "muted",
                },
            ],
            "details": [
                {"run_id": "r7e42b", "label": "Asset", "value": "marts.orders"},
                {"run_id": "r7e42b", "label": "Orchestrator", "value": "Dagster"},
                {"run_id": "r7e42b", "label": "Trigger", "value": "Schedule \u00b7 orders_hourly"},
                {"run_id": "r7e42b", "label": "Partition", "value": "2026-09-13"},
                {"run_id": "r7e42b", "label": "Code version", "value": "a41c9f2"},
                {"run_id": "r7e42b", "label": "Candidate snapshot", "value": "938106"},
                {"run_id": "r7e42b", "label": "Released snapshot", "value": "938105"},
                {"run_id": "r7e42b", "label": "Candidate branch", "value": "wap/r7e42b"},
            ],
            "consumers": [
                {
                    "run_id": "r7e42b",
                    "name": "Revenue dashboard",
                    "role": "Superset \u00b7 Analytics",
                    "current_snapshot": "938105",
                },
                {
                    "run_id": "r7e42b",
                    "name": "Orders API",
                    "role": "PostgREST \u00b7 Commerce",
                    "current_snapshot": "938105",
                },
                {
                    "run_id": "r7e42b",
                    "name": "Finance reconciliation",
                    "role": "dbt \u00b7 Finance",
                    "current_snapshot": "938105",
                },
            ],
            "artifacts": [
                {
                    "run_id": "r7e42b",
                    "name": "quality-results.json",
                    "size": "4 KB",
                    "checksum": "sha256:1c9f\u2026",
                },
                {
                    "run_id": "r7e42b",
                    "name": "failed-rows.parquet",
                    "size": "18 KB",
                    "checksum": "sha256:7ab2\u2026",
                },
                {
                    "run_id": "r7e42b",
                    "name": "dbt-run-results.json",
                    "size": "96 KB",
                    "checksum": "sha256:e310\u2026",
                },
            ],
        }
    ],
    "dataset_detail": [
        {
            "id": "marts.orders",
            "name": "Orders",
            "status": "Published",
            "summary": "marts.orders \u00b7 Order-level revenue and fulfilment data for analytics and downstream APIs.",
            "metrics": [
                {
                    "label": "Freshness",
                    "value": "Fresh",
                    "hint": "95m old \u00b7 2h freshness target",
                    "tone": "success",
                },
                {
                    "label": "Last released",
                    "value": "08:00 UTC",
                    "hint": "13 Sep 2026 \u00b7 Run r6b190",
                    "tone": "muted",
                },
                {
                    "label": "Released snapshot",
                    "value": "938105",
                    "hint": "Iceberg \u00b7 Nessie main",
                    "tone": "muted",
                },
                {
                    "label": "Released rows",
                    "value": "1,187,320",
                    "hint": "24.8 MB \u00b7 12 data files",
                    "tone": "muted",
                },
                {
                    "label": "Released quality",
                    "value": "12 / 12 passed",
                    "hint": "Checks bound to snapshot 938105",
                    "tone": "success",
                },
            ],
            "schema_fields": [
                {
                    "field": "order_id",
                    "type": "string",
                    "nullable": "No",
                    "role": "Primary key \u00b7 Unique",
                },
                {
                    "field": "customer_id",
                    "type": "string",
                    "nullable": "No",
                    "role": "Customer reference",
                },
                {
                    "field": "created_at",
                    "type": "timestamp",
                    "nullable": "No",
                    "role": "Partition source",
                },
                {"field": "status", "type": "string", "nullable": "No", "role": "Accepted values"},
                {
                    "field": "order_total",
                    "type": "decimal(12,2)",
                    "nullable": "No",
                    "role": "Non-negative",
                },
                {"field": "currency", "type": "string", "nullable": "No", "role": "ISO 4217"},
                {"field": "line_items", "type": "json", "nullable": "Yes", "role": "Nested array"},
                {
                    "field": "updated_at",
                    "type": "timestamp",
                    "nullable": "Yes",
                    "role": "CDC watermark",
                },
            ],
            "preview": {
                "columns": ["order_id", "customer_id", "created_at", "status", "order_total"],
                "rows": [
                    ["ORD-20913", "C-88142", "2026-09-13 08:59:01", "shipped", "412.90"],
                    ["ORD-20914", "C-10293", "2026-09-13 08:59:02", "pending", "89.99"],
                    ["ORD-20915", "C-55310", "2026-09-13 08:59:02", "shipped", "1204.00"],
                    ["ORD-20916", "C-88142", "2026-09-13 08:59:03", "cancelled", "45.50"],
                    ["ORD-20917", "C-29401", "2026-09-13 08:59:04", "shipped", "231.75"],
                    ["ORD-20918", "C-77120", "2026-09-13 08:59:05", "pending", "99.00"],
                ],
            },
            "checks": [
                {
                    "name": "order_id must be unique",
                    "outcome": "Failed \u00b7 blocking",
                    "tone": "danger",
                },
                {"name": "order_total non-negative", "outcome": "Passed", "tone": "success"},
                {"name": "currency in ISO 4217", "outcome": "Passed", "tone": "success"},
                {"name": "status in accepted set", "outcome": "Passed", "tone": "success"},
                {"name": "created_at not None", "outcome": "Passed", "tone": "success"},
                {
                    "name": "customer_id references dim_customer",
                    "outcome": "Warning",
                    "tone": "warning",
                },
            ],
            "lineage": [
                {"name": "postgres.orders", "role": "Postgres \u00b7 Source", "current": False},
                {"name": "stg_orders", "role": "dbt \u00b7 Model", "current": False},
                {"name": "marts.orders", "role": "This dataset", "current": True},
                {
                    "name": "Revenue dashboard",
                    "role": "Superset \u00b7 Analytics",
                    "current": False,
                },
                {"name": "Orders API", "role": "PostgREST \u00b7 Commerce", "current": False},
                {"name": "Finance reconciliation", "role": "dbt \u00b7 Finance", "current": False},
            ],
            "ownership": {
                "owner": "Data platform",
                "domain": "Commerce",
                "freshness_target": "2 hours",
                "schedule": "Hourly",
                "classification": "Internal",
                "contract_version": "v3 \u00b7 Approved",
                "retention": "7 years \u00b7 cold after 1",
            },
            "access": [
                {"principal": "Analytics", "kind": "read", "scope": "Supabase \u00b7 marts.orders"},
                {"principal": "Finance", "kind": "read", "scope": "Supabase \u00b7 marts.orders"},
                {"principal": "Orders API", "kind": "service", "scope": "PostgREST \u00b7 /orders"},
            ],
            "runs": [
                {
                    "run_id": "r7e42b",
                    "finished_at": "09:27",
                    "outcome": "Failed validation",
                    "release": "Not promoted",
                    "duration": "2m 14s",
                },
                {
                    "run_id": "r6b190",
                    "finished_at": "08:00",
                    "outcome": "Succeeded",
                    "release": "938105",
                    "duration": "1m 58s",
                },
                {
                    "run_id": "r5d871",
                    "finished_at": "07:00",
                    "outcome": "Succeeded",
                    "release": "938104",
                    "duration": "2m 03s",
                },
            ],
        }
    ],
    "governance_summary": [
        {
            "id": "gov-ownership",
            "label": "Ownership",
            "value": "124 / 128",
            "hint": "4 datasets need an owner",
            "tone": "muted",
        },
        {
            "id": "gov-classification",
            "label": "Classification",
            "value": "126 / 128",
            "hint": "2 datasets unclassified",
            "tone": "muted",
        },
        {
            "id": "gov-contracts",
            "label": "Contract coverage",
            "value": "120 / 128",
            "hint": "8 incomplete contracts",
            "tone": "muted",
        },
        {
            "id": "gov-reviews",
            "label": "Publication reviews",
            "value": "3 pending",
            "hint": "1 ready · 2 blocked",
            "tone": "muted",
        },
        {
            "id": "gov-drift",
            "label": "Access verification",
            "value": "1 drift detected",
            "hint": "7 of 8 targets match policy",
            "tone": "danger",
        },
    ],
    "publication_plan": [
        {
            "id": "shipments",
            "dataset_id": "logistics.shipments",
            "subtitle": "Review the internal Dataset publication transition.",
            "status": "Ready",
            "rows": [
                {"label": "Dataset", "value": "logistics.shipments"},
                {"label": "Owner", "value": "Logistics"},
                {"label": "Classification", "value": "Internal"},
                {"label": "Contract", "value": "v2 \u00b7 Complete"},
                {"label": "Policy verdict", "value": "Requirements satisfied"},
                {"label": "Transition", "value": "Draft \u2192 Published"},
                {"label": "Expected state version", "value": "7"},
            ],
        }
    ],
    "settings_summary": [
        {
            "id": "ss-providers",
            "label": "Provider connections",
            "value": "2 of 3",
            "hint": "Polaris unreachable · evidence stale",
            "tone": "muted",
        },
        {
            "id": "ss-unreachable",
            "label": "Unreachable",
            "value": "1",
            "hint": "Polaris · no response since 09:21",
            "tone": "danger",
        },
        {
            "id": "ss-notify",
            "label": "Notification rules",
            "value": "5",
            "hint": "3 channels configured",
            "tone": "warning",
        },
        {
            "id": "ss-members",
            "label": "Members",
            "value": "14",
            "hint": "4 operators · 3 reviewers",
            "tone": "muted",
        },
        {
            "id": "ss-pending",
            "label": "Pending changes",
            "value": "None",
            "hint": "All settings applied",
            "tone": "muted",
        },
        {
            "id": "ss-env",
            "label": "Environment",
            "value": "Production",
            "hint": "Freshness default · 2h",
            "tone": "success",
        },
    ],
    "overview_summary": [
        {
            "id": "os-data",
            "label": "Data",
            "value": "128 datasets",
            "hint": "121 fresh · 4 late · 3 unknown",
            "tone": "muted",
        },
        {
            "id": "os-ingestion",
            "label": "Ingestion",
            "value": "18 sources",
            "hint": "16 current · 1 delayed · 1 idle",
            "tone": "muted",
        },
        {
            "id": "os-quality",
            "label": "Quality",
            "value": "612 checks",
            "hint": "608 passed · 3 warnings · 1 failed",
            "tone": "muted",
        },
        {
            "id": "os-execution",
            "label": "Execution",
            "value": "156 runs",
            "hint": "149 succeeded · 4 active · 3 failed",
            "tone": "muted",
        },
        {
            "id": "os-releases",
            "label": "Releases",
            "value": "2 pending",
            "hint": "1 ready · 1 evidence blocked",
            "tone": "muted",
        },
        {
            "id": "os-governance",
            "label": "Governance",
            "value": "124 owned",
            "hint": "4 unassigned · 2 reviews due",
            "tone": "muted",
        },
    ],
    "platform_services": [
        {
            "id": "loki",
            "name": "Loki",
            "role": "Log storage",
            "runtime_state": "Running",
            "readiness_state": "Not ready",
            "probe": "Timeout \u00b7 5s",
            "action": "Selected",
            "attention": True,
        },
        {
            "id": "dagster",
            "name": "Dagster",
            "role": "Orchestration",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "postgres",
            "name": "Postgres",
            "role": "Serving / state",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "Connection OK",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "minio",
            "name": "MinIO",
            "role": "Object storage",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "nessie",
            "name": "Nessie",
            "role": "Branch catalog",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "polaris",
            "name": "Polaris",
            "role": "REST catalog",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "trino",
            "name": "Trino",
            "role": "Query engine",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "superset",
            "name": "Superset",
            "role": "BI",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "postgrest",
            "name": "PostgREST",
            "role": "Data API",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "traefik",
            "name": "Traefik",
            "role": "Routing",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "oauth2-proxy",
            "name": "OAuth2 Proxy",
            "role": "Authentication",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
        {
            "id": "alloy",
            "name": "Alloy",
            "role": "Telemetry collector",
            "runtime_state": "Running",
            "readiness_state": "Ready",
            "probe": "HTTP 200",
            "action": "Open",
            "attention": False,
        },
    ],
    "platform_diagnostics": [
        {
            "id": "loki",
            "name": "Loki",
            "readiness_state": "Not ready",
            "summary": "Container is running. /ready timed out after 5 seconds; log queries also failed.",
            "facts": [
                {"label": "First failure", "value": "09:28 UTC"},
                {"label": "Last successful probe", "value": "09:27 UTC"},
                {"label": "Runtime source", "value": "Container engine"},
                {"label": "Readiness source", "value": "HTTP /ready"},
            ],
            "dependencies": [
                {
                    "source": "Alloy",
                    "target": "Loki",
                    "outcome": "Delivery unconfirmed",
                    "tone": "warning",
                },
                {
                    "source": "Loki",
                    "target": "Log queries",
                    "outcome": "Unavailable",
                    "tone": "danger",
                },
                {
                    "source": "Postgres",
                    "target": "Run evidence",
                    "outcome": "Available",
                    "tone": "success",
                },
            ],
            "dependency_note": "Collector readiness does not confirm log delivery. Check exporter retries and backend ingestion.",
            "capability_checks": "Compatibility checks: 16 / 16 passed",
            "capabilities": [
                {
                    "name": "Support channel",
                    "detail": "Declared support tier",
                    "outcome": "Alpha",
                    "tone": "warning",
                },
                {
                    "name": "Production readiness",
                    "detail": "Certification state",
                    "outcome": "Not certified",
                    "tone": "warning",
                },
            ],
            "stale": True,
            "last_confirmed_at": "2026-09-13T09:27:00Z",
        }
    ],
    "backup_coverage": [
        {
            "id": "b1",
            "name": "Postgres \u00b7 State & serving",
            "outcome": "Complete",
            "tone": "success",
        },
        {"id": "b2", "name": "MinIO \u00b7 Object data", "outcome": "Complete", "tone": "success"},
        {
            "id": "b3",
            "name": "Nessie \u00b7 Catalog state",
            "outcome": "Complete",
            "tone": "success",
        },
        {
            "id": "b4",
            "name": "Polaris \u00b7 Catalog state",
            "outcome": "Complete",
            "tone": "success",
        },
    ],
    "maintenance": [
        {"id": "m1", "name": "Table compaction", "outcome": "6 / 6 completed", "tone": "muted"},
        {
            "id": "m2",
            "name": "Snapshot expiration",
            "outcome": "Preview \u00b7 02:00 tomorrow",
            "tone": "muted",
        },
        {
            "id": "m3",
            "name": "Retention safeguard",
            "outcome": "7 days \u00b7 Pinned refs kept",
            "tone": "muted",
        },
        {
            "id": "m4",
            "name": "Restore rehearsal",
            "outcome": "11 Sep \u00b7 Verified",
            "tone": "muted",
        },
    ],
    "release_candidates": [
        {
            "id": "rel-c204",
            "dataset": "Customers",
            "provider": "Polaris",
            "strategy": "Snapshots",
            "readiness": "Ready for review",
            "evidence": "Complete \u00b7 14/14 passed",
            "created_at": "09:31",
            "action": "Selected",
        },
        {
            "id": "rel-o193",
            "dataset": "Orders",
            "provider": "Nessie",
            "strategy": "Branch merge",
            "readiness": "Blocked",
            "evidence": "Complete \u00b7 11/12 passed",
            "created_at": "09:27",
            "action": "Inspect",
        },
    ],
    "release_candidate_detail": [
        {
            "id": "rel-c204",
            "dataset_id": "Customers",
            "subtitle": "Polaris snapshot publication \u00b7 Run r8c291 \u00b7 Data platform",
            "status": "Ready for review",
            "revision": "Release revision 42 \u2192 proposed 43",
            "snapshot_changes": [
                {
                    "table": "crm.customers",
                    "released_snapshot": "720114",
                    "candidate_snapshot": "720128",
                    "row_delta": "+1,204",
                },
                {
                    "table": "crm.customer_segments",
                    "released_snapshot": "881020",
                    "candidate_snapshot": "881031",
                    "row_delta": "+312",
                },
            ],
            "required_evidence": [
                {
                    "name": "Quality checks",
                    "detail": "14 passed \u00b7 0 blocking failures",
                    "outcome": "Passed",
                    "tone": "success",
                },
                {
                    "name": "Run evidence",
                    "detail": "All required stages and artifacts recorded",
                    "outcome": "Complete",
                    "tone": "success",
                },
                {
                    "name": "Snapshot audit",
                    "detail": "Both candidate snapshots match audit",
                    "outcome": "Matched",
                    "tone": "success",
                },
                {
                    "name": "Release revision",
                    "detail": "Expected 42 \u00b7 Observed 42",
                    "outcome": "Current",
                    "tone": "success",
                },
            ],
            "publication_plan": [
                {"label": "Operation", "value": "Publish audited snapshots"},
                {"label": "Catalog", "value": "Polaris \u00b7 analytics"},
                {"label": "Expected revision", "value": "42"},
                {"label": "Intent", "value": "Not submitted"},
            ],
        }
    ],
    "completed_releases": [
        {
            "id": "rel-s188",
            "dataset": "Sessions",
            "provider": "Nessie",
            "provider_strategy": "Nessie \u00b7 Branch merge",
            "reference": "main \u00b7 6fb812a",
            "finished_at": "09:12 UTC",
            "outcome": "Merge confirmed",
        },
        {
            "id": "rel-o187",
            "dataset": "Orders",
            "provider": "Nessie",
            "provider_strategy": "Nessie \u00b7 Branch merge",
            "reference": "main \u00b7 4a70d92 \u00b7 Snapshot 938105",
            "finished_at": "08:00 UTC",
            "outcome": "Merge confirmed",
        },
    ],
    "publication_reviews": [
        {
            "id": "pr-shipments",
            "dataset": "Shipments",
            "owner": "Logistics",
            "contract": "v2 \u00b7 Complete",
            "verdict": "Ready",
            "reason": "All publication requirements met",
            "action": "Selected",
            "selected": True,
        },
        {
            "id": "pr-contacts",
            "dataset": "Customer contacts",
            "owner": "Unassigned",
            "contract": "v1 \u00b7 Complete",
            "verdict": "Blocked",
            "reason": "Accountable owner missing",
            "action": "Inspect",
            "selected": False,
        },
        {
            "id": "pr-forecast",
            "dataset": "Revenue forecast",
            "owner": "Finance",
            "contract": "v3 \u00b7 Incomplete",
            "verdict": "Blocked",
            "reason": "Freshness expectation missing",
            "action": "Inspect",
            "selected": False,
        },
    ],
    "ownership_gaps": [
        {
            "id": "og1",
            "dataset": "Pageviews",
            "requirement": "Accountable owner",
            "owner": "Unassigned",
            "action": "Assign owner",
        },
        {
            "id": "og2",
            "dataset": "Support tickets",
            "requirement": "Classification",
            "owner": "Support",
            "action": "Review classification",
        },
        {
            "id": "og3",
            "dataset": "Inventory balances",
            "requirement": "Freshness expectation",
            "owner": "Operations",
            "action": "Review contract",
        },
    ],
    "access_drift": [
        {
            "id": "orders",
            "dataset": "Orders",
            "verdict": "Unexpected UPDATE grant",
            "subtitle": "Postgres \u00b7 marts.orders \u00b7 Role finance_reader \u00b7 Verified at 09:32 UTC",
            "evidence": [
                {
                    "evidence": "Declared policy \u00b7 v12",
                    "permissions": "SELECT",
                    "result": "Read only",
                    "drifted": False,
                },
                {
                    "evidence": "Compiled grants \u00b7 v12",
                    "permissions": "SELECT",
                    "result": "Matches policy",
                    "drifted": False,
                },
                {
                    "evidence": "Verified backend grants",
                    "permissions": "SELECT, UPDATE",
                    "result": "Drift detected",
                    "drifted": True,
                },
            ],
        }
    ],
    "audit_events": [
        {
            "id": "ae1",
            "at": "09:32",
            "actor": "Policy verifier",
            "action": "Verify backend grants",
            "target": "Orders \u00b7 Postgres",
            "outcome": "Drift recorded",
            "tone": "warning",
        },
        {
            "id": "ae2",
            "at": "09:21",
            "actor": "data.steward",
            "action": "Update contract \u00b7 v1 \u2192 v2",
            "target": "Shipments",
            "outcome": "Applied \u00b7 Version 7",
            "tone": "success",
        },
        {
            "id": "ae3",
            "at": "08:45",
            "actor": "data.steward",
            "action": "Publish Dataset",
            "target": "Orders",
            "outcome": "Published \u00b7 Version 12",
            "tone": "success",
        },
    ],
    "provider_connections": [
        {
            "id": "polaris",
            "name": "Polaris",
            "role": "REST catalog \u00b7 owns release evidence",
            "endpoint": "polaris.internal/api",
            "state": "Unreachable \u00b7 timing out",
            "detail": "Last confirmed response 09:21 \u00b7 credential resolved",
            "action": "Retry check",
            "degraded": True,
            "credential_configured": True,
        },
        {
            "id": "nessie",
            "name": "Nessie",
            "role": "Versioned catalog \u00b7 owns commit history",
            "endpoint": "nessie.internal:19120",
            "state": "Connected \u00b7 42ms",
            "detail": "Last check 09:35 \u00b7 credential resolved",
            "action": "Test connection",
            "degraded": False,
            "credential_configured": True,
        },
        {
            "id": "object-storage",
            "name": "Object storage",
            "role": "S3-compatible \u00b7 holds table files",
            "endpoint": "s3://phlo-lake-prod",
            "state": "Connected \u00b7 18ms",
            "detail": "Last check 09:35 \u00b7 credential resolved",
            "action": "Test connection",
            "degraded": False,
            "credential_configured": True,
        },
    ],
    "provider_impact": [
        {
            "id": "polaris",
            "provider": "Polaris",
            "degraded": [
                "Release evidence and promotion status \u2014 shown as last confirmed",
                "Freshness on Polaris-served datasets \u2014 stamped, not live",
                "New promotions blocked \u2014 evidence cannot be re-verified",
            ],
            "unaffected": [
                "Released data \u2014 consumers keep reading snapshot 938105",
                "Execution \u2014 runs proceed; outcomes queue for reconcile",
                "Nessie-sourced history \u2014 commit trail stays live",
            ],
        }
    ],
    "notification_rules": [
        {"id": "nr1", "name": "Release blocked", "channel": "#phlo-ops", "urgency": "Immediate"},
        {"id": "nr2", "name": "Evidence degraded", "channel": "#phlo-ops", "urgency": "Immediate"},
        {"id": "nr3", "name": "Run failed", "channel": "#phlo-data", "urgency": "Immediate"},
        {"id": "nr4", "name": "Nightly digest", "channel": "Email", "urgency": "Daily 08:00"},
        {
            "id": "nr5",
            "name": "Maintenance window",
            "channel": "Email",
            "urgency": "Advance notice",
        },
    ],
    "workspace_members": [
        {"id": "wm1", "name": "Gareth Price", "role": "Workspace admin", "status": "active"},
        {"id": "wm2", "name": "data.steward", "role": "Operator", "status": "active"},
        {"id": "wm3", "name": "Policy verifier", "role": "Reviewer", "status": "active"},
        {"id": "wm4", "name": "Finance", "role": "Viewer", "status": "invited"},
    ],
    "workspace_defaults": [
        {"id": "wd1", "name": "Freshness target", "value": "2 hours"},
        {"id": "wd2", "name": "Classification default", "value": "Internal"},
        {"id": "wd3", "name": "Retention", "value": "7 years \u00b7 cold after 1"},
        {"id": "wd4", "name": "Evidence retention", "value": "30 days"},
        {"id": "wd5", "name": "Timezone", "value": "UTC"},
    ],
}
