"""Iceberg table maintenance jobs and schedules for Dagster.

This module provides scheduled and on-demand maintenance operations for
Apache Iceberg tables through Dagster jobs and ops. It handles retention
planning, guarded snapshot-expiry execution, and table statistics collection.

Maintenance Operations:
    - expire_table_snapshots: Plan snapshot expiry and execute it with an explicit confirmation token
    - cleanup_orphan_files: Discover unreferenced files; v1 destructive execution is refused
    - collect_table_stats: Gather table metadata for monitoring and policy evaluation

Jobs Provided:
    - iceberg_maintenance_job: Runs all maintenance operations
    - expire_snapshots_job: Plan-first snapshot expiry
    - orphan_cleanup_job: Orphan-file discovery only
    - table_stats_job: Statistics collection only

Schedule:
    Default schedule runs full maintenance daily at 2 AM UTC (stopped by default).

Safety Features:
    - Planning mode by default; snapshot expiry requires a plan token and ref-aware executor
    - Orphan cleanup remains planning-only
    - Table allowlist for targeted maintenance
    - Error collection and reporting without failing entire job

Configuration:
    Uses MaintenanceConfig with fields:
    - namespace: Target namespace (or "all")
    - snapshot_retention_days: Age threshold for snapshots
    - snapshot_retain_last: Minimum snapshots to keep
    - orphan_retention_days: Age threshold for orphan files
    - dry_run: Plan-only mode for both retention operations
    - catalog, confirmation_token, max_affected_objects, max_affected_bytes: Plan-binding evidence and future-adapter validation
    - ref: Nessie branch reference

Integration Requirements:
    Requires a registered ``table_store:iceberg`` provider implementing the
    neutral maintenance discovery and retention contracts. Dagster does not
    import a concrete provider package.

Example:
    Including maintenance in definitions::

        from phlo_dagster.iceberg_maintenance import get_maintenance_definitions

        maintenance_defs = get_maintenance_definitions()
        defs = dg.Definitions.merge(your_defs, maintenance_defs)

"""

import time
from typing import Any, cast

import dagster as dg

from phlo.capabilities import (
    MaintenanceExecutor,
    MaintenanceRetentionStore,
    resolve_capability,
)
from phlo.logging import get_logger

from phlo_dagster.iceberg_maintenance_utils import (
    MaintenanceConfig,
    finish_maintenance_op,
    list_tables,
    maintenance_log_extra,
    resolve_namespaces,
    resolve_maintenance_discovery,
    start_maintenance_op,
)

logger = get_logger(__name__)


def _load_maintenance_retention_store() -> MaintenanceRetentionStore:
    """Resolve the provider-neutral retention store capability."""
    resolution = resolve_capability("table_store", "iceberg")
    if resolution is None:
        raise RuntimeError("Retention maintenance requires a table_store:iceberg capability.")
    store = resolution.provider
    if not isinstance(store, MaintenanceRetentionStore):
        raise RuntimeError(
            "Resolved table_store:iceberg does not implement the retention maintenance contract."
        )
    return store


def _load_snapshot_expiry_executor() -> MaintenanceExecutor:
    """Resolve the neutral ref-aware executor only for destructive expiry."""
    resolution = resolve_capability("maintenance_executor")
    if resolution is None:
        raise RuntimeError(
            "Snapshot expiry requires exactly one configured maintenance_executor capability."
        )
    executor = resolution.provider
    if not isinstance(executor, MaintenanceExecutor):
        raise RuntimeError(
            "Resolved maintenance executor does not implement the snapshot-expiry contract."
        )
    return executor


def _confirmation_token_for_table(config: MaintenanceConfig, table_name: str) -> str | None:
    """Resolve the token bound to one table's exact plan."""
    if config.confirmation_tokens is not None:
        return config.confirmation_tokens.get(table_name)
    return config.confirmation_token


def _validate_orphan_dry_run_compatibility(config: MaintenanceConfig) -> None:
    """Reject contradictory legacy and contract-level execution flags."""
    if config.orphan_dry_run is not None and config.orphan_dry_run != config.dry_run:
        raise ValueError("orphan_dry_run is deprecated; set it equal to dry_run or omit it")


