"""Service management commands for the phlo CLI.

Subcommands import lazily on first invocation so that plugin discovery pulling
in utility modules stays lightweight; registration runs once per process.
"""

from __future__ import annotations

import click

_COMMANDS_REGISTERED = False


def _register_commands() -> None:
    """Import and register service subcommands lazily.

    This keeps `phlo.cli.commands.services` lightweight when utility modules are imported
    from service-specific plugins during CLI plugin discovery.
    """
    global _COMMANDS_REGISTERED
    if _COMMANDS_REGISTERED:
        return

    from phlo.cli.commands.services.add import add_cmd
    from phlo.cli.commands.services.exec import exec_cmd
    from phlo.cli.commands.services.init import init_cmd
    from phlo.cli.commands.services.list import list_cmd
    from phlo.cli.commands.services.logs import logs_cmd
    from phlo.cli.commands.services.ports import ports_cmd
    from phlo.cli.commands.services.remove import remove_cmd
    from phlo.cli.commands.services.reset import reset_cmd
    from phlo.cli.commands.services.restart import restart_cmd
    from phlo.cli.commands.services.start import start_cmd
    from phlo.cli.commands.services.status import status_cmd
    from phlo.cli.commands.services.stop import stop_cmd

    services_group.add_command(init_cmd)
    services_group.add_command(list_cmd)
    services_group.add_command(ports_cmd)
    services_group.add_command(start_cmd)
    services_group.add_command(stop_cmd)
    services_group.add_command(reset_cmd)
    services_group.add_command(restart_cmd)
    services_group.add_command(status_cmd)
    services_group.add_command(add_cmd)
    services_group.add_command(remove_cmd)
    services_group.add_command(logs_cmd)
    services_group.add_command(exec_cmd)
    _COMMANDS_REGISTERED = True


@click.group(name="services", invoke_without_command=True)
@click.pass_context
def services_group(ctx: click.Context) -> None:
    """Manage Phlo infrastructure services (Docker)."""
    _register_commands()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


__all__ = ["services_group", "_register_commands"]
