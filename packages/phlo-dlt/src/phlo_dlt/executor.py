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
        validation_schema=UserSchema,
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Sequence

import pandas as pd

from phlo.logging import log_event
from phlo.operations.ingestion import BaseIngester, IngestionResult
from phlo.hooks import (
    HookCorrelation,
    IngestionEventContext,
    IngestionEventEmitter,
    TelemetryEventContext,
    TelemetryEventEmitter,
)
from phlo._attempt import normalize_attempt
from phlo.run_evidence import emit_lifecycle_safely, emit_observation
from phlo.run_evidence.redaction import safe_error_summary
from phlo.capabilities.interfaces import TableStore
from phlo.capabilities.runtime import routing_from_context

from phlo_dlt.dlt_helpers import (
    inject_metadata_columns,
    merge_to_table_store,
    setup_dlt_pipeline,
    stage_to_parquet,
)
from phlo_dlt.evidence import (
    dlt_execution_identity,
    dlt_observed_metrics,
    normalize_source_identity,
    staged_object_inventory,
    table_state,
)
from phlo_dlt.pandera_checks import (
    PanderaContractValidationError,
    evaluate_pandera_contract_parquet_files,
    serialize_pandera_contract_evaluation,
)
from phlo_dlt.registry import TableConfig


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
    ) -> None:
        """Store failed check evidence for the decorator's check-result surface."""
        self.evaluations = evaluations
        self.parquet_paths = parquet_paths
        failed = next(evaluation for evaluation in evaluations if not evaluation.passed)
        super().__init__(f"Domain quality check failed: {failed.check_name}: {failed.violation}")


