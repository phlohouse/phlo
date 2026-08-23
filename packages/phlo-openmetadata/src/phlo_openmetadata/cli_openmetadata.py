"""OpenMetadata CLI commands.

Provides CLI commands for managing OpenMetadata integration:
    - health: Check OpenMetadata connectivity
    - sync: Sync Nessie catalog and dbt documentation to OpenMetadata

Example:
    $ phlo openmetadata health
    $ phlo openmetadata sync --include-namespace bronze --dbt

"""

from __future__ import annotations

import json
import sys

import click
from rich.console import Console

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.output import user_error
from phlo.logging import get_logger
from phlo_openmetadata.authorization import get_openmetadata_adapter
from phlo_openmetadata.capabilities import resolve_catalog_scanner
from phlo_openmetadata.dbt_sync import DbtManifestParser
from phlo_openmetadata.nessie_sync import sync_nessie_tables_to_openmetadata
from phlo_openmetadata.openmetadata import OpenMetadataClient
from phlo_openmetadata.settings import get_settings

console = Console()
logger = get_logger(__name__)


def _resolve_database_name() -> str:
    """Resolve the database name, converting failures to a user-facing CLI error."""
    try:
        return get_settings().openmetadata_database()
    except RuntimeError as exc:
        logger.error("openmetadata_database_resolution_failed", error=str(exc))
        raise user_error(
            "OpenMetadata database is not configured",
            run="phlo openmetadata health",
        ) from exc


def _resolve_service_type() -> str:
    """Resolve the service type (e.g., 'Trino'), converting failures to a
    user-facing CLI error."""
    try:
        return get_settings().openmetadata_database_service_type()
    except RuntimeError as exc:
        logger.error("openmetadata_service_type_resolution_failed", error=str(exc))
        raise user_error(
            "OpenMetadata service type is not configured",
            run="phlo openmetadata health",
        ) from exc


@click.group()
def openmetadata():
    """Manage OpenMetadata integration (optional): check health and sync
    catalog tables and dbt documentation.
    """


@openmetadata.command()
def health() -> None:
    """Check OpenMetadata connectivity using configured credentials; exits 1
    when the server is unreachable.
    """
    cfg = get_settings()
    database_name = _resolve_database_name()
    service_type = _resolve_service_type()
    client = OpenMetadataClient(
        base_url=cfg.openmetadata_uri(),
        username=cfg.openmetadata_username,
        password=cfg.openmetadata_password,
        verify_ssl=cfg.openmetadata_verify_ssl,
        timeout=10,
        service_name=cfg.openmetadata_service_name,
        service_type=service_type,
        database_name=database_name,
    )
    logger.debug("openmetadata_health_check_started")
    ok = client.health_check()
    if ok:
        logger.info("openmetadata_health_check_succeeded")
        console.print("[green]OpenMetadata is reachable[/green]")
        return
    logger.warning("openmetadata_health_check_failed")
    console.print("[red]OpenMetadata is not reachable[/red]")
    sys.exit(1)


@openmetadata.command()
@click.option("--include-namespace", multiple=True, help="Only sync these namespaces (repeatable)")
@click.option("--exclude-namespace", multiple=True, help="Skip these namespaces (repeatable)")
@click.option("--dbt/--no-dbt", default=True, help="Also sync dbt models (if manifest exists)")
@click.option("--dbt-schema", default=None, help="Limit dbt sync to a schema (e.g., bronze)")
def sync(
    include_namespace: tuple[str, ...],
    exclude_namespace: tuple[str, ...],
    dbt: bool,
    dbt_schema: str | None,
) -> None:
    """Sync Nessie catalog tables (and optionally dbt docs) into OpenMetadata;
    exits 1 when sync fails or OpenMetadata is unreachable.
    """
    enforce_surface_mutation_authorization("openmetadata.sync", get_openmetadata_adapter)
    logger.info(
        "openmetadata_sync_started",
        include_namespaces=list(include_namespace),
        exclude_namespaces=list(exclude_namespace),
        dbt_enabled=dbt,
        dbt_schema=dbt_schema,
    )
    cfg = get_settings()
    database_name = _resolve_database_name()
    service_type = _resolve_service_type()
    client = OpenMetadataClient(
        base_url=cfg.openmetadata_uri(),
        username=cfg.openmetadata_username,
        password=cfg.openmetadata_password,
        verify_ssl=cfg.openmetadata_verify_ssl,
        timeout=30,
        service_name=cfg.openmetadata_service_name,
        service_type=service_type,
        database_name=database_name,
    )

    if not client.health_check():
        logger.warning("openmetadata_sync_health_check_failed")
        console.print("[red]OpenMetadata is not reachable[/red]")
        sys.exit(1)

    try:
        scanner = resolve_catalog_scanner(cfg.openmetadata_catalog_scanner)
    except RuntimeError as exc:
        logger.error(
            "openmetadata_catalog_scanner_unavailable",
            scanner_name=cfg.openmetadata_catalog_scanner,
            error=str(exc),
        )
        raise user_error(
            "OpenMetadata catalog scanner is unavailable",
            details={"Scanner": cfg.openmetadata_catalog_scanner},
            run="phlo services status",
        ) from exc

    nessie_stats = sync_nessie_tables_to_openmetadata(
        scanner,
        client,
        include_namespaces=list(include_namespace) or None,
        exclude_namespaces=list(exclude_namespace) or None,
    )
    logger.info("openmetadata_nessie_sync_completed", stats=nessie_stats)
    console.print(f"[green]Nessie sync[/green]: {nessie_stats}")

    if dbt:
        try:
            parser = DbtManifestParser.from_settings(cfg)
            if dbt_schema:
                dbt_stats = parser.sync_to_openmetadata(client, schema_name=dbt_schema)
            else:
                # Default to syncing schemas present in manifest by iterating models.
                manifest = parser.load_manifest()
                schemas = {m.get("schema") for m in parser.get_models(manifest).values()}
                schemas = {s for s in schemas if isinstance(s, str)}
                dbt_stats = {"created": 0, "failed": 0}
                for schema_name in sorted(schemas):
                    partial = parser.sync_to_openmetadata(client, schema_name=schema_name)
                    dbt_stats["created"] += partial.get("created", 0)
                    dbt_stats["failed"] += partial.get("failed", 0)
            console.print(f"[green]dbt sync[/green]: {dbt_stats}")
            logger.info("openmetadata_dbt_sync_completed", stats=dbt_stats)
        except FileNotFoundError:
            logger.warning("openmetadata_dbt_manifest_missing")
            console.print("[yellow]dbt manifest not found; skipping dbt sync[/yellow]")
        except json.JSONDecodeError as e:
            logger.error("openmetadata_dbt_manifest_invalid_json", error=str(e))
            raise user_error(
                "could not parse dbt manifest",
                run="dbt docs generate",
            ) from e
