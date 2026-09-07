"""Discover the installed CLI contract without invoking domain operations.

The Click tree is the source of truth: optional package commands appear only
when registered. Agents can inspect parameters and supported output/preview
modes before choosing a command; humans get a compact command catalogue.
"""

from __future__ import annotations

from typing import Any

import click

from phlo.cli.contract import PhloCommand
from phlo.cli.output import json_envelope


def _describe(command: click.Command, path: list[str]) -> dict[str, Any]:
    parameters = []
    flags: set[str] = set()
    for param in command.params:
        options = list(param.opts) if isinstance(param, click.Option) else []
        flags.update(options)
        item: dict[str, Any] = {
            "name": param.name,
            "kind": "option" if isinstance(param, click.Option) else "argument",
            "options": options,
            "type": param.type.name,
            "required": param.required,
            "nargs": param.nargs,
        }
        if isinstance(param, click.Option):
            item.update(multiple=param.multiple, is_flag=param.is_flag, help=param.help)
            item["secondary_options"] = list(param.secondary_opts)
        if isinstance(param.type, click.Choice):
            item["choices"] = list(param.type.choices)
        parameters.append(item)
    group = isinstance(command, click.Group)
    return {
        "command": " ".join(path),
        "description": command.get_short_help_str(limit=200),
        "kind": "group" if group else "command",
        "parameters": parameters,
        "capabilities": {
            "json": "--json" in flags and not group,
            "dry_run": "--dry-run" in flags,
            "confirmation_flag": "--yes" in flags,
        },
    }


def describe_commands(root: click.Group, path: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Return deterministic metadata from registered commands, never execute them."""
    command: click.Command = root
    prefix = ["phlo"]
    for part in path:
        if not isinstance(command, click.Group) or part not in command.commands:
            raise click.BadParameter(f"Unknown command path: {' '.join(path)}", param_hint="PATH")
        command = command.commands[part]
        prefix.append(part)
    items = [_describe(command, prefix)]

    def visit(group: click.Group, parent: list[str]) -> None:
        for name, child in sorted(group.commands.items()):
            if child.hidden:
                continue
            child_path = [*parent, name]
            items.append(_describe(child, child_path))
            if isinstance(child, click.Group):
                visit(child, child_path)

    if isinstance(command, click.Group):
        visit(command, prefix)
    return items


@click.command("commands", cls=PhloCommand)
@click.argument("path", nargs=-1)
@click.option(
    "--json", "output_json", is_flag=True, help="Emit installed command metadata as JSON."
)
@click.pass_context
def commands_cmd(ctx: click.Context, path: tuple[str, ...], output_json: bool) -> None:
    """List installed commands and their output, preview, and confirmation options.

    Examples:
        phlo commands
        phlo commands services reset --json
        phlo --json commands
    """
    root = ctx.find_root().command
    if not isinstance(root, click.Group):
        raise click.ClickException("Command discovery must be invoked through phlo.")
    items = describe_commands(root, path)
    if output_json:
        click.echo(json_envelope(data={"commands": items}))
        return
    for item in items:
        if item["kind"] == "group":
            continue
        capabilities = item["capabilities"]
        modes = [name for name in ("json", "dry_run") if capabilities[name]]
        suffix = f" [{', '.join(modes)}]" if modes else ""
        click.echo(f"{item['command']}{suffix}\n  {item['description']}")
    click.echo("\nUse phlo commands <command path> --json for parameters and capabilities.")
