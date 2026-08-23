"""Settings for phlo-dlt package.

This module provides configuration management for the phlo-dlt package
using Pydantic settings. It defines default values and allows customization
via environment variables or configuration files.

Key Components:
    - :class:`DltSettings`: Pydantic settings class for DLT configuration
    - :func:`get_settings`: Cached factory for settings instance

Configuration Options:
    - dlt_default_namespace: Default schema/namespace for table names

Environment Variables:
    Settings can be configured via environment variables with the prefix
    ``PHLO_DLT_`` (e.g., ``PHLO_DLT_DEFAULT_NAMESPACE``).

See Also:
    - :mod:`phlo.config.base`: Base configuration class
    - :mod:`phlo_dlt.registry`: Uses settings for namespace resolution
    - Pydantic Settings: https://docs.pydantic.dev/latest/concepts/settings/

Example:
    ```python
    from phlo_dlt.settings import get_settings

    settings = get_settings()
    print(settings.dlt_default_namespace)  # "raw"
    ```


Package-local settings module built on the shared phlo.config base and caching machinery.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached


class DltSettings(BaseConfig):
    """Configuration for DLT ingestion defaults.

    Pydantic settings for DLT ingestion defaults, overridable via
    environment variables (``PHLO_DLT_`` prefix) or .env files.
    ``dlt_default_namespace`` is prepended to table names to form
    full_table_name.

    Example:
        ```python
        from phlo_dlt.settings import DltSettings

        # Default namespace is "raw"
        settings = DltSettings()
        print(settings.dlt_default_namespace)  # "raw"

        # Override via environment
        import os
        os.environ["PHLO_DLT_DEFAULT_NAMESPACE"] = "staging"
        settings = DltSettings()
        print(settings.dlt_default_namespace)  # "staging"
        ```

    """

    dlt_default_namespace: str = Field(
        default="raw",
        description="Default namespace/schema used for generated ingestion table names.",
    )


@project_root_cached
def get_settings(project_root: Path) -> DltSettings:
    """Return cached DLT settings for the selected project root.

    Cached per resolved project root (up to 16 entries) so repeated calls
    for the same root return the same instance while keeping project
    configuration isolated.

    Example:
        ```python
        from phlo_dlt.settings import get_settings

        # First call creates the instance
        settings = get_settings()

        # Subsequent calls for the same root return the same instance
        settings2 = get_settings()
        assert settings is settings2  # True
        ```
    """
    return DltSettings()
