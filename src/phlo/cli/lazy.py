"""Root command metadata and lazy resolution.

Help uses static descriptions, not provider imports. The complete Click tree is
loaded explicitly by the command catalogue and reference-document generator.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stdout
from importlib import import_module
from io import StringIO

import click

from phlo.cli.contract import PhloGroup


class LazyPhloGroup(PhloGroup):
    """Resolve built-ins individually and discover extensions on a lookup miss."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lazy_commands: dict[str, tuple[str, str]] = {}
        self.plugin_help: Callable[[], dict[str, str]] = dict
        self.load_plugins: Callable[[], None] | None = None

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(set(self.commands) | self.lazy_commands.keys() | self.plugin_help().keys())

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        # Imports and provider initialization are not command output.
        with redirect_stdout(StringIO()):
            if cmd_name in self.lazy_commands:
                target, _ = self.lazy_commands[cmd_name]
                module, attribute = target.split(":")
                command = getattr(import_module(module), attribute)
                if not isinstance(command, click.Command):
                    command = command()
                self.add_command(command, cmd_name)
                del self.lazy_commands[cmd_name]
            command = super().get_command(ctx, cmd_name)
            if command is None and self.load_plugins is not None:
                self.load_plugins()
                command = super().get_command(ctx, cmd_name)
        return command

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # Click's default formatter resolves every command, defeating laziness.
        plugin_help = self.plugin_help()
        names = sorted(set(self.commands) | self.lazy_commands.keys() | plugin_help.keys())
        limit = formatter.width - 6 - max(map(len, names), default=0)
        rows = []
        for name in names:
            command = self.commands.get(name)
            if command is not None:
                if command.hidden:
                    continue
                description = command.get_short_help_str(limit=limit)
            else:
                text = (
                    self.lazy_commands[name][1] if name in self.lazy_commands else plugin_help[name]
                )
                description = click.Command(name, help=text).get_short_help_str(limit=limit)
            rows.append((name, description))
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)

    def materialize_commands(self) -> None:
        """Load the full command contract without executing command callbacks."""
        with click.Context(self) as ctx, redirect_stdout(StringIO()):
            for name in list(self.lazy_commands):
                self.get_command(ctx, name)
            if self.load_plugins is not None:
                self.load_plugins()
