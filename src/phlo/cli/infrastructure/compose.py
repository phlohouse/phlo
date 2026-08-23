"""Build docker/podman compose commands for a Phlo project's selected backend.

Single seam between CLI commands and the container backend: the backend is
chosen per project (with an optional CLI override) and supplies the base
compose tokens, so callers never hardcode docker or podman.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from phlo.cli.infrastructure.container_backend import select_project_container_backend


def compose_base_cmd(
    *,
    phlo_dir: Path,
    project_name: str,
    profiles: Iterable[str] = (),
    backend_name: str | None = None,
) -> list[str]:
    """Build the base compose command for a Phlo project.

    The backend is selected per project unless overridden; profiles enable
    named compose profile groups.
    """
    backend = select_project_container_backend(cli_backend=backend_name)
    return backend.compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=project_name,
        profiles=tuple(profiles),
    )
