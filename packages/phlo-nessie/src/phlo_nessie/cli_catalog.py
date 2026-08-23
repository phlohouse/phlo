"""Catalog CLI commands for Nessie-backed Iceberg.

This module provides CLI commands for managing and querying the lakehouse
catalog backed by Nessie. It uses PyIceberg for direct Iceberg table operations
when available.

Commands include listing tables, describing table metadata, and viewing table
snapshot history across Nessie branches.

Example:
    $ phlo catalog tables --ref main
    $ phlo catalog describe raw.customers --ref dev
    $ phlo catalog history raw.customers --limit 5

Commands:
    tables: List all Iceberg tables in the catalog.
    describe: Show detailed table metadata including schema and properties.
    history: Show table snapshot history with operation details.

"""

from __future__ import annotations

import json
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from phlo.cli.output import user_error
from phlo.logging import get_logger

console = Console()
logger = get_logger(__name__)


def _value_or_call(value):
    """Return an attribute value across PyIceberg property/method API versions."""
    return value() if callable(value) else value


def _schema_field_display(field) -> tuple[str, str, str]:
    """Return display-safe name, type, and required marker for PyIceberg fields."""
    field_type = getattr(field, "field_type", getattr(field, "type", None))
    required_flag = getattr(field, "required", None)
    if required_flag is None and field_type is not None:
        is_optional = getattr(field_type, "is_optional", False)
        required_flag = not is_optional
    required = "✓" if required_flag else ""
    return str(field.name), str(field_type), required


def _get_iceberg_catalog(ref: str = "main"):
    """Load the PyIceberg catalog for the specified Nessie reference.

    Raises: RuntimeError when the catalog backend or pyiceberg is not installed.

    Example:
        >>> catalog = _get_iceberg_catalog("main")
        >>> print(list(catalog.list_namespaces()))

    """
    logger.debug(
        "nessie_catalog_catalog_load_requested",
        ref=ref,
    )
    try:
        from phlo_nessie.catalog_backend import load_pyiceberg_catalog
    except ImportError as exc:  # pragma: no cover
        logger.error("nessie_catalog_catalog_backend_missing", ref=ref, exc_info=True)
        raise RuntimeError(
            "Iceberg catalog support is not installed. "
            "Install `phlo-nessie[iceberg-cli]` or `pyiceberg`."
        ) from exc

    return load_pyiceberg_catalog(ref=ref)


@click.group()
def catalog():
    """Manage the lakehouse catalog (Nessie-backed)."""


@catalog.command()
@click.option("--namespace", default=None, help="Filter by namespace (e.g., raw, bronze)")
@click.option("--ref", default="main", help="Nessie branch/tag reference")
@click.option("--format", "output_format", type=click.Choice(["table", "json"]), default="table")
def tables(namespace: Optional[str], ref: str, output_format: str) -> None:
    """List all Iceberg tables in the catalog."""
    logger.info(
        "nessie_catalog_tables_requested",
        namespace=namespace,
        ref=ref,
        output_format=output_format,
    )
    try:
        cat = _get_iceberg_catalog(ref=ref)

        all_tables: list[dict[str, str]] = []
        for ns_tuple in cat.list_namespaces():
            ns_name = ".".join(ns_tuple)
            if namespace and namespace != ns_name:
                continue
            try:
                for table_id in cat.list_tables(ns_name):
                    table_name = table_id[-1] if isinstance(table_id, tuple) else str(table_id)
                    full_name = ".".join(table_id) if isinstance(table_id, tuple) else str(table_id)
                    all_tables.append(
                        {
                            "namespace": ns_name,
                            "table": table_name,
                            "full_name": full_name,
                        }
                    )
            except Exception as e:
                logger.warning(
                    "nessie_catalog_tables_namespace_list_failed",
                    namespace=ns_name,
                    ref=ref,
                    error=str(e),
                    exc_info=True,
                )
                console.print(f"[yellow]Warning: Could not list tables in {ns_name}[/yellow]")

        if not all_tables:
            logger.info(
                "nessie_catalog_tables_empty",
                namespace=namespace,
                ref=ref,
            )
            if output_format == "json":
                click.echo("[]")
                return
            console.print("[yellow]No tables found[/yellow]")
            return

        if output_format == "json":
            logger.info(
                "nessie_catalog_tables_rendered",
                output_format=output_format,
                ref=ref,
                table_count=len(all_tables),
            )
            click.echo(json.dumps(all_tables, indent=2))
            return

        table = Table(title=f"Iceberg Tables (ref: {ref})")
        table.add_column("Namespace", style="cyan")
        table.add_column("Table Name", style="green")
        table.add_column("Full Name", style="dim")

        for row in sorted(all_tables, key=lambda x: x["full_name"]):
            table.add_row(row["namespace"], row["table"], row["full_name"])

        logger.info(
            "nessie_catalog_tables_rendered",
            output_format=output_format,
            ref=ref,
            table_count=len(all_tables),
        )
        console.print(table)
        console.print(f"\n[dim]Total: {len(all_tables)} tables[/dim]")
    except Exception as e:
        logger.error(
            "nessie_catalog_tables_failed",
            namespace=namespace,
            ref=ref,
            output_format=output_format,
            error=str(e),
            exc_info=True,
        )
        raise user_error(
            "could not list catalog tables",
            details={"Reference": ref},
            run="phlo services status",
        ) from e


