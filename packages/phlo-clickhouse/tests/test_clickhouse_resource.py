"""Tests for ClickHouse resource.

Pins resource defaults and override handling plus the query-engine support
flags, including that snapshots are not supported for ClickHouse.
"""

from phlo_clickhouse.resource import CLICKHOUSE_QUERY_ENGINE_SUPPORT, ClickHouseResource


def test_clickhouse_resource_defaults():
    """Validate ClickHouse resource default values."""

    resource = ClickHouseResource()

    assert resource.host is None
    assert resource.port is None
    assert resource.user is None
    assert resource.password is None
    assert resource.database is None
    assert resource.secure is None


def test_clickhouse_resource_with_overrides():
    """Validate ClickHouse resource with override values."""

    resource = ClickHouseResource(
        host="my-clickhouse",
        port=9000,
        user="admin",
        password="secret",
        database="mydb",
        secure=True,
    )

    assert resource.host == "my-clickhouse"
    assert resource.port == 9000
    assert resource.user == "admin"
    assert resource.password == "secret"
    assert resource.database == "mydb"
    assert resource.secure is True


def test_clickhouse_query_engine_support():
    """Validate ClickHouse query engine support flags."""

    assert CLICKHOUSE_QUERY_ENGINE_SUPPORT.supports_snapshots is False
    assert CLICKHOUSE_QUERY_ENGINE_SUPPORT.supports_time_travel is False
