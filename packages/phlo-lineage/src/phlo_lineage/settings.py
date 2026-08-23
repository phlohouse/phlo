"""Lineage settings built on the shared phlo.config base and caching machinery.

lineage_db_url is resolved from the first available environment variable:
LINEAGE_DB_URL, PHLO_LINEAGE_DB_URL, or DAGSTER_PG_DB_CONNECTION_STRING.
Settings are accessed via the cached, project-root-scoped get_settings().

Example:
    >>> from phlo_lineage.settings import get_settings, LineageSettings
    >>>
    >>> # Access cached settings
    >>> settings = get_settings()
    >>> print(settings.lineage_db_url)
    'postgresql://user:pass@localhost:5432/phlo'
    >>>
    >>> # Create settings directly (bypass cache)
    >>> settings = LineageSettings()  # Reads env vars fresh
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached


class LineageSettings(BaseConfig):
    """Configuration settings for the lineage store and related features.

    lineage_db_url is resolved from the first available source:
    1. LINEAGE_DB_URL (explicit lineage setting)
    2. PHLO_LINEAGE_DB_URL (namespaced lineage setting)
    3. DAGSTER_PG_DB_CONNECTION_STRING (reuse Dagster's database)

    Example:
        >>> import os
        >>> os.environ["LINEAGE_DB_URL"] = "postgresql://localhost/lineage"
        >>>
        >>> settings = LineageSettings()
        >>> print(settings.lineage_db_url)
        'postgresql://localhost/lineage'

    """

    lineage_db_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LINEAGE_DB_URL",
            "PHLO_LINEAGE_DB_URL",
            "DAGSTER_PG_DB_CONNECTION_STRING",
        ),
        description="PostgreSQL DSN for the row-level lineage store",
    )


@project_root_cached
def get_settings(project_root: Path) -> LineageSettings:
    """Get cached LineageSettings for the selected project root.

    Results are cached per resolved project root (process-local,
    thread-safe, up to 16 entries). To reload after environment changes:

    >>> get_settings.cache_clear()
    >>> new_settings = get_settings()

    Example:
        >>> from phlo_lineage.settings import get_settings
        >>>
        >>> settings = get_settings()
        >>> if settings.lineage_db_url:
        ...     print("Lineage database configured")
        ... else:
        ...     print("No lineage database URL found")

    """
    return LineageSettings()