@catalog.command()
@click.argument("table_name")
@click.option("--ref", default="main", help="Nessie branch/tag reference")
def describe(table_name: str, ref: str) -> None:
    """Show detailed table metadata."""
    logger.info(
        "nessie_catalog_describe_requested",
        table_name=table_name,
        ref=ref,
    )
    try:
        cat = _get_iceberg_catalog(ref=ref)
        try:
            table = cat.load_table(table_name)
        except Exception as e:
            logger.warning(
                "nessie_catalog_describe_table_not_found",
                table_name=table_name,
                ref=ref,
                error=str(e),
            )
            raise user_error(
                "table not found",
                details={
                    "Table": table_name,
                    "Reference": ref,
                },
                run=f"phlo catalog tables --ref {ref}",
            ) from e

        schema = table.schema()
        current_snapshot = table.current_snapshot()

        console.print(f"\n[bold blue]Table: {table_name}[/bold blue]")
        console.print(f"Location: {_value_or_call(table.location)}")
        console.print(
            f"Current Snapshot ID: {current_snapshot.snapshot_id if current_snapshot else 'None'}"
        )
        console.print(f"Format Version: {_value_or_call(table.format_version)}")

        console.print("\n[bold]Schema:[/bold]")
        schema_table = Table()
        schema_table.add_column("Column Name", style="cyan")
        schema_table.add_column("Type", style="green")
        schema_table.add_column("Required", justify="center")

        for field in schema.fields:
            schema_table.add_row(*_schema_field_display(field))
        console.print(schema_table)

        spec = table.spec()
        if spec and spec.fields:
            console.print("\n[bold]Partitioning:[/bold]")
            part_table = Table()
            part_table.add_column("Field", style="cyan")
            part_table.add_column("Transform", style="green")
            for part_field in spec.fields:
                part_table.add_row(str(part_field.source_id), str(part_field.transform))
            console.print(part_table)

        properties = _value_or_call(table.properties)
        if properties:
            console.print("\n[bold]Properties:[/bold]")
            prop_table = Table()
            prop_table.add_column("Key", style="cyan")
            prop_table.add_column("Value", style="green")
            for key, value in sorted(properties.items()):
                prop_table.add_row(key, value)
            console.print(prop_table)
        logger.info(
            "nessie_catalog_describe_succeeded",
            table_name=table_name,
            ref=ref,
            has_snapshot=current_snapshot is not None,
            schema_field_count=len(schema.fields),
        )
    except Exception as e:
        if isinstance(e, click.ClickException):
            raise
        logger.error(
            "nessie_catalog_describe_failed",
            table_name=table_name,
            ref=ref,
            error=str(e),
            exc_info=True,
        )
        raise user_error(
            "could not describe catalog table",
            details={
                "Table": table_name,
                "Reference": ref,
            },
            run="phlo services status",
        ) from e


@catalog.command()
@click.argument("table_name")
@click.option("--limit", type=int, default=10, help="Number of snapshots to show")
@click.option("--ref", default="main", help="Nessie branch/tag reference")
@click.option("--format", "output_format", type=click.Choice(["table", "json"]), default="table")
def history(table_name: str, limit: int, ref: str, output_format: str) -> None:
    """Show table snapshot history."""
    logger.info(
        "nessie_catalog_history_requested",
        table_name=table_name,
        limit=limit,
        ref=ref,
        output_format=output_format,
    )
    try:
        cat = _get_iceberg_catalog(ref=ref)
        try:
            table = cat.load_table(table_name)
        except Exception as exc:
            logger.warning(
                "nessie_catalog_history_table_not_found",
                table_name=table_name,
                ref=ref,
                error=str(exc),
                exc_info=True,
            )
            raise user_error(
                "table not found",
                details={
                    "Table": table_name,
                    "Reference": ref,
                },
                run=f"phlo catalog tables --ref {ref}",
            ) from exc

        snapshots = []
        for snapshot in table.snapshots():
            summary = snapshot.summary or {}
            snapshots.append(
                {
                    "id": snapshot.snapshot_id,
                    "timestamp": snapshot.timestamp_ms,
                    "operation": summary.get("operation", ""),
                    "added_files": summary.get("added-data-files", ""),
                    "removed_files": summary.get("deleted-data-files", ""),
                }
            )

        snapshots = sorted(snapshots, key=lambda x: x["timestamp"], reverse=True)[:limit]

        if output_format == "json":
            logger.info(
                "nessie_catalog_history_rendered",
                table_name=table_name,
                ref=ref,
                output_format=output_format,
                snapshot_count=len(snapshots),
            )
            click.echo(json.dumps(snapshots, indent=2))
            return

        table_out = Table(title=f"Snapshot History: {table_name} (ref: {ref})")
        table_out.add_column("Snapshot ID", style="cyan")
        table_out.add_column("Timestamp (ms)", style="green")
        table_out.add_column("Operation")
        table_out.add_column("Added Files")
        table_out.add_column("Removed Files")

        for s in snapshots:
            table_out.add_row(
                str(s["id"]),
                str(s["timestamp"]),
                str(s["operation"]),
                str(s["added_files"]),
                str(s["removed_files"]),
            )

        logger.info(
            "nessie_catalog_history_rendered",
            table_name=table_name,
            ref=ref,
            output_format=output_format,
            snapshot_count=len(snapshots),
        )
        console.print(table_out)
    except Exception as e:
        if isinstance(e, click.ClickException):
            raise
        logger.error(
            "nessie_catalog_history_failed",
            table_name=table_name,
            limit=limit,
            ref=ref,
            output_format=output_format,
            error=str(e),
            exc_info=True,
        )
        raise user_error(
            "could not show table history",
            details={
                "Table": table_name,
                "Reference": ref,
            },
            run="phlo services status",
        ) from e