def _run_retention_resource_operation(
    *,
    operation: str,
    table_name: str,
    config: MaintenanceConfig,
    store: MaintenanceRetentionStore,
) -> dict[str, Any]:
    """Plan once, then execute only with the caller's exact plan token."""
    common: dict[str, Any] = {
        "table_name": table_name,
        "override_ref": config.ref,
        "catalog": config.catalog,
        "retention_hours": (
            config.snapshot_retention_days
            if operation == "expire_snapshots"
            else config.orphan_retention_days
        )
        * 24,
        "max_affected_objects": config.max_affected_objects,
        "max_affected_bytes": config.max_affected_bytes,
        "operation_id": None,
    }
    method = (
        store.expire_snapshots if operation == "expire_snapshots" else store.cleanup_orphan_files
    )
    if operation == "expire_snapshots":
        common["retain_last"] = config.snapshot_retain_last
    plan = method(**common, dry_run=True)
    if config.dry_run:
        return plan
    before_revision = cast(int | str | None, plan.get("before_revision"))
    # Execute only if the catalog is still at the revision observed during
    # planning; a moved revision makes the provider refuse rather than delete
    # against a stale plan.
    if operation == "expire_snapshots":
        common["executor"] = _load_snapshot_expiry_executor()
    return method(
        **common,
        dry_run=False,
        expected_snapshot_id=before_revision,
        confirmation_token=_confirmation_token_for_table(config, table_name),
    )


@dg.op
def expire_table_snapshots(
    context: dg.OpExecutionContext,
    config: MaintenanceConfig,
) -> dict[str, Any]:
    """Plan snapshot expiry and execute only with guarded, non-atomic evidence.

    Returns a summary dict with processed tables, snapshot candidates, zero
    deletions, and errors; per-table failures are logged as warnings.

    """
    tables_processed = 0
    total_snapshots_deleted = 0
    total_candidate_snapshots = 0
    errors: list[str] = []
    operation = "expire_snapshots"
    start_time = time.time()
    telemetry = start_maintenance_op(context, config, operation, dry_run=config.dry_run)
    store = _load_maintenance_retention_store()

    for namespace in resolve_namespaces(config):
        for table_name in list_tables(namespace, config.ref):
            if config.table_allowlist and table_name not in config.table_allowlist:
                continue
            try:
                result = _run_retention_resource_operation(
                    operation=operation,
                    table_name=table_name,
                    config=config,
                    store=store,
                )
                if result.get("status") in {"blocked", "failed"}:
                    tables_processed += 1
                    planned = result.get("planned") or {}
                    candidate_count = len(planned.get("candidate_snapshots", []))
                    total_candidate_snapshots += candidate_count
                    failure = result.get("failure") or {}
                    error_msg = (
                        f"Maintenance refused for {table_name}: "
                        f"{failure.get('message', result.get('status'))}"
                    )
                    errors.append(error_msg)
                    context.log.warning(
                        error_msg,
                        extra=maintenance_log_extra(
                            context,
                            config,
                            operation=operation,
                            table_name=table_name,
                            candidate_snapshots=candidate_count,
                            plan_token=result.get("plan_token"),
                            before_revision=result.get("before_revision"),
                            deletion_submitted=False,
                        ),
                    )
                    continue
                tables_processed += 1
                planned = result.get("planned", {})
                total_candidate_snapshots += len(planned.get("candidate_snapshots", []))
                total_snapshots_deleted += int(
                    result.get("affected", {}).get("deleted_snapshots") or 0
                )
                context.log.info(
                    f"Processed {table_name}: {result.get('status', 'unknown')}",
                    extra=maintenance_log_extra(
                        context,
                        config,
                        operation=operation,
                        table_name=table_name,
                        snapshots_deleted=result.get("affected", {}).get("deleted_snapshots", 0),
                    ),
                )
            except Exception as e:
                error_msg = f"Failed to expire snapshots for {table_name}: {e}"
                context.log.warning(
                    error_msg,
                    extra=maintenance_log_extra(
                        context,
                        config,
                        operation=operation,
                        table_name=table_name,
                        error=str(e),
                    ),
                )
                errors.append(error_msg)

    summary_payload = finish_maintenance_op(
        context,
        config,
        telemetry,
        operation,
        duration_seconds=time.time() - start_time,
        errors=errors,
        tables_processed=tables_processed,
        snapshots_deleted=total_snapshots_deleted,
        total_candidate_snapshots=total_candidate_snapshots,
    )
    summary_payload.update(
        {
            "tables_processed": tables_processed,
            "total_snapshots_deleted": total_snapshots_deleted,
            "total_candidate_snapshots": total_candidate_snapshots,
            "errors": errors,
        }
    )
    return summary_payload


