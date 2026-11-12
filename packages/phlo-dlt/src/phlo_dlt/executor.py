"""DLT ingestion executor implementation.

This module provides the DltIngester class, which implements the full ingestion
pipeline from DLT extraction through Parquet staging to table store loading.
It orchestrates the helpers from :mod:`phlo_dlt.dlt_helpers` and validation
from :mod:`phlo_dlt.pandera_checks` to execute complete ingestion runs.

The executor follows the Write-Audit-Publish (WAP) pattern when strict
validation is enabled, writing to isolated branches for validation before
promotion to the main branch.

Key Class:
    - :class:`DltIngester`: Main ingestion executor implementing BaseIngester

Execution Flow:
    1. Setup DLT pipeline for extraction
    2. Stage data to Parquet files
    3. Inject metadata columns (_phlo_row_id, etc.)
    4. Validate against Pandera schema (if configured)
    5. Merge to table store (append or upsert)
    6. Emit telemetry and return results

Hook Integration:
    The executor integrates with Phlo's hook system for event emission:
    - IngestionEventEmitter: Lifecycle events (start, end)
    - TelemetryEventEmitter: Metrics and logs

See Also:
    - :class:`phlo.operations.ingestion.BaseIngester`: Abstract base class
    - :mod:`phlo_dlt.dlt_helpers`: Helper functions used by executor
    - :mod:`phlo_dlt.pandera_checks`: Validation integration
    - :mod:`phlo.hooks`: Event emission system

Example:
    ```python
    from phlo_dlt.executor import DltIngester
    from phlo_dlt.registry import TableConfig

    ingester = DltIngester(
        context=dagster_context,
        logger=logger,
        table_config=table_config,
        table_store_resource=iceberg_store,
        dlt_source_func=fetch_users,
        validate=True,
        strict_validation=True,
    )
    result = ingester.run_ingestion(
        partition_key="2024-01-01",
        parameters={"branch_name": "main", "run_id": "run-123"}
    )
    ```

"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Sequence

import pandas as pd

from phlo.exceptions import PhloConfigError
from phlo.logging import log_event
from phlo.operations.ingestion import BaseIngester, IngestionResult
from phlo.operations.parquet_validation import ValidationMode
from phlo.hooks import (
    HookCorrelation,
    IngestionEventContext,
    IngestionEventEmitter,
    LineageEventContext,
    LineageEventEmitter,
    TelemetryEventContext,
    TelemetryEventEmitter,
)
from phlo._attempt import normalize_attempt
from phlo.run_evidence import emit_lifecycle_safely, emit_observation
from phlo.run_evidence.redaction import safe_error_summary
from phlo.capabilities.history import HistoryWriteError
from phlo.capabilities.interfaces import TableStore
from phlo.capabilities.runtime import DataInterval, RuntimeRouting, routing_from_context
from phlo.capabilities.specs import CheckResult

from phlo_dlt.contract_coverage import detect_dropped_source_columns
from phlo_dlt.dlt_helpers import (
    apply_data_interval,
    apply_validation_schema_hints,
    extract_to_parquet,
    inject_metadata_columns,
    merge_to_table_store,
    validate_interval_options,
    validate_merge_config,
)
from phlo_dlt.evidence import (
    dlt_execution_identity,
    dlt_observed_metrics,
    normalize_source_identity,
    staged_object_inventory,
    table_state,
)
from phlo_dlt.pandera_checks import (
    PanderaContractEvaluation,
    PanderaContractValidationError,
    evaluate_pandera_contract_parquet_files,
    pandera_contract_asset_check_result,
)
from phlo_dlt.registry import TableConfig


def validate_runtime_options(
    *,
    merge_strategy: str,
    unique_key: str,
    merge_config: dict[str, Any] | None,
    validation_mode: ValidationMode,
    validation_batch_size: int,
    validation_sample_size: int,
    interval_column: str | None,
    interval_lookback: timedelta,
) -> None:
    """Check runtime configuration before registration or source execution.

    No schema is required here: direct no-data runs need not resolve one.
    Merge defaults remain the caller's policy rather than being expanded here.
    """
    validate_interval_options(interval_column, interval_lookback, merge_strategy, unique_key)
    validate_merge_config(merge_strategy, unique_key, merge_config)
    if validation_mode not in {"materialized", "full", "sample"}:
        raise PhloConfigError(message=f"Unknown validation mode: {validation_mode}")
    if validation_batch_size <= 0 or validation_sample_size <= 0:
        raise PhloConfigError(message="Validation batch and sample sizes must be positive")


@dataclass
class DltIngestionResult(IngestionResult):
    """Return ingestion metrics and checks evaluated before publication."""

    check_results: tuple[CheckResult, ...] = ()


@dataclass(frozen=True, slots=True)
class DomainQualityEvaluation:
    """Record one staged domain-quality check result before publication."""

    check_name: str
    violation: str | None

    @property
    def passed(self) -> bool:
        """Return whether the domain check accepted the staged rows."""
        return self.violation is None


class DomainQualityValidationError(RuntimeError):
    """Carry failed staged domain checks that prevented an irreversible write."""

    def __init__(
        self,
        *,
        evaluations: tuple[DomainQualityEvaluation, ...],
        parquet_paths: tuple[Path, ...],
        check_results: tuple[CheckResult, ...],
    ) -> None:
        """Store failed check evidence for the decorator's check-result surface."""
        self.evaluations = evaluations
        self.parquet_paths = parquet_paths
        self.check_results = check_results
        failed = next(evaluation for evaluation in evaluations if not evaluation.passed)
        super().__init__(f"Domain quality check failed: {failed.check_name}: {failed.violation}")


