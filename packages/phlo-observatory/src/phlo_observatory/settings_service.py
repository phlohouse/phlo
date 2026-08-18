"""Observatory settings storage backend (backward-compatible re-exports).

This module provides backward-compatible access to the settings storage
infrastructure used by the Observatory UI for persisting user preferences,
configuration, and extension settings.

The settings system supports:
    - In-memory storage for development and testing
    - PostgreSQL-backed persistent storage for production
    - Scoped global and extension settings
    - Type-safe settings records with validation

Backward Compatibility:
    These exports are maintained for existing code. New implementations should
    import directly from phlo.plugins.observatory_settings.

Exported Contracts:
    - SettingsStore: Neutral settings storage contract
    - InMemorySettingsService: Non-persistent in-memory implementation
    - SettingsRecord: Individual setting with metadata
    - SettingsScope: Enumeration of setting scopes (global, extension)
    - get_settings_service: Factory function for settings service instances

Example:
    >>> from phlo_observatory.settings_service import get_settings_service
    >>> service = get_settings_service()
    >>> service.put(SettingsScope.GLOBAL, 'observatory.core', {'theme': 'dark'})

See Also:
    phlo.plugins.observatory_settings: Source of truth for settings API.
    phlo_observatory.settings: Observatory-specific configuration.

"""

from phlo.plugins.observatory_settings import (
    InMemorySettingsService,
    SettingsRecord,
    SettingsScope,
    SettingsStore,
    get_settings_service,
)

__all__ = [
    "InMemorySettingsService",
    "SettingsRecord",
    "SettingsScope",
    "SettingsStore",
    "get_settings_service",
]
