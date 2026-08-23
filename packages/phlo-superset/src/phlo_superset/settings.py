"""Superset settings configuration.

This module defines the configuration schema and loading mechanisms for
Apache Superset integration within the Phlo platform. Settings are managed
through Pydantic models with environment variable support.

Example:
    >>> from phlo_superset.settings import SupersetSettings, get_settings
    >>> settings = get_settings()
    >>> print(f"Superset available at port {settings.superset_port}")
    'Superset available at port 10007'


    Settings for the Superset BI service, built on the shared phlo.config base/cache helpers.
    Loaded within phlo_superset (hooks and package init) through get_settings().
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached


class SupersetSettings(BaseConfig):
    """Configuration settings for Apache Superset integration.

    Defines all configurable parameters for the Superset service including
    network ports, authentication credentials, and administrative settings;
    values can be overridden via SUPERSET_* environment variables.

    Example:
        >>> settings = SupersetSettings(superset_port=8088)
        >>> print(settings.superset_admin_user)
        'admin'

    """

    superset_port: int = Field(default=10007, description="Superset web port")
    superset_admin_user: str = Field(default="admin", description="Superset admin username")
    superset_admin_password: str = Field(default="admin", description="Superset admin password")
    superset_admin_email: str = Field(
        default="admin@example.com", description="Superset admin email"
    )


@project_root_cached
def get_settings(project_root: Path) -> SupersetSettings:
    """Return cached Superset settings for the selected project root."""
    return SupersetSettings()
