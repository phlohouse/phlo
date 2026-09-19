"""Per-run and per-dataset evidence read models.

Live-mode run evidence is served from the durable run-evidence store,
scoped by (project, run, attempt) so every displayed id provably belongs to
the selected run. Events and logs are bounded pages; payloads are redacted
before they leave the store. Demo mode continues to serve the seed
collections. Unknown runs fail closed with 404.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from phlo_api.observatory_api.observatory_mission_control_models import (
    ReadEnvelope,
    DatasetGovernance,
    MissionDatasetDetail,
    MissionExecutionRow,
    MissionRunDetail,
    MissionRunList,
    MissionRunRow,
    MissionRunLogLine,
    RunArtifact,
    RunConfigurationRow,
    RunConsumer,
    RunEvent,
    RunEventPage,
    RunLogPage,
    RunQualityFailure,
    RunQualityReport,
    RunSpan,
    RunStageView,
)
from phlo_api.observatory_api.observatory_mission_control_sources import (
    derive_dataset,
    derive_run,
    derive_run_artifacts,
    derive_run_consumers,
    derive_run_configuration,
    derive_run_events,
    derive_run_logs,
    derive_run_quality,
    derive_run_spans,
    derive_run_stages,
    derive_runs_page,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    load_records,
    serve_collection,
    serve_record,
    serve_sourced_record,
)
from phlo_api.observatory_api.observatory_mission_control_mode import (
    SourceOutcome,
    data_mode,
    demo_envelope,
    live_envelope,
    outcome_data,
)
from phlo_api.run_evidence import RunEvidenceStore, get_run_evidence_store

router = APIRouter()


def _serve_run_section(
    derive: Callable[[], SourceOutcome],
    collection: str,
    model,
    run_id: str,
):
    """Demo mode filters the seed collection by run; live mode serves the
    store-scoped outcome."""
    if data_mode() == "demo":
        return _by_run(collection, model, run_id)
    outcome = derive()
    data = outcome_data(outcome)
    return live_envelope(
        data,
        source=outcome.source or "run_evidence",
        stale=outcome.stale,
        last_confirmed_at=outcome.last_confirmed_at,
    )


def _stored_runs_page(limit: int, cursor: str | None) -> tuple[list[MissionRunRow], str | None]:
    """Demo-mode run page over the seeded execution collection."""
    result = load_records("execution", MissionExecutionRow)
    try:
        offset = int(cursor) if cursor else 0
    except ValueError:
        offset = 0
    offset = max(0, offset)
    safe_limit = max(1, min(limit, 500))
    page = result.records[offset : offset + safe_limit]
    next_cursor = str(offset + safe_limit) if offset + safe_limit < len(result.records) else None
    return [
        MissionRunRow(
            id=row.run_id or row.id,
            name=row.workflow,
            status=row.stage,
            asset_ids=[],
        )
        for row in page
    ], next_cursor


def _by_run(collection: str, model, run_id: str):
    result = serve_collection(collection, model)
    return ReadEnvelope(
        data=[record for record in result.data or [] if record.run_id == run_id],
        evidence=result.evidence,
    )


@router.get("/mission/runs")
def list_mission_runs(limit: int = 50, cursor: str | None = None) -> ReadEnvelope[MissionRunList]:
    """Bounded run list — the same orchestrator population the overview
    execution counters are computed from, so drilldown and summary agree."""
    if data_mode() == "demo":
        rows, next_cursor = _stored_runs_page(limit=limit, cursor=cursor)
        return demo_envelope(MissionRunList(items=rows, next_cursor=next_cursor), source="seed")
    outcome = derive_runs_page(limit, cursor)
    data = outcome_data(outcome)
    return live_envelope(
        data,
        source=outcome.source or "orchestrator",
        stale=outcome.stale,
        last_confirmed_at=outcome.last_confirmed_at,
    )


@router.get("/mission/runs/{run_id:path}/stages")
def get_mission_run_stages(
    run_id: str,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[list[RunStageView]]:
    """Return the execution timeline for a run from its recorded stages."""
    return _serve_run_section(
        lambda: derive_run_stages(store, run_id), "run_stages", RunStageView, run_id
    )


@router.get("/mission/runs/{run_id:path}/quality")
def get_mission_run_quality(
    run_id: str,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[RunQualityReport]:
    """Return every quality outcome pinned to this run's attempt."""
    if data_mode() == "demo":
        known = load_records("run_detail", MissionRunDetail)
        if not any(record.run_id == run_id for record in known.records):
            raise HTTPException(status_code=404, detail=f"Unknown run {run_id}")
        failing = _by_run("run_quality", RunQualityFailure, run_id)
        failure = failing.data[0] if failing.data else None
        return ReadEnvelope(
            data=RunQualityReport(
                run_id=run_id,
                attempt=None,
                results=[],
                blocking_failure=failure,
            ),
            evidence=failing.evidence,
        )
    outcome = derive_run_quality(store, run_id)
    data = outcome_data(outcome)
    return live_envelope(
        data,
        source=outcome.source or "run_evidence",
        stale=outcome.stale,
        last_confirmed_at=outcome.last_confirmed_at,
    )


