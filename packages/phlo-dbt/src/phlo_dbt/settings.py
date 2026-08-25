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


def _project_root() -> Path:
    return Path(os.environ.get("PHLO_PROJECT_PATH", Path.cwd())).resolve()


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _project_root() / path


def _discover_dbt_project_path(root: Path) -> Path | None:
    workflows_root = root / "workflows"
    if not workflows_root.exists():
        return None

    candidates = sorted(
        (path.parent for path in workflows_root.rglob("dbt_project.yml")),
        key=lambda path: (len(path.parts), str(path)),
    )
    return candidates[0] if candidates else None


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

    @property
    def dbt_project_path(self) -> Path:
        """Return the dbt project path as a ``Path``.

        Auto-discovery only applies when the default directory is configured
        and does not exist on disk: the shallowest dbt project under
        workflows/ wins, while an explicitly configured custom path is
        returned as-is even if missing.
        """
        configured_path = _resolve_project_path(self.dbt_project_dir)
        if self.dbt_project_dir != "workflows/transforms/dbt" or configured_path.exists():
            return configured_path
        return _discover_dbt_project_path(_project_root()) or configured_path

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


@project_root_cached
def get_settings(project_root: Path) -> DbtSettings:
    """Return cached dbt settings for the selected project root."""
    return DbtSettings()
