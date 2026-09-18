"""Per-run and per-dataset evidence read models.

Evidence is addressed by run id (stages, quality failure sample, events, traces,
artifacts, consumers, configuration) and by dataset id (ownership, access,
recent runs). Both fail closed with 404 when the record is unknown rather than
returning an empty shape, so the UI can distinguish "no evidence yet" from
"nothing recorded".
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from phlo_api.observatory_api.observatory_mission_control_models import (
    DatasetGovernance,
    MissionDatasetDetail,
    MissionRunDetail,
    MissionRunLogLine,
    RunArtifact,
    RunConfigurationRow,
    RunConsumer,
    RunEvent,
    RunQualityFailure,
    RunSpan,
    RunStageView,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    find_by,
    load_records,
)

router = APIRouter()


def _by_run(collection: str, model, run_id: str):
    return [record for record in load_records(collection, model) if record.run_id == run_id]


@router.get("/mission/runs/{run_id}/stages")
def get_mission_run_stages(run_id: str) -> list[RunStageView]:
    """Return the execution timeline for a run."""
    return _by_run("run_stages", RunStageView, run_id)


@router.get("/mission/runs/{run_id}/quality")
def get_mission_run_quality(run_id: str) -> RunQualityFailure:
    """Return the blocking quality failure and its bounded row sample."""
    failing = _by_run("run_quality", RunQualityFailure, run_id)
    if not failing:
        raise HTTPException(status_code=404, detail=f"No quality evidence for run {run_id}")
    return failing[0]


@router.get("/mission/runs/{run_id}/events")
def get_mission_run_events(run_id: str) -> list[RunEvent]:
    """Return key execution events for a run."""
    return _by_run("run_events", RunEvent, run_id)


@router.get("/mission/runs/{run_id}/traces")
def get_mission_run_traces(run_id: str) -> list[RunSpan]:
    """Return the trace span waterfall for a run."""
    return _by_run("run_spans", RunSpan, run_id)


@router.get("/mission/runs/{run_id}/artifacts")
def get_mission_run_artifacts(run_id: str) -> list[RunArtifact]:
    """Return recorded evidence artifacts for a run."""
    return _by_run("run_artifacts", RunArtifact, run_id)


@router.get("/mission/runs/{run_id}/consumers")
def get_mission_run_consumers(run_id: str) -> list[RunConsumer]:
    """Return downstream readers affected by a run's outcome."""
    return _by_run("run_consumers", RunConsumer, run_id)


@router.get("/mission/runs/{run_id}/configuration")
def get_mission_run_configuration(run_id: str) -> list[RunConfigurationRow]:
    """Return the resolved configuration for a run."""
    return _by_run("run_config", RunConfigurationRow, run_id)


@router.get("/mission/datasets/{dataset_id}/governance")
def get_mission_dataset_governance(dataset_id: str) -> DatasetGovernance:
    """Return ownership, access grants and recent runs for a dataset."""
    record = find_by("dataset_governance", DatasetGovernance, dataset_id=dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No governance record for {dataset_id}")
    return record


@router.get("/mission/runs/{run_id}")
def get_mission_run(run_id: str) -> MissionRunDetail:
    """Return the run header, metrics and details rail payload."""
    record = find_by("run_detail", MissionRunDetail, run_id=run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run {run_id}")
    return record


@router.get("/mission/runs/{run_id}/logs")
def get_mission_run_logs(run_id: str) -> list[MissionRunLogLine]:
    """Return raw log lines for a run."""
    return _by_run("run_logs", MissionRunLogLine, run_id)


@router.get("/mission/datasets/{dataset_id}")
def get_mission_dataset(dataset_id: str) -> MissionDatasetDetail:
    """Return the full Dataset page read model in one payload."""
    record = find_by("dataset_detail", MissionDatasetDetail, id=dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown dataset {dataset_id}")
    return record
