"""Tests for the MinIO service plugin.

Pins deployment invariants: data lives on a named volume (never a host
bind-mount), server and setup share one publishable Phlo image, setup
waits for mc readiness, and the plugin exposes an object_store capability
backed by MinioResourceProvider.
"""

from pathlib import Path

from phlo.plugins.discovery._service_definition import ServiceDefinition
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


def test_minio_services_share_publishable_image() -> None:
    server = MinioServicePlugin().service_definition
    setup = MinioSetupServicePlugin().service_definition

    assert server["image"] == setup["image"] == "ghcr.io/phlohouse/phlo-minio:0.17.1"
    assert (
        server["build"]
        == setup["build"]
        == {
            "context": "./minio",
            "dockerfile": "Dockerfile",
        }
    )
    assert {"source": "Dockerfile", "dest": "minio/Dockerfile"} in server["files"]
    assert (Path(__file__).resolve().parents[1] / "src/phlo_minio/Dockerfile").is_file()
    assert "until mc ready myminio" in setup["compose"]["entrypoint"]

    volume_setup = ServiceDefinition.from_yaml(
        Path(__file__).resolve().parents[1] / "src" / "phlo_minio" / "minio-volume-setup.yaml"
    )
    assert volume_setup.image == (
        "alpine:3.24.1@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"
    )


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
