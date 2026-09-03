"""Planning helpers for services lifecycle commands.

Resolves requested service names, profiles, and config into pure plan objects:
ServiceSelectionPlan for what will start, StartPreflightPlan for paths and
project identity before startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

from phlo.cli.commands.services.utils import get_enabled_disabled_service_names
from phlo.plugins.discovery import ServiceDefinition
from phlo.utils import dedupe_preserve_order


@dataclass(frozen=True, slots=True)
class ServiceSelectionPlan:
    """Selected services for a services command."""

    selected_services: tuple[ServiceDefinition, ...] = ()
    disabled_names: frozenset[str] = frozenset()
    requested_names: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StartPreflightPlan:
    """Inputs needed by services start preflight checks."""

    phlo_dir: Path
    compose_file: Path
    project_root: Path
    project_name: str
    service_names: list[str]
    backend_name: str | None
    environment: str | None = None


def build_service_selection_plan(
    *,
    services: dict[str, ServiceDefinition],
    config: dict,
    profiles: tuple[str, ...],
    requested_names: list[str],
) -> ServiceSelectionPlan:
    """Resolve requested services (or defaults, profile matches, and enabled
    entries when none requested) into a selection plan.

    Rejects unknown or phlo.yaml-disabled names with ClickException.
    """
    unknown_requested = [name for name in requested_names if name not in services]
    if unknown_requested:
        raise click.ClickException(f"Unknown service name(s): {', '.join(unknown_requested)}")

    enabled_names, disabled_names = get_enabled_disabled_service_names(config)
    disabled_requested = [name for name in requested_names if name in disabled_names]
    if disabled_requested:
        raise click.ClickException(
            f"Requested service(s) are disabled in phlo.yaml: {', '.join(disabled_requested)}"
        )

    selected_names: list[str] = []
    if requested_names:
        selected_names.extend(requested_names)
    else:
        selected_names.extend(
            service.name
            for service in services.values()
            if service.default and service.name not in disabled_names
        )
        selected_names.extend(
            service.name
            for service in services.values()
            if service.profile in profiles and service.name not in disabled_names
        )
        selected_names.extend(name for name in enabled_names if name in services)

    selected = [
        services[name]
        for name in dedupe_preserve_order(selected_names)
        if name in services and name not in disabled_names
    ]
    return ServiceSelectionPlan(
        selected_services=tuple(selected),
        disabled_names=frozenset(disabled_names),
        requested_names=tuple(requested_names),
        profiles=profiles,
    )


def build_start_preflight_plan(
    *,
    phlo_dir: Path,
    compose_file: Path,
    project_root: Path,
    project_name: str,
    backend_name: str | None,
    services: list[ServiceDefinition] | None = None,
    service_names: list[str] | None = None,
    environment: str | None = None,
) -> StartPreflightPlan:
    """Assemble the inputs needed by services start preflight checks.

    Requires at least one service across service_names or services.
    """
    resolved_service_names = (
        service_names if service_names is not None else [service.name for service in services or []]
    )
    if not resolved_service_names:
        raise ValueError("service_names or services must include at least one service")

    return StartPreflightPlan(
        phlo_dir=phlo_dir,
        compose_file=compose_file,
        project_root=project_root,
        project_name=project_name,
        service_names=resolved_service_names,
        backend_name=backend_name,
        environment=environment,
    )
