"""Authenticated durable run-report endpoint.

Serves the evidence projection for one exact run attempt; an unknown
project/run/attempt combination is always a 404, never a partial report.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path

from phlo.run_evidence.report import RunReport, RunReportNotFound, build_run_report
from phlo_api.run_evidence import RunEvidenceStore, get_run_evidence_store

router = APIRouter(tags=["observatory"])


@router.get(
    "/projects/{project_id}/runs/{run_id}/attempts/{attempt}/report",
    response_model=RunReport,
)
def get_observatory_run_report(
    project_id: str,
    run_id: str,
    attempt: int = Path(..., ge=1),
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> RunReport:
    """Return the durable evidence projection for one exact run attempt."""
    try:
        return build_run_report(store, project_id, run_id, attempt)
    except RunReportNotFound as exc:
        raise HTTPException(status_code=404, detail={"error": "run_not_found"}) from exc