@dg.op
def cleanup_orphan_files(
    context: dg.OpExecutionContext,
    config: MaintenanceConfig,
) -> dict[str, Any]:
    """Discover orphan files, refusing destructive execution on the v1 boundary.

    Dry-run mode reports candidates from the configured object-store listing.
    Execute mode returns a structured unsupported result because the blessed
    Trino procedure cannot bind the submitted deletion set to that plan.
    Per-table failures are logged as warnings.

    """
    _validate_orphan_dry_run_compatibility(config)
    tables_processed = 0
    total_candidate_files = 0
    total_deleted_files = 0
    deleted_file_evidence_complete = True
    errors: list[str] = []
    results: dict[str, Any] = {
        "tables_processed": tables_processed,
        "total_orphan_files": 0,
        "total_candidate_files": 0,
        "total_deleted_files": 0,
        "dry_run": config.dry_run,
        "errors": errors,
    }
    operation = "cleanup_orphan_files"
    start_time = time.time()
    telemetry = start_maintenance_op(context, config, operation, dry_run=config.dry_run)
    store = _load_maintenance_retention_store()

    if not config.dry_run:
        context.log.warning(
            "EXECUTION REQUEST REFUSED: dry_run=False is unsupported for orphan cleanup "
            "on the blessed Trino boundary; no provider deletion is submitted.",
            extra=maintenance_log_extra(
                context,
                config,
                operation=operation,
                dry_run=config.dry_run,
            ),
        )

    for namespace in resolve_namespaces(config):
        for table_name in list_tables(namespace, config.ref):
            if config.table_allowlist and table_name not in config.table_allowlist:
                continue
            try:
                result = _run_retention_resource_operation(
                    operation=operation,
                    table_name=table_name,
                    config=config,
                    store=store,
                )
                if result.get("status") in {"blocked", "failed"}:
                    tables_processed += 1
                    planned = result.get("planned") or {}
                    candidate_count = len(planned.get("candidate_files", []))
                    total_candidate_files += candidate_count
                    failure = result.get("failure") or {}
                    error_msg = (
                        f"Maintenance refused for {table_name}: "
                        f"{failure.get('message', result.get('status'))}"
                    )
                    errors.append(error_msg)
                    context.log.warning(
                        error_msg,
                        extra=maintenance_log_extra(
                            context,
                            config,
                            operation=operation,
                            table_name=table_name,
                            candidate_orphan_files=candidate_count,
                            deleted_orphan_files=0,
                            plan_token=result.get("plan_token"),
                            before_revision=result.get("before_revision"),
                            deletion_submitted=False,
                        ),
                    )
                    continue
                tables_processed += 1
                affected = result.get("affected", {})
                planned = result.get("planned", {})
                candidate_count = len(planned.get("candidate_files", []))
                deleted_value = affected.get("deleted_files")
                if not config.dry_run and deleted_value is None:
                    deleted_file_evidence_complete = False
                deleted_count = int(deleted_value or 0)
                total_candidate_files += candidate_count
                total_deleted_files += deleted_count
                displayed_count: int | str = (
                    candidate_count
                    if config.dry_run
                    else (deleted_count if deleted_value is not None else "unknown")
                )
                action = "Planned" if config.dry_run else "Removed"
                context.log.info(
                    f"{action} {displayed_count} orphan files in {table_name}",
                    extra=maintenance_log_extra(
                        context,
                        config,
                        operation=operation,
                        table_name=table_name,
                        orphan_files=displayed_count,
                        candidate_orphan_files=candidate_count,
                        deleted_orphan_files=deleted_count,
                        dry_run=config.dry_run,
                    ),
                )
            except Exception as e:
                error_msg = f"Failed to cleanup orphan files for {table_name}: {e}"
                context.log.warning(
                    error_msg,
                    extra=maintenance_log_extra(
                        context,
                        config,
                        operation=operation,
                        table_name=table_name,
                        dry_run=config.dry_run,
                        error=str(e),
                    ),
                )
                errors.append(error_msg)

    finish_maintenance_op(
        context,
        config,
        telemetry,
        operation,
        duration_seconds=time.time() - start_time,
        errors=errors,
        extra_tags={"dry_run": config.dry_run},
        tables_processed=tables_processed,
        orphan_files=(
            total_candidate_files
            if config.dry_run
            else (total_deleted_files if deleted_file_evidence_complete else None)
        ),
        candidate_orphan_files=total_candidate_files,
        deleted_orphan_files=(total_deleted_files if deleted_file_evidence_complete else None),
        unavailable_deleted_file_evidence=(0 if deleted_file_evidence_complete else 1),
    )

    results["tables_processed"] = tables_processed
    results["total_orphan_files"] = (
        total_candidate_files
        if config.dry_run
        else (total_deleted_files if deleted_file_evidence_complete else None)
    )
    results["total_candidate_files"] = total_candidate_files
    results["total_deleted_files"] = total_deleted_files if deleted_file_evidence_complete else None
    results["unavailable_deleted_file_evidence"] = 0 if deleted_file_evidence_complete else 1
    results["errors"] = errors

    return results


