"""Guarded plan-first maintenance operations (ADR 0049, Plan 010).

`phlo operations maintenance inventory|plan|apply` — inventory is read-only,
plan is mutation-free and returns a JSON envelope, apply is authorized and
bound to the exact plan token. Orphan deletion is always rejected.
"""

from __future__ import annotations

import json
from typing import Any

import click

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.logging import get_logger

logger = get_logger(__name__)


def _emit(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=False))


@click.group("operations")
def operations_group() -> None:
    """Guarded plan-first operations (maintenance, backup, restore, upgrade)."""


@operations_group.group("maintenance")
def maintenance_group() -> None:
    """Plan and apply v1 table maintenance (compaction, snapshot expiry)."""


@maintenance_group.command("inventory")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
def maintenance_inventory(output_format: str) -> None:
    """List v1 tables with their provider and maintenance state (read-only)."""
    from phlo.capabilities import list_capabilities, resolve_capability
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()
    executors = list_capabilities("maintenance_executor")
    if not executors:
        raise click.ClickException("no maintenance executor capability is registered")

    inventory: list[dict[str, Any]] = []
    for name in executors:
        resolution = resolve_capability("maintenance_executor", name)
        if resolution is None:
            continue
        provider = resolution.provider
        get_inventory = getattr(provider, "get_inventory", None)
        if callable(get_inventory):
            inventory.extend(get_inventory())

    if output_format == "json":
        _emit({"executors": executors, "tables": inventory})
    else:
        click.echo(f"Executors: {', '.join(executors)}")
        for entry in inventory:
            click.echo(f"  {entry}")


@maintenance_group.command("plan")
@click.option("--operation", type=click.Choice(["compact", "snapshot_expiry"]), required=True)
@click.option("--table", required=True, help="Fully qualified table name.")
@click.option("--ref", default="main", help="Catalog ref/branch.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="json")
def maintenance_plan(operation: str, table: str, ref: str, output_format: str) -> None:
    """Create a deterministic maintenance plan (read-only, no mutation)."""
    from phlo.capabilities import resolve_capability
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()
    resolution = resolve_capability("maintenance_executor", operation)
    if resolution is None:
        raise click.ClickException(f"no maintenance executor registered for {operation!r}")

    provider = resolution.provider
    plan_fn = getattr(provider, "plan", None)
    if not callable(plan_fn):
        raise click.ClickException(
            f"maintenance executor {resolution.name!r} does not support planning"
        )

    plan = plan_fn(table_name=table, ref=ref)
    _emit(plan)


@maintenance_group.command("apply")
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to the JSON plan file.",
)
@click.option("--confirmation-token", required=True, help="The plan token from the plan step.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="json")
@require_mutation_authorization("operations.maintenance.apply")
def maintenance_apply(plan_path: str, confirmation_token: str, output_format: str) -> None:
    """Apply an exact, still-current maintenance plan (authorized, fail-before-mutation)."""
    from pathlib import Path

    from phlo.capabilities import resolve_capability
    from phlo.capabilities.discovery import discover_capabilities
    from phlo.operations.journal import (
        OperationJournalError,
        claim_operation,
        complete_operation,
        mark_submitted,
    )

    with Path(plan_path).open(encoding="utf-8") as f:
        plan_data = json.load(f)

    plan_token = plan_data.get("plan_token", "")
    operation = plan_data.get("operation", "")
    table = plan_data.get("table_name", "")
    ref = plan_data.get("ref", "main")

    if not plan_token or plan_token != confirmation_token:
        raise click.ClickException("confirmation token does not match the plan token")

    if operation == "orphan_delete":
        raise click.ClickException("orphan deletion is unsupported in v1")

    discover_capabilities()
    resolution = resolve_capability("maintenance_executor", operation)
    if resolution is None:
        raise click.ClickException(f"no maintenance executor registered for {operation!r}")

    provider = resolution.provider
    execute_fn = getattr(provider, "execute", None)
    if not callable(execute_fn):
        raise click.ClickException(
            f"maintenance executor {resolution.name!r} does not support execution"
        )

    from phlo.operations.journal import InMemoryOperationJournalStore

    journal = InMemoryOperationJournalStore()
    operation_id = f"{operation}:{table}:{ref}"
    try:
        claim_operation(
            journal,
            operation_id=operation_id,
            subject="operator",
            action=operation,
            target=table,
            plan_token=plan_token,
        )
        mark_submitted(journal, operation_id)
        result = execute_fn(table_name=table, ref=ref, plan_token=plan_token)
        result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        complete_operation(journal, operation_id, result_dict)
        if output_format == "json":
            _emit(result_dict)
        else:
            click.echo(
                f"Maintenance {operation} on {table}: {result_dict.get('status', 'unknown')}"
            )
    except OperationJournalError as exc:
        raise click.ClickException(
            f"journal error: {exc.code} ({', '.join(exc.identifiers)})"
        ) from exc
