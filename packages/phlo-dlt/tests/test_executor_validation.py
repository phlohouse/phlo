"""Tests for executor-level strict validation semantics.

Strict validation must block the write entirely before any merge runs;
non-strict mode still writes but records the failed Pandera evaluation.
Failure telemetry keeps runtime correlation intact, evidence-sink errors
never mask the provider exception, a failed submission records an unknown
after-state with redacted error details, and a successful provider call
stays successful even when readback is contradictory.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pandas as pd
import pytest
from pandera.pandas import DataFrameModel
from pandera.typing import Series  # type: ignore[possibly-missing-import]

from phlo.logging import get_logger
from phlo.hooks.events import IngestionEvent, TelemetryEvent
from phlo_dlt.executor import DltIngester
from phlo_dlt.registry import TableConfig


class StrictExecutorSchema(DataFrameModel):
    """Schema used to verify executor-level strict validation behavior."""

    name: Series[str]
    value: Series[int]


def test_strict_validation_blocks_visible_write(monkeypatch, tmp_path) -> None:
    invalid_path = tmp_path / "invalid.parquet"
    pd.DataFrame([{"name": "test", "value": "not_an_int"}]).to_parquet(invalid_path)
    merge_called = False

    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline",
        lambda **_kwargs: (SimpleNamespace(), tmp_path),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.stage_to_parquet",
        lambda **_kwargs: ([invalid_path], 0.01),
    )

    def _merge_to_table_store(**_kwargs):
        nonlocal merge_called
        merge_called = True
        return {"rows_inserted": 1, "rows_deleted": 0}

    monkeypatch.setattr("phlo_dlt.executor.merge_to_table_store", _merge_to_table_store)

    ingester = DltIngester(
        context=None,
        logger=get_logger("test_dlt_executor_strict_validation"),
        table_config=TableConfig(
            table_name="entries",
            table_schema=None,
            validation_schema=StrictExecutorSchema,
            unique_key="name",
            group_name="raw",
        ),
        table_store_resource=cast(Any, SimpleNamespace()),
        dlt_source_func=lambda partition_date: object(),
        validation_schema=StrictExecutorSchema,
        validate=True,
        strict_validation=True,
    )

    with pytest.raises(RuntimeError, match="Pandera contract validation failed"):
        ingester.run_ingestion(partition_key="2026-03-05")

    assert merge_called is False


def test_non_strict_validation_allows_write_and_records_evaluation(monkeypatch, tmp_path) -> None:
    invalid_path = tmp_path / "invalid.parquet"
    pd.DataFrame([{"name": "test", "value": "not_an_int"}]).to_parquet(invalid_path)

    pipeline_setup: dict[str, str] = {}

    def _setup_dlt_pipeline(**kwargs):
        pipeline_setup.update(kwargs)
        return SimpleNamespace(), tmp_path

    monkeypatch.setattr("phlo_dlt.executor.setup_dlt_pipeline", _setup_dlt_pipeline)
    monkeypatch.setattr(
        "phlo_dlt.executor.stage_to_parquet",
        lambda **_kwargs: ([invalid_path], 0.01),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.merge_to_table_store",
        lambda **_kwargs: {"rows_inserted": 1, "rows_deleted": 0},
    )

    ingester = DltIngester(
        context=None,
        logger=get_logger("test_dlt_executor_non_strict_validation"),
        table_config=TableConfig(
            table_name="entries",
            table_schema=None,
            validation_schema=StrictExecutorSchema,
            unique_key="name",
            group_name="raw",
        ),
        table_store_resource=cast(Any, SimpleNamespace()),
        dlt_source_func=lambda partition_date: object(),
        validation_schema=StrictExecutorSchema,
        validate=True,
        strict_validation=False,
    )

    result = ingester.run_ingestion(partition_key="2026-03-05")

    assert result.status == "success"
    assert result.metadata["pandera_evaluation"]["passed"] is False
    assert pipeline_setup == {
        "pipeline_name": "entries_2026_03_05",
        "dataset_name": "entries_2026_03_05",
    }


def test_dlt_failure_events_carry_runtime_correlation(monkeypatch, tmp_path) -> None:
    class RecordingBus:
        def __init__(self) -> None:
            self.events: list[object] = []

        def emit(self, event: object) -> None:
            self.events.append(event)

    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)
    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline",
        lambda **_kwargs: (SimpleNamespace(), tmp_path),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.stage_to_parquet",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("stage failed")),
    )

    runtime = SimpleNamespace(run_id="run-22", job_name="daily_ingestion")
    ingester = DltIngester(
        context=runtime,
        logger=get_logger("test_dlt_executor_correlation"),
        table_config=TableConfig(
            table_name="entries",
            table_schema=None,
            validation_schema=StrictExecutorSchema,
            unique_key="name",
            group_name="raw",
        ),
        table_store_resource=cast(Any, SimpleNamespace()),
        dlt_source_func=lambda partition_date: object(),
        validation_schema=StrictExecutorSchema,
        validate=False,
    )

    with pytest.raises(RuntimeError, match="stage failed"):
        ingester.run_ingestion(
            partition_key="2026-03-05",
            parameters={"run_id": "run-22", "branch_name": "main"},
        )

    ingestion_events = [event for event in bus.events if isinstance(event, IngestionEvent)]
    telemetry_events = [event for event in bus.events if isinstance(event, TelemetryEvent)]
    assert ingestion_events
    assert telemetry_events
    ingestion = ingestion_events[-1]
    telemetry = telemetry_events[-1]
    assert ingestion.correlation.run_id == "run-22"
    assert ingestion.correlation.job_name == "daily_ingestion"
    assert ingestion.correlation.partition_key == "2026-03-05"
    assert ingestion.correlation.asset_key == "dlt_entries"
    assert telemetry.correlation.run_id == "run-22"
    assert telemetry.correlation.job_name == "daily_ingestion"
    assert telemetry.correlation.partition_key == "2026-03-05"
    assert telemetry.correlation.asset_key == "dlt_entries"


@pytest.mark.parametrize("sink_error", [RuntimeError("sink failed"), TypeError("bad sink")])
def test_lifecycle_sink_failures_do_not_mask_provider_failure(
    monkeypatch, tmp_path, sink_error: Exception
) -> None:
    """Start/end/failure evidence sinks cannot replace the provider exception."""

    class RaisingBus:
        def emit(self, _event: object) -> None:
            raise sink_error

    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: RaisingBus())
    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline",
        lambda **_kwargs: (SimpleNamespace(), tmp_path),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.stage_to_parquet",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("provider stage failed")),
    )

    ingester = DltIngester(
        context=SimpleNamespace(run_id="run-sink", tags={"phlo/project_id": "project-sink"}),
        logger=get_logger("test_dlt_sink_failure"),
        table_config=TableConfig(
            table_name="entries",
            table_schema=None,
            validation_schema=None,
            unique_key="name",
            group_name="raw",
        ),
        table_store_resource=cast(Any, SimpleNamespace()),
        dlt_source_func=lambda partition_date: object(),
        validate=False,
    )

    with pytest.raises(RuntimeError, match="provider stage failed"):
        ingester.run_ingestion(partition_key="2026-03-05")


def test_provider_exception_records_unknown_output_without_claiming_no_write(
    monkeypatch, tmp_path
) -> None:
    """A failed submission still records the target with an unknown after-state."""
    parquet_path = tmp_path / "staged.parquet"
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline",
        lambda **_kwargs: (SimpleNamespace(), tmp_path),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.stage_to_parquet",
        lambda **_kwargs: ([parquet_path], 0.01),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.staged_object_inventory",
        lambda _paths: [{"identity": "staged/file.parquet", "checksum": "abc", "byte_count": 1}],
    )
    monkeypatch.setattr("phlo_dlt.executor.dlt_execution_identity", lambda *args: ("exec-1", True))
    monkeypatch.setattr("phlo_dlt.executor.dlt_observed_metrics", lambda _pipeline: {})
    monkeypatch.setattr(
        "phlo_dlt.executor.table_state",
        lambda *_args: {
            "state": "present",
            "snapshot_id": "before-snapshot",
            "schema_hash": "before-schema",
            "metadata": {},
        },
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.merge_to_table_store",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("provider committed? customer@example.com account=acct-123")
        ),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.emit_observation",
        lambda **kwargs: captured.append(kwargs),
    )

    ingester = DltIngester(
        context=SimpleNamespace(run_id="run-unknown", tags={"phlo/project_id": "project-unknown"}),
        logger=get_logger("test_dlt_unknown_output"),
        table_config=TableConfig(
            table_name="entries",
            table_schema=None,
            validation_schema=None,
            unique_key="name",
            group_name="raw",
        ),
        table_store_resource=cast(Any, SimpleNamespace()),
        dlt_source_func=lambda partition_date: object(),
        validate=False,
        add_metadata_columns=False,
    )

    with pytest.raises(RuntimeError, match=r"provider committed\?"):
        ingester.run_ingestion(partition_key="2026-03-05")

    output = next(resource for resource in captured[0]["resources"] if resource["role"] == "output")
    assert output["snapshot_before"] == "before-snapshot"
    assert output["snapshot_after"] is None
    assert output["metadata"]["outcome"] == "unknown"
    assert output["metadata"]["evidence_completeness"] == "incomplete"
    assert "customer@example.com" not in captured[0]["error"]
    assert "acct-123" not in captured[0]["error"]
    assert "fingerprint:" in captured[0]["error"]


def test_successful_write_with_contradictory_readback_stays_successful(
    monkeypatch, tmp_path
) -> None:
    """A successful provider call is retained when readback says the table is absent."""
    parquet_path = tmp_path / "staged.parquet"
    captured: list[dict[str, Any]] = []
    states = iter(
        [
            {
                "state": "present",
                "snapshot_id": "before",
                "schema_hash": "schema-before",
                "metadata": {},
            },
            {
                "state": "absent",
                "snapshot_id": None,
                "schema_hash": None,
                "metadata": {},
            },
        ]
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline",
        lambda **_kwargs: (SimpleNamespace(), tmp_path),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.stage_to_parquet",
        lambda **_kwargs: ([parquet_path], 0.01),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.staged_object_inventory",
        lambda _paths: [{"identity": "file.parquet", "checksum": "abc", "byte_count": 1}],
    )
    monkeypatch.setattr("phlo_dlt.executor.dlt_execution_identity", lambda *args: ("exec-1", True))
    monkeypatch.setattr("phlo_dlt.executor.dlt_observed_metrics", lambda _pipeline: {})
    monkeypatch.setattr("phlo_dlt.executor.table_state", lambda *_args: next(states))
    monkeypatch.setattr(
        "phlo_dlt.executor.merge_to_table_store",
        lambda **_kwargs: {"rows_inserted": 1, "rows_deleted": 0},
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.emit_observation",
        lambda **kwargs: captured.append(kwargs),
    )

    ingester = DltIngester(
        context=SimpleNamespace(
            run_id="run-contradictory", tags={"phlo/project_id": "project-contradictory"}
        ),
        logger=get_logger("test_dlt_contradictory_readback"),
        table_config=TableConfig(
            table_name="entries",
            table_schema=None,
            validation_schema=None,
            unique_key="name",
            group_name="raw",
        ),
        table_store_resource=cast(Any, SimpleNamespace()),
        dlt_source_func=lambda partition_date: object(),
        validate=False,
        add_metadata_columns=False,
    )

    result = ingester.run_ingestion(partition_key="2026-03-05")

    assert result.status == "success"
    output = next(resource for resource in captured[0]["resources"] if resource["role"] == "output")
    assert output["metadata"]["outcome"] == "contradictory"
    assert output["metadata"]["evidence_completeness"] == "incomplete"
