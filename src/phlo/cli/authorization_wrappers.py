"""CLI command authorization wrappers.

Provides decorators and wrapper functions to enforce authorization
on mutation commands through the CLI regulated surface adapter.

Usage:
    from phlo.cli.authorization import require_mutation_authorization

    @click.command("start")
    @click.option(...)
    @require_mutation_authorization("services.start")
    def start_cmd(...):
        ...

Or use the enforce_mutation context manager for more control:
    from phlo.cli.authorization import enforce_mutation_context

    def some_mutation_handler():
        with enforce_mutation_context("services.start", resource_id="my-service"):
            # mutation logic
            pass
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

import click

from phlo.logging import get_logger
from phlo.security.adapters import EnforcementResult
from phlo.security.enforcement import EnforcementContext

logger = get_logger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


def require_mutation_authorization(
    command: str,
    resource_id: str | Callable[[Any], str] | None = None,
    when: Callable[[dict[str, Any]], bool] | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator that enforces authorization on a mutation Click command.

    Resolves the principal from the environment and enforces through core,
    exiting when denied. ``resource_id`` may be None (command as id), a static
    string, or a callable receiving the click.Context. ``when`` optionally
    gates enforcement on the callback kwargs; a broken predicate enforces
    rather than bypasses authorization.

    Example:
        @click.command("start")
        @click.option("-d", "--detach", is_flag=True)
        @require_mutation_authorization("services.start", resource_id=lambda ctx: ctx.params.get("service"))
        def start_cmd(detach, service):
            ...
    """

    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if not check_cli_surface_active():
                return fn(*args, **kwargs)

            if when is not None:
                # A broken predicate enforces rather than bypasses authorization.
                try:
                    should_enforce = when(dict(kwargs))
                except Exception:
                    should_enforce = True
                if not should_enforce:
                    return fn(*args, **kwargs)

            from phlo.cli.authorization import get_cli_adapter

            resolved_resource_id: str | None = None
            if resource_id is None:
                resolved_resource_id = None
            elif isinstance(resource_id, str):
                resolved_resource_id = resource_id
            else:
                try:
                    resolved_resource_id = resource_id(kwargs.get("ctx"))
                except Exception:
                    resolved_resource_id = command

            adapter = get_cli_adapter()
            result = adapter.enforce_mutation(command, resolved_resource_id)

            if not result.allowed:
                logger.warning(
                    "cli_command_authorization_denied",
                    command=command,
                    reason_code=result.reason_code,
                    explanation=result.explanation,
                )
                msg = f"Error: Authorization denied for '{command}'"
                if result.explanation:
                    msg += f": {result.explanation}"
                click.echo(msg, err=True)
                raise SystemExit(1)

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def enforce_surface_mutation_authorization(
    command: str,
    adapter_getter: Callable[[], Any],
    resource_id: str | None = None,
) -> None:
    """Enforce a package CLI mutation command when regulated mode is active."""
    if not check_cli_surface_active():
        return

    adapter = adapter_getter()
    result = adapter.enforce_mutation(command, resource_id)
    if result.allowed:
        return

    logger.warning(
        "cli_surface_command_authorization_denied",
        command=command,
        reason_code=result.reason_code,
        explanation=result.explanation,
    )
    msg = f"Error: Authorization denied for '{command}'"
    if result.explanation:
        msg += f": {result.explanation}"
    click.echo(msg, err=True)
    raise SystemExit(1)


class MutationContext:
    """Context manager for enforcing mutation authorization around a block."""

    def __init__(
        self,
        command: str,
        resource_id: str | None = None,
        adapter=None,
    ) -> None:
        self.command = command
        self.resource_id = resource_id
        self.adapter = adapter
        self._result: EnforcementResult | None = None

    def __enter__(self) -> MutationContext:
        if self.adapter is None:
            from phlo.cli.authorization import get_cli_adapter

            self.adapter = get_cli_adapter()

        self._result = self.adapter.enforce_mutation(self.command, self.resource_id)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    @property
    def result(self) -> EnforcementResult | None:
        """Return the enforcement result captured on entry, or None before entry."""
        return self._result

    def is_allowed(self) -> bool:
        """Return True when enforcement ran and allowed the mutation."""
        return self._result is not None and self._result.allowed


def enforce_mutation_context(
    command: str,
    resource_id: str | None = None,
) -> MutationContext:
    """Create a mutation context for use in a with statement.

    Example:
        from phlo.cli.authorization import enforce_mutation_context

        def stop_service(service_name: str):
            with enforce_mutation_context("services.stop", resource_id=service_name) as ctx:
                if not ctx.is_allowed():
                    raise PermissionError("Not authorized")
                # perform stop
    """
    return MutationContext(command=command, resource_id=resource_id)


def emit_cli_audit_event(
    command: str,
    decision: str,
    subject: str,
    resource_type: str,
    resource_id: str,
    reason_code: str | None = None,
) -> None:
    """Emit a CLI audit event after an authorization decision ("allowed" or "denied").

    Called by command wrappers; emission failures are logged, never raised.
    """
    try:
        ctx = EnforcementContext.get_instance()
        ctx.audit_emitter.emit_authorization(
            surface="phlo-cli",
            action=f"cli.{command}",
            resource_type=resource_type,
            resource_id=resource_id,
            actor_subject=subject,
            actor_type="user",
            actor_roles=(),
            authentication_source=os.environ.get("PHLO_AUTH_TYPE", "unknown"),
            decision=decision,
            reason_code=reason_code or "",
            policy_id=None,
            request_id=os.environ.get("PHLO_REQUEST_ID"),
        )
    except Exception:
        logger.exception("cli_audit_event_emission_failed")


def check_cli_surface_active() -> bool:
    """Check if the CLI surface is active and regulated mode is enabled.

    Detection failure counts as not regulated, so commands run without
    enforcement rather than being blocked by an unrelated error.
    """
    try:
        from phlo.security.mode import is_regulated

        return is_regulated()
    except Exception:
        return False
