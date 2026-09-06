"""Airbyte connection assets.

``phlo_airbyte_connection`` declares a named, pre-existing Airbyte connection
plus its expected output tables. Dagster owns scheduling: the asset starts one
sync, polls it to a verified terminal state, and only then emits a
materialization carrying the job id, connection id, output tables, and
timestamps as lineage evidence. Source credentials stay in Airbyte's secret
store; Phlo never stores them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from phlo.capabilities import AssetSpec, MaterializeResult, PartitionSpec, RunSpec
from phlo.capabilities.runtime import RuntimeContext
from phlo.exceptions import PhloConfigError
from phlo.logging import log_event

from phlo_airbyte.client import AirbyteClient

# Populated at decoration time and kept for the process lifetime. Cleared only
# by clear_airbyte_assets(), which tests and plugin reloads use to reset state.
_AIRBYTE_ASSETS: list[AssetSpec] = []


def get_airbyte_assets() -> list[AssetSpec]:
    """Return registered Airbyte connection asset specifications."""
    return list(_AIRBYTE_ASSETS)


def clear_airbyte_assets() -> None:
    """Clear all registered Airbyte assets (for tests and plugin reloads)."""
    _AIRBYTE_ASSETS.clear()


def _validate_connection_config(connection_id: str, tables: list[str]) -> None:
    if not connection_id or not connection_id.strip():
        raise PhloConfigError(
            message="Airbyte connection assets require a connection_id",
            suggestions=["Copy the connection id from the Airbyte workspace."],
        )
    if not tables:
        raise PhloConfigError(
            message="Airbyte connection assets must declare their expected output tables",
            suggestions=[
                "List the tables the connection writes so downstream assets can depend on them."
            ],
        )


def _build_asset_run(
    *,
    connection_id: str,
    tables: list[str],
    client_factory: Callable[[], AirbyteClient] | None,
) -> Callable[[RuntimeContext], Iterator[MaterializeResult]]:
    """Build the run callable that triggers and polls one Airbyte sync."""

    def run(runtime: RuntimeContext) -> Iterator[MaterializeResult]:
        logger = runtime.logger
        log_event(logger, "info", "starting_airbyte_sync", connection_id=connection_id)

        if client_factory is not None:
            client = client_factory()
        else:
            client = AirbyteClient()
        evidence = client.run_sync(connection_id)

        log_event(
            logger,
            "info",
            "airbyte_sync_completed",
            job_id=evidence["job_id"],
            status=evidence["status"],
        )
        yield MaterializeResult(
            metadata={
                "airbyte_job_id": evidence["job_id"],
                "airbyte_connection_id": evidence["connection_id"],
                "airbyte_status": evidence["status"],
                "output_tables": tables,
                "airbyte_started_at": evidence.get("started_at"),
                "airbyte_ended_at": evidence.get("ended_at"),
                "airbyte_elapsed_seconds": evidence.get("elapsed_seconds"),
                "source_state": {"job_id": evidence["job_id"], "status": evidence["status"]},
            },
            status="ok",
        )

    return run


def phlo_airbyte_connection(
    connection_id: str,
    tables: list[str],
    group: str,
    *,
    name: str | None = None,
    destination: str = "iceberg",
    description: str | None = None,
    max_runtime_seconds: int = 3600,
    max_retries: int = 1,
    retry_delay_seconds: int = 60,
    cron: str | None = None,
    client_factory: Callable[[], AirbyteClient] | None = None,
) -> AssetSpec:
    """Register an Airbyte connection asset and return its specification.

    ``connection_id`` must reference a connection that already exists in the
    Airbyte workspace; Phlo triggers and observes the sync but never mutates
    Airbyte configuration.
    """
    _validate_connection_config(connection_id, tables)
    asset_name = name or connection_id[:8]
    asset_key = f"{group}.{asset_name}"
    spec = AssetSpec(
        key=asset_key,
        group=group,
        description=description
        or f"Airbyte sync for connection {connection_id} landing {', '.join(tables)}",
        kinds={"airbyte", "ingestion"},
        tags={
            "provider": "airbyte",
            "asset_type": "ingestion",
            "source": "airbyte",
            "destination": destination,
        },
        metadata={
            "provider": "airbyte",
            "airbyte_connection_id": connection_id,
            "output_tables": tables,
            "destination": destination,
            "group": group,
        },
        partitions=PartitionSpec(kind="daily"),
        resources=set(),
        run=RunSpec(
            fn=_build_asset_run(
                connection_id=connection_id,
                tables=tables,
                client_factory=client_factory,
            ),
            max_runtime_seconds=max_runtime_seconds,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            cron=cron,
            freshness_hours=None,
        ),
        checks=[],
    )
    _AIRBYTE_ASSETS.append(spec)
    return spec
