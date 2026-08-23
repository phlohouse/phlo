"""Settings for phlo-sling package.

This module defines the configuration settings for the phlo-sling package,
including defaults for replication modes, namespace handling, and connection
management. Settings are loaded from environment variables and configuration
files with sensible defaults.

Classes:
    SlingSettings: Pydantic-based configuration class for Sling settings.

Functions:
    get_settings: Returns a cached instance of SlingSettings.
Package configuration boundary, building on phlo.config.base and phlo.config.cache;
consumed through get_settings() at runtime.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached


class SlingSettings(BaseConfig):
    """Configuration for Sling replication defaults.
    This class defines the configuration schema and defaults for Sling
    replication operations within the Phlo platform. Settings are loaded
    from environment variables prefixed appropriately and validated
    using Pydantic.

    Example:
        Load settings with defaults::

            from phlo_sling.settings import get_settings

            settings = get_settings()
            print(settings.sling_default_namespace)  # "raw"
    """

    sling_default_namespace: str = Field(
        default="raw",
        description="Default namespace for generated replication table names.",
    )
    sling_binary_path: str | None = Field(
        default=None,
        description="Override path to the sling binary. None uses the bundled binary.",
    )
    sling_default_mode: str = Field(
        default="incremental",
        description="Default replication mode (full-refresh, incremental, snapshot, backfill).",
    )
    sling_auto_connections: bool = Field(
        default=True,
        description="Auto-generate Sling connections from Phlo capability metadata.",
    )
    sling_connections_dir: str | None = Field(
        default=None,
        description="Directory containing Sling env.yaml files for explicit connections.",
    )


@project_root_cached
def get_settings(project_root: Path) -> SlingSettings:
    """Return cached Sling settings for the selected project root.
    Settings are cached per resolved project root, with up to 16 entries,
    avoiding repeated configuration loading while isolating project state.
    Settings are loaded from environment variables and configuration files
    on first access for each root.

    Example:
        Get settings in application code::

            settings = get_settings()
            namespace = settings.sling_default_namespace
    """
    return SlingSettings()
