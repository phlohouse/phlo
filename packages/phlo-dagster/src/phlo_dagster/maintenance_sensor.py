"""Dagster sensor and ops for policy-driven Iceberg table maintenance.

This module implements automated Iceberg table maintenance through Dagster
sensors that evaluate table statistics against configured policies and
trigger maintenance operations when thresholds are exceeded.

Maintenance Operations:
    - Snapshot expiration: Remove old snapshots beyond retention policy
    - File optimization: Compact small files via Trino OPTIMIZE
    - Statistics collection: Gather table metadata for policy evaluation

Policy-Driven Automation:
    The maintenance_policy_sensor continuously evaluates tables against
    NamespacePolicy configurations loaded from YAML files. When thresholds
    are exceeded (e.g., snapshot count > 20), the sensor triggers appropriate
    maintenance jobs.

Integration Requirements:
    - phlo-iceberg: For table statistics and maintenance operations
    - phlo-trino: For OPTIMIZE command execution
    - maintenance_policy.yaml: Policy configuration file

Configuration File Format:
    policies:
      - namespace: raw
        expire:
          snapshot_count_gt: 20
          older_than_days: 7
          retain_last: 5
        optimize:
          avg_file_size_mb_lt: 64.0
      - namespace: curated
        ref: main

Example:
    Including policy maintenance in definitions::

        from phlo_dagster.maintenance_sensor import get_policy_maintenance_definitions

        policy_defs = get_policy_maintenance_definitions()
        defs = dg.Definitions.merge(your_defs, policy_defs)

"""

import os
import re
import time
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

import dagster as dg

from phlo.capabilities import (
    MaintenanceExecutor,
    MaintenanceTableStore,
    MaintenanceOperationResult,
    MaintenanceOperationState,
    QueryEngine,
    resolve_capability,
)
from phlo.logging import get_logger

from phlo_dagster.iceberg_maintenance_utils import (
    MaintenanceConfig,
    finish_maintenance_op,
    list_tables,
    resolve_maintenance_discovery,
    start_maintenance_op,
)
from phlo_dagster.maintenance_policy import (
    NamespacePolicy,
    TableAction,
    evaluate_table,
    load_policies,
)

logger = get_logger(__name__)

_DEFAULT_POLICY_PATH = "maintenance_policy.yaml"
_VALID_TABLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")


def _validate_table_name(table_name: str) -> str:
    """Validate a fully-qualified table name before SQL interpolation.

    Raises ValueError when the table name is invalid.

    """
    if not _VALID_TABLE_NAME.fullmatch(table_name):
        raise ValueError(f"Invalid table name: {table_name}")
    return table_name


def _load_iceberg_stats() -> Any:
    """Resolve table statistics through the neutral maintenance contract."""
    return resolve_maintenance_discovery().get_table_stats


def _load_optimize_query_engine() -> QueryEngine:
    """Resolve the query engine used for maintenance OPTIMIZE operations.

    Raises RuntimeError when the query_engine:trino capability is unavailable.

    """
    resolution = resolve_capability("query_engine", "trino")
    if resolution is None:
        raise RuntimeError(
            "Trino OPTIMIZE requires a query_engine:trino capability. "
            "Install phlo-trino or another provider exposing that capability."
        )
    return resolution.provider


def _evaluate_namespace(
    policy: NamespacePolicy,
    get_table_stats: Any,
    context: dg.SensorEvaluationContext,
) -> list[TableAction]:
    """Evaluate all tables in a namespace against a policy.

    Returns the TableActions with at least one action flagged.

    """
    actions: list[TableAction] = []
    tables = list_tables(policy.namespace, policy.ref)

    for table_name in tables:
        try:
            stats = get_table_stats(table_name=table_name, ref=policy.ref)
        except Exception:
            logger.exception("maintenance_sensor_stats_failed", table_name=table_name)
            continue

        action = evaluate_table(table_name, stats, policy)
        if action.expire_snapshots or action.optimize:
            actions.append(action)

    return actions


class OptimizeConfig(dg.Config):
    """Configuration for the Trino OPTIMIZE op."""

    table_names: list[str]
    ref: str = "main"
    dry_run: bool = False


