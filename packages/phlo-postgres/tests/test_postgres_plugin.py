"""Tests for Postgres service and resource plugins.

Pins digest-pinned upstream images for the server and exporter, the
volume-setup guard that refuses pre-18 data layouts, and the resource
provider's single postgres resource plus serving-role publish target.
"""

from phlo.capabilities import PublishTargetSpec
from phlo_postgres.plugin import (
    PostgresExporterServicePlugin,
    PostgresResourceProvider,
    PostgresServicePlugin,
    PostgresVolumeSetupServicePlugin,
)
from phlo_postgres.publish_target import PostgresPublishTarget


def test_postgres_service_definition():
    """Validate Postgres service definition fields."""
    plugin = PostgresServicePlugin()
    service_definition = plugin.service_definition

    assert service_definition["name"] == "postgres"
    assert service_definition["category"] == "core"


def test_postgres_service_uses_pinned_upstream_image() -> None:
    service_definition = PostgresServicePlugin().service_definition

    assert service_definition["image"] == (
        "postgres:18.4-alpine3.24@"
        "sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"
    )
    assert "build" not in service_definition


def test_postgres_volume_setup_rejects_pre_18_data_layout() -> None:
    command = PostgresVolumeSetupServicePlugin().service_definition["compose"]["command"]

    assert "/var/lib/postgresql/PG_VERSION" in command
    assert "PostgreSQL 16 data volume detected" in command
    assert "exit 1" in command


def test_postgres_exporter_uses_pinned_upstream_image() -> None:
    service_definition = PostgresExporterServicePlugin().service_definition

    assert service_definition["image"] == (
        "quay.io/prometheuscommunity/postgres-exporter:v0.20.1@"
        "sha256:ac5ec343104fae0e2d84a27bb8d69b38430a11910c5382cad85d478d2bab713e"
    )
    assert "build" not in service_definition


def test_postgres_resource_provider():
    """Validate Postgres resource provider output."""
    provider = PostgresResourceProvider()
    resources = provider.get_resources()

    assert len(resources) == 1
    assert resources[0].name == "postgres"


def test_postgres_resource_provider_exposes_publish_target() -> None:
    provider = PostgresResourceProvider()
    publish_targets = provider.get_publish_targets()

    assert publish_targets == [
        PublishTargetSpec(
            name="postgres",
            provider=PostgresPublishTarget(),
            metadata={"target_system": "postgres", "role": "serving"},
        )
    ]