class StagedFrameLoader:
    """Read staged Parquet into one frame on first use and share it across checks."""

    def __init__(self, parquet_paths: Sequence[Path]) -> None:
        """Remember the staged files without reading them yet."""
        self._parquet_paths = tuple(parquet_paths)
        self._frame: pd.DataFrame | None = None
        self._error: Exception | None = None

    def load(self) -> pd.DataFrame:
        """Return the staged rows, reading the files at most once.

        A read failure is remembered and re-raised for every later caller, so each
        check still reports it instead of retrying the read.
        """
        if self._error is not None:
            raise self._error
        if self._frame is None:
            try:
                self._frame = pd.concat(
                    [pd.read_parquet(parquet_path) for parquet_path in self._parquet_paths],
                    ignore_index=True,
                )
            except Exception as exc:
                self._error = exc
                raise
        frame = self._frame.copy(deep=not _copy_on_write_enabled())
        # Pandas copies blocks, not nested objects. Copy object cells recursively
        # while leaving numeric blocks shared under copy-on-write.
        for column, dtype in self._frame.dtypes.items():
            if pd.api.types.is_object_dtype(dtype):
                frame[column] = self._frame[column].map(deepcopy)
        return frame


def _copy_on_write_enabled() -> bool:
    """Return whether pandas copy-on-write protects shallow copies."""
    if int(pd.__version__.split(".", 1)[0]) >= 3:
        return True
    return pd.options.mode.copy_on_write is True


def _evaluate_domain_quality_checks(
    *,
    parquet_paths: Sequence[Path],
    quality_checks: Sequence[Callable[[pd.DataFrame], str | None]],
) -> tuple[DomainQualityEvaluation, ...]:
    """Evaluate domain checks against staged rows before the table-store write."""
    evaluations: list[DomainQualityEvaluation] = []
    staged_frames = StagedFrameLoader(parquet_paths)
    for index, quality_check in enumerate(quality_checks):
        check_name = getattr(quality_check, "__name__", None) or f"quality_{index}"
        if not parquet_paths:
            violation = "no staged parquet available for domain checks"
        else:
            try:
                violation = quality_check(staged_frames.load())
            except Exception as exc:  # noqa: BLE001 - a check error rejects strict publication
                violation = f"quality check raised: {exc}"
        evaluations.append(DomainQualityEvaluation(check_name=check_name, violation=violation))
    return tuple(evaluations)


def _resource_identity(
    *,
    project_id: str | None,
    resource_type: str,
    resource_id: str,
    attributes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the provider's canonical, project-scoped report identity."""
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "tenant": project_id,
        "attributes": attributes or {},
    }