def _load_optimize_maintenance_executor() -> MaintenanceExecutor:
    """Resolve the explicit ref-aware maintenance executor capability."""
    resolution = resolve_capability("maintenance_executor", "trino")
    if resolution is None:
        raise RuntimeError(
            "Trino compaction requires a maintenance_executor:trino capability. "
            "Install phlo-trino or another provider exposing that capability."
        )
    executor = resolution.provider
    if not isinstance(executor, MaintenanceExecutor):
        raise RuntimeError("Resolved maintenance_executor:trino does not implement the contract")
    return executor


def _load_optimize_table_store() -> MaintenanceTableStore:
    """Resolve the provider-neutral table store used for compaction."""
    resolution = resolve_capability("table_store", "iceberg")
    if resolution is None:
        raise RuntimeError(
            "Compaction requires a table_store:iceberg capability. "
            "Install or configure a provider exposing the maintenance table-store contract."
        )
    table_store = resolution.provider
    if not isinstance(table_store, MaintenanceTableStore):
        raise RuntimeError(
            "Resolved table_store:iceberg does not implement the maintenance table-store contract"
        )
    return table_store


@dg.op
def optimize_table_files(
    context: dg.OpExecutionContext,
    config: OptimizeConfig,
) -> dict[str, Any]:
    """Run the shared Iceberg compaction operation for selected tables."""
    table_store = _load_optimize_table_store()
    executor = (
        _load_optimize_maintenance_executor().for_ref(config.ref) if not config.dry_run else None
    )
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    maintenance_config = MaintenanceConfig(
        namespace="selected",
        ref=config.ref,
    )
    started_at = time.monotonic()
    telemetry = start_maintenance_op(
        context,
        maintenance_config,
        "compact",
        dry_run=config.dry_run,
    )

    for table_name in config.table_names:
        try:
            _validate_table_name(table_name)
            result = table_store.compact(
                table_name=table_name,
                override_ref=config.ref,
                dry_run=config.dry_run,
                operation_id=f"{context.run_id}:{table_name}",
                executor=executor,
            )
            results.append(result)
            status = str(result.get("status", "unknown"))
            if status in {"failed", "blocked"}:
                error = result.get("failure") or {"message": "compaction failed"}
                error_msg = f"Failed to compact {table_name}: {error}"
                context.log.warning(error_msg)
                errors.append(error_msg)
            else:
                context.log.info(f"Compaction {status} for {table_name}")
        except Exception as e:
            error_msg = f"Failed to optimize {table_name}: {e}"
            context.log.warning(error_msg)
            errors.append(error_msg)
            results.append(
                MaintenanceOperationResult(
                    operation="compact",
                    table_name=table_name,
                    ref=config.ref,
                    dry_run=config.dry_run,
                    status=MaintenanceOperationState.FAILED,
                    accepted=False,
                    executed=False,
                    failure={
                        "code": "invalid_request",
                        "type": type(e).__name__,
                        "message": str(e),
                        "retryable": False,
                    },
                    operation_id=f"{context.run_id}:{table_name}",
                    retry_safe=False,
                ).to_dict()
            )

    summary = finish_maintenance_op(
        context,
        maintenance_config,
        telemetry,
        "compact",
        duration_seconds=time.monotonic() - started_at,
        errors=errors,
        extra_tags={"dry_run": config.dry_run},
        evidence={"results": results},
        tables_processed=len(results),
    )
    return {
        "operation": "compact",
        "dry_run": config.dry_run,
        "results": results,
        "errors": errors,
        "status": summary["status"],
        "run_id": context.run_id,
    }


@dg.job(description="Optimize Iceberg table files via Trino EXECUTE optimize")
def optimize_tables_job():
    """Job that runs Trino OPTIMIZE on selected tables."""
    optimize_table_files()


expire_snapshots_job: dg.JobDefinition | None = None
# expire_snapshots_job lives behind an optional dependency (phlo-iceberg).
# Import it lazily and degrade to optimize-only maintenance when absent.
try:
    candidate_job = getattr(
        import_module("phlo_dagster.iceberg_maintenance"), "expire_snapshots_job", None
    )
    if isinstance(candidate_job, dg.JobDefinition):
        expire_snapshots_job = candidate_job
