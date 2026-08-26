"""Tests for Sling connection auto-discovery.

Auto-connections resolve from phlo-iceberg settings and the active
object_store capability, and never guess when discovery is disabled or more
than one object store exists. Exported connections become JSON-string env
vars for the Sling CLI.
"""

import json
from types import SimpleNamespace

from phlo_sling.connections import (
    _resolve_iceberg_connection,
    _resolve_s3_connection,
    export_sling_env,
    resolve_phlo_connections,
)


def test_export_sling_env_empty():
    """Empty connections produce empty env."""
    result = export_sling_env({})
    assert result == {}


def test_export_sling_env_format():
    """Connection config is exported as JSON strings."""
    connections = {
        "TEST_PG": {"type": "postgres", "host": "localhost", "port": 5432},
    }
    result = export_sling_env(connections)
    assert "TEST_PG" in result
    parsed = json.loads(result["TEST_PG"])
    assert parsed["type"] == "postgres"
    assert parsed["host"] == "localhost"


def test_resolve_phlo_connections_respects_auto_connections_setting(monkeypatch) -> None:
    """Auto-generated connections should be skipped when disabled."""
    monkeypatch.setattr(
        "phlo_sling.connections.get_settings",
        lambda: SimpleNamespace(sling_auto_connections=False),
    )
    monkeypatch.setattr(
        "phlo_sling.connections._resolve_postgres_connection",
        lambda: {"PHLO_POSTGRES": {"type": "postgres"}},
    )
    monkeypatch.setattr(
        "phlo_sling.connections._resolve_iceberg_connection",
        lambda: {"PHLO_ICEBERG": {"type": "iceberg"}},
    )
    monkeypatch.setattr(
        "phlo_sling.connections._resolve_s3_connection",
        lambda: {"PHLO_S3": {"type": "s3"}},
    )

    assert resolve_phlo_connections() == {}


def test_resolve_iceberg_connection_uses_phlo_iceberg_settings(monkeypatch) -> None:
    """Iceberg auto-connections should resolve from phlo-iceberg settings."""
    monkeypatch.setattr(
        "phlo_sling.connections._get_iceberg_settings",
        lambda: SimpleNamespace(
            iceberg_default_ref="main",
            iceberg_default_namespace="raw",
            get_pyiceberg_catalog_config=lambda ref: {
                "uri": f"http://localhost:19120/iceberg/{ref}",
                "warehouse": f"s3://lake/{ref}",
                "s3.endpoint": "http://localhost:10001",
                "s3.access-key-id": "minio",
                "s3.secret-access-key": "secret",
                "s3.region": "us-east-1",
            },
        ),
    )

    result = _resolve_iceberg_connection()

    assert result["PHLO_ICEBERG"]["type"] == "iceberg"
    assert result["PHLO_ICEBERG"]["catalog_type"] == "rest"
    assert result["PHLO_ICEBERG"]["rest_uri"] == "http://localhost:19120/iceberg/main"
    assert result["PHLO_ICEBERG"]["rest_warehouse"] == "s3://lake/main"
    assert result["PHLO_ICEBERG"]["s3_endpoint"] == "http://localhost:10001"
    assert result["PHLO_ICEBERG"]["schema"] == "raw"


def test_resolve_s3_connection_uses_object_store_capability(monkeypatch) -> None:
    """S3 auto-connections should resolve from the active object_store capability."""
    monkeypatch.setattr("phlo_sling.connections._ensure_capabilities_discovered", lambda *_k: None)
    monkeypatch.setattr("phlo_sling.connections.list_capabilities", lambda _kind: ["minio"])
    monkeypatch.setattr(
        "phlo_sling.connections.resolve_capability",
        lambda _kind, _name=None: SimpleNamespace(
            name="minio",
            provider=SimpleNamespace(
                to_sling_connection=lambda: {
                    "type": "s3",
                    "endpoint": "http://minio:9000",
                    "access_key_id": "minio",
                    "secret_access_key": "secret",
                    "region": "us-east-1",
                }
            ),
            metadata={},
        ),
    )

    result = _resolve_s3_connection()

    assert result["PHLO_S3"]["access_key_id"] == "minio"
    assert result["PHLO_S3"]["secret_access_key"] == "secret"


def test_resolve_s3_connection_skips_when_object_store_is_ambiguous(monkeypatch) -> None:
    """Auto-connections should not guess when multiple object_store capabilities exist."""
    monkeypatch.setattr("phlo_sling.connections._ensure_capabilities_discovered", lambda *_k: None)
    monkeypatch.setattr(
        "phlo_sling.connections.list_capabilities",
        lambda _kind: ["minio", "rustfs"],
    )
    monkeypatch.setattr(
        "phlo_sling.connections.resolve_capability",
        lambda _kind, _name=None: None,
    )

    assert _resolve_s3_connection() == {}


def test_clickhouse_connection_uses_native_settings(monkeypatch) -> None:
    from types import SimpleNamespace

    fake = SimpleNamespace(
        clickhouse_host="ch-host",
        clickhouse_native_port=19000,
        clickhouse_db="analytics",
        clickhouse_user="svc",
        clickhouse_password="pw",
    )
    import phlo_clickhouse.settings as ch_settings

    monkeypatch.setattr(ch_settings, "get_settings", lambda: fake)
    from phlo_sling.connections import _resolve_clickhouse_connection

    conn = _resolve_clickhouse_connection()
    assert conn["PHLO_CLICKHOUSE"] == {
        "type": "clickhouse",
        "host": "ch-host",
        "port": 19000,
        "database": "analytics",
        "user": "svc",
        "password": "pw",
    }


def test_delta_connection_uses_warehouse_path(monkeypatch) -> None:
    fake = SimpleNamespace(delta_warehouse_path="s3://lake/warehouse/delta")
    import phlo_delta.settings as delta_settings

    monkeypatch.setattr(delta_settings, "get_settings", lambda: fake)
    from phlo_sling.connections import _resolve_delta_connection

    conn = _resolve_delta_connection()
    assert conn["PHLO_DELTA"] == {
        "type": "file",
        "root_path": "s3://lake/warehouse/delta",
    }


def test_resolvers_return_empty_when_provider_missing(monkeypatch) -> None:
    import phlo_sling.connections as conn_mod

    def raise_import_error():
        raise ImportError("not installed")

    monkeypatch.setattr(conn_mod, "_resolve_clickhouse_connection", lambda: {})
    assert conn_mod._resolve_clickhouse_connection() == {}