def _ingestion_attempt(raw_attempt: Any) -> int | None:
    """Keep invalid attempt correlation out of durable ingestion evidence."""
    try:
        return normalize_attempt(raw_attempt)
    except ValueError:
        return None


class DltIngester(BaseIngester):
    """DLT-specific implementation of the ingestion engine.

    This class orchestrates the complete DLT-based ingestion flow, from
    extracting data via DLT to loading it into the configured table store.
    It implements the orchestrator-agnostic BaseIngester interface.

    The ingester supports:
    - DLT extraction to Parquet
    - Automatic metadata column injection
    - Pandera schema validation
    - Strict validation with WAP pattern
    - Append and merge strategies
    - Telemetry and event emission

    Example:
        ```python
        from phlo_dlt.executor import DltIngester

        ingester = DltIngester(
            context=dagster_context,
            logger=structlog_logger,
            table_config=table_config,
            table_store_resource=iceberg_table_store,
            dlt_source_func=lambda partition_date: rest_api_source(...),
            validate=True,
            strict_validation=True,
            merge_strategy="merge",
        )
        ```

    """

    def __init__(
        self,
        context: Any,  # Can be generic context or specific object with .log/.run_id
        logger: Any,
        table_config: TableConfig,
        table_store_resource: TableStore,
        dlt_source_func: Callable[..., Any],
        validate: bool = True,
        strict_validation: bool = True,
        add_metadata_columns: bool = True,
        merge_strategy: str = "merge",
        merge_config: Dict[str, Any] | None = None,
        quality_checks: Sequence[Callable[[pd.DataFrame], str | None]] | None = None,
        validation_mode: ValidationMode = "materialized",
        validation_batch_size: int = 65_536,
        validation_sample_size: int = 1_000,
        interval_column: str | None = None,
        interval_lookback: timedelta = timedelta(0),
    ):
        """Store the ingester's collaborators and validation/merge options."""
        validate_runtime_options(
            merge_strategy=merge_strategy,
            unique_key=table_config.unique_key,
            merge_config=merge_config,
            validation_mode=validation_mode,
            validation_batch_size=validation_batch_size,
            validation_sample_size=validation_sample_size,
            interval_column=interval_column,
            interval_lookback=interval_lookback,
        )
        super().__init__(context, logger)
        self.table_config = table_config
        self.table_store = table_store_resource
        self.dlt_source_func = dlt_source_func
        self.validate = validate
        self.strict_validation = strict_validation
        self.add_metadata_columns = add_metadata_columns
        self.merge_strategy = merge_strategy
        self.merge_config = merge_config or {}
        self.quality_checks = tuple(quality_checks or ())
        self.validation_mode = validation_mode
        self.validation_batch_size = validation_batch_size
        self.validation_sample_size = validation_sample_size
        self.interval_column = interval_column
        self.interval_lookback = interval_lookback

    def _create_source(self, partition_key: str | None, routing: RuntimeRouting) -> Any:
        """Prepare legacy or stateless bounded extraction before any records are read."""
        if self.interval_column is None:
            source = self.dlt_source_func(partition_date=partition_key)
        else:
            interval = routing.data_interval
            if interval is None:
                raise ValueError("Interval ingestion requires a runtime data interval")
            if routing.interval_mode == "scheduled":
                interval = DataInterval(interval.start - self.interval_lookback, interval.end)
            source = apply_data_interval(
                self.dlt_source_func(data_interval=interval), self.interval_column, interval
            )
        return apply_validation_schema_hints(source, self.table_config.validation_schema)

    def _no_data_result(
        self,
        emitter: IngestionEventEmitter,
        attempt: int | None,
        context: IngestionEventContext,
        source_identity: str | None,
        correlation_error: str | None,
    ) -> DltIngestionResult:
        """Record empty-source evidence before returning without a write."""
        log_event(self.logger, "info", "ingestion_no_data", partition_key=context.partition_key)
        if attempt is not None:
            emit_lifecycle_safely(emitter, "emit_end", status="no_data", metrics={"rows_loaded": 0})
        if context.run_id and context.run_id != "unknown":
            emit_observation(
                project_id=context.project_id,
                run_id=context.run_id,
                attempt=attempt,
                correlation_error=correlation_error,
                observation_type="ingest",
                status="no_data",
                producer="phlo-dlt",
                resources=[
                    {
                        "resource_kind": "external_source",
                        "role": "input",
                        "normalized_identity": source_identity,
                        "resource_identity": _resource_identity(
                            project_id=context.project_id,
                            resource_type=(
                                "external_source" if source_identity else "ingestion_asset"
                            ),
                            resource_id=source_identity or context.table_name,
                            attributes={
                                "table_name": context.table_name,
                                "partition_key": context.partition_key or "unpartitioned",
                            },
                        ),
                        "ref_name": context.branch_name,
                        "metadata": {
                            "partition": {"status": "observed", "value": context.partition_key},
                            "watermark": {"status": "unavailable"},
                            "records_read": {"status": "observed", "value": 0},
                        },
                    }
                ],
                metrics={"records_read": 0},
                identity_parts=(context.table_name, context.partition_key, "no_data"),
            )
        schema = self.table_config.validation_schema
        checks = (
            (
                pandera_contract_asset_check_result(
                    PanderaContractEvaluation(True, 0, 0, []),
                    partition_key=context.partition_key,
                    asset_key=self.table_config.ingestion_asset_key,
                    schema_class=schema,
                    query_or_sql="status:no_data",
                    blocking=self.strict_validation,
                    validation_mode=self.validation_mode,
                ),
            )
            if self.validate and schema is not None
            else ()
        )
        return DltIngestionResult(
            status="no_data",
            rows_inserted=0,
            rows_deleted=0,
            metadata={"status": "no_data"},
            check_results=checks,
        )

    def _validate_staged_contract(
        self, parquet_paths: list[Path], partition_key: str | None
    ) -> tuple[CheckResult, ...]:
        """Validate staged schemas and domain rules before publication."""
        checks: list[CheckResult] = []
        schema = self.table_config.validation_schema
        query = ",".join(f"parquet://{path}" for path in parquet_paths)
        evaluation = None
        evaluation_error = None
        if self.validate and schema is not None:
            details = {
                "table_name": self.table_config.full_table_name,
                "partition_date": partition_key,
                "parquet_path": str(parquet_paths[0]),
                "parquet_path_count": len(parquet_paths),
            }
            log_event(self.logger, "info", "pandera_contract_evaluation_started", **details)
            try:
                evaluation = evaluate_pandera_contract_parquet_files(
                    parquet_paths,
                    schema_class=schema,
                    mode=self.validation_mode,
                    batch_size=self.validation_batch_size,
                    sample_size=self.validation_sample_size,
                )
            except Exception as exc:  # Report evaluator failures as failed checks.
                evaluation_error = exc
                evaluation = PanderaContractEvaluation(False, 1, 0, [{"error": str(exc)}], str(exc))
            log_event(
                self.logger,
                "info" if evaluation.passed else "warning",
                "pandera_contract_evaluation_passed"
                if evaluation.passed
                else "pandera_contract_evaluation_failed",
                **details,
                total_count=evaluation.total_count,
                failed_count=evaluation.failed_count,
                error=evaluation.error,
            )
            checks.append(
                pandera_contract_asset_check_result(
                    evaluation,
                    partition_key=partition_key,
                    asset_key=self.table_config.ingestion_asset_key,
                    schema_class=schema,
                    query_or_sql=query,
                    blocking=self.strict_validation or evaluation_error is not None,
                    validation_mode=self.validation_mode,
                )
            )
        domain = _evaluate_domain_quality_checks(
            parquet_paths=parquet_paths, quality_checks=self.quality_checks
        )
        for outcome in domain:
            checks.append(
                CheckResult(
                    passed=outcome.passed,
                    check_name=f"quality_{outcome.check_name}",
                    metadata={
                        "source": "domain",
                        "partition_key": partition_key,
                        "violation": outcome.violation,
                        "staged_parquet": query,
                    },
                    severity=None
                    if outcome.passed
                    else ("error" if self.strict_validation else "warn"),
                    asset_key=self.table_config.ingestion_asset_key,
                )
            )
            if not outcome.passed:
                log_event(
                    self.logger,
                    "warning",
                    "domain_quality_check_failed",
                    table_name=self.table_config.full_table_name,
                    check=outcome.check_name,
                    violation=outcome.violation,
                )
        results = tuple(checks)
        # An inability to evaluate is fatal even in warning mode, as before.
        if evaluation is not None and (
            evaluation_error is not None or (self.strict_validation and not evaluation.passed)
        ):
            raise PanderaContractValidationError(
                evaluation=evaluation, parquet_paths=tuple(parquet_paths), check_results=results
            ) from evaluation_error
        if self.strict_validation and any(not outcome.passed for outcome in domain):
            raise DomainQualityValidationError(
                evaluations=domain, parquet_paths=tuple(parquet_paths), check_results=results
            )
        return results

    def _source_column_coverage(self, parquet_paths: list[Path]) -> list[str]:
        """Report advisory source columns omitted by the configured contract."""
        schema = self.table_config.validation_schema
        if not self.validate or schema is None:
            return []
        try:
            dropped = detect_dropped_source_columns(parquet_paths, schema)
        except Exception as exc:  # Coverage is advisory, not a write gate.
            log_event(
                self.logger,
                "warning",
                "source_column_coverage_check_failed",
                table_name=self.table_config.full_table_name,
                error=str(exc),
            )
            return []
        if dropped:
            log_event(
                self.logger,
                "warning",
                "source_columns_dropped_by_contract",
                table_name=self.table_config.full_table_name,
                dropped_columns=dropped,
                hint="Add these columns to the validation_schema or remove them "
                "from the source; they will not be written to the table store.",
            )
        return dropped

    def _annotate_failure(
        self,
        exc: Exception,
        resources: list[dict[str, Any]],
        project_id: str | None,
        branch_name: str,
        safe_error: str,
    ) -> None:
        """Retain failed validation and ambiguous history-write evidence."""
        if isinstance(exc, HistoryWriteError):
            for resource in resources:
                if resource.get("role") == "output":
                    resource["metadata"].update(
                        outcome="unknown" if exc.reconciliation is not None else "failed",
                        reconciliation=exc.reconciliation,
                    )
        if isinstance(exc, DomainQualityValidationError):
            for evaluation in exc.evaluations:
                if evaluation.passed:
                    continue
                resources.append(
                    {
                        "resource_kind": "quality_check",
                        "role": "validation",
                        "resource_identity": _resource_identity(
                            project_id=project_id,
                            resource_type="quality_check",
                            resource_id=f"{self.table_config.full_table_name}:{evaluation.check_name}",
                            attributes={"check_name": evaluation.check_name},
                        ),
                        "ref_name": branch_name,
                        "metadata": {"status": "failed", "error": safe_error},
                    }
                )

    def _emit_ingestion_lineage(
        self,
        *,
        run_id: str,
        project_id: str | None,
        attempt: int | None,
        staged_objects: list[dict[str, Any]],
        source_identity: str | None,
        partition_key: str | None,
        execution_identity: str | None,
    ) -> None:
        """Emit exact lineage only when run correlation is complete."""
        if run_id == "unknown" or not project_id or attempt is None:
            return
        edges = [
            (str(item["identity"]), self.table_config.full_table_name) for item in staged_objects
        ]
        if source_identity:
            edges.extend((source_identity, str(item["identity"])) for item in staged_objects)
        emit_lifecycle_safely(
            LineageEventEmitter(
                LineageEventContext(
                    project_id=project_id,
                    run_id=run_id,
                    producer="phlo-dlt",
                    correlation=HookCorrelation(
                        project_id=project_id, run_id=run_id, attempt=attempt
                    ),
                )
            ),
            "emit_edges",
            edges=edges,
            metadata={"origin": "phlo-dlt", "derivation": "exact"},
            operation_id=f"{self.table_config.full_table_name}:{partition_key}:{execution_identity}",
        )

    def run_ingestion(
        self,
        partition_key: str | None,
        parameters: Dict[str, Any] | None = None,
    ) -> DltIngestionResult:
        """Run the full DLT -> Parquet -> table_store flow for one partition.

        `partition_key` is the partition date (YYYY-MM-DD); `parameters` may
        carry branch_name, target_branch_name, and run_id routing. Raises
        PanderaContractValidationError on strict validation failure and
        RuntimeError for other ingestion failures.

        Example:
            ```python
            result = ingester.run_ingestion(
                partition_key="2024-01-01",
                parameters={
                    "branch_name": "main",
                    "target_branch_name": "main",
                    "run_id": "dagster-run-123",
                }
            )
            print(f"Status: {result.status}, Rows: {result.rows_inserted}")
            ```

        """
        parameters = parameters or {}
        routing = routing_from_context(self.context)
        branch_name = parameters.get("branch_name", "main")
        target_branch_name = parameters.get("target_branch_name", branch_name)
        run_id = parameters.get("run_id") or routing.run_id or "unknown"
        project_id = parameters.get("project_id") or routing.project_id
        project_error = getattr(routing, "project_error", None)
        if project_error:
            project_id = None
        raw_attempt = parameters.get("attempt", routing.attempt)
        attempt = _ingestion_attempt(raw_attempt)
        correlation_attempt = attempt if attempt is not None else 1

        partition_date = partition_key or "unpartitioned"
        pipeline_identity = self.table_config.asset_key or self.table_config.table_name
        pipeline_name = f"{pipeline_identity}_{partition_date.replace('-', '_')}"
        group_name = self.table_config.group_name

        # Emission Setup
        event_context = IngestionEventContext(
            asset_key=self.table_config.ingestion_asset_key,
            table_name=self.table_config.full_table_name,
            group_name=group_name,
            partition_key=partition_key,
            project_id=project_id,
            run_id=run_id,
            branch_name=branch_name,
            catalog_system=parameters.get("catalog_system"),
            tags={"group": group_name, "source": "dlt"},
            correlation=HookCorrelation(
                run_id=run_id,
                project_id=project_id,
                attempt=correlation_attempt,
                asset_key=self.table_config.ingestion_asset_key,
                partition_key=partition_key,
                job_name=getattr(self.context, "job_name", None),
            ),
        )
        emitter = IngestionEventEmitter(event_context)
        telemetry = TelemetryEventEmitter(
            TelemetryEventContext(
                tags={
                    "asset": self.table_config.ingestion_asset_key,
                    "group": group_name,
                    "source": "dlt",
                },
                correlation=HookCorrelation(
                    run_id=run_id,
                    project_id=project_id,
                    attempt=correlation_attempt,
                    asset_key=self.table_config.ingestion_asset_key,
                    partition_key=partition_key,
                    job_name=getattr(self.context, "job_name", None),
                ),
            )
        )

        log_event(self.logger, "info", "starting_ingestion", partition_key=partition_key)
        start_time = time.time()
        evidence_resources: list[dict[str, Any]] = []
        source_identity: str | None = None
        if attempt is not None:
            emit_lifecycle_safely(emitter, "emit_start")

        try:
            dlt_source = self._create_source(partition_key, routing)
            source_identity = normalize_source_identity(
                dlt_source, parameters.get("source_identity")
            )

            if dlt_source is None:
                return self._no_data_result(
                    emitter, attempt, event_context, source_identity, project_error
                )

            staged = extract_to_parquet(
                dlt_source=dlt_source,
                pipeline_name=pipeline_name,
                log=self.logger,
                allow_empty=self.interval_column is not None,
            )
            parquet_paths = staged.parquet_paths
            dlt_elapsed = staged.elapsed_seconds

            if not parquet_paths:
                return self._no_data_result(
                    emitter, attempt, event_context, source_identity, project_error
                )

            if self.add_metadata_columns:
                for parquet_path in parquet_paths:
                    inject_metadata_columns(
                        parquet_path=parquet_path,
                        partition_date=partition_date,
                        run_id=run_id,
                        log=self.logger,
                    )

            # Inventory after Phlo metadata mutation, immediately before
            # validation/write, so evidence describes the loaded artifacts.
            staged_objects = staged_object_inventory(parquet_paths)
            known_record_counts = [item.get("record_count") for item in staged_objects]
            staged_record_count = (
                sum(value for value in known_record_counts if isinstance(value, int))
                if staged_objects and all(isinstance(value, int) for value in known_record_counts)
                else None
            )
            staged_byte_count = sum(
                item["byte_count"]
                for item in staged_objects
                if isinstance(item.get("byte_count"), int)
            )
            execution_identity, execution_identity_observed = dlt_execution_identity(
                staged.load_info, dlt_source, parameters, staged_objects
            )
            dlt_metrics = dlt_observed_metrics(staged.load_info)
            before_state = table_state(
                self.table_store, self.table_config.full_table_name, branch_name
            )
            evidence_resources = [
                {
                    "resource_kind": "external_source",
                    "role": "input",
                    "normalized_identity": source_identity,
                    "resource_identity": _resource_identity(
                        project_id=project_id,
                        resource_type=("external_source" if source_identity else "ingestion_asset"),
                        resource_id=source_identity or self.table_config.full_table_name,
                        attributes={
                            "table_name": self.table_config.full_table_name,
                            "partition_key": partition_date,
                        },
                    ),
                    "ref_name": branch_name,
                    "record_count": dlt_metrics.get("records_read"),
                    "byte_count": dlt_metrics.get("bytes_read"),
                    "metadata": {
                        "partition": {"status": "observed", "value": partition_key},
                        "watermark": {"status": "unavailable"},
                        "source_metrics": {
                            "status": "observed" if dlt_metrics else "unavailable",
                            **dlt_metrics,
                        },
                    },
                },
                {
                    "resource_kind": "staged_object",
                    "role": "staged",
                    "resource_identity": _resource_identity(
                        project_id=project_id,
                        resource_type="staged_object",
                        resource_id=execution_identity
                        or ",".join(
                            str(item["identity"])
                            for item in staged_objects
                            if isinstance(item.get("identity"), str)
                        )
                        or self.table_config.full_table_name,
                        attributes={
                            "table_name": self.table_config.full_table_name,
                            "partition_key": partition_date,
                        },
                    ),
                    "ref_name": branch_name,
                    "staged_objects": staged_objects,
                    "record_count": staged_record_count,
                    "byte_count": staged_byte_count,
                    "metadata": {
                        "inventory": {"status": "observed"},
                        "record_count": {
                            "status": "observed" if staged_record_count is not None else "partial"
                        },
                    },
                },
            ]

            check_results = self._validate_staged_contract(parquet_paths, partition_key)
            dropped_source_columns = self._source_column_coverage(parquet_paths)

            output_resource: dict[str, Any] = {
                "resource_kind": "iceberg_table",
                "role": "output",
                "table_name": self.table_config.full_table_name,
                "resource_identity": _resource_identity(
                    project_id=project_id,
                    resource_type="iceberg_table",
                    resource_id=self.table_config.full_table_name,
                    attributes={"catalog_ref": branch_name},
                ),
                "ref_name": branch_name,
                "schema_hash": before_state["schema_hash"],
                "schema_hash_before": before_state["schema_hash"],
                "schema_hash_after": None,
                "snapshot_before": before_state["snapshot_id"],
                "snapshot_after": None,
                "metadata": {
                    "before": before_state["metadata"],
                    "after": {"state": "unavailable"},
                    "outcome": "unknown",
                    "evidence_completeness": "incomplete",
                },
            }
            evidence_resources.append(output_resource)

            merge_metrics = merge_to_table_store(
                log=self.logger,
                table_store=self.table_store,
                table_config=self.table_config,
                parquet_paths=parquet_paths,
                branch_name=branch_name,
                merge_strategy=self.merge_strategy,
                merge_config=self.merge_config,
            )

            after_state = table_state(
                self.table_store, self.table_config.full_table_name, branch_name
            )
            contradictory_readback = after_state["state"] == "absent"
            output_resource.update(
                schema_hash=after_state["schema_hash"],
                schema_hash_after=after_state["schema_hash"],
                snapshot_after=after_state["snapshot_id"],
                record_count=merge_metrics.get("rows_inserted"),
                metadata={
                    "before": before_state["metadata"],
                    "after": after_state["metadata"],
                    "state": after_state["state"],
                    "outcome": "contradictory" if contradictory_readback else "success",
                    "evidence_completeness": (
                        "incomplete"
                        if before_state["state"] == "unavailable"
                        or after_state["state"] == "unavailable"
                        or contradictory_readback
                        else "complete"
                    ),
                },
            )

            total_elapsed = time.time() - start_time
            log_event(
                self.logger,
                "info",
                "ingestion_completed",
                partition_key=partition_key,
                total_elapsed_seconds=total_elapsed,
            )

            if attempt is not None:
                emit_lifecycle_safely(
                    emitter,
                    "emit_end",
                    status="success",
                    metrics={
                        **merge_metrics,
                        "dlt_elapsed_seconds": dlt_elapsed,
                        "total_elapsed_seconds": total_elapsed,
                        "target_branch_name": target_branch_name,
                    },
                )
            if run_id != "unknown":
                emit_observation(
                    project_id=project_id,
                    run_id=run_id,
                    attempt=attempt,
                    correlation_error=project_error,
                    observation_type="ingest",
                    status="success",
                    producer="phlo-dlt",
                    resources=evidence_resources,
                    artifacts=[
                        {
                            "artifact_id": f"{attempt}:{item['identity']}",
                            "artifact_kind": "staged_parquet",
                            "content_type": "application/vnd.apache.parquet",
                            "checksum": item["checksum"],
                            # Local staging paths are transient; retain the observed
                            # content identity without promising a download location.
                            "resource_identity": _resource_identity(
                                project_id=project_id,
                                resource_type="artifact",
                                resource_id=item["identity"],
                                attributes={},
                            ),
                        }
                        for item in staged_objects
                    ],
                    metrics={**merge_metrics, **dlt_metrics, "dlt_elapsed_seconds": dlt_elapsed},
                    event_id=parameters.get("evidence_event_id"),
                    identity_parts=(
                        self.table_config.full_table_name,
                        partition_key,
                        execution_identity,
                        tuple(item.get("checksum") for item in staged_objects),
                    ),
                )

            self._emit_ingestion_lineage(
                run_id=run_id,
                project_id=project_id,
                attempt=attempt,
                staged_objects=staged_objects,
                source_identity=source_identity,
                partition_key=partition_key,
                execution_identity=execution_identity,
            )

            return DltIngestionResult(
                status="success",
                rows_inserted=merge_metrics["rows_inserted"],
                rows_deleted=merge_metrics.get("rows_deleted", 0),
                check_results=check_results,
                metadata={
                    **merge_metrics,
                    "snapshot_id": after_state["snapshot_id"],
                    "dlt_elapsed_seconds": dlt_elapsed,
                    "parquet_path": str(parquet_paths[0]),
                    "parquet_paths": [str(parquet_path) for parquet_path in parquet_paths],
                    "dropped_source_columns": dropped_source_columns,
                    "total_elapsed_seconds": total_elapsed,
                    "target_branch_name": target_branch_name,
                    "evidence_execution_id": execution_identity,
                    "evidence_execution_id_observed": execution_identity_observed,
                },
            )

        except Exception as exc:
            total_elapsed = time.time() - start_time
            safe_error = safe_error_summary(exc)
            failure_metrics = exc.metrics if isinstance(exc, HistoryWriteError) else {}
            self._annotate_failure(exc, evidence_resources, project_id, branch_name, safe_error)
            if attempt is not None:
                emit_lifecycle_safely(
                    emitter,
                    "emit_end",
                    status="failure",
                    metrics={**failure_metrics, "total_elapsed_seconds": total_elapsed},
                    error=safe_error,
                )
            if run_id != "unknown":
                emit_observation(
                    project_id=project_id,
                    run_id=run_id,
                    attempt=attempt,
                    correlation_error=project_error,
                    observation_type="ingest",
                    status="failed",
                    producer="phlo-dlt",
                    resources=evidence_resources,
                    metrics=failure_metrics,
                    error=safe_error,
                    event_id=parameters.get("evidence_event_id"),
                    identity_parts=(
                        self.table_config.full_table_name,
                        partition_key,
                        locals().get("execution_identity"),
                        "failed",
                    ),
                )
            if attempt is not None:
                emit_lifecycle_safely(
                    telemetry,
                    "emit_log",
                    name="ingestion.failure",
                    level="error",
                    payload={"error": safe_error, "elapsed_seconds": total_elapsed},
                )
            raise