except Exception:  # noqa: BLE001 - optional dependency integration
    pass

_SENSOR_TARGET_JOBS: list[dg.JobDefinition] = [optimize_tables_job]
if expire_snapshots_job is not None:
    _SENSOR_TARGET_JOBS.insert(0, expire_snapshots_job)


@dg.sensor(
    name="maintenance_policy_sensor",
    description=(
        "Evaluates table stats against maintenance policies "
        "and triggers maintenance when thresholds are exceeded"
    ),
    jobs=_SENSOR_TARGET_JOBS,
    minimum_interval_seconds=1800,
    default_status=dg.DefaultSensorStatus.STOPPED,
)
def maintenance_policy_sensor(context: dg.SensorEvaluationContext):
    """Evaluate tables against maintenance policies and yield RunRequests as needed."""
    cursor_key = context.cursor or datetime.now(timezone.utc).isoformat()
    policy_path = os.environ.get("PHLO_MAINTENANCE_POLICY_PATH", _DEFAULT_POLICY_PATH)

    try:
        policies = load_policies(policy_path)
    except Exception:
        logger.exception("maintenance_sensor_policy_load_failed", path=policy_path)
        return

    get_table_stats = _load_iceberg_stats()

    for policy in policies:
        actions = _evaluate_namespace(policy, get_table_stats, context)
        if not actions:
            continue

        expire_tables = [a.table_name for a in actions if a.expire_snapshots]
        optimize_tables = [a.table_name for a in actions if a.optimize]

        if expire_tables:
            if expire_snapshots_job is None:
                logger.warning(
                    "maintenance_sensor_expire_job_unavailable",
                    namespace=policy.namespace,
                    table_count=len(expire_tables),
                )
                continue
            logger.info(
                "maintenance_sensor_expire_triggered",
                namespace=policy.namespace,
                table_count=len(expire_tables),
            )
            # The cursor timestamp in the run key deduplicates re-evaluations
            # within one tick while allowing a fresh trigger on the next tick.
            yield dg.RunRequest(
                run_key=f"expire_{policy.namespace}_{cursor_key}",
                job_name="expire_snapshots_job",
                run_config=dg.RunConfig(
                    ops={
                        "expire_table_snapshots": {
                            "config": {
                                "namespace": policy.namespace,
                                "ref": policy.ref,
                                "snapshot_retention_days": (
                                    policy.expire.older_than_days if policy.expire else 7
                                ),
                                "snapshot_retain_last": (
                                    policy.expire.retain_last if policy.expire else 5
                                ),
                                "table_allowlist": expire_tables,
                            }
                        }
                    }
                ),
            )

        if optimize_tables:
            logger.info(
                "maintenance_sensor_optimize_triggered",
                namespace=policy.namespace,
                table_count=len(optimize_tables),
            )
            yield dg.RunRequest(
                run_key=f"optimize_{policy.namespace}_{cursor_key}",
                job_name="optimize_tables_job",
                run_config=dg.RunConfig(
                    ops={
                        "optimize_table_files": {
                            "config": {
                                "table_names": optimize_tables,
                                "ref": policy.ref,
                            }
                        }
                    }
                ),
            )

    context.update_cursor(datetime.now(timezone.utc).isoformat())


def get_policy_maintenance_definitions() -> dg.Definitions:
    """Return Dagster definitions for policy-driven maintenance.

    Includes the policy sensor and optimize job, for merging into a project's
    main definitions. Logs a warning when the expire job is unavailable.

    """
    jobs: list[dg.JobDefinition] = [optimize_tables_job]
    if expire_snapshots_job is None:
        logger.warning("dagster_policy_maintenance_expire_job_unavailable")
    else:
        jobs.insert(0, expire_snapshots_job)

    logger.info(
        "dagster_policy_maintenance_definitions_built",
        job_count=len(jobs),
        sensor_count=1,
    )
    return dg.Definitions(
        jobs=jobs,
        sensors=[maintenance_policy_sensor],
    )
