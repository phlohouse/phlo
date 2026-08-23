"""Decorator helpers for registering Sling-backed Phlo assets.

This module provides decorators and helper functions for registering Sling-based
data replication assets within the Phlo orchestration platform. It supports both
single asset registration via @phlo_sling_replication and batch registration
via @phlo_sling_assets.

Functions:
    get_sling_assets: Retrieve all registered Sling asset specifications.
    clear_sling_assets: Clear the internal asset registry.
    phlo_sling_replication: Decorator for registering a single Sling asset.
    phlo_sling_assets: Decorator for registering multiple Sling assets
        from a discovery function.

Private Functions:
    _validate_replication_mode: Validates replication mode strings.
    _validate_incremental_config: Validates incremental mode requirements.
    _normalize_primary_key: Normalizes primary key input to a list.
    _build_replication_config: Builds a ReplicationConfig from decorator args.
    _build_asset_run: Creates the runtime execution function for assets.
    _register_sling_asset: Registers a Sling asset in the internal registry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any, Literal

from phlo.capabilities import AssetSpec, MaterializeResult, PartitionSpec, RunSpec
from phlo.capabilities.runtime import RuntimeContext
from phlo.contracts import Consumer, SLA, normalize_consumers, serialize_consumers, serialize_sla
from phlo.exceptions import PhloConfigError
from phlo.logging import log_event

from phlo_sling.registry import ReplicationConfig, SlingReplication
from phlo_sling.settings import get_settings

# Populated at decoration time and kept for the process lifetime. Cleared only
# by clear_sling_assets(), which tests and plugin reloads use to reset state.
_SLING_ASSETS: list[AssetSpec] = []


def get_sling_assets() -> list[AssetSpec]:
    """Return registered Sling replication asset specifications.

    Returns AssetSpec objects registered via the @phlo_sling_replication or
    @phlo_sling_assets decorators. The returned list is a copy; mutating it
    does not affect the registry.

    """
    return list(_SLING_ASSETS)


def clear_sling_assets() -> None:
    """Clear all registered Sling assets from the internal registry.

    Intended for tests and plugin reloads that need to reset registry state.
    """
    _SLING_ASSETS.clear()


def _validate_replication_mode(mode: str) -> None:
    """Check the replication mode is one Sling supports.

    Raises: PhloConfigError when mode is not one of full-refresh, incremental,
        snapshot, or backfill.
    """
    valid_modes = {"full-refresh", "incremental", "snapshot", "backfill"}
    if mode not in valid_modes:
        raise PhloConfigError(
            message=f"Invalid replication mode: {mode}",
            suggestions=[f"Use one of: {', '.join(sorted(valid_modes))}"],
        )


def _validate_incremental_config(mode: str, update_key: str | None) -> None:
    """Ensure incremental replication declares an update key.

    Raises: PhloConfigError when mode is "incremental" and update_key is empty.
    """
    if mode == "incremental" and not update_key:
        raise PhloConfigError(
            message="Incremental mode requires an update_key",
            suggestions=["Set update_key to a timestamp or incrementing column"],
        )


def _normalize_primary_key(primary_key: list[str] | str | None) -> list[str]:
    """Normalize primary key input into a list of column name strings."""
    return [primary_key] if isinstance(primary_key, str) else list(primary_key or [])


def _build_replication_config(
    *,
    stream_name: str,
    table_name: str,
    source_conn: str,
    group: str,
    target_conn: str | None,
    mode: Literal["full-refresh", "incremental", "snapshot", "backfill"] | None,
    primary_key: list[str] | str | None,
    update_key: str | None,
    object: str | None,
    select: list[str] | None,
    where: str | None,
    source_options: dict[str, Any] | None,
    target_options: dict[str, Any] | None,
) -> ReplicationConfig:
    """Build a validated ReplicationConfig from decorator-style inputs.

    Falls back to settings for an unset mode, normalizes the primary key, and
    substitutes empty collections for None options.

    Raises: PhloConfigError when validation fails, such as an invalid mode or
        a missing update_key for incremental replication.
    """
    resolved_mode = mode or get_settings().sling_default_mode
    _validate_replication_mode(resolved_mode)
    _validate_incremental_config(resolved_mode, update_key)

    return ReplicationConfig(
        stream_name=stream_name,
        table_name=table_name,
        source_conn=source_conn,
        target_conn=target_conn,
        mode=resolved_mode,
        primary_key=_normalize_primary_key(primary_key),
        update_key=update_key,
        group_name=group,
        object=object,
        select=select or [],
        where=where,
        source_options=source_options or {},
        target_options=target_options or {},
    )


def _build_asset_run(
    repl_config: ReplicationConfig,
    source_func: Callable[..., Any],
    override_resolver: Callable[[RuntimeContext], dict[str, Any] | None],
) -> Callable[[RuntimeContext], Iterator[MaterializeResult]]:
    """Build the run callable that executes a Sling replication.

    Returns a callable taking the RuntimeContext that runs the ingestion via
    SlingIngester and yields MaterializeResult objects, including a no_data
    result when nothing was ingested.
    """

    def run(runtime: RuntimeContext) -> Iterator[MaterializeResult]:
        """Run the Sling replication for this runtime context and yield results."""
        partition_date = runtime.partition_key or "latest"
        run_id = runtime.run_id or "unknown"
        logger = runtime.logger

        log_event(logger, "info", "starting_sling_replication", partition_date=partition_date)
        log_event(
            logger,
            "info",
            "sling_stream_selected",
            stream_name=repl_config.stream_name,
            table_name=repl_config.full_table_name,
            mode=repl_config.mode,
        )

        from phlo_sling.executor import SlingIngester

        ingester = SlingIngester(
            context=runtime,
            logger=logger,
            replication_config=repl_config,
            source_func=source_func,
            overrides=override_resolver(runtime),
        )
        result = ingester.run_ingestion(
            partition_key=partition_date,
            parameters={"run_id": run_id},
        )

        if result.status == "no_data":
            yield MaterializeResult(
                metadata={
                    "partition_date": partition_date,
                    "rows_loaded": 0,
                    "status": "no_data",
                    "stream_name": repl_config.stream_name,
                },
                status="no_data",
            )
            return

        yield MaterializeResult(
            metadata={
                "partition_date": partition_date,
                "rows_inserted": result.rows_inserted,
                "rows_deleted": result.rows_deleted,
                "table_name": repl_config.full_table_name,
                "stream_name": repl_config.stream_name,
                "mode": repl_config.mode,
                "sling_elapsed_seconds": result.metadata.get("sling_elapsed_seconds", 0.0),
                "total_elapsed_seconds": result.metadata.get("total_elapsed_seconds", 0.0),
            },
            status=result.status,
        )

    return run


def _register_sling_asset(
    *,
    repl_config: ReplicationConfig,
    source_func: Callable[..., Any],
    override_resolver: Callable[[RuntimeContext], dict[str, Any] | None],
    description: str | None,
    owner: str | None,
    consumers: list[Consumer | str] | None,
    sla: SLA | None,
    max_runtime_seconds: int,
    max_retries: int,
    retry_delay_seconds: int,
    cron: str | None,
    freshness_hours: tuple[int, int] | None,
    extra_metadata: dict[str, Any] | None = None,
    extra_tags: dict[str, str] | None = None,
) -> AssetSpec:
    """Create an AssetSpec from the replication config and register it.

    Fills in a default description, provider tags and metadata, daily
    partitions, and a run spec from the scheduling parameters, then appends
    the spec to the internal registry before returning it.
    """
    normalized_consumers = normalize_consumers(consumers)
    asset_spec = AssetSpec(
        key=repl_config.asset_key,
        group=repl_config.group_name,
        description=description
        or f"Replicates {repl_config.stream_name} to {repl_config.full_table_name}",
        kinds={"sling", "replication"},
        tags={
            **(extra_tags or {}),
            "provider": "sling",
            "asset_type": "ingestion",
            "source": "sling",
            "mode": repl_config.mode,
        },
        metadata={
            **(extra_metadata or {}),
            "provider": "sling",
            "asset_type": "ingestion",
            "source_name": repl_config.stream_name,
            "stream_name": repl_config.stream_name,
            "table_name": repl_config.table_name,
            "write_mode": repl_config.mode,
            "source_conn": repl_config.source_conn,
            "mode": repl_config.mode,
            "primary_key": repl_config.primary_key,
            "schema_ref": None,
            "quality_provider": None,
            "update_key": repl_config.update_key,
            "group": repl_config.group_name,
            "owner": owner,
            "consumers": serialize_consumers(normalized_consumers),
            "sla": serialize_sla(sla),
        },
        partitions=PartitionSpec(kind="daily"),
        resources=set(),
        run=RunSpec(
            fn=_build_asset_run(repl_config, source_func, override_resolver),
            max_runtime_seconds=max_runtime_seconds,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            cron=cron,
            freshness_hours=freshness_hours,
        ),
        checks=[],
    )
    _SLING_ASSETS.append(asset_spec)
    return asset_spec


def phlo_sling_replication(
    stream_name: str,
    table_name: str,
    source_conn: str,
    group: str,
    *,
    target_conn: str | None = None,
    mode: Literal["full-refresh", "incremental", "snapshot", "backfill"] | None = None,
    primary_key: list[str] | str | None = None,
    update_key: str | None = None,
    object: str | None = None,
    select: list[str] | None = None,
    where: str | None = None,
    source_options: dict[str, Any] | None = None,
    target_options: dict[str, Any] | None = None,
    cron: str | None = None,
    freshness_hours: tuple[int, int] | None = None,
    max_runtime_seconds: int = 600,
    max_retries: int = 3,
    retry_delay_seconds: int = 30,
    owner: str | None = None,
    consumers: list[Consumer | str] | None = None,
    sla: SLA | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as a Sling-backed replication asset.

    The decorated function receives a ``RuntimeContext`` and may return a
    dict of Sling overrides such as a dynamically resolved ``src_stream`` or
    partition-specific ``where`` clause.

    Example:
        Basic incremental replication::

            @phlo_sling_replication(
                stream_name="public.users",
                table_name="users",
                source_conn="PHLO_POSTGRES",
                group="ingestion",
                mode="incremental",
                update_key="updated_at",
            )
            def replicate_users(context):
                # Optional: return runtime overrides
                return {"where": f"updated_at >= '{context.partition_key}'"}

    """
    repl_config = _build_replication_config(
        stream_name=stream_name,
        table_name=table_name,
        source_conn=source_conn,
        group=group,
        target_conn=target_conn,
        mode=mode,
        primary_key=primary_key,
        update_key=update_key,
        object=object,
        select=select,
        where=where,
        source_options=source_options,
        target_options=target_options,
    )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap a replication source function as a Phlo asset definition."""

        def resolve_overrides(runtime: RuntimeContext) -> dict[str, Any] | None:
            """Return the function's dict of runtime overrides, else None."""
            overrides = func(runtime)
            return overrides if isinstance(overrides, dict) else None

        _register_sling_asset(
            repl_config=repl_config,
            source_func=func,
            override_resolver=resolve_overrides,
            description=func.__doc__,
            owner=owner,
            consumers=consumers,
            sla=sla,
            max_runtime_seconds=max_runtime_seconds,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            cron=cron,
            freshness_hours=freshness_hours,
        )
        setattr(func, "_phlo_replication_config", repl_config)
        return func

    return decorator


