"""Tests for Superset hooks configuration.

Pins the precedence rules: explicit env config wins, then query-engine
capability metadata, then settings defaults. URI resolution failure or missing
admin credentials never raises — the hook logs and skips registration.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from phlo_superset import hooks


def test_superset_auth_provider_defaults(monkeypatch) -> None:
    """Login provider should default cleanly when unset."""
    monkeypatch.delenv("SUPERSET_AUTH_PROVIDER", raising=False)

    assert hooks._superset_auth_provider() == "db"


def test_superset_database_name_is_configurable(monkeypatch) -> None:
    """Displayed database name should come from configuration when set."""
    monkeypatch.setenv("SUPERSET_DATABASE_NAME", "analytics")

    assert hooks._superset_database_name() == "analytics"


def test_superset_database_name_uses_query_engine_metadata(monkeypatch) -> None:
    """Database name should fall back to query-engine catalog metadata."""
    monkeypatch.delenv("SUPERSET_DATABASE_NAME", raising=False)
    monkeypatch.delenv("SUPERSET_QUERY_ENGINE", raising=False)
    monkeypatch.setattr(hooks, "discover_capabilities", lambda: None)
    monkeypatch.setattr(
        hooks,
        "resolve_capability",
        lambda kind, name=None: type(
            "Resolution", (), {"metadata": {"default_catalog": "iceberg"}}
        )(),
    )

    assert hooks._superset_database_name() == "iceberg"


def test_superset_database_uri_prefers_explicit_config(monkeypatch) -> None:
    """Explicit SQLAlchemy URI should bypass capability discovery."""
    monkeypatch.setenv("SUPERSET_DATABASE_URI", "duckdb:///warehouse.duckdb")

    assert hooks._configured_database_uri() == "duckdb:///warehouse.duckdb"


def test_superset_database_uri_can_be_derived_from_query_engine_metadata(monkeypatch) -> None:
    """Capability metadata should provide a query-engine-neutral database URI."""
    monkeypatch.delenv("SUPERSET_DATABASE_URI", raising=False)
    monkeypatch.setattr(hooks, "discover_capabilities", lambda: None)
    monkeypatch.setattr(
        hooks,
        "resolve_capability",
        lambda capability, name=None: SimpleNamespace(
            metadata={
                "sqlalchemy_uri_template": "trino://{host}:{port}/{default_catalog}",
                "host": "trino",
                "port": 8080,
                "default_catalog": "analytics",
            }
        ),
    )

    assert hooks._discover_query_engine_database_uri() == "trino://trino:8080/analytics"


def test_superset_database_uri_fails_without_config_or_metadata(monkeypatch) -> None:
    """Superset setup should fail clearly when no neutral database URI can be resolved."""
    monkeypatch.delenv("SUPERSET_DATABASE_URI", raising=False)
    monkeypatch.setattr(hooks, "discover_capabilities", lambda: None)
    monkeypatch.setattr(hooks, "resolve_capability", lambda capability, name=None: None)

    with pytest.raises(RuntimeError, match="SUPERSET_DATABASE_URI"):
        hooks._discover_query_engine_database_uri()


def test_superset_admin_credentials_fall_back_to_settings(monkeypatch, tmp_path) -> None:
    """Hook should use standard settings defaults when env vars are absent."""
    monkeypatch.delenv("SUPERSET_ADMIN_USER", raising=False)
    monkeypatch.delenv("SUPERSET_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    hooks.get_settings.cache_clear()

    assert hooks._superset_admin_credentials() == ("admin", "admin")


def test_add_query_engine_database_handles_database_uri_resolution_failure(monkeypatch) -> None:
    """Hook should log and return when no database URI can be resolved."""
    monkeypatch.setenv("SUPERSET_ADMIN_USER", "test-admin")
    monkeypatch.setenv("SUPERSET_ADMIN_PASSWORD", "test-password")
    session = Mock()
    session.headers = {}
    login_response = Mock()
    login_response.raise_for_status.return_value = None
    login_response.json.return_value = {"access_token": "token"}
    csrf_response = Mock()
    csrf_response.json.return_value = {"result": "csrf"}
    list_response = Mock()
    list_response.json.return_value = {"result": []}
    session.post.return_value = login_response
    session.get.side_effect = [csrf_response, list_response]

    monkeypatch.setattr(
        hooks.requests, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200)
    )
    monkeypatch.setattr(hooks.requests, "Session", lambda: session)
    monkeypatch.setattr(hooks, "_superset_database_name", lambda: "analytics")
    monkeypatch.setattr(hooks, "_configured_database_uri", lambda: None)
    monkeypatch.setattr(
        hooks,
        "_discover_query_engine_database_uri",
        lambda: (_ for _ in ()).throw(RuntimeError("missing uri metadata")),
    )

    hooks.add_query_engine_database()

    assert session.post.call_count == 1


def test_add_query_engine_database_uses_settings_when_env_missing(monkeypatch, tmp_path) -> None:
    """Hook startup should still log in with generated default settings."""
    monkeypatch.delenv("SUPERSET_ADMIN_USER", raising=False)
    monkeypatch.delenv("SUPERSET_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    hooks.get_settings.cache_clear()
    session = Mock()
    session.headers = {}
    login_response = Mock()
    login_response.raise_for_status.return_value = None
    login_response.json.return_value = {"access_token": "token"}
    csrf_response = Mock()
    csrf_response.json.return_value = {"result": "csrf"}
    list_response = Mock()
    list_response.json.return_value = {"result": []}
    create_response = Mock()
    create_response.raise_for_status.return_value = None
    session.post.side_effect = [login_response, create_response]
    session.get.side_effect = [csrf_response, list_response]

    monkeypatch.setattr(
        hooks.requests, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200)
    )
    monkeypatch.setattr(hooks.requests, "Session", lambda: session)
    monkeypatch.setattr(hooks, "_superset_database_name", lambda: "analytics")
    monkeypatch.setattr(hooks, "_configured_database_uri", lambda: "trino://trino:8080/iceberg")

    hooks.add_query_engine_database()

    assert session.post.call_args_list[0].kwargs["json"]["username"] == "admin"
    assert session.post.call_args_list[0].kwargs["json"]["password"] == "admin"


def test_add_query_engine_database_returns_when_admin_credentials_missing(monkeypatch) -> None:
    """Hook should preserve its no-raise contract when credentials are missing."""
    monkeypatch.delenv("SUPERSET_ADMIN_USER", raising=False)
    monkeypatch.delenv("SUPERSET_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(
        hooks,
        "get_settings",
        lambda: SimpleNamespace(superset_admin_user="", superset_admin_password=""),
    )
    session_factory = Mock()
    monkeypatch.setattr(hooks.requests, "Session", session_factory)

    hooks.add_query_engine_database()

    session_factory.assert_not_called()
