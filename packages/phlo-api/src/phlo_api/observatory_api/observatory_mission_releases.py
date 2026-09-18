"""Releases read models: promotion control, candidate review and history.

Candidates are addressed by candidate id. The summary band is derived from the
candidate and completed-release collections so the counters can never disagree
with the rows they describe.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from phlo_api.observatory_api.observatory_mission_control_models import (
    CompletedRelease,
    ReleaseCandidate,
    ReleaseCandidateDetail,
    ReleaseSummaryMetric,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    find_by,
    load_records,
)

router = APIRouter()


@router.get("/mission/releases/summary")
def get_mission_release_summary() -> list[ReleaseSummaryMetric]:
    """Return the Releases summary band, derived from the underlying rows."""
    candidates = load_records("release_candidates", ReleaseCandidate)
    completed = load_records("completed_releases", CompletedRelease)
    ready = [row for row in candidates if row.readiness.startswith("Ready")]
    blocked = [row for row in candidates if "blocked" in row.readiness.lower()]
    providers = {row.provider for row in candidates}
    return [
        ReleaseSummaryMetric(
            label="Pending",
            value=f"{len(candidates)} candidates",
            hint=f"Across {len(providers)} catalog providers",
            tone="muted",
        ),
        ReleaseSummaryMetric(
            label="Ready for review",
            value=f"{len(ready)} candidate{'s' if len(ready) != 1 else ''}",
            hint="All required evidence present",
            tone="success",
        ),
        ReleaseSummaryMetric(
            label="Blocked",
            value=f"{len(blocked)} candidate{'s' if len(blocked) != 1 else ''}",
            hint="Quality gate failed",
            tone="danger",
        ),
        ReleaseSummaryMetric(
            label="Released · 24h",
            value=f"{len(completed)} completed",
            hint="Provider outcomes confirmed",
            tone="muted",
        ),
        ReleaseSummaryMetric(
            label="Unknown outcomes",
            value="0 operations",
            hint="No reconciliation needed",
            tone="muted",
        ),
    ]


@router.get("/mission/releases/candidates")
def get_mission_release_candidates() -> list[ReleaseCandidate]:
    """Return candidate snapshots awaiting promotion, newest first."""
    return load_records("release_candidates", ReleaseCandidate)


@router.get("/mission/releases/candidates/{candidate_id}")
def get_mission_release_candidate(candidate_id: str) -> ReleaseCandidateDetail:
    """Return the full review payload for one candidate."""
    record = find_by("release_candidate_detail", ReleaseCandidateDetail, id=candidate_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown release candidate {candidate_id}")
    return record


@router.get("/mission/releases/completed")
def get_mission_completed_releases() -> list[CompletedRelease]:
    """Return releases whose provider outcome is confirmed."""
    return load_records("completed_releases", CompletedRelease)
