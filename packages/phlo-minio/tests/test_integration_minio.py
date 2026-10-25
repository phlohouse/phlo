"""Tests for phlo-minio plugin identity, service definition, and settings.

Pins the shipped service definition (pinned image, compose wiring) and the
endpoint composition that S3 clients depend on.
"""

from phlo_minio.plugin import MinioServicePlugin
from phlo_minio.settings import MinioSettings


def test_plugin_metadata():
    """MinIO advertises its own name and a non-empty version."""
    plugin = MinioServicePlugin()
    metadata = plugin.metadata

    assert metadata.name == "minio"
    assert metadata.version


def test_service_definition_loads():
    """The packaged service definition exposes the flat service contract."""
    plugin = MinioServicePlugin()
    service_def = plugin.service_definition

    assert isinstance(service_def, dict)
    assert service_def["name"] == "minio"
    assert "compose" in service_def


def test_service_definition_has_minio_image():
    """The definition names the pinned MinIO image rather than a build context."""
    definition = MinioServicePlugin().service_definition

    assert definition["image"].startswith("ghcr.io/phlohouse/phlo-minio:")
    assert definition["build"]["dockerfile"] == "Dockerfile"


def test_minio_endpoint_joins_host_and_port():
    """The endpoint is ``host:port`` so S3 clients can build a URL from it."""
    settings = MinioSettings(minio_host="localhost", minio_api_port=9000)

    assert settings.minio_endpoint() == "localhost:9000"
