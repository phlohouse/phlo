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

    assert "minio-data:/data" in volumes
    assert all("./volumes/minio" not in volume for volume in volumes)


def test_minio_services_use_pinned_upstream_images() -> None:
    server = MinioServicePlugin().service_definition
    setup = MinioSetupServicePlugin().service_definition

    assert server["image"] == (
        "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z@"
        "sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
    )
    assert setup["image"] == (
        "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z@"
        "sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"
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
