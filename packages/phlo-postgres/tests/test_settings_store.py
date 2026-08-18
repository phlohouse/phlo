"""Tests for the PostgreSQL settings store capability.

Verifies that phlo-postgres owns all psycopg2/SQL/DSN behavior for the
durable Observatory settings store, including connection failure
sanitisation, same-process recovery, and DSN override.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phlo.capabilities import SettingsStoreSpec
from phlo.plugins.observatory_settings import (
    SettingsScope,
    SettingsStore,
    StorageUnavailableError,
)
from phlo_postgres.plugin import PostgresResourceProvider
from phlo_postgres.settings_store import PostgresSettingsStore, get_settings_stores


# ---------------------------------------------------------------------------
# Construction and DSN resolution
# ---------------------------------------------------------------------------


def test_postgres_settings_store_implements_settings_store_protocol() -> None:
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        mock.return_value.get_postgres_connection_string.return_value = (
            "postgresql://phlo:phlo@localhost:5432/phlo"
        )
        store = PostgresSettingsStore()
    assert isinstance(store, SettingsStore)


def test_postgres_settings_store_uses_default_dsn() -> None:
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        mock.return_value.get_postgres_connection_string.return_value = (
            "postgresql://phlo:phlo@localhost:5432/phlo"
        )
        store = PostgresSettingsStore()
    assert store._db_url == "postgresql://phlo:phlo@localhost:5432/phlo"


def test_postgres_settings_store_uses_dsn_override(monkeypatch) -> None:
    """Explicit PHLO_OBSERVATORY_SETTINGS_DB_URL overrides default PostgresSettings DSN."""
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_DB_URL", "postgresql://override:5432/phlo")
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        store = PostgresSettingsStore()
        mock.assert_not_called()
    assert store._db_url == "postgresql://override:5432/phlo"


# ---------------------------------------------------------------------------
# Capability registration
# ---------------------------------------------------------------------------


def test_get_settings_stores_returns_capability_spec() -> None:
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        mock.return_value.get_postgres_connection_string.return_value = (
            "postgresql://phlo:phlo@localhost:5432/phlo"
        )
        specs = get_settings_stores()
    assert len(specs) == 1
    assert isinstance(specs[0], SettingsStoreSpec)
    assert specs[0].name == "postgres"
    assert isinstance(specs[0].provider, PostgresSettingsStore)


def test_postgres_resource_provider_exposes_settings_store() -> None:
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        mock.return_value.get_postgres_connection_string.return_value = (
            "postgresql://phlo:phlo@localhost:5432/phlo"
        )
        provider = PostgresResourceProvider()
        specs = provider.get_settings_stores()
    assert len(specs) == 1
    assert specs[0].name == "postgres"
    assert isinstance(specs[0].provider, SettingsStore)


# ---------------------------------------------------------------------------
# Connection failure → StorageUnavailableError (no DSN leak)
# ---------------------------------------------------------------------------


def test_connection_failure_raises_storage_unavailable() -> None:
    """A psycopg2 connection failure must surface as StorageUnavailableError."""
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        mock.return_value.get_postgres_connection_string.return_value = (
            "postgresql://invalid:5432/phlo"
        )
        store = PostgresSettingsStore()

    with (
        patch("psycopg2.connect", side_effect=OSError("connection refused")),
        pytest.raises(StorageUnavailableError, match="Settings storage is unavailable"),
    ):
        store.get(SettingsScope.GLOBAL, "observatory")


def test_storage_unavailable_error_contains_no_dsn() -> None:
    """The sanitised error must not contain the DSN or password."""
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        mock.return_value.get_postgres_connection_string.return_value = (
            "postgresql://user:secret@host:5432/db"
        )
        store = PostgresSettingsStore()

    with patch("psycopg2.connect", side_effect=OSError("connection refused")):
        try:
            store.get(SettingsScope.GLOBAL, "observatory")
            pytest.fail("expected StorageUnavailableError")
        except StorageUnavailableError as exc:
            msg = str(exc)
            assert "user:secret" not in msg
            assert "postgresql://" not in msg
            assert "host:5432" not in msg


# ---------------------------------------------------------------------------
# Same-process recovery
# ---------------------------------------------------------------------------


def test_recovery_after_database_becomes_available() -> None:
    """First call fails, second call succeeds in the same process."""
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        mock.return_value.get_postgres_connection_string.return_value = (
            "postgresql://localhost:5432/phlo"
        )
        store = PostgresSettingsStore()

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = ({"version": 1}, None)
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False

    call_count = {"n": 0}

    def fake_connect(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("connection refused")
        return mock_conn

    with patch("psycopg2.connect", side_effect=fake_connect):
        with pytest.raises(StorageUnavailableError):
            store.get(SettingsScope.GLOBAL, "observatory")

        record = store.get(SettingsScope.GLOBAL, "observatory")
        assert record is not None
        assert record.settings == {"version": 1}


def test_put_connection_failure_raises_storage_unavailable() -> None:
    """PUT must also surface connection failures as StorageUnavailableError."""
    with patch("phlo_postgres.settings_store.get_postgres_settings") as mock:
        mock.return_value.get_postgres_connection_string.return_value = (
            "postgresql://invalid:5432/phlo"
        )
        store = PostgresSettingsStore()

    with (
        patch("psycopg2.connect", side_effect=OSError("connection refused")),
        pytest.raises(StorageUnavailableError, match="Settings storage is unavailable"),
    ):
        store.put(SettingsScope.GLOBAL, "observatory", {"v": 1})
