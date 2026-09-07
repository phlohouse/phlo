"""Shared invocation boundary for human and machine CLI clients.

Commands still own their data and human presentation. This boundary selects a
command's explicit JSON renderer, normalizes legacy JSON documents, and renders
parse, authorization, and execution failures through the same envelope. Raw
exports and subprocess streams are never captured unless the command explicitly
advertises a JSON result. In particular, a child's ``--json`` after ``--`` is
not a Phlo option.
"""

from __future__ import annotations

import functools
import io
import json
import logging
import sys
from collections.abc import Sequence
from contextlib import redirect_stdout
from typing import Any, cast
from weakref import WeakSet

import click

from phlo.cli.output import json_envelope, user_error

_CONFIGURED_COMMANDS: WeakSet[click.Command] = WeakSet()


def _json_option(command: click.Command) -> click.Option | None:
    return next(
        (p for p in command.params if isinstance(p, click.Option) and "--json" in p.opts),
        None,
    )


def _requests_json(command: click.Command, args: Sequence[str]) -> bool:
    """Recognize Phlo options without mistaking argument values for flags.

    This is an intent check before Click parses (and potentially rejects) the
    invocation. Click remains authoritative for validation. Stop at passthrough
    arguments, so wrapped programs retain ownership of their option vocabulary.
    """
    tokens = list(args)
    options = {
        opt: p
        for p in command.params
        if isinstance(p, click.Option)
        for opt in (*p.opts, *p.secondary_opts)
    }
    positional = [p for p in command.params if isinstance(p, click.Argument)]
    index = 0
    arg_index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return False
        name = token.split("=", 1)[0]
        option = options.get(name)
        if option is not None:
            if name == "--json":
                return True
            index += 1
            if not option.is_flag and "=" not in token:
                index += option.nargs
            continue
        if token.startswith("-"):
            index += 1
            continue
        if isinstance(command, click.Group):
            child = command.commands.get(token)
            if child is None:
                # Still honor a root machine request on a misspelled command.
                return False
            return _requests_json(child, tokens[index + 1 :])
        if arg_index < len(positional):
            argument = positional[arg_index]
            if argument.type == click.UNPROCESSED:
                return False
            if argument.nargs == -1:
                index += 1
                continue
            arg_index += 1
            index += max(argument.nargs, 1)
        else:
            index += 1
    return False


