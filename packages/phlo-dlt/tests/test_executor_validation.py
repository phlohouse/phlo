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

from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger
from phlo.hooks.events import IngestionEvent, TelemetryEvent
from phlo_dlt.decorator import phlo_ingestion
from phlo_dlt.dlt_helpers import DltStagingResult
from phlo_dlt.executor import DltIngester
from phlo_dlt.pandera_checks import PanderaContractValidationError
from phlo_dlt.registry import TableConfig


class StrictExecutorSchema(DataFrameModel):
    """Schema used to verify executor-level strict validation behavior."""

    name: Series[str]
    value: Series[int]


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("validate", [True, False])
@pytest.mark.parametrize(
    "options, message",
    [
        ({"merge_strategy": "typo"}, "Invalid merge_strategy"),
        ({"merge_config": {"unknown": True}}, "Unsupported merge_config option"),
        ({"merge_config": {"deduplication_method": "typo"}}, "deduplication_method"),
        ({"merge_config": {"deduplication": "yes"}}, "must be a boolean"),
        ({"merge_config": []}, "merge_config must be a dict"),
        ({"merge_strategy": "history"}, "entity_key"),
        ({"validation_mode": "typo"}, "Unknown validation mode"),
        ({"validation_batch_size": 0}, "sizes must be positive"),
        ({"validation_sample_size": -1}, "sizes must be positive"),
    ],
)
def test_invalid_runtime_options_are_rejected_before_run(
    registered, validate, options, message
) -> None:
    """Even disabled validation cannot defer malformed options until extraction."""
    with pytest.raises(PhloConfigError, match=message):
        if registered:
            phlo_ingestion(
                table_name="entries",
                unique_key="name",
                group="raw",
                validation_schema=StrictExecutorSchema,
                validate=validate,
                **options,
            )
            return
        DltIngester(
            context=None,
            logger=get_logger("test_dlt_preflight"),
            table_config=TableConfig("entries", None, None, "name", "raw"),
            table_store_resource=cast(Any, SimpleNamespace()),
            dlt_source_func=lambda partition_date: pytest.fail("source must not run"),
            validate=validate,
            **options,
        )


@pytest.mark.parametrize("validate", [True, False])
def test_direct_no_data_run_does_not_require_a_schema(validate) -> None:
    ingester = DltIngester(
        context=None,
        logger=get_logger("test_dlt_schema_less_no_data"),
        table_config=TableConfig("entries", None, None, "name", "raw"),
        table_store_resource=cast(Any, SimpleNamespace()),
        dlt_source_func=lambda partition_date: None,
        validate=validate,
    )

    result = ingester.run_ingestion(partition_key="2026-03-05")

    assert result.status == "no_data"
    assert result.check_results == ()


def test_strict_validation_blocks_visible_write(monkeypatch, tmp_path) -> None:
    invalid_path = tmp_path / "invalid.parquet"
    pd.DataFrame([{"name": "test", "value": "not_an_int"}]).to_parquet(invalid_path)
    merge_called = False

    monkeypatch.setattr(
        "phlo_dlt.executor.extract_to_parquet",
        lambda **_kwargs: DltStagingResult([invalid_path], 0.01, None),
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
        validate=True,
        strict_validation=True,
    )

    with pytest.raises(RuntimeError, match="Pandera contract validation failed"):
        ingester.run_ingestion(partition_key="2026-03-05")

    assert merge_called is False


def test_strict_domain_check_rejects_rows_in_a_later_staged_file(monkeypatch, tmp_path) -> None:
    """A strict domain check must inspect every staged file before any merge."""
    accepted_path = tmp_path / "accepted.parquet"
    rejected_path = tmp_path / "rejected.parquet"
    pd.DataFrame([{"name": "accepted", "value": 1}]).to_parquet(accepted_path)
    pd.DataFrame([{"name": "rejected", "value": 2}]).to_parquet(rejected_path)
    merge_called = False
    observed_values: list[int] = []

    monkeypatch.setattr(
        "phlo_dlt.executor.extract_to_parquet",
        lambda **_kwargs: DltStagingResult([accepted_path, rejected_path], 0.01, None),
    )

    def _merge_to_table_store(**_kwargs):
        nonlocal merge_called
        merge_called = True
        return {"rows_inserted": 2, "rows_deleted": 0}

    monkeypatch.setattr("phlo_dlt.executor.merge_to_table_store", _merge_to_table_store)

    def reject_later_file(frame: pd.DataFrame) -> str | None:
        observed_values.extend(frame["value"].tolist())
        return "value 2 is rejected" if 2 in frame["value"].values else None

    ingester = DltIngester(
        context=None,
        logger=get_logger("test_dlt_executor_domain_quality_all_files"),
        table_config=TableConfig(
            table_name="entries",
            table_schema=None,
            validation_schema=StrictExecutorSchema,
            unique_key="name",
            group_name="raw",
        ),
        table_store_resource=cast(Any, SimpleNamespace()),
        dlt_source_func=lambda partition_date: object(),
        validate=True,
        strict_validation=True,
        quality_checks=[reject_later_file],
    )

    with pytest.raises(RuntimeError, match="Domain quality check failed"):
        ingester.run_ingestion(partition_key="2026-03-05")

    assert observed_values == [1, 2]
    assert merge_called is False


@pytest.mark.parametrize("corrupt_staging", [False, True])
def test_non_strict_validation_only_allows_evaluated_rows(
    monkeypatch, tmp_path, corrupt_staging
) -> None:
    invalid_path = tmp_path / "invalid.parquet"
    pd.DataFrame([{"name": "test", "value": "not_an_int"}]).to_parquet(invalid_path)
    if corrupt_staging:
        invalid_path.write_bytes(b"not a parquet file")
    writes: list[int] = []

    monkeypatch.setattr(
        "phlo_dlt.executor.extract_to_parquet",
        lambda **_kwargs: DltStagingResult([invalid_path], 0.01, None),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.merge_to_table_store",
        lambda **_kwargs: writes.append(1) or {"rows_inserted": 1, "rows_deleted": 0},
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
        validate=True,
        strict_validation=False,
        add_metadata_columns=False,
    )

    if corrupt_staging:
        with pytest.raises(PanderaContractValidationError) as error:
            ingester.run_ingestion(partition_key="2026-03-05")
        check = error.value.check_results[0]
        assert check.passed is False
        assert check.severity == "error"
        assert "Parquet" in check.metadata["error"]
        assert writes == []
        return

    result = ingester.run_ingestion(partition_key="2026-03-05")

    assert result.status == "success"
    assert len(result.check_results) == 1
    assert result.check_results[0].passed is False
    assert result.check_results[0].severity == "warn"
    assert writes == [1]


def test_dlt_failure_events_carry_runtime_correlation(monkeypatch, tmp_path) -> None:
    class RecordingBus:
        def __init__(self) -> None:
            self.events: list[object] = []

        def emit(self, event: object) -> None:
            self.events.append(event)

    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)
    monkeypatch.setattr(
        "phlo_dlt.executor.extract_to_parquet",
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
        "phlo_dlt.executor.extract_to_parquet",
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
        "phlo_dlt.executor.extract_to_parquet",
        lambda **_kwargs: DltStagingResult([parquet_path], 0.01, None),
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
        "phlo_dlt.executor.extract_to_parquet",
        lambda **_kwargs: DltStagingResult([parquet_path], 0.01, None),
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
