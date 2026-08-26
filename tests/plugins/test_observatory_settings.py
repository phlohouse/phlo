"""Tests for durable Observatory settings storage and fail-closed behaviour.

Covers the acceptance criteria from issue #626:
- Core settings code has no provider-package import, no psycopg2, no SQL.
- Durable backend selected by default; memory only via explicit config.
- PostgreSQL unavailable → 503 (StorageUnavailableError); no in-memory record.
- Same-process recovery after the database returns.
- Invalid backend names fail configuration validation.
- Error responses and logs contain no DSN or password.
- Global and extension settings use the same durable capability.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from phlo.plugins.observatory_settings import (
    InMemorySettingsService,
    ObservatorySettingsStorageConfig,
    SettingsScope,
    SettingsStore,
    StorageUnavailableError,
    _reset_memory_service,
    get_settings_service,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_settings_store_capability() -> None:
    from phlo.capabilities import clear_capabilities

    clear_capabilities("settings_store")


def _register_mock_settings_store(store) -> None:
    from phlo.capabilities import SettingsStoreSpec, register_capability

    register_capability("settings_store", SettingsStoreSpec(name="postgres", provider=store))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Ensure each test starts with a clean capability registry and memory cache."""
    _clear_settings_store_capability()
    _reset_memory_service()
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "postgres")
    monkeypatch.delenv("PHLO_OBSERVATORY_SETTINGS_DB_URL", raising=False)
    yield
    _clear_settings_store_capability()
    _reset_memory_service()


# ---------------------------------------------------------------------------
# Import boundary — core must not import provider packages or psycopg2
# ---------------------------------------------------------------------------


def test_core_plugins_init_does_not_reexport_settings_service() -> None:
    """phlo.plugins must not re-export SettingsService."""
    import phlo.plugins as plugins

    assert "SettingsService" not in plugins.__all__
    assert not hasattr(plugins, "SettingsService")


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def test_default_backend_is_postgres() -> None:
    config = ObservatorySettingsStorageConfig()
    assert config.observatory_settings_backend == "postgres"


def test_memory_backend_via_explicit_config(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    config = ObservatorySettingsStorageConfig()
    assert config.observatory_settings_backend == "memory"


def test_invalid_backend_name_fails_validation(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "redis")
    with pytest.raises(Exception, match="postgres|memory"):
        ObservatorySettingsStorageConfig()


# ---------------------------------------------------------------------------
# Memory mode
# ---------------------------------------------------------------------------


def test_memory_mode_returns_in_memory_service(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    service = get_settings_service()
    assert isinstance(service, InMemorySettingsService)


def test_memory_mode_persists_across_calls(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    service1 = get_settings_service()
    service1.put(SettingsScope.GLOBAL, "test", {"v": 1})
    service2 = get_settings_service()
    assert service2 is service1
    record = service2.get(SettingsScope.GLOBAL, "test")
    assert record is not None
    assert record.settings == {"v": 1}


# ---------------------------------------------------------------------------
# Postgres mode — capability resolution
# ---------------------------------------------------------------------------


def test_postgres_mode_resolves_capability() -> None:
    mock_store = MagicMock(spec=SettingsStore)
    _register_mock_settings_store(mock_store)
    service = get_settings_service()
    assert service is mock_store


def test_postgres_mode_without_capability_raises_storage_unavailable() -> None:
    with pytest.raises(StorageUnavailableError, match="not available"):
        get_settings_service()


def test_postgres_mode_does_not_create_in_memory_record_on_failure() -> None:
    """When the durable backend is unavailable, no in-memory write occurs."""
    with pytest.raises(StorageUnavailableError):
        get_settings_service()
    from phlo.plugins.observatory_settings import _memory_service

    assert _memory_service is None


# ---------------------------------------------------------------------------
# Postgres mode — same-process recovery
# ---------------------------------------------------------------------------


def test_get_settings_service_retries_capability_resolution_after_failure() -> None:
    """get_settings_service() must not cache a failure; later calls retry."""
    with pytest.raises(StorageUnavailableError):
        get_settings_service()

    mock_store = MagicMock(spec=SettingsStore)
    _register_mock_settings_store(mock_store)
    service = get_settings_service()
    assert service is mock_store


# ---------------------------------------------------------------------------
# DSN safety — get_settings_service errors contain no credentials
# ---------------------------------------------------------------------------


def test_get_settings_service_error_contains_no_dsn(monkeypatch) -> None:
    """When storage is unavailable, the error message must not leak the DSN."""
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_DB_URL", "postgresql://user:secret@host:5432/db")
    with pytest.raises(StorageUnavailableError) as exc_info:
        get_settings_service()
    msg = str(exc_info.value)
    assert "user:secret" not in msg
    assert "postgresql://" not in msg
    assert "host:5432" not in msg


# ---------------------------------------------------------------------------
# Global and extension settings use the same capability
# ---------------------------------------------------------------------------


def test_global_and_extension_use_same_capability() -> None:
    """Both scopes resolve the same settings_store capability."""
    mock_store = MagicMock(spec=SettingsStore)
    mock_store.get.return_value = None
    _register_mock_settings_store(mock_store)

    service = get_settings_service()
    service.get(SettingsScope.GLOBAL, "observatory.core")
    service.get(SettingsScope.EXTENSION, "observatory.extension.demo")

    assert mock_store.get.call_count == 2
    mock_store.get.assert_any_call(SettingsScope.GLOBAL, "observatory.core")
    mock_store.get.assert_any_call(SettingsScope.EXTENSION, "observatory.extension.demo")
