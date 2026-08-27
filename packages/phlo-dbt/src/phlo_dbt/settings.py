"""dbt settings and configuration management.

This module provides Pydantic-based configuration management for dbt integration
within the Phlo platform. It handles query engine settings, project paths, and
derived artifact locations.

Example:
    >>> from phlo_dbt.settings import get_settings, DbtSettings
    >>> settings = get_settings()
    >>> print(f"Project: {settings.dbt_project_path}")
    >>> print(f"Catalog: {settings.dbt_query_catalog}")
    >>>
    >>> # Create custom settings
    >>> custom = DbtSettings(dbt_query_catalog="analytics", dbt_query_threads=8)


Package-local settings module built on the shared phlo.config base and caching machinery.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, computed_field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host
from phlo.logging import get_logger

from phlo_dbt.discovery import find_dbt_projects

logger = get_logger(__name__)


def _project_root() -> Path:
    return Path(os.environ.get("PHLO_PROJECT_PATH", Path.cwd())).resolve()


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _project_root() / path


def _discover_dbt_project_paths(root: Path) -> list[Path]:
    """Return discovered dbt projects under workflows/ in canonical order.

    Delegates to ``phlo_dbt.discovery.find_dbt_projects`` so settings and
    discovery share one ordering rule (shallowest-then-alphabetical).
    """
    return find_dbt_projects(root_dir=root)


def _warn_skipped_projects(activated: Path, discovered: list[Path]) -> None:
    """Log a loud one-time warning when discovery found projects that stay inert."""
    if len(discovered) <= 1:
        return
    skipped = [path for path in discovered if path != activated]
    if not skipped or activated in _warned_single_activations:
        return
    _warned_single_activations.add(activated)
    logger.warning(
        "dbt_multiple_projects_discovered_single_activated",
        activated=str(activated),
        skipped=[str(path) for path in skipped],
        hint=(
            "Set DBT_PROJECT_DIRS (comma-separated) to activate multiple dbt "
            "projects, or DBT_PROJECT_DIR to pick one explicitly."
        ),
    )


_warned_single_activations: set[Path] = set()


class DbtSettings(BaseConfig):
    """dbt project configuration settings.

    Pydantic-based configuration class that manages all dbt-related settings
    including query engine connection parameters, project paths, and artifact
    locations. Uses environment variables and .env files for configuration.

    Example:
        >>> settings = DbtSettings(
        ...     dbt_query_catalog="analytics",
        ...     dbt_query_threads=8,
        ...     dbt_project_dir="custom/path"
        ... )
        >>> print(settings.dbt_project_path)
        PosixPath('custom/path')
        >>> print(settings.dbt_profiles_path)
        PosixPath('custom/path/profiles')

    """

    dbt_query_engine_type: str = Field(
        default="trino",
        description="Query engine adapter used by dbt profiles",
    )
    dbt_query_password: str = Field(
        default="",
        description="Query engine password for generated dbt profiles",
    )
    dbt_query_host: str = Field(
        default="trino",
        description="Query engine host for generated dbt profiles",
    )
    dbt_query_port: int = Field(
        default=8080,
        description="Query engine port for generated dbt profiles",
    )
    dbt_query_catalog: str = Field(
        default="iceberg",
        description="Base query engine catalog for generated dbt profiles",
    )
    dbt_query_schema: str = Field(
        default="raw",
        description="Default schema for generated dbt profiles",
    )
    dbt_query_user: str = Field(
        default="dagster",
        description="Query engine user for generated dbt profiles",
    )
    dbt_query_http_scheme: str = Field(
        default="http",
        description="HTTP scheme for generated dbt profiles",
    )
    dbt_query_auth_method: str = Field(
        default="none",
        description="Auth method for generated dbt profiles",
    )
    dbt_query_threads: int = Field(
        default=2,
        description="Worker threads for generated dbt profiles",
    )

    dbt_project_dir: str = Field(
        default="workflows/transforms/dbt",
        description="Path to dbt project directory",
    )
    dbt_project_dirs: str = Field(
        default="",
        description=(
            "Comma-separated list of dbt project directories to activate "
            "together (multi-project federation). Overrides dbt_project_dir."
        ),
    )
    dbt_namespaced_asset_keys: bool = Field(
        default=False,
        description=(
            "Prefix dbt-derived asset keys with the dbt project name "
            "(e.g. sales.deal_pipeline) to prevent cross-domain collisions. "
            "Recommended when multiple projects are activated."
        ),
    )
    dbt_shared_schema: bool = Field(
        default=False,
        description=(
            "Keep the shared dbt_query_schema for every activated project. "
            "By default, multi-project activation isolates each project in "
            "its own schema ({project_name}_{schema}) so independent domains "
            "do not write into the same relations."
        ),
    )
    dbt_manifest_path: str = Field(
        default="",
        description="Path to dbt manifest.json after running dbt docs generate",
    )
    dbt_catalog_path: str = Field(
        default="",
        description="Path to dbt catalog.json for column-level documentation",
    )

    def model_post_init(self, __context: object) -> None:
        """Populate derived dbt artifact paths after model initialization."""
        host, port = resolve_host(
            self.dbt_query_host, self.dbt_query_port, port_env_var="TRINO_PORT"
        )
        object.__setattr__(self, "dbt_query_host", host)
        object.__setattr__(self, "dbt_query_port", port)
        if not self.dbt_manifest_path:
            object.__setattr__(
                self, "dbt_manifest_path", f"{self.dbt_project_dir}/target/manifest.json"
            )
        if not self.dbt_catalog_path:
            object.__setattr__(
                self, "dbt_catalog_path", f"{self.dbt_project_dir}/target/catalog.json"
            )

    @computed_field
    @property
    def dbt_profiles_dir(self) -> str:
        """Return the dbt profiles directory path string."""
        return f"{self.dbt_project_dir}/profiles"

    def _configured_project_dirs(self) -> list[str]:
        """Return explicitly configured multi-project dirs, trimmed and non-empty."""
        return [part.strip() for part in self.dbt_project_dirs.split(",") if part.strip()]

    @property
    def dbt_project_path(self) -> Path:
        """Return the dbt project path as a ``Path``.

        The first configured multi-project dir wins when ``dbt_project_dirs``
        is set. Otherwise, auto-discovery only applies when the default
        directory is configured and does not exist on disk: the shallowest
        dbt project under workflows/ wins, while an explicitly configured
        custom path is returned as-is even if missing. When discovery finds
        several projects, the first is activated and the rest logged loudly.
        """
        configured_dirs = self._configured_project_dirs()
        if configured_dirs:
            return _resolve_project_path(configured_dirs[0])
        configured_path = _resolve_project_path(self.dbt_project_dir)
        if self.dbt_project_dir != "workflows/transforms/dbt" or configured_path.exists():
            return configured_path
        discovered = _discover_dbt_project_paths(_project_root())
        activated = discovered[0] if discovered else configured_path
        _warn_skipped_projects(activated, discovered)
        return activated

    @property
    def dbt_project_paths(self) -> list[Path]:
        """Return every dbt project path activated for asset building.

        Resolution order:
        1. ``dbt_project_dirs`` (comma-separated) — explicit multi-project
           federation; each entry resolves relative to the project root.
        2. ``dbt_project_path`` — the single-project behavior, including
           auto-discovery.
        """
        configured_dirs = self._configured_project_dirs()
        if configured_dirs:
            return [_resolve_project_path(part) for part in configured_dirs]
        return [self.dbt_project_path]

    @property
    def dbt_profiles_path(self) -> Path:
        """Return the dbt profiles path as a ``Path``.

        Profiles follow project auto-discovery: when the discovered project
        differs from the configured default directory, its own profiles/
        subdirectory is used instead of the default location.
        """
        if self.dbt_project_dir == "workflows/transforms/dbt":
            discovered_project = self.dbt_project_path
            if discovered_project != _resolve_project_path(self.dbt_project_dir):
                return discovered_project / "profiles"
        return _resolve_project_path(self.dbt_profiles_dir)

    def dbt_profiles_path_for(self, project_path: Path) -> Path:
        """Return the profiles directory for one activated dbt project.

        The globally activated project keeps its configured profiles path
        (which may be a custom location); every other federated project uses
        its own ``profiles/`` subdirectory.
        """
        if project_path == self.dbt_project_path:
            return self.dbt_profiles_path
        return project_path / "profiles"


@project_root_cached
def get_settings(project_root: Path) -> DbtSettings:
    """Return cached dbt settings for the selected project root."""
    return DbtSettings()
