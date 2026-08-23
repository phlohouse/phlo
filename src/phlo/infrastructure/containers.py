"""Core helpers for resolving running service containers.

Resolution follows a fixed fallback chain: configured name, compose default,
legacy names, then a pattern match against running containers; a failed
container listing is logged and treated as empty rather than raised.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from phlo.cli.infrastructure.container_backend import select_project_container_backend
from phlo.infrastructure.config import load_infrastructure_config
from phlo.logging import get_logger

logger = get_logger(__name__)


def resolve_container_name(service_name: str, project_name: str) -> str:
    """Resolve container name for a service from infrastructure settings."""
    infra = load_infrastructure_config()
    configured = infra.get_container_name(service_name, project_name)
    if configured:
        return configured
    return infra.container_naming_pattern.format(project=project_name, service=service_name)


def list_running_containers(project_name: str, backend_name: str | None = None) -> list[str]:
    """List running compose container names for a project."""
    try:
        backend = select_project_container_backend(cli_backend=backend_name)
        return [container.name for container in backend.list_project_containers(project_name)]
    except Exception:
        logger.warning(
            "container_list_failed",
            project=project_name,
            backend_name=backend_name,
            exc_info=True,
        )
        return []


def select_first_existing(candidates: Iterable[str], existing: Iterable[str]) -> str | None:
    """Return the first candidate present in the existing container list."""
    existing_set = set(existing)
    for candidate in candidates:
        if candidate and candidate in existing_set:
            return candidate
    return None


def find_service_container(
    *,
    project_name: str,
    service_name: str,
    legacy_names: Iterable[str] = (),
    include_pattern: str | None = None,
    exclude_substrings: Iterable[str] = (),
) -> str:
    """Find a running service container for a compose project."""
    configured_name = resolve_container_name(service_name, project_name)
    default_name = f"{project_name}-{service_name}-1"
    preferred = [configured_name, default_name, *legacy_names]

    existing = list_running_containers(project_name)
    chosen = select_first_existing(preferred, existing)
    if chosen:
        return chosen

    pattern = include_pattern or rf"{re.escape(project_name)}.*{re.escape(service_name)}"
    for name in existing:
        if not re.search(pattern, name):
            continue
        if any(excluded in name for excluded in exclude_substrings):
            continue
        return name

    legacy_list = [name for name in legacy_names if name]
    expected = [default_name, *legacy_list]
    expected_rendered = " or ".join(expected) if expected else default_name
    raise RuntimeError(
        f"Could not find running {service_name} container for project '{project_name}'. "
        f"Expected container name: {expected_rendered}"
    )