def _set_mode(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if value:
        ctx.meta["phlo_json" if param.name == "json" else "phlo_non_interactive"] = True


def _prepare_command(ctx: click.Context, command: click.Command) -> None:
    option = _json_option(command)
    requested = bool(ctx.meta.get("phlo_json"))
    local = bool(option and option.name and ctx.params.get(option.name))
    if local:
        ctx.meta["phlo_json"] = True
    if requested and not isinstance(command, click.Group):
        if option is None:
            raise user_error(
                f"'{ctx.command_path}' does not provide a structured JSON result",
                reason_code="json_not_supported",
                details=["Run without --json to use its native output."],
                run="phlo commands --json",
            )
        if option.name and option.expose_value:
            ctx.params[option.name] = True


def _configure(command: click.Command) -> None:
    """Adapt package commands without replacing custom Click classes or parsers."""
    if isinstance(command, click.Group):
        for child in command.commands.values():
            _configure(child)
    if isinstance(command, (PhloCommand, PhloGroup)) or command in _CONFIGURED_COMMANDS:
        return
    original = command.invoke

    @functools.wraps(original)
    def invoke(ctx: click.Context) -> Any:
        _prepare_command(ctx, command)
        return original(ctx)

    # Preserve callback identity and its decorator chain for package consumers
    # which call the callback directly; policy belongs to Click invocation.
    cast(Any, command).invoke = invoke
    _CONFIGURED_COMMANDS.add(command)


class _InvocationBoundary(click.Command):
    """Own machine serialization once, at the outermost invoked command."""

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        args = list(sys.argv[1:] if args is None else args)
        if not _requests_json(self, args):
            return super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                **extra,
            )
        stdout = io.StringIO()
        error = None
        exit_code = 0
        try:
            with redirect_stdout(stdout):
                result = super().main(
                    args=args,
                    prog_name=prog_name,
                    complete_var=complete_var,
                    standalone_mode=False,
                    **extra,
                )
                # Click returns the code for Context.exit with standalone mode off.
                if isinstance(result, int):
                    exit_code = result
        except click.ClickException as exc:
            error = exc
            exit_code = exc.exit_code
        except (click.Abort, KeyboardInterrupt, EOFError):
            error = user_error("Operation cancelled", reason_code="cancelled")
            exit_code = 1
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:
            # Internal details may contain credentials; record only the exception type.
            logging.getLogger(__name__).error(
                "cli_unexpected_failure", extra={"error_type": type(exc).__name__}
            )
            error = user_error(
                "Unexpected command failure",
                reason_code="internal_error",
                run="phlo doctor",
            )
            exit_code = 1
        content = stdout.getvalue().strip()
        payload: dict[str, Any]
        if error is not None:
            payload = json.loads(
                json_envelope(
                    errors=[error.format_message()],
                    reason_code=getattr(
                        error,
                        "reason_code",
                        "invalid_arguments" if exit_code == 2 else "operation_failed",
                    ),
                    status="cancelled"
                    if getattr(error, "reason_code", None) == "cancelled"
                    else "error",
                    next_steps=getattr(error, "next_steps", []),
                )
            )
        else:
            try:
                data = json.loads(content)
            except ValueError:
                if "--help" in args or "--version" in args:
                    payload = json.loads(json_envelope(data={"help": content}))
                else:
                    payload = json.loads(
                        json_envelope(
                            errors=[
                                "Command failed"
                                if exit_code
                                else "Command did not produce a JSON result"
                            ],
                            reason_code="operation_failed" if exit_code else "invalid_json_output",
                        )
                    )
                    exit_code = exit_code or 1
            else:
                if isinstance(data, dict) and {"data", "warnings", "errors"} <= data.keys():
                    payload = data
                    payload.setdefault("schema_version", 1)
                    payload.setdefault(
                        "status", "error" if exit_code or data["errors"] else "success"
                    )
                    payload.setdefault("reason_code", None)
                    payload.setdefault("next_steps", [])
                else:
                    payload = json.loads(
                        json_envelope(data=data, status="error" if exit_code else "success")
                    )
                if exit_code and payload["status"] == "success":
                    payload["status"] = "error"
                if payload["status"] in {"error", "partial", "cancelled"} or payload["errors"]:
                    exit_code = exit_code or 1
                if exit_code and not payload.get("reason_code"):
                    payload["reason_code"] = "operation_failed"
        payload["exit_code"] = exit_code
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        if standalone_mode:
            raise SystemExit(exit_code)
        if exit_code:
            raise click.exceptions.Exit(exit_code)
        return payload


class PhloCommand(_InvocationBoundary):
    """A command with structured failures when its --json option is selected."""

    def invoke(self, ctx: click.Context) -> Any:
        _prepare_command(ctx, self)
        return super().invoke(ctx)


class PhloGroup(_InvocationBoundary, click.Group):
    """A group with inherited output/interaction policy for installed commands."""

    command_class = PhloCommand
    group_class = type

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.extend(
            [
                click.Option(
                    ["--json"],
                    is_flag=True,
                    expose_value=False,
                    callback=_set_mode,
                    help="Return a structured result (for commands advertising JSON support).",
                ),
                click.Option(
                    ["--non-interactive"],
                    is_flag=True,
                    expose_value=False,
                    callback=_set_mode,
                    help="Never prompt; fail when an action needs confirmation.",
                ),
            ]
        )

    def add_command(self, cmd: click.Command, name: str | None = None) -> None:
        _configure(cmd)
        super().add_command(cmd, name)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            # Core and plugin groups may populate children after registration.
            _configure(command)
        return command
