"""dbt project discovery for auto-wiring.

This module provides utilities for automatically discovering dbt projects
within the workspace. It searches common locations and environment variables
to locate dbt_project.yml files, enabling zero-configuration setup in many cases.

Example:
    >>> from phlo_dbt.discovery import find_dbt_projects, get_dbt_project_dir
    >>>
    >>> # Find all dbt projects in workspace
    >>> projects = find_dbt_projects()
    >>> for project in projects:
    ...     print(f"Found: {project}")
    >>>
    >>> # Get the primary project directory
    >>> project_dir = get_dbt_project_dir()
    >>> print(f"Using: {project_dir}")

"""

from __future__ import annotations

import os
from pathlib import Path

from phlo.logging import get_logger, setup_logging

logger = get_logger(__name__)

DEFAULT_DBT_PROJECT_DIR = Path("workflows/transforms/dbt")
DEFAULT_SEARCH_PATHS = [str(DEFAULT_DBT_PROJECT_DIR)]


def find_dbt_projects(
    root_dir: str | Path | None = None,
    search_paths: list[str] | None = None,
) -> list[Path]:
    """Discover dbt projects in the workspace.

    Searches for dbt_project.yml files in specified paths relative to the root
    directory. Returns paths to directories containing valid dbt projects.

    Example:
        >>> # Find projects in default locations
        >>> projects = find_dbt_projects()
        >>> print(projects)
        [PosixPath('workflows/transforms/dbt')]
        >>>
        >>> # Search custom locations
        >>> projects = find_dbt_projects(
        ...     root_dir="/custom/path",
        ...     search_paths=["analytics/dbt", "data/transforms"]
        ... )

    """
    if root_dir is None:
        root_dir = Path.cwd()
    else:
        root_dir = Path(root_dir)

    discovered: list[Path] = []
    seen: set[Path] = set()

    for search_path in search_paths or DEFAULT_SEARCH_PATHS:
        candidate = root_dir / search_path / "dbt_project.yml"
        if candidate.exists() and candidate.parent not in seen:
            seen.add(candidate.parent)
            discovered.append(candidate.parent)
            logger.info("Discovered dbt project: %s", candidate.parent)

    # Explicit search paths disable this scan: callers that narrow the search
    # opt out of the full workflows walk, which can be slow on large trees.
    if search_paths is None:
        workflows_root = root_dir / "workflows"
        if workflows_root.exists():
            for candidate in sorted(workflows_root.rglob("dbt_project.yml")):
                if candidate.parent not in seen:
                    seen.add(candidate.parent)
                    discovered.append(candidate.parent)
                    logger.info("Discovered dbt project: %s", candidate.parent)

    return discovered


def get_dbt_project_dir() -> Path:
    """Get the dbt project directory, auto-discovering if not explicitly set.

    Resolves the dbt project directory using a priority system:
    1. DBT_PROJECT_DIR environment variable
    2. Auto-discovered project in workspace
    3. Default: workflows/transforms/dbt

    Example:
        >>> # With environment variable set
        >>> import os
        >>> os.environ["DBT_PROJECT_DIR"] = "/custom/dbt"
        >>> project_dir = get_dbt_project_dir()
        >>> print(project_dir)
        PosixPath('/custom/dbt')
        >>>
        >>> # With auto-discovery
        >>> project_dir = get_dbt_project_dir()
        >>> # Returns first discovered project or default path

    """
    # Check explicit environment variable
    env_path = os.environ.get("DBT_PROJECT_DIR")
    if env_path:
        return Path(env_path)

    # Auto-discover
    projects = find_dbt_projects()
    if projects:
        return projects[0]

    # Fall back to default
    return DEFAULT_DBT_PROJECT_DIR


if __name__ == "__main__":
    setup_logging()
    projects = find_dbt_projects()
    logger.info("Discovered %s dbt projects:", len(projects))
    for project in projects:
        logger.info("  - %s", project)