def _evaluate_domain_quality_checks(
    *,
    parquet_paths: Sequence[Path],
    quality_checks: Sequence[Callable[[pd.DataFrame], str | None]],
) -> tuple[DomainQualityEvaluation, ...]:
    """Evaluate domain checks against staged rows before the table-store write."""
    evaluations: list[DomainQualityEvaluation] = []
    for index, quality_check in enumerate(quality_checks):
        check_name = getattr(quality_check, "__name__", None) or f"quality_{index}"
        if not parquet_paths:
            violation = "no staged parquet available for domain checks"
        else:
            try:
                staged_frame = pd.concat(
                    [pd.read_parquet(parquet_path) for parquet_path in parquet_paths],
                    ignore_index=True,
                )
                violation = quality_check(staged_frame)
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
            validation_schema=UserSchema,
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
        validation_schema: type[Any] | None = None,
        validate: bool = True,
        strict_validation: bool = True,
        add_metadata_columns: bool = True,
        merge_strategy: str = "merge",
        merge_config: Dict[str, Any] | None = None,
        quality_checks: Sequence[Callable[[pd.DataFrame], str | None]] | None = None,
    ):
        """Store the ingester's collaborators and validation/merge options."""
        super().__init__(context, logger)
        self.table_config = table_config
        self.table_store = table_store_resource
        self.dlt_source_func = dlt_source_func
        self.validation_schema = validation_schema
        self.validate = validate
        self.strict_validation = strict_validation
        self.add_metadata_columns = add_metadata_columns
        self.merge_strategy = merge_strategy
        self.merge_config = merge_config or {}
        self.quality_checks = tuple(quality_checks or ())

    def run_ingestion(
        self,
        partition_key: str | None,
        parameters: Dict[str, Any] | None = None,
    ) -> IngestionResult:
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
        try:
            attempt = normalize_attempt(raw_attempt)
        except ValueError:
            attempt = None
        correlation_attempt = attempt if attempt is not None else 1

        partition_date = partition_key or "unpartitioned"
        pipeline_name = f"{self.table_config.table_name}_{partition_date.replace('-', '_')}"
        group_name = self.table_config.group_name

        # Emission Setup
        emitter = IngestionEventEmitter(
            IngestionEventContext(
                asset_key=f"dlt_{self.table_config.table_name}",
                table_name=self.table_config.full_table_name,
                group_name=group_name,
                partition_key=partition_key,
                project_id=project_id,
                run_id=run_id,
                branch_name=branch_name,
                tags={"group": group_name, "source": "dlt"},
                correlation=HookCorrelation(
                    run_id=run_id,
                    project_id=project_id,
                    attempt=correlation_attempt,
                    asset_key=f"dlt_{self.table_config.table_name}",
                    partition_key=partition_key,
                    job_name=getattr(self.context, "job_name", None),
                ),
            )
        )
        telemetry = TelemetryEventEmitter(
            TelemetryEventContext(
                tags={
                    "asset": f"dlt_{self.table_config.table_name}",
                    "group": group_name,
                    "source": "dlt",
                },
                correlation=HookCorrelation(
                    run_id=run_id,
                    project_id=project_id,
                    attempt=correlation_attempt,
                    asset_key=f"dlt_{self.table_config.table_name}",
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
            dlt_source = self.dlt_source_func(partition_date=partition_key)
            source_identity = normalize_source_identity(
                dlt_source, parameters.get("source_identity")
            )

            if dlt_source is None:
                log_event(self.logger, "info", "ingestion_no_data", partition_key=partition_key)
                if attempt is not None:
                    emit_lifecycle_safely(
                        emitter, "emit_end", status="no_data", metrics={"rows_loaded": 0}
                    )
                if run_id != "unknown":
                    emit_observation(
                        project_id=project_id,
                        run_id=run_id,
                        attempt=attempt,
                        correlation_error=project_error,
                        observation_type="ingest",
                        status="no_data",
                        producer="phlo-dlt",
                        resources=[
                            {
                                "resource_kind": "external_source",
                                "role": "input",
                                "normalized_identity": source_identity,
                                "resource_identity": _resource_identity(
                                    project_id=project_id,
                                    resource_type=(
                                        "external_source" if source_identity else "ingestion_asset"
                                    ),
                                    resource_id=source_identity
                                    or self.table_config.full_table_name,
                                    attributes={
                                        "table_name": self.table_config.full_table_name,
                                        "partition_key": partition_date,
                                    },
                                ),
                                "ref_name": branch_name,
                                "metadata": {
                                    "partition": {"status": "observed", "value": partition_key},
                                    "watermark": {"status": "unavailable"},
                                    "records_read": {"status": "observed", "value": 0},
                                },
                            }
                        ],
                        metrics={"records_read": 0},
                        identity_parts=(
                            self.table_config.full_table_name,
                            partition_key,
                            "no_data",
                        ),
                    )
                return IngestionResult(
                    status="no_data",
                    rows_inserted=0,
                    rows_deleted=0,
                    metadata={"status": "no_data"},
                )

            pipeline, local_staging_root = setup_dlt_pipeline(
                pipeline_name=pipeline_name,
                # DLT's filesystem destination uses dataset_name as a directory.
                # A Dagster group can contain several assets, so it cannot also
                # identify a pipeline's staging area.
                dataset_name=pipeline_name,
            )

            # dlt_helpers log through a context object exposing ``.log``;
            # this shim adapts the plain logger to that contract.

            class ContextShim:
                """Expose a `.log` attribute expected by DLT helpers."""

                def __init__(self, logger):
                    """Store logger on `.log` to match helper context contract."""
                    self.log = logger

            shim = ContextShim(self.logger)

            parquet_paths, dlt_elapsed = stage_to_parquet(
                context=shim,
                pipeline=pipeline,
                dlt_source=dlt_source,
                local_staging_root=local_staging_root,
            )

            if self.add_metadata_columns:
                for parquet_path in parquet_paths:
                    inject_metadata_columns(
                        parquet_path=parquet_path,
                        partition_date=partition_date,
                        run_id=run_id,
                        context=shim,
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
                pipeline, dlt_source, parameters, staged_objects
            )
            dlt_metrics = dlt_observed_metrics(pipeline)
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

            evaluation_metadata: dict[str, Any] | None = None
            if self.validate and self.validation_schema is not None:
                evaluation = evaluate_pandera_contract_parquet_files(
                    parquet_paths,
                    schema_class=self.validation_schema,
                )
                evaluation_metadata = serialize_pandera_contract_evaluation(evaluation)
                if self.strict_validation and not evaluation.passed:
                    raise PanderaContractValidationError(
                        evaluation=evaluation,
                        parquet_paths=tuple(parquet_paths),
                    )

            domain_quality_evaluations = _evaluate_domain_quality_checks(
                parquet_paths=parquet_paths,
                quality_checks=self.quality_checks,
            )
            if self.strict_validation and any(
                not evaluation.passed for evaluation in domain_quality_evaluations
            ):
                raise DomainQualityValidationError(
                    evaluations=domain_quality_evaluations,
                    parquet_paths=tuple(parquet_paths),
                )

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
                context=shim,
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
                        "rows_inserted": merge_metrics["rows_inserted"],
                        "rows_deleted": merge_metrics.get("rows_deleted", 0),
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
                    metrics={**merge_metrics, **dlt_metrics, "dlt_elapsed_seconds": dlt_elapsed},
                    event_id=parameters.get("evidence_event_id"),
                    identity_parts=(
                        self.table_config.full_table_name,
                        partition_key,
                        execution_identity,
                        tuple(item.get("checksum") for item in staged_objects),
                    ),
                )

            return IngestionResult(
                status="success",
                rows_inserted=merge_metrics["rows_inserted"],
                rows_deleted=merge_metrics.get("rows_deleted", 0),
                metadata={
                    "dlt_elapsed_seconds": dlt_elapsed,
                    "parquet_path": str(parquet_paths[0]),
                    "parquet_paths": [str(parquet_path) for parquet_path in parquet_paths],
                    "pandera_evaluation": evaluation_metadata,
                    "domain_quality_evaluations": [
                        {
                            "check_name": evaluation.check_name,
                            "violation": evaluation.violation,
                        }
                        for evaluation in domain_quality_evaluations
                    ],
                    "total_elapsed_seconds": total_elapsed,
                    "target_branch_name": target_branch_name,
                    "evidence_execution_id": execution_identity,
                    "evidence_execution_id_observed": execution_identity_observed,
                },
            )

        except Exception as exc:
            total_elapsed = time.time() - start_time
            safe_error = safe_error_summary(exc)
            if isinstance(exc, DomainQualityValidationError):
                for evaluation in exc.evaluations:
                    if evaluation.passed:
                        continue
                    evidence_resources.append(
                        {
                            "resource_kind": "quality_check",
                            "role": "validation",
                            "resource_identity": _resource_identity(
                                project_id=project_id,
                                resource_type="quality_check",
                                resource_id=(
                                    f"{self.table_config.full_table_name}:{evaluation.check_name}"
                                ),
                                attributes={"check_name": evaluation.check_name},
                            ),
                            "ref_name": branch_name,
                            "metadata": {
                                "status": "failed",
                                "error": safe_error,
                            },
                        }
                    )
            if attempt is not None:
                emit_lifecycle_safely(
                    emitter,
                    "emit_end",
                    status="failure",
                    metrics={"total_elapsed_seconds": total_elapsed},
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
