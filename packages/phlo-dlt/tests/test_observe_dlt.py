"""Tests for phlo-observe instrumentation in the DLT staging path.

``stage_to_parquet`` owns a run-terminal ``dlt.pipeline.run`` event: it binds
a provisional run id up front (so a failure inside a retried Dagster step can
never poison the enclosing run's status) and rebinds to the real DLT load id
once ``pipeline.run`` returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import phlo.telemetry as phlo_observe


class _RecordingScope:
    """Context manager capturing correlation rebinding and exit status."""

    def __init__(self, sink: list[tuple[str, Any]]) -> None:
        self.sink = sink

    def __enter__(self) -> _RecordingScope:
        self.sink.append(("enter", None))
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.sink.append(("exit", exc_type))
        return None

    def set(self, **attrs: Any) -> None:
        self.sink.append(("set", attrs))

    def set_correlation(self, **values: Any) -> None:
        self.sink.append(("set_correlation", values))


def _patch_observe(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "dlt_pipeline_scope", lambda _p: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "dlt_pipeline_run", lambda _p, **kw: _RecordingScope(sink))
    monkeypatch.setattr(
        phlo_observe,
        "dlt_load_info_attributes",
        lambda _info: {"load_id": "load-1"},
    )
    return sink


def _pipeline(load_info: Any) -> MagicMock:
    pipeline = MagicMock()
    pipeline.pipeline_name = "users"
    pipeline.run.return_value = load_info
    return pipeline


def _load_info(load_id: str | None = "load-1") -> MagicMock:
    job = MagicMock()
    job.file_path = "out/part-0001.parquet"
    package = MagicMock()
    package.jobs = {"failed_jobs": [], "completed_jobs": [job]}
    info = MagicMock()
    info.load_id = load_id
    info.load_ids = [load_id] if load_id else []
    info.load_packages = [package]
    return info


def test_stage_to_parquet_rebinds_run_id_to_load_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sink = _patch_observe(monkeypatch)
    from phlo_dlt.dlt_helpers import stage_to_parquet

    paths, _elapsed = stage_to_parquet(MagicMock(), _pipeline(_load_info()), MagicMock(), tmp_path)
    assert len(paths) == 1

    correlations = [v for kind, v in sink if kind == "set_correlation"]
    # First binding is provisional (<pipeline>-<ulid>), then the real load id.
    assert len(correlations) == 2
    provisional = correlations[0]["run_id"]
    assert provisional.startswith("users-")
    assert correlations[1] == {"run_id": "load-1"}
    assert ("set", {"load_id": "load-1"}) in sink


def test_stage_to_parquet_failure_records_exception_in_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sink = _patch_observe(monkeypatch)
    from phlo_dlt.dlt_helpers import stage_to_parquet

    with pytest.raises(RuntimeError, match="no load info"):
        stage_to_parquet(MagicMock(), _pipeline(None), MagicMock(), tmp_path)
    # The run scope exited with the RuntimeError, so the terminal
    # dlt.pipeline.run event records failure rather than success.
    exits = [v for kind, v in sink if kind == "exit"]
    assert exits[-1] is RuntimeError


def test_stage_to_parquet_failed_jobs_records_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sink = _patch_observe(monkeypatch)
    info = _load_info()
    info.load_packages[0].jobs["failed_jobs"] = [MagicMock()]
    pipeline = _pipeline(info)
    from phlo_dlt.dlt_helpers import stage_to_parquet

    with pytest.raises(RuntimeError, match="failed loader jobs"):
        stage_to_parquet(MagicMock(), pipeline, MagicMock(), tmp_path)
    # The failed-jobs check sits inside the run scope so the terminal
    # dlt.pipeline.run event records failure against the real load id.
    correlations = [v for kind, v in sink if kind == "set_correlation"]
    assert correlations[-1] == {"run_id": "load-1"}
    exits = [v for kind, v in sink if kind == "exit"]
    assert exits[-1] is RuntimeError
    # The diagnostic stash must be set even on failure — a failed load's
    # info is exactly what post-hoc inspection needs.
    assert getattr(pipeline, "_phlo_last_load_info") is info
