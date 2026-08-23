"""Tests for OpenMetadata settings.

Pins local-development defaults, the localhost fallback when the configured
host is unresolvable, and the precedence rule: explicitly configured database
and service type win over values resolved from the query-engine capability.
"""

import socket
from unittest.mock import Mock, patch

from phlo_openmetadata.settings import OpenMetadataSettings


def test_openmetadata_settings_defaults() -> None:
    """Settings keep local-development credentials by default."""
    settings = OpenMetadataSettings()

    assert settings.openmetadata_username == "admin"
    assert settings.openmetadata_password == "admin"


def test_openmetadata_settings_resolves_unreachable_host(tmp_path, monkeypatch) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env.local").write_text("OPENMETADATA_PORT=18585\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENMETADATA_PORT", raising=False)

    def raise_unresolvable(_host: str) -> str:
        raise socket.gaierror()

    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", raise_unresolvable)

    settings = OpenMetadataSettings()

    assert settings.openmetadata_uri() == "http://localhost:18585/api"


def test_openmetadata_database_prefers_explicit_name():
    """Explicit configured database name wins over capability resolution."""
    settings = OpenMetadataSettings(openmetadata_database_name="warehouse")

    with patch("phlo_openmetadata.settings.resolve_query_engine_catalog") as resolve_mock:
        assert settings.openmetadata_database() == "warehouse"

    resolve_mock.assert_not_called()


def test_openmetadata_database_uses_query_engine_capability():
    """Settings resolve the database name from the query engine capability."""
    settings = OpenMetadataSettings(
        openmetadata_database_name=None,
        openmetadata_query_engine="duckdb",
    )
    globals_dict = OpenMetadataSettings.openmetadata_database.__globals__
    resolve_mock = Mock(return_value="iceberg_dev")

    with patch.dict(
        globals_dict,
        {"resolve_query_engine_catalog": resolve_mock},
    ):
        assert settings.openmetadata_database() == "iceberg_dev"

    resolve_mock.assert_called_once_with("duckdb")


def test_openmetadata_service_type_prefers_explicit_value():
    """Explicit configured service type wins over capability resolution."""
    settings = OpenMetadataSettings(openmetadata_service_type="Postgres")

    with patch("phlo_openmetadata.settings.resolve_query_engine_service_type") as resolve_mock:
        assert settings.openmetadata_database_service_type() == "Postgres"

    resolve_mock.assert_not_called()


def test_openmetadata_service_type_uses_query_engine_capability():
    """Settings resolve the service type from the query engine capability."""
    settings = OpenMetadataSettings(
        openmetadata_service_type=None,
        openmetadata_query_engine="duckdb",
    )
    globals_dict = OpenMetadataSettings.openmetadata_database_service_type.__globals__
    resolve_mock = Mock(return_value="Trino")

    with patch.dict(
        globals_dict,
        {"resolve_query_engine_service_type": resolve_mock},
    ):
        assert settings.openmetadata_database_service_type() == "Trino"

    resolve_mock.assert_called_once_with("duckdb")
