"""Shared helpers for service CLI commands.

Parses and validates --service/--profile selections against discovered
profiles, reads service names from the compose file (a malformed file yields
no services rather than an error), and runs compose commands with uniform
ClickException error handling.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING

import click

from phlo.cli.infrastructure.command import run_command
from phlo.logging import get_logger
from phlo.plugins.discovery import ServiceDiscovery

if TYPE_CHECKING:
    import subprocess

logger = get_logger(__name__)


def parse_service_args(values: tuple[str, ...] | list[str]) -> list[str]:
    """Parse comma-separated and repeated --service arguments into a flat list."""
    names: list[str] = []
    for value in values:
        names.extend(part.strip() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(names))


def validate_requested_profiles(profile_names: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize and validate requested profile names.

    Raises click.ClickException for unknown profiles.
    """
    requested = tuple(dict.fromkeys(name.strip() for name in profile_names if name.strip()))
    if not requested:
        return ()

    available = ServiceDiscovery().get_available_profiles()
    unknown = sorted(set(requested) - available)
    if unknown:
        label = "profile" if len(unknown) == 1 else "profiles"
        raise click.ClickException(
            f"Invalid {label}: {', '.join(unknown)}. "
            f"Valid profile options: {', '.join(sorted(available)) or '(none)'}"
        )
    return requested


def load_compose_service_names(compose_file: Path) -> list[str]:
    """Load service names from a docker-compose.yml file."""
    import yaml

    try:
        config = yaml.safe_load(compose_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    return list((config.get("services") or {}).keys())


def run_compose(
    cmd: list[str],
    *,
    check: bool = False,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run a compose command with standardized error handling.

    Raises click.ClickException on FileNotFoundError, TimeoutExpired, or OSError.
    """
    try:
        return run_command(cmd, check=check, capture_output=capture_output)
    except FileNotFoundError:
        backend = cmd[0] if cmd else "container"
        raise click.ClickException(
            f"{backend} command not found. Install or configure the selected container backend."
        ) from None
    except TimeoutExpired as exc:
        raise click.ClickException(f"container compose timed out: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise click.ClickException(f"container compose failed unexpectedly: {exc}") from exc
