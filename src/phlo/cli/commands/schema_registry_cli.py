"""Schema registry CLI commands.

Implements the ``contracts`` group: snapshot a JSON schema into the
registry and check a table's schema compatibility against its previous
snapshot, exiting non-zero when compatibility fails.
Imported by the phlo CLI main entry point, which mounts the contracts command group.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from phlo.capabilities.schema import CLASSIFICATION_ORDER
from phlo.logging import get_logger
from phlo.schema_migration.planning import plan_schema_migration
from phlo.schema_registry import (
    SchemaRegistry,
    deserialize_schema,
    resolve_registry_db_url,
)

console = Console()
logger = get_logger(__name__)


def _require_registry_db_url() -> str:
    """Resolve and validate schema registry database URL."""
    db_url = resolve_registry_db_url()
    if db_url:
        return db_url

    console.print("[red]No registry database URL configured.[/red]")
    console.print(
        "Set PHLO_REGISTRY_DB_URL, PHLO_LINEAGE_DB_URL, or DAGSTER_PG_DB_CONNECTION_STRING."
    )
    raise SystemExit(1)


@click.group("contracts")
def contracts() -> None:
    """Schema registry and data contract management."""


@contracts.command("snapshot")
@click.option("--table", required=True, help="Fully-qualified table name")
@click.option(
    "--schema-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to canonical schema JSON file",
)
@click.option("--run-id", default=None, help="Pipeline run ID")
@click.option("--source", default="cli", help="Snapshot source label")
def snapshot(table: str, schema_file: str, run_id: str | None, source: str) -> None:
    """Snapshot a schema from a JSON file into the registry."""
    db_url = _require_registry_db_url()

    try:
        with Path(schema_file).open() as f:
            schema = deserialize_schema(f.read())
    except Exception as exc:
        raise click.ClickException(f"Failed to read schema file: {exc}") from exc

    registry = SchemaRegistry(db_url)
    try:
        snapshot_id = registry.snapshot_schema(table, schema, run_id=run_id, source=source)
    except Exception as exc:
        raise click.ClickException(f"Failed to snapshot schema: {exc}") from exc
    console.print(f"[green]Snapshot:[/green] {snapshot_id}")


@contracts.command("check")
@click.option("--table", required=True, help="Fully-qualified table name")
@click.option(
    "--fail-on",
    type=click.Choice(["breaking", "warning"]),
    default="breaking",
    help="Exit non-zero when worst classification meets or exceeds this level",
)
def check(table: str, fail_on: str) -> None:
    """Check schema compatibility for a table against its previous snapshot.

    Exits with status 1 when the change classification ranks at or beyond
    ``--fail-on`` (safe < warning < breaking), so it can gate CI.
    """
    db_url = _require_registry_db_url()

    registry = SchemaRegistry(db_url)
    try:
        snapshots = registry.get_latest_snapshots(table, limit=2)
        if len(snapshots) < 2:
            console.print(
                f"[yellow]Fewer than 2 snapshots for {table}; nothing to compare.[/yellow]"
            )
            return

        current = deserialize_schema(snapshots[0].schema_json)
        previous = deserialize_schema(snapshots[1].schema_json)
        plan = plan_schema_migration(table_name=table, current=previous, desired=current)
    except Exception as exc:
        raise click.ClickException(f"Failed to check schema compatibility: {exc}") from exc

    classification_colors = {"safe": "green", "warning": "yellow", "breaking": "red"}
    color = classification_colors.get(plan.classification, "white")

    console.print(f"\n[bold]Compatibility Check: {plan.table_name}[/bold]")
    console.print(f"Classification: [{color}]{plan.classification}[/{color}]")
    console.print(f"Requires approval: {'Yes' if plan.requires_approval else 'No'}\n")

    if plan.changes:
        tbl = Table()
        tbl.add_column("Field", style="cyan")
        tbl.add_column("Change", style="magenta")
        tbl.add_column("Old", style="dim")
        tbl.add_column("New", style="dim")
        tbl.add_column("Classification")

        for change in plan.changes:
            c_color = classification_colors.get(change.classification, "white")
            tbl.add_row(
                change.field_name,
                change.change_type,
                change.old_value or "",
                change.new_value or "",
                f"[{c_color}]{change.classification}[/{c_color}]",
            )
        console.print(tbl)
    else:
        console.print("[green]No changes detected.[/green]")

    fail_on_idx = CLASSIFICATION_ORDER.index(fail_on)
    actual_idx = CLASSIFICATION_ORDER.index(plan.classification)
    if actual_idx >= fail_on_idx:
        sys.exit(1)
