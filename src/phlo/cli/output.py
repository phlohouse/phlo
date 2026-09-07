"""Shared user-facing CLI output helpers.

``json_envelope`` is the machine-readable contract: every JSON response
carries ``data``, ``warnings``, and ``errors``. Exceptions built here hold
user-facing text only; internal diagnostics go to structured logs instead.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import click


def json_envelope(
    *,
    data: Any = None,
    warnings: Sequence[str] | None = None,
    errors: Sequence[str] | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    next_steps: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Render the shared agent-friendly JSON envelope."""
    return json.dumps(
        {
            "schema_version": 1,
            "status": status or ("error" if errors else "success"),
            "data": data,
            "warnings": list(warnings or ()),
            "errors": list(errors or ()),
            "reason_code": reason_code,
            "next_steps": list(next_steps or ()),
        },
        indent=2,
        sort_keys=True,
    )


def user_error(
    summary: str,
    *,
    missing: str | Path | None = None,
    run: str | None = None,
    details: Mapping[str, Any] | Sequence[str] | None = None,
    reason_code: str = "operation_failed",
) -> click.ClickException:
    """Build a concise, recoverable CLI error.

    The returned exception intentionally contains only user-facing text. Internal
    diagnostics should be logged separately with structured fields.
    """
    lines = [summary]

    if missing is not None:
        lines.extend(["", f"Missing: {missing}"])

    if details:
        lines.append("")
        if isinstance(details, Mapping):
            lines.extend(f"{key}: {value}" for key, value in details.items())
        else:
            lines.extend(str(item) for item in details)

    if run:
        lines.extend(["", f"Run: {run}"])

    return PhloError("\n".join(lines), reason_code=reason_code, run=run)


class PhloError(click.ClickException):
    """One failure with human text and machine-readable recovery metadata."""

    def __init__(self, message: str, *, reason_code: str, run: str | None = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.next_steps = [{"command": run, "when": "Resolve this error"}] if run else []


def confirm_action(message: str, *, yes: bool = False, non_interactive: bool = False) -> bool:
    """Never wait for approval when stdin is a pipe or machine output is requested."""
    if yes:
        return True
    ctx = click.get_current_context(silent=True)
    machine = bool(ctx and ctx.meta.get("phlo_json"))
    unattended = non_interactive or bool(ctx and ctx.meta.get("phlo_non_interactive"))
    if machine or unattended or not sys.stdin.isatty():
        raise user_error(
            "Confirmation required",
            details=["Review the dry-run preview, then pass --yes to approve this action."],
            reason_code="confirmation_required",
        )
    return click.confirm(message, default=False)


def missing_phlo_project_error() -> click.ClickException:
    """Return the standard error for commands that require `.phlo/`."""
    return user_error(
        "Phlo services have not been initialized",
        missing=".phlo/",
        run="phlo services init",
        reason_code="project_not_initialized",
    )


def missing_compose_file_error(compose_file: str | Path) -> click.ClickException:
    """Return the standard error for commands that require generated Compose config."""
    return user_error(
        "Phlo services have not been initialized",
        missing=compose_file,
        run="phlo services init",
        reason_code="project_not_initialized",
    )


def exclusive_options_error(left: str, right: str) -> click.ClickException:
    """Return the standard error for mutually-exclusive input options."""
    return user_error(
        "choose one input source",
        details=[f"Use either {left} or {right}, not both."],
    )


def missing_query_error(*, command_hint: str) -> click.ClickException:
    """Return the standard error for query commands with no SQL input."""
    return user_error(
        "no SQL query provided",
        details=["Provide an inline query argument or pass --file."],
        run=command_hint,
    )


def file_read_error(path: str | Path) -> click.ClickException:
    """Return the standard error for unreadable command input files."""
    return user_error("could not read file", details={"File": path})


def empty_file_error(path: str | Path) -> click.ClickException:
    """Return the standard error for empty command input files."""
    return user_error("file is empty", details={"File": path})


def command_failed_error(
    command_name: str,
    *,
    exit_code: int | None = None,
    run: str | None = None,
    details: Mapping[str, Any] | Sequence[str] | None = None,
) -> click.ClickException:
    """Return the standard error for external command failures."""
    failure_details: list[str] = []
    if exit_code is not None:
        failure_details.append(f"Exit code: {exit_code}")
    if details:
        if isinstance(details, Mapping):
            failure_details.extend(f"{key}: {value}" for key, value in details.items())
        else:
            failure_details.extend(str(item) for item in details)
    return user_error(f"{command_name} failed", details=failure_details, run=run)


def service_unavailable_error(
    service: str, *, run: str = "phlo services start"
) -> click.ClickException:
    """Return the standard error for commands that need a running service."""
    return user_error(
        f"{service} is not available",
        details=[f"Make sure the {service} service is running."],
        run=run,
    )
