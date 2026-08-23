"""Tests IcebergSettings catalog config: host names resolve to service URLs
and endpoints stay explicit for local stacks."""

from __future__ import annotations

import socket

from phlo_iceberg.settings import IcebergSettings


def test_get_pyiceberg_catalog_config_resolves_host_service_urls(monkeypatch) -> None:
    def _raise_unresolvable(_host: str) -> str:
        raise socket.gaierror()

    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", _raise_unresolvable)
    monkeypatch.setenv("NESSIE_PORT", "19120")
    monkeypatch.setenv("MINIO_API_PORT", "9000")

    settings = IcebergSettings(
        iceberg_catalog_uri="http://nessie:19120/iceberg",
        iceberg_s3_endpoint="http://minio:9000",
    )

    config = settings.get_pyiceberg_catalog_config(ref="main")

    assert config["uri"] == "http://localhost:19120/iceberg/main"
    assert config["s3.endpoint"] == "http://localhost:9000"


def test_get_pyiceberg_catalog_config_preserves_resolvable_urls(monkeypatch) -> None:
    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", lambda _host: "127.0.0.1")

    settings = IcebergSettings(
        iceberg_catalog_uri="http://localhost:19120/iceberg",
        iceberg_s3_endpoint="http://localhost:9000",
    )

    config = settings.get_pyiceberg_catalog_config(ref="dev")

    assert config["uri"] == "http://localhost:19120/iceberg/dev"
    assert config["s3.endpoint"] == "http://localhost:9000"


def test_pyiceberg_uses_nessie_identifier_and_retains_physical_warehouse_path(monkeypatch) -> None:
    monkeypatch.setenv("ICEBERG_WAREHOUSE_PATH", "s3://other-lake/physical-warehouse")

    settings = IcebergSettings()
    config = settings.get_pyiceberg_catalog_config(ref="main")

    assert config["warehouse"] == "warehouse"
    assert settings.iceberg_warehouse_path == "s3://other-lake/physical-warehouse"


def test_iceberg_settings_accept_short_s3_env_alias(monkeypatch) -> None:
    monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://localhost:10001")
    monkeypatch.setenv("ICEBERG_S3_ACCESS_KEY", "local-key")
    monkeypatch.setenv("ICEBERG_S3_SECRET_KEY", "local-secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")

    settings = IcebergSettings()

    assert settings.iceberg_s3_endpoint == "http://localhost:10001"
    assert settings.iceberg_s3_access_key == "local-key"
    assert settings.iceberg_s3_secret_key == "local-secret"
    assert settings.iceberg_s3_region == "eu-west-2"
