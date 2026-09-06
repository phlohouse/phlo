"""Airbyte CLI commands: status, connections, and manual sync."""

from __future__ import annotations

import click

from phlo.cli.output import command_failed_error


@click.command(name="airbyte")
@click.argument("airbyte_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def airbyte_group(ctx: click.Context, airbyte_args: tuple[str, ...]) -> None:
    """Interact with the Airbyte control plane (status, connections, sync)."""
    args = list(airbyte_args)
    if not args or args[0] in {"-h", "--help", "help"}:
        click.echo(ctx.get_help())
        return
    command = args.pop(0)
    if command == "status":
        _status()
        return
    if command == "connections":
        _connections()
        return
    if command == "sync":
        if not args:
            command_failed_error("sync requires a connection id")
        _sync(args[0])
        return
    click.echo(f"Unknown airbyte command: {command}", err=True)
    ctx.exit(2)


def _status() -> None:
    from phlo_airbyte.client import AirbyteClient

    client = AirbyteClient()
    healthy = client.health_check()
    click.echo(f"Airbyte health: {'ok' if healthy else 'unavailable'}")


def _connections() -> None:
    from phlo_airbyte.client import AirbyteClient

    client = AirbyteClient()
    try:
        connections = client.list_connections()
    except Exception as exc:
        command_failed_error(f"Could not list Airbyte connections: {exc}")
    for connection in connections:
        click.echo(f"  - {connection.get('connectionId', '?')}: {connection.get('name', '')}")


def _sync(connection_id: str) -> None:
    from phlo_airbyte.client import AirbyteClient

    client = AirbyteClient()
    try:
        evidence = client.run_sync(connection_id)
    except Exception as exc:
        command_failed_error(f"Airbyte sync failed: {exc}")
    click.echo(f"Sync {evidence['job_id']}: {evidence['status']}")
