"""Observatory UI settings and configuration.

This module provides the settings infrastructure for the Observatory UI package,
including database connection configuration for persistent settings storage.

Settings for the Observatory UI package, built on the shared phlo.config base/cache helpers.
Loaded within phlo_observatory (settings service and package init) through get_settings().
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached


class ObservatorySettings(BaseConfig):
    """Configuration settings for the Observatory UI.

    Example:
        >>> settings = get_settings()
        >>> print(settings.observatory_settings_db_url)
        'postgresql://user:pass@localhost/observatory'

    """

    observatory_settings_db_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_OBSERVATORY_SETTINGS_DB_URL"),
        description="PostgreSQL DSN for Observatory settings storage",
    )


@project_root_cached
def get_settings(project_root: Path) -> ObservatorySettings:
    """Return cached Observatory settings for the selected project root.

    Settings are parsed from environment variables using PHLO_OBSERVATORY_*
    prefixes and cached per resolved project root, with up to 16 entries.

    Settings are parsed from ``PHLO_OBSERVATORY_*`` environment variables.

    Example:
        >>> settings = get_settings()
        >>> db_url = settings.observatory_settings_db_url

    """

    return ObservatorySettings()