def phlo_sling_assets(
    *,
    group: str,
    cron: str | None = None,
    freshness_hours: tuple[int, int] | None = None,
    max_runtime_seconds: int = 600,
    max_retries: int = 3,
    retry_delay_seconds: int = 30,
    owner: str | None = None,
    consumers: list[Consumer | str] | None = None,
    sla: SLA | None = None,
) -> Callable[[Callable[[], Iterable[SlingReplication]]], Callable[[], Iterable[SlingReplication]]]:
    """Register multiple Sling-backed assets from a discovery function.

    Runs once at decoration time: the decorated function yields
    ``SlingReplication`` objects, one per asset to register. This is the
    Python-first API for filesystem scans, schema discovery, and similar
    dynamic asset generation.

    Example:
        Discover and register multiple tables::

            from phlo_sling import phlo_sling_assets, SlingReplication

            @phlo_sling_assets(group="ingestion")
            def discover_all_tables():
                tables = ["users", "orders", "products"]
                for table in tables:
                    yield SlingReplication(
                        stream_name=f"public.{table}",
                        table_name=table,
                        source_conn="PHLO_POSTGRES",
                        mode="full-refresh",
                    )

    """

    def decorator(
        func: Callable[[], Iterable[SlingReplication]],
    ) -> Callable[[], Iterable[SlingReplication]]:
        """Discover replications and register each as a Phlo asset."""
        replications = list(func())
        # Discovery runs once, here at decoration time. Registered assets never
        # call func again at runtime: each asset's run uses a no-op override
        # resolver, so per-run overrides are only available through
        # @phlo_sling_replication.

        for replication in replications:
            repl_config = _build_replication_config(
                stream_name=replication.stream_name,
                table_name=replication.table_name,
                source_conn=replication.source_conn,
                group=replication.group_name or group,
                target_conn=replication.target_conn,
                mode=replication.mode,
                primary_key=replication.primary_key,
                update_key=replication.update_key,
                object=replication.object,
                select=replication.select,
                where=replication.where,
                source_options=replication.source_options,
                target_options=replication.target_options,
            )

            _register_sling_asset(
                repl_config=repl_config,
                source_func=func,
                override_resolver=lambda _runtime: None,
                description=replication.description,
                owner=replication.owner or owner,
                consumers=consumers,
                sla=sla,
                max_runtime_seconds=max_runtime_seconds,
                max_retries=max_retries,
                retry_delay_seconds=retry_delay_seconds,
                cron=cron,
                freshness_hours=freshness_hours,
                extra_metadata=replication.metadata,
                extra_tags=replication.tags,
            )

        return func

    return decorator