@router.get("/mission/runs/{run_id:path}/events")
def get_mission_run_events(
    run_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[RunEventPage]:
    """Return one bounded page of the run's recorded events."""
    if data_mode() == "demo":
        events = _by_run("run_events", RunEvent, run_id)
        return ReadEnvelope(
            data=RunEventPage(items=events.data or [], next_cursor=None),
            evidence=events.evidence,
        )
    outcome = derive_run_events(store, run_id, limit=limit, cursor=cursor)
    data = outcome_data(outcome)
    return live_envelope(
        data,
        source=outcome.source or "run_evidence",
        stale=outcome.stale,
        last_confirmed_at=outcome.last_confirmed_at,
    )


@router.get("/mission/runs/{run_id:path}/traces")
def get_mission_run_traces(
    run_id: str,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[list[RunSpan]]:
    """Return the trace span waterfall — spans recorded for this run only."""
    return _serve_run_section(lambda: derive_run_spans(store, run_id), "run_spans", RunSpan, run_id)


@router.get("/mission/runs/{run_id:path}/artifacts")
def get_mission_run_artifacts(
    run_id: str,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[list[RunArtifact]]:
    """Return recorded evidence artifacts for a run."""
    return _serve_run_section(
        lambda: derive_run_artifacts(store, run_id), "run_artifacts", RunArtifact, run_id
    )


@router.get("/mission/runs/{run_id:path}/consumers")
def get_mission_run_consumers(
    run_id: str,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[list[RunConsumer]]:
    """Return downstream readers recorded in this run's lineage edges."""
    return _serve_run_section(
        lambda: derive_run_consumers(store, run_id), "run_consumers", RunConsumer, run_id
    )


@router.get("/mission/runs/{run_id:path}/configuration")
def get_mission_run_configuration(
    run_id: str,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[list[RunConfigurationRow]]:
    """Return the resolved configuration recorded for a run."""
    return _serve_run_section(
        lambda: derive_run_configuration(store, run_id),
        "run_config",
        RunConfigurationRow,
        run_id,
    )


@router.get("/mission/datasets/{dataset_id:path}/governance")
def get_mission_dataset_governance(dataset_id: str) -> ReadEnvelope[DatasetGovernance]:
    """Return ownership, access grants and recent runs for a dataset."""
    return serve_record(
        "dataset_governance",
        DatasetGovernance,
        f"No governance record for {dataset_id}",
        dataset_id=dataset_id,
    )


@router.get("/mission/runs/{run_id:path}/logs")
def get_mission_run_logs(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[RunLogPage]:
    """Return one bounded page of retained log lines for a run."""
    if data_mode() == "demo":
        lines = _by_run("run_logs", MissionRunLogLine, run_id)
        return ReadEnvelope(
            data=RunLogPage(items=lines.data or [], next_cursor=None),
            evidence=lines.evidence,
        )
    outcome = derive_run_logs(store, run_id, limit=limit, cursor=cursor)
    data = outcome_data(outcome)
    return live_envelope(
        data,
        source=outcome.source or "run_evidence",
        stale=outcome.stale,
        last_confirmed_at=outcome.last_confirmed_at,
    )


# NOTE: the {run_id:path} detail route must be registered after every
# /runs/{run_id}/... subroute — the path converter would otherwise swallow
# the suffix as part of the id.


@router.get("/mission/runs/{run_id:path}")
def get_mission_run(
    run_id: str,
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[MissionRunDetail]:
    """Return the run header, metrics, identity join and details rail.

    Live mode answers from the orchestrator joined to the durable record;
    when the orchestrator is unreachable but retained evidence exists, the
    retained record is served flagged stale — it is never silently dropped.
    """
    return serve_sourced_record(
        lambda: derive_run(store, run_id),
        "run_detail",
        MissionRunDetail,
        f"Unknown run {run_id}",
        run_id=run_id,
    )


@router.get("/mission/datasets/{dataset_id:path}")
def get_mission_dataset(dataset_id: str) -> ReadEnvelope[MissionDatasetDetail]:
    """Return the full Dataset page read model in one payload.

    Live mode answers from the asset graph: an unknown asset is a 404 and an
    unreachable provider a 503 — a stored record never substitutes for provider
    truth.
    """
    return serve_sourced_record(
        lambda: derive_dataset(dataset_id),
        "dataset_detail",
        MissionDatasetDetail,
        f"Unknown dataset {dataset_id}",
        id=dataset_id,
    )
