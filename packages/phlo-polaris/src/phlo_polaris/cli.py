"""Polaris CLI commands: status, bootstrap, and Nessie migration.

Registered on the root ``phlo`` CLI through the ``phlo.plugins.cli`` entry
point. Mutating commands require surface-mutation authorization.
"""

from __future__ import annotations

import click
import requests

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.output import command_failed_error
from phlo_polaris.authorization import get_polaris_cli_adapter


@click.command(name="polaris")
@click.argument("polaris_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def polaris_group(ctx: click.Context, polaris_args: tuple[str, ...]) -> None:
    """Manage the Polaris catalog service (status, bootstrap, migration)."""
    polaris_group_impl(ctx, polaris_args)


def polaris_group_impl(ctx: click.Context, polaris_args: tuple[str, ...]) -> None:
    args = list(polaris_args)
    if not args or args[0] in {"-h", "--help", "help"}:
        click.echo(ctx.get_help())
        return
    command = args.pop(0)
    if command == "status":
        _status()
        return
    if command == "bootstrap":
        _bootstrap()
        return
    if command == "migrate-from-nessie":
        _migrate_from_nessie(confirm="--confirm" in args)
        return
    click.echo(f"Unknown polaris command: {command}", err=True)
    ctx.exit(2)


def _status() -> None:
    from phlo_polaris.resource import PolarisResource

    client = PolarisResource()
    healthy = client.health_check()
    click.echo(f"Polaris health:  {'ok' if healthy else 'unavailable'}")
    if not healthy:
        return
    try:
        catalogs = client.list_catalogs()
    except requests.RequestException as exc:
        command_failed_error(f"Could not list Polaris catalogs: {exc}")
    for catalog in catalogs:
        click.echo(f"  - {catalog.get('name', '?')}")


def _bootstrap() -> None:
    enforce_surface_mutation_authorization("bootstrap.run", get_polaris_cli_adapter)
    from phlo_polaris.hooks import bootstrap

    code = bootstrap()
    if code != 0:
        command_failed_error("Polaris bootstrap failed")
    click.echo("Polaris bootstrap complete")


def _migrate_from_nessie(*, confirm: bool) -> None:
    from phlo_polaris.migration import import_tables, plan_migration

    try:
        entries = plan_migration()
    except Exception as exc:
        command_failed_error(f"Could inventory the Nessie source catalog: {exc}")
    click.echo(f"Migration plan: {len(entries)} table(s)")
    for entry in entries:
        click.echo(f"  - {entry.namespace}.{entry.table_name}")
    if not confirm:
        click.echo(
            "Dry run only. Pass --confirm to register these tables in Polaris. "
            "Nessie metadata and data are never modified."
        )
        return
    enforce_surface_mutation_authorization("migrate.import", get_polaris_cli_adapter)
    results = import_tables(entries)
    registered = sum(1 for result in results if result.get("registered"))
    click.echo(f"Registered {registered}/{len(results)} table(s) in Polaris")


@click.command(name="polaris-status")
def polaris_status_command() -> None:
    """Show Polaris service health and registered catalogs."""
    _status()
