"""Core application settings for Phlo.

This module defines the primary configuration settings used throughout the Phlo
framework. Settings are loaded from environment variables and `.phlo/.env` files
with validation via Pydantic.

All settings can be customized through environment variables or by creating
a `.phlo/.env` file in your project root.
"""

from pathlib import Path

from pydantic import AliasChoices, Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached

CONFIG_SCHEMA_VERSION = "1"


class Settings(BaseConfig):
    """Core configuration for Phlo.

    This class defines all configurable aspects of the Phlo framework including
    logging, orchestration, plugin management, and observability settings.
    Values are loaded from environment variables with sensible defaults.

    Environment variables are read with case-insensitive matching. Aliases
    are provided for common configuration patterns (e.g., OTEL_* variables).

    Example:
        ```python
        from phlo.config import get_settings

        settings = get_settings()
        print(f"Orchestrator: {settings.phlo_orchestrator}")
        print(f"Log level: {settings.phlo_log_level}")
        ```
    """

    phlo_orchestrator: str = Field(
        default="dagster",
        validation_alias=AliasChoices("PHLO_ORCHESTRATOR", "PHLO_ORCHESTRATOR_NAME"),
        description="Active orchestrator adapter name",
    )

    phlo_log_level: str = Field(default="WARNING", description="Default log level for Phlo")
    phlo_log_format: str = Field(
        default="auto",
        description="Log format (auto|json|console)",
    )
    phlo_log_router_enabled: bool = Field(
        default=True,
        description="Emit structured log events to the hook bus",
    )
    phlo_log_service_name: str = Field(
        default="phlo",
        description="Default service name for log records",
    )
    phlo_log_file_template: str = Field(
        default=".phlo/logs/{YMD}.log",
        description="Optional log file path template (empty to disable)",
    )
    phlo_environment: str = Field(
        default="dev",
        validation_alias=AliasChoices("PHLO_ENVIRONMENT", "ENVIRONMENT"),
        description="Runtime environment attached to structured logs",
    )
    phlo_service_namespace: str = Field(
        default="phlo",
        validation_alias=AliasChoices("PHLO_SERVICE_NAMESPACE", "OTEL_SERVICE_NAMESPACE"),
        description="Default service namespace attached to observability resources",
    )
    phlo_service_version: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_SERVICE_VERSION", "OTEL_SERVICE_VERSION"),
        description="Optional default service version attached to observability resources",
    )
    phlo_service_instance_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_SERVICE_INSTANCE_ID", "OTEL_SERVICE_INSTANCE_ID"),
        description="Optional default service instance identifier for observability resources",
    )
    phlo_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_PROJECT"),
        description="Optional project identifier attached to observability resources",
    )
    phlo_default_capabilities: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("PHLO_DEFAULT_CAPABILITIES"),
        description=(
            "Default capability provider names keyed by capability type "
            "(for example {'table_store': 'iceberg'})"
        ),
    )

    plugins_enabled: bool = Field(default=True, description="Enable plugin system")
    plugins_auto_discover: bool = Field(
        default=True,
        description="Automatically discover plugins from entry points on import",
    )
    plugins_whitelist: list[str] = Field(
        default_factory=list,
        description="Whitelist of plugin names to load (empty = all allowed)",
    )
    plugins_blacklist: list[str] = Field(
        default_factory=list, description="Blacklist of plugin names to exclude"
    )
    plugin_registry_url: str = Field(
        default="https://registry.phlohouse.com/plugins.json",
        description="URL for the plugin registry catalog",
    )
    plugin_registry_cache_ttl_seconds: int = Field(
        default=3600, description="Registry cache TTL in seconds"
    )
    plugin_registry_timeout_seconds: int = Field(
        default=10, description="Registry fetch timeout in seconds"
    )


@project_root_cached
def _get_config(project_root: Path) -> Settings:
    """Build Settings for an already-resolved project root.

    Cached by ``project_root_cached`` (LRU, 16 entries) so repeated calls
    skip env-file I/O and validation. Internal; use :func:`get_settings`.
    """
    return Settings()


def get_settings(project_root: Path | str | None = None) -> Settings:
    """Get application settings.

    This is the recommended way to access configuration in application code.
    It returns a cached Settings instance for the selected project root and
    supports future dependency injection patterns for testing.

    Example:
        ```python
        from phlo.config import get_settings

        settings = get_settings()
        if settings.phlo_environment == "production":
            # Apply production-specific logic
            pass
        ```
    """
    return _get_config(project_root)