@dg.op
def collect_table_stats(
    context: dg.OpExecutionContext,
    config: MaintenanceConfig,
) -> dict[str, Any]:
    """Collect statistics for all tables in the specified namespace.

    Returns a results dict with tables, total_size_mb, total_records, and
    errors; per-table failures are logged as warnings.

    """
    tables: list[dict[str, Any]] = []
    total_size_mb = 0.0
    total_records = 0
    errors: list[str] = []
    results: dict[str, Any] = {
        "tables": tables,
        "total_size_mb": total_size_mb,
        "total_records": total_records,
        "errors": errors,
    }
    operation = "collect_table_stats"
    start_time = time.time()
    telemetry = start_maintenance_op(context, config, operation)
    get_table_stats = resolve_maintenance_discovery().get_table_stats

    for namespace in resolve_namespaces(config):
        for table_name in list_tables(namespace, config.ref):
            if config.table_allowlist and table_name not in config.table_allowlist:
                continue
            try:
                stats = get_table_stats(table_name=table_name, ref=config.ref)
                tables.append(stats)
                total_size_mb += stats["total_size_mb"]
                total_records += stats["total_records"]
                context.log.info(
                    f"Table {table_name}: {stats['total_records']} records, "
                    f"{stats['total_size_mb']} MB, {stats['snapshot_count']} snapshots",
                    extra=maintenance_log_extra(
                        context,
                        config,
                        operation=operation,
                        table_name=table_name,
                        total_records=stats["total_records"],
                        total_size_mb=stats["total_size_mb"],
                        snapshot_count=stats["snapshot_count"],
                    ),
                )
            except Exception as e:
                error_msg = f"Failed to get stats for {table_name}: {e}"
                context.log.warning(
                    error_msg,
                    extra=maintenance_log_extra(
                        context,
                        config,
                        operation=operation,
                        table_name=table_name,
                        error=str(e),
                    ),
                )
                errors.append(error_msg)

    finish_maintenance_op(
        context,
        config,
        telemetry,
        operation,
        duration_seconds=time.time() - start_time,
        errors=errors,
        tables_processed=len(tables),
        total_records=total_records,
        total_size_mb=total_size_mb,
    )

    results["tables"] = tables
    results["total_size_mb"] = total_size_mb
    results["total_records"] = total_records
    results["errors"] = errors

    return results


@dg.job(
    description=(
        "Plan Iceberg snapshot expiry and orphan cleanup without submitting deletion, "
        "then collect table statistics"
    ),
)
def iceberg_maintenance_job():
    """Plan retention operations, then collect table statistics."""
    expire_table_snapshots()
    cleanup_orphan_files()
    collect_table_stats()


@dg.job(
    description="Plan Iceberg snapshot expiry; execution requires an explicit plan token",
)
def expire_snapshots_job():
    """Job that plans snapshot expiry and accepts guarded execution requests."""
    expire_table_snapshots()


@dg.job(
    description="Discover Iceberg orphan files; destructive execution is refused",
)
def orphan_cleanup_job():
    """Job that only discovers orphan files and refuses deletion."""
    cleanup_orphan_files()


@dg.job(
    description="Collect statistics for all Iceberg tables",
)
def table_stats_job():
    """Job that only collects table statistics."""
    collect_table_stats()


# Default schedule: run full maintenance daily at 2 AM
iceberg_maintenance_schedule = dg.ScheduleDefinition(
    job=iceberg_maintenance_job,
    cron_schedule="0 2 * * *",
    default_status=dg.DefaultScheduleStatus.STOPPED,
    execution_timezone="UTC",
)


def get_maintenance_definitions() -> dg.Definitions:
    """Return Dagster definitions for Iceberg maintenance, for merging into a project."""
    logger.info(
        "dagster_iceberg_maintenance_definitions_built",
        job_count=4,
        schedule_count=1,
    )
    return dg.Definitions(
        jobs=[
            iceberg_maintenance_job,
            expire_snapshots_job,
            orphan_cleanup_job,
            table_stats_job,
        ],
        schedules=[iceberg_maintenance_schedule],
    )
