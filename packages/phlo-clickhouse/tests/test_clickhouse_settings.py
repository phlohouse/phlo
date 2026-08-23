"""Tests for ClickHouse settings defaults, endpoint generation, and caching.

Host resolution failures fall back to localhost so local stacks without DNS
still start, and get_settings() returns a cached instance across calls.
"""

import socket

import pytest

from phlo_clickhouse.settings import ClickHouseSettings, get_settings


@pytest.fixture(autouse=True)
def resolvable_hosts(monkeypatch):
    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", lambda _host: "127.0.0.1")


def test_clickhouse_settings_defaults():
    """Validate ClickHouse settings default values."""

    settings = ClickHouseSettings()

    assert settings.clickhouse_host == "clickhouse"
    assert settings.clickhouse_http_port == 8123
    assert settings.clickhouse_native_port == 19000
    assert settings.clickhouse_user == "default"
    assert settings.clickhouse_password == ""
    assert settings.clickhouse_db == "default"
    assert settings.clickhouse_secure is False


def test_clickhouse_settings_http_endpoint():
    """Validate ClickHouse HTTP endpoint generation."""

    settings = ClickHouseSettings()

    assert settings.clickhouse_http_endpoint() == "clickhouse:8123"


def test_clickhouse_settings_native_endpoint():
    """Validate ClickHouse native endpoint generation."""

    settings = ClickHouseSettings()

    assert settings.clickhouse_native_endpoint() == "clickhouse:19000"


def test_clickhouse_settings_with_overrides():
    """Validate ClickHouse settings with override values."""

    settings = ClickHouseSettings(
        clickhouse_host="my-host",
        clickhouse_http_port=9000,
        clickhouse_native_port=9001,
        clickhouse_user="admin",
        clickhouse_password="secret",
        clickhouse_db="mydb",
        clickhouse_secure=True,
    )

    assert settings.clickhouse_host == "my-host"
    assert settings.clickhouse_http_port == 9000
    assert settings.clickhouse_native_port == 9001
    assert settings.clickhouse_user == "admin"
    assert settings.clickhouse_password == "secret"
    assert settings.clickhouse_db == "mydb"
    assert settings.clickhouse_secure is True


def test_clickhouse_settings_resolves_unreachable_host(tmp_path, monkeypatch):
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env.local").write_text("CLICKHOUSE_HTTP_PORT=18123\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLICKHOUSE_HTTP_PORT", raising=False)

    def raise_unresolvable(_host: str) -> str:
        raise socket.gaierror()

    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", raise_unresolvable)

    settings = ClickHouseSettings()

    assert settings.clickhouse_http_endpoint() == "localhost:18123"


def test_get_settings_returns_cached():
    """Validate that get_settings returns cached instance."""

    settings1 = get_settings()
    settings2 = get_settings()

    assert settings1 is settings2
