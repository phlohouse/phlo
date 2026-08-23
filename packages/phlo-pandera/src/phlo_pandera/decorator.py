"""
@phlo_pandera decorator for declarative quality checks.

This decorator reduces quality check boilerplate from 30-40 lines to 5-10 lines
by automatically generating Dagster asset checks from declarative quality check definitions.
"""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

from phlo.capabilities import (
    AssetCheckSpec,
    CheckResult,
    get_capability_registry,
    register_capability,
)
from phlo.capabilities.runtime import RuntimeContext
from phlo.contracts import Consumer, SLA, normalize_consumers, serialize_sla

from phlo_pandera.checks import QualityCheck, QualityCheckResult
from phlo_pandera.checks_extra import SchemaCheck
from phlo_pandera.contract import PANDERA_CONTRACT_CHECK_NAME, QualityCheckContract
from phlo_pandera.decorator_helpers import (
    _build_metadata,
    _collect_failure_sample,
    _contract_metadata,
    _estimate_failed_count,
    _load_data,
    _make_emitters,
    _repro_sql,
)
from phlo_pandera.partitioning import PartitionScope, apply_partition_scope, get_partition_key
from phlo_pandera.severity import severity_for_pandera_contract, severity_for_quality_check


def phlo_pandera(
    table: str,
    checks: List[QualityCheck],
    asset_key: Optional[str] = None,
    group: Optional[str] = None,
    blocking: bool = True,
    partition_aware: bool = True,
    warn_threshold: float = 0.0,
    partition_column: str = "_phlo_partition_date",
    rolling_window_days: int | None = 7,
    full_table: bool = False,
    description: Optional[str] = None,
    query: Optional[str] = None,
    backend: str = "trino",
    owner: str | None = None,
    consumers: list[Consumer | str] | None = None,
    sla: SLA | None = None,
) -> Callable:
    """Generate Dagster asset checks from declarative quality check definitions.

    The decorated asset function is returned unchanged after its checks are
    registered; ``table`` scopes the queries and every other option tunes
    scoping (partition column, rolling window, full table), severity
    (``warn_threshold``, ``blocking``), or contract metadata (owner,
    consumers, SLA). The query defaults to ``SELECT * FROM {table}`` on the
    given backend ("trino" or "duckdb").

    """
    normalized_consumers = normalize_consumers(consumers)
    serialized_sla = serialize_sla(sla)
    contract_tags: dict[str, str] = {}
    if owner:
        contract_tags["contract_owner"] = owner
    if normalized_consumers:
        contract_tags["contract_consumers"] = ",".join(c.name for c in normalized_consumers)
    if serialized_sla:
        contract_tags["contract_sla"] = json.dumps(serialized_sla, sort_keys=True)

    def decorator(func: Callable) -> Callable:
        """Register the declared checks for this asset function and return it unchanged."""
        nonlocal asset_key, description, full_table

        # Without partition awareness there is no partition filter to apply,
        # so validation always runs against the full table.
        if not partition_aware:
            full_table = True

        if asset_key is None:
            # Extract table name from fully qualified name
            # e.g., "bronze.weather_observations" -> "weather_observations"
            table_parts = table.split(".")
            asset_key = table_parts[-1]

        # Auto-generate description if not provided
        if description is None:
            check_names = [check.name for check in checks]
            description = f"Quality checks for {table}: {', '.join(check_names[:3])}" + (
                "..." if len(check_names) > 3 else ""
            )

        # Build the SQL query
        default_query = f"SELECT * FROM {table}"
        sql_query = query or default_query

        schema_checks = [check for check in checks if isinstance(check, SchemaCheck)]
        non_schema_checks = [check for check in checks if not isinstance(check, SchemaCheck)]

        assert asset_key is not None
        asset_key_value = asset_key

        if schema_checks:

            def pandera_contract_check(runtime: RuntimeContext) -> CheckResult:
                """Run schema-based checks against the scoped partition and emit a CheckResult."""
                partition_key = get_partition_key(runtime)
                partition_key_value = str(partition_key) if partition_key else None
                emitter, telemetry = _make_emitters(
                    runtime, asset_key_value, partition_key_value, "pandera", backend
                )
                scope = PartitionScope(
                    partition_key=partition_key,
                    partition_column=partition_column,
                    rolling_window_days=rolling_window_days,
                    full_table=full_table,
                )
                final_query = apply_partition_scope(sql_query, scope=scope)
                if partition_key and not full_table:
                    runtime.logger.info("validating_partition", partition_key=partition_key)

                try:
                    df = _load_data(runtime, final_query, backend)
                except Exception as exc:
                    runtime.logger.error(
                        "quality_data_load_failed",
                        table=table,
                        partition_key=partition_key_value,
                        backend=backend,
                        error=str(exc),
                        exc_info=True,
                    )
                    telemetry.emit_log(
                        name="quality.query_failed",
                        level="error",
                        payload={"error": str(exc), "table": table},
                    )
                    contract = QualityCheckContract(
                        source="pandera",
                        partition_key=partition_key_value,
                        failed_count=1,
                        total_count=None,
                        query_or_sql=final_query,
                        repro_sql=_repro_sql(final_query),
                        sample=[{"error": str(exc)}],
                    )
                    event_metadata = _contract_metadata(contract)
                    event_metadata.update(
                        {"reason": "query_failed", "error": str(exc), "table": table}
                    )
                    emitter.emit_result(
                        check_name=PANDERA_CONTRACT_CHECK_NAME,
                        passed=False,
                        severity="error",
                        check_type="pandera",
                        metadata=event_metadata,
                    )
                    return CheckResult(
                        passed=False,
                        check_name=PANDERA_CONTRACT_CHECK_NAME,
                        asset_key=asset_key_value,
                        severity="error",
                        metadata={
                            **contract.to_metadata(),
                            "reason": "query_failed",
                            "error": str(exc),
                        },
                    )

                # An empty frame contains no violations, so the contract
                # passes; the no_data note explains why total_count is zero.
                if df.empty:
                    contract = QualityCheckContract(
                        source="pandera",
                        partition_key=partition_key_value,
                        failed_count=0,
                        total_count=0,
                        query_or_sql=final_query,
                        repro_sql=_repro_sql(final_query),
                        sample=[],
                    )
                    event_metadata = _contract_metadata(contract)
                    event_metadata["note"] = "no_data"
                    event_metadata["table"] = table
                    emitter.emit_result(
                        check_name=PANDERA_CONTRACT_CHECK_NAME,
                        passed=True,
                        check_type="pandera",
                        metadata=event_metadata,
                    )
                    telemetry.emit_metric(
                        name="quality.rows_validated",
                        value=0,
                        unit="rows",
                        payload={"status": "no_data", "table": table},
                    )
                    return CheckResult(
                        passed=True,
                        check_name=PANDERA_CONTRACT_CHECK_NAME,
                        asset_key=asset_key_value,
                        metadata={
                            **contract.to_metadata(),
                            "note": "No data available for validation",
                        },
                    )

                failures: list[Any] = []
                failed_count = 0
                schema_names: list[str] = []
                all_passed = True

                for check in schema_checks:
                    schema_names.append(getattr(check.schema, "__name__", str(type(check.schema))))
                    result = check.execute(df, runtime)
                    if not result.passed:
                        all_passed = False
                        failed_checks = int(
                            result.metadata.get("failed_checks", 0) if result.metadata else 0
                        )
                        failed_count += failed_checks
                        if result.metadata and "sample_failures" in result.metadata:
                            failures.extend(result.metadata["sample_failures"])

                contract = QualityCheckContract(
                    source="pandera",
                    partition_key=partition_key_value,
                    failed_count=failed_count,
                    total_count=len(df),
                    query_or_sql=final_query,
                    repro_sql=_repro_sql(final_query),
                    sample=failures,
                )
                severity = severity_for_pandera_contract(passed=all_passed)
                event_metadata = _contract_metadata(contract)
                event_metadata["schemas"] = schema_names
                event_metadata["table"] = table
                emitter.emit_result(
                    check_name=PANDERA_CONTRACT_CHECK_NAME,
                    passed=all_passed,
                    severity=severity,
                    check_type="pandera",
                    metadata=event_metadata,
                )
                telemetry.emit_metric(
                    name="quality.rows_validated",
                    value=len(df),
                    unit="rows",
                    payload={"table": table},
                )
                telemetry.emit_metric(
                    name="quality.failed_count",
                    value=failed_count,
                    unit="checks",
                    payload={"table": table},
                )
                telemetry.emit_metric(
                    name="quality.schemas_checked",
                    value=len(schema_names),
                    unit="schemas",
                    payload={"table": table},
                )
                return CheckResult(
                    passed=all_passed,
                    check_name=PANDERA_CONTRACT_CHECK_NAME,
                    asset_key=asset_key_value,
                    severity=severity,
                    metadata={**contract.to_metadata(), "schemas": schema_names},
                )

            pandera_spec = AssetCheckSpec(
                name=PANDERA_CONTRACT_CHECK_NAME,
                asset_key=asset_key_value,
                fn=pandera_contract_check,
                blocking=True,
                description=f"Pandera schema contract for {table}",
                tags=dict(contract_tags),
            )
            register_capability("check", pandera_spec)

        if non_schema_checks:

            def quality_check_wrapper(runtime: RuntimeContext) -> CheckResult:
                """Run non-schema checks against the scoped partition, aggregating metadata."""
                partition_key = get_partition_key(runtime)
                partition_key_value = str(partition_key) if partition_key else None
                emitter, telemetry = _make_emitters(
                    runtime, asset_key_value, partition_key_value, "phlo", backend
                )
                scope = PartitionScope(
                    partition_key=partition_key,
                    partition_column=partition_column,
                    rolling_window_days=rolling_window_days,
                    full_table=full_table,
                )
                final_query = apply_partition_scope(sql_query, scope=scope)
                if partition_key and not full_table:
                    runtime.logger.info("validating_partition", partition_key=partition_key)

                try:
                    df = _load_data(runtime, final_query, backend)
                except Exception as exc:
                    runtime.logger.error(
                        "quality_data_load_failed",
                        table=table,
                        partition_key=partition_key_value,
                        backend=backend,
                        error=str(exc),
                        exc_info=True,
                    )
                    telemetry.emit_log(
                        name="quality.query_failed",
                        level="error",
                        payload={"error": str(exc), "table": table},
                    )
                    emitter.emit_result(
                        check_name=getattr(func, "__name__", "quality_check"),
                        passed=False,
                        severity="error",
                        check_type="phlo",
                        metadata={
                            "reason": "query_failed",
                            "error": str(exc),
                            "query_or_sql": final_query,
                            "table": table,
                        },
                    )
                    return CheckResult(
                        passed=False,
                        check_name=getattr(func, "__name__", "quality_check"),
                        asset_key=asset_key_value,
                        severity="error",
                        metadata={
                            "reason": "query_failed",
                            "error": str(exc),
                            "query": final_query,
                        },
                    )

                # No rows means nothing to violate: report a pass so an empty
                # partition does not block downstream assets.
                if df.empty:
                    runtime.logger.warning("No rows returned; marking check as skipped.")
                    emitter.emit_result(
                        check_name=getattr(func, "__name__", "quality_check"),
                        passed=True,
                        check_type="phlo",
                        metadata={
                            "note": "no_data",
                            "query_or_sql": final_query,
                            "table": table,
                        },
                    )
                    telemetry.emit_metric(
                        name="quality.rows_validated",
                        value=0,
                        unit="rows",
                        payload={"status": "no_data", "table": table},
                    )
                    return CheckResult(
                        passed=True,
                        check_name=getattr(func, "__name__", "quality_check"),
                        asset_key=asset_key_value,
                        metadata={
                            "rows_validated": 0,
                            "note": "No data available for validation",
                        },
                    )

                runtime.logger.info(
                    "executing_quality_checks",
                    check_count=len(non_schema_checks),
                    row_count=len(df),
                )

                check_results: List[QualityCheckResult] = []
                all_passed = True

                for check in non_schema_checks:
                    try:
                        result = check.execute(df, runtime)
                        check_results.append(result)

                        if not result.passed:
                            all_passed = False
                            runtime.logger.warning(
                                "quality_check_failed",
                                check_name=check.name,
                                failure_message=result.failure_message,
                            )
                        else:
                            runtime.logger.info("quality_check_passed", check_name=check.name)
                    except Exception as exc:
                        runtime.logger.exception(
                            "quality_check_execution_error",
                            check_name=check.name,
                            error=str(exc),
                        )
                        check_results.append(
                            QualityCheckResult(
                                passed=False,
                                metric_name=check.name,
                                metric_value=None,
                                metadata={"error": str(exc)},
                                failure_message=f"Check execution failed: {exc}",
                            )
                        )
                        all_passed = False

                metadata = _build_metadata(df, check_results)
                contract = QualityCheckContract(
                    source="phlo",
                    partition_key=str(partition_key) if partition_key else None,
                    failed_count=_estimate_failed_count(check_results),
                    total_count=len(df),
                    query_or_sql=final_query,
                    repro_sql=_repro_sql(final_query),
                    sample=_collect_failure_sample(check_results),
                )
                metadata.update(contract.to_metadata())

                passed_count = sum(1 for r in check_results if r.passed)
                failed_count = sum(1 for r in check_results if not r.passed)
                failure_fraction = failed_count / len(check_results) if check_results else 0.0

                summary = f"{passed_count}/{len(check_results)} quality checks passed"
                if failed_count > 0:
                    failed_checks = [
                        f"{r.metric_name}: {r.failure_message}"
                        for r in check_results
                        if not r.passed
                    ]
                    metadata["failures"] = "## Failed Checks\n\n" + "\n".join(
                        f"- {f}" for f in failed_checks
                    )
                metadata["summary"] = summary

                severity = severity_for_quality_check(
                    passed=all_passed,
                    failure_fraction=failure_fraction,
                    warn_threshold=warn_threshold,
                )
                if severity == "warn":
                    runtime.logger.warning(
                        "quality_check_warning_threshold",
                        failure_fraction=failure_fraction,
                        warn_threshold=warn_threshold,
                    )

                # Emit one event per individual check so the UI can render
                # per-check outcomes; the aggregate verdict is the CheckResult
                # returned below.
                severity_label = severity if not all_passed else None
                for check, result in zip(non_schema_checks, check_results):
                    failed_count = _estimate_failed_count([result])
                    contract = QualityCheckContract(
                        source="phlo",
                        partition_key=partition_key_value,
                        failed_count=failed_count,
                        total_count=len(df),
                        query_or_sql=final_query,
                        repro_sql=_repro_sql(final_query),
                        sample=_collect_failure_sample([result]),
                    )
                    event_metadata = _contract_metadata(contract)
                    event_metadata["table"] = table
                    if result.metric_value is not None:
                        event_metadata["metric_value"] = result.metric_value
                    if result.failure_message:
                        event_metadata["failure_message"] = result.failure_message
                    if result.metadata:
                        event_metadata.update(result.metadata)
                    emitter.emit_result(
                        check_name=result.metric_name,
                        passed=result.passed,
                        severity=severity_label if not result.passed else None,
                        check_type=type(check).__name__,
                        metadata=event_metadata,
                    )

                telemetry.emit_metric(
                    name="quality.rows_validated",
                    value=len(df),
                    unit="rows",
                    payload={"table": table},
                )
                telemetry.emit_metric(
                    name="quality.checks_total",
                    value=len(check_results),
                    unit="checks",
                    payload={"table": table},
                )
                telemetry.emit_metric(
                    name="quality.checks_failed",
                    value=failed_count,
                    unit="checks",
                    payload={"table": table},
                )
                telemetry.emit_metric(
                    name="quality.failure_fraction",
                    value=failure_fraction,
                    unit="ratio",
                    payload={"table": table},
                )

                return CheckResult(
                    passed=all_passed,
                    check_name=getattr(func, "__name__", "quality_check"),
                    asset_key=asset_key_value,
                    severity=severity,
                    metadata=metadata,
                )

            quality_spec = AssetCheckSpec(
                name=getattr(func, "__name__", "quality_check"),
                asset_key=asset_key_value,
                fn=quality_check_wrapper,
                blocking=blocking,
                description=description,
                tags=dict(contract_tags),
            )
            register_capability("check", quality_spec)

        if schema_checks or non_schema_checks:
            return func

        raise ValueError("phlo_pandera requires at least one check")

    return decorator


def get_quality_checks() -> list[AssetCheckSpec]:
    """List all asset check specs registered through @phlo_pandera."""
    registry = get_capability_registry()
    return registry.list("check")


def clear_quality_checks() -> None:
    """Drop all registered quality check specs; mainly useful in tests."""
    registry = get_capability_registry()
    registry.clear("check")
