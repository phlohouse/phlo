"""Tests for the MinIO service plugin.

Pins deployment invariants: data lives on a named volume (never a host
bind-mount), upstream images are pinned by digest with no local builds, setup
waits for mc readiness, and the plugin exposes an object_store capability
backed by MinioResourceProvider.
"""

from phlo_minio.plugin import MinioResourceProvider, MinioServicePlugin, MinioSetupServicePlugin


def test_minio_service_definition():
    """Validate MinIO service definition fields."""

    plugin = MinioServicePlugin()
    service_definition = plugin.service_definition

    assert service_definition["name"] == "minio"
    assert service_definition["category"] == "core"


def test_minio_service_uses_named_volume():
    """MinIO should not bind-mount a local data directory."""

    plugin = MinioServicePlugin()
    volumes = plugin.service_definition["compose"]["volumes"]

    assert "minio-data:/bitnami/minio/data" in volumes
    assert all("./volumes/minio" not in volume for volume in volumes)


def test_minio_services_use_pinned_upstream_images() -> None:
    server = MinioServicePlugin().service_definition
    setup = MinioSetupServicePlugin().service_definition

    assert server["image"] == (
        "bitnamilegacy/minio:2025.7.23-debian-12-r5@"
        "sha256:6dabb4a2088c9a79908de3bc05f4586c23ad2182c8908e7e3acbf61c1467fb20"
    )
    assert setup["image"] == (
        "bitnamilegacy/minio-client:2025.7.21-debian-12-r3@"
        "sha256:73bd39f7899a0cef12b8dd5df13aa93a3ed1aaa44236542442e9ac76819ac158"
    )
    assert "build" not in server
    assert "build" not in setup
    assert "until mc ready myminio" in setup["compose"]["entrypoint"]


def test_minio_resource_provider_exposes_object_store(monkeypatch) -> None:
    """MinIO should expose an object_store capability."""
    monkeypatch.setattr(
        "phlo_minio.plugin.MinioObjectStoreProvider.to_sling_connection",
        lambda _self: {
            "type": "s3",
            "endpoint": "http://minio:9000",
            "access_key_id": "minio",
            "secret_access_key": "secret",
            "region": "us-east-1",
        },
    )

    provider = MinioResourceProvider()

    object_stores = provider.get_object_stores()

    assert len(object_stores) == 1
    assert object_stores[0].name == "minio"
    assert object_stores[0].metadata["endpoint"] == "http://minio:9000"
