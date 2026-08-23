"""Tests for ClickHouse resource provider capabilities.

Verifies the provider-neutral surface of ClickHouseResourceProvider: one
resource, table store, query engine, and publish target, each named
"clickhouse", with snapshots unsupported and schema evolution supported.
"""

from phlo_clickhouse.plugin import ClickHouseResourceProvider


def test_clickhouse_resource_provider_metadata():
    """Validate ClickHouse resource provider metadata."""

    provider = ClickHouseResourceProvider()
    metadata = provider.metadata

    assert metadata.name == "clickhouse"
    assert metadata.version == "0.1.0"


def test_clickhouse_resource_provider_get_resources():
    """Validate ClickHouse resource provider returns resources."""

    provider = ClickHouseResourceProvider()
    resources = provider.get_resources()

    assert len(resources) == 1
    assert resources[0].name == "clickhouse"


def test_clickhouse_resource_provider_get_table_stores():
    """Validate ClickHouse resource provider returns table store specs."""

    provider = ClickHouseResourceProvider()
    table_stores = provider.get_table_stores()

    assert len(table_stores) == 1
    assert table_stores[0].name == "clickhouse"
    assert table_stores[0].support.supports_snapshots is False
    assert table_stores[0].support.supports_schema_evolution is True


def test_clickhouse_resource_provider_get_query_engines():
    """Validate ClickHouse resource provider returns query engine specs."""

    provider = ClickHouseResourceProvider()
    query_engines = provider.get_query_engines()

    assert len(query_engines) == 1
    assert query_engines[0].name == "clickhouse"
    assert query_engines[0].metadata["service_type"] == "ClickHouse"
    assert query_engines[0].support.supports_snapshots is False
    assert query_engines[0].support.supports_time_travel is False


def test_clickhouse_resource_provider_get_publish_targets():
    """Validate ClickHouse resource provider returns publish target specs."""

    provider = ClickHouseResourceProvider()
    publish_targets = provider.get_publish_targets()

    assert len(publish_targets) == 1
    assert publish_targets[0].name == "clickhouse"
    assert publish_targets[0].metadata["target_system"] == "clickhouse"
