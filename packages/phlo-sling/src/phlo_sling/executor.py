"""Sling replication executor with hook event emission.

This module provides the execution engine for Sling-based data replication
within the Phlo platform. It wraps the Sling library with Phlo's hook system
to enable event emission, telemetry collection, and standardized result
handling.

Classes:
    SlingIngester: Implements the BaseIngester interface for Sling replication.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict

from phlo.exceptions import PhloConfigError
from phlo.logging import log_event
from phlo.operations.ingestion import BaseIngester, IngestionResult
from phlo.hooks import (
    HookCorrelation,
    IngestionEventContext,
    IngestionEventEmitter,
    TelemetryEventContext,
    TelemetryEventEmitter,
)

from phlo_sling.registry import ReplicationConfig


class SlingIngester(BaseIngester):
    """Sling-specific implementation of the ingestion engine.

    Mirrors DltIngester: wraps Sling execution with hook event emission, timing,
    and standardized IngestionResult output. ``context`` carries the orchestrator
    runtime, ``replication_config`` the replication-level configuration,
    ``source_func`` the decorated user function, and ``overrides`` its optional
    runtime overrides.

    Example:
        Execute a Sling replication::

            ingester = SlingIngester(
                context=runtime_context,
                logger=logger,
                replication_config=config,
                source_func=user_func,
                overrides={"where": "updated_at > '2024-01-01'"},
            )
            result = ingester.run_ingestion(partition_key="2024-01-15")
    """

    def __init__(
        self,
        context: Any,
        logger: Any,
        replication_config: ReplicationConfig,
        source_func: Callable[..., Any],
        overrides: dict[str, Any] | None = None,
    ):
        """Initialize the Sling ingester from runtime context and configuration."""
        super().__init__(context, logger)
        self.replication_config = replication_config
        self.source_func = source_func
        self.overrides = overrides or {}

    def run_ingestion(
        self, partition_key: str | None, parameters: Dict[str, Any]
    ) -> IngestionResult:
        """Run the configured Sling replication flow.

        Emits hook events for start, completion, and failure, then returns an
        IngestionResult with status, row counts, and metadata. Re-raises any Sling
        execution exception after emitting failure events.
        """
        parameters = parameters or {}
        run_id = parameters.get("run_id", "unknown")
        config = self.replication_config

        emitter = IngestionEventEmitter(
            IngestionEventContext(
                asset_key=config.asset_key,
                table_name=config.full_table_name,
                group_name=config.group_name,
                partition_key=partition_key,
                run_id=run_id,
                tags={"group": config.group_name, "source": "sling", "mode": config.mode},
                correlation=HookCorrelation(
                    run_id=run_id,
                    asset_key=config.asset_key,
                    partition_key=partition_key,
                    job_name=getattr(self.context, "job_name", None),
                ),
            )
        )
        telemetry = TelemetryEventEmitter(
            TelemetryEventContext(
                tags={
                    "asset": config.asset_key,
                    "group": config.group_name,
                    "source": "sling",
                    "mode": config.mode,
                },
                correlation=HookCorrelation(
                    run_id=run_id,
                    asset_key=config.asset_key,
                    partition_key=partition_key,
                    job_name=getattr(self.context, "job_name", None),
                ),
            )
        )

        log_event(self.logger, "info", "starting_sling_replication", partition_key=partition_key)
        start_time = time.time()
        emitter.emit_start()

        try:
            from sling import Sling
            from phlo_sling.connections import apply_sling_connection_env

            # Connections must be in the environment before Sling resolves
            # src_conn/tgt_conn names; auto-discovered values never overwrite
            # variables that are already set.
            apply_sling_connection_env()
            sling_kwargs = self._build_sling_kwargs(partition_key)
            sling_config = Sling(**sling_kwargs)

            log_event(
                self.logger,
                "info",
                "sling_execution_started",
                stream_name=config.stream_name,
                mode=config.mode,
            )

            sling_start = time.time()
            sling_config.run()
            sling_elapsed = time.time() - sling_start

            rows_inserted = getattr(sling_config, "rows_count", 0) or 0

            total_elapsed = time.time() - start_time
            log_event(
                self.logger,
                "info",
                "sling_replication_completed",
                partition_key=partition_key,
                rows_inserted=rows_inserted,
                sling_elapsed_seconds=sling_elapsed,
                total_elapsed_seconds=total_elapsed,
            )

            emitter.emit_end(
                status="success",
                metrics={
                    "rows_inserted": rows_inserted,
                    "sling_elapsed_seconds": sling_elapsed,
                    "total_elapsed_seconds": total_elapsed,
                },
            )

            return IngestionResult(
                status="success",
                rows_inserted=rows_inserted,
                rows_deleted=0,
                metadata={
                    "sling_elapsed_seconds": sling_elapsed,
                    "total_elapsed_seconds": total_elapsed,
                    "stream_name": config.stream_name,
                    "mode": config.mode,
                },
            )

        except Exception as exc:
            total_elapsed = time.time() - start_time
            emitter.emit_end(
                status="failure",
                metrics={"total_elapsed_seconds": total_elapsed},
                error=str(exc),
            )
            telemetry.emit_log(
                name="sling.replication.failure",
                level="error",
                payload={"error": str(exc), "elapsed_seconds": total_elapsed},
            )
            raise

    def _build_sling_kwargs(self, partition_key: str | None) -> dict[str, Any]:
        """Build keyword arguments for the Sling constructor.

        Merges static ReplicationConfig values with runtime overrides and injects a
        dynamic WHERE clause from ``partition_key``. Raise PhloConfigError when no
        target object can be determined.
        """
        config = self.replication_config

        kwargs: dict[str, Any] = {
            "src_conn": config.source_conn,
            "src_stream": config.stream_name,
            "mode": config.mode,
        }

        if config.target_conn:
            kwargs["tgt_conn"] = config.target_conn
        if config.object:
            kwargs["tgt_object"] = config.object
        if config.primary_key:
            kwargs["primary_key"] = config.primary_key
        if config.update_key:
            kwargs["update_key"] = config.update_key
        if config.select:
            kwargs["select"] = config.select
        if config.where:
            kwargs["where"] = config.where
        if config.source_options:
            kwargs["src_options"] = config.source_options
        if config.target_options:
            kwargs["tgt_options"] = config.target_options

        kwargs.update(self.overrides)

        target_object = kwargs.get("tgt_object")
        if not target_object and kwargs.get("tgt_conn"):
            kwargs["tgt_object"] = config.full_table_name
        elif not target_object:
            raise PhloConfigError(
                message="Sling replication requires a destination object",
                suggestions=[
                    f"Set target_conn to a Sling connection so Phlo can target {config.full_table_name}",
                    "Or return a tgt_object/tgt_conn override from the decorated function",
                    "Or set object=... for file-based targets",
                ],
            )

        return kwargs
