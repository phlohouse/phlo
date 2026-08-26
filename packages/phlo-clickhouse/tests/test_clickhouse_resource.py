"""Tests for ClickHouse resource.

Pins resource defaults and override handling plus the query-engine support
flags, including that snapshots are not supported for ClickHouse.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pyarrow as pa

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


def _recording_resource():
    """Build a resource whose client records DDL and insert targets."""
    resource = ClickHouseResource()
    client = MagicMock()
    client.command.return_value = None
    resource.get_client = lambda: client  # type: ignore[method-assign]
    return resource, client


class FakeEvaluation:
    def __init__(self, passed, severity=None, blocking=None):
        self.passed = passed
        if severity is not None:
            self.severity = severity
        if blocking is not None:
            self.blocking = blocking


def test_resolve_target_splits_namespace():
    """Namespace-qualified names select their database; bare names default."""
    from phlo_clickhouse.settings import get_settings

    resource = ClickHouseResource()

    database, table = resource._resolve_target("raw.platform_events")
    assert database == "`raw`"
    assert table == "`platform_events`"

    database, table = resource._resolve_target("events")
    assert database == f"`{get_settings().clickhouse_db}`"
    assert table == "`events`"


def test_ensure_table_creates_namespace_database_and_table(monkeypatch):
    """DDL creates the namespace database on demand and qualifies the table."""
    import pyarrow as pa

    resource, client = _recording_resource()
    monkeypatch.setattr(
        "phlo_clickhouse.settings.get_settings",
        lambda: SimpleNamespace(clickhouse_db="default"),
    )
    schema = pa.schema([("event_id", pa.string()), ("latency_ms", pa.int64())])

    resource.ensure_table(table_name="raw.platform_events", schema=schema)

    statements = [call.args[0] for call in client.command.call_args_list]
    assert any("CREATE DATABASE IF NOT EXISTS `raw`" in sql for sql in statements)
    create = next(sql for sql in statements if "CREATE TABLE" in sql)
    assert "CREATE TABLE IF NOT EXISTS `raw`.`platform_events`" in create
    assert "`event_id` String" in create
    assert "`latency_ms` Int64" in create


def test_schema_to_columns_supports_arrow_schemas():
    """Arrow schemas render backtick-quoted names with CH types."""
    import pyarrow as pa

    resource = ClickHouseResource()
    schema = pa.schema(
        [
            ("event_id", pa.string()),
            ("occurred_at", pa.timestamp("us", tz="UTC")),
            ("duration_ms", pa.int64()),
        ]
    )
    rendered = resource._schema_to_columns(schema)
    assert rendered == (
        "`event_id` String, `occurred_at` DateTime64(6, 'UTC'), `duration_ms` Int64"
    )


def test_arrow_type_to_clickhouse_mappings():
    """Core arrow types map onto ClickHouse type names."""
    import pyarrow as pa

    resource = ClickHouseResource()
    assert resource._arrow_type_to_clickhouse(pa.string()) == "String"
    assert resource._arrow_type_to_clickhouse(pa.int64()) == "Int64"
    assert resource._arrow_type_to_clickhouse(pa.float64()) == "Float64"
    assert resource._arrow_type_to_clickhouse(pa.bool_()) == "Bool"
    naive = pa.timestamp("us")
    assert resource._arrow_type_to_clickhouse(naive) == "DateTime64(6)"
    tz = pa.timestamp("us", tz="UTC")
    assert resource._arrow_type_to_clickhouse(tz) == "DateTime64(6, 'UTC')"
    assert resource._arrow_type_to_clickhouse(pa.decimal128(10, 2)) == "String"


def test_schema_from_validation_schema_returns_arrow_with_metadata():
    """The converter returns an arrow schema including traceability columns."""
    from pandera.pandas import DataFrameModel

    resource = ClickHouseResource()

    class TenantSchema(DataFrameModel):
        tenant_id: str
        tier: str

    schema = resource.schema_from_validation_schema(TenantSchema)
    assert isinstance(schema, pa.Schema)
    assert schema.names[:2] == ["tenant_id", "tier"]
    assert "_phlo_row_id" in schema.names
    assert "_dlt_load_id" in schema.names


def test_append_parquet_targets_resolved_namespace(monkeypatch):
    """Inserts route through the parsed database/table identifiers."""
    import pandas as pd
    from unittest.mock import MagicMock

    resource, client = _recording_resource()
    monkeypatch.setattr(
        "phlo_clickhouse.resource.pd",
        MagicMock(read_parquet=lambda _path: pd.DataFrame({"a": [1]})),
    )

    result = resource.append_parquet(table_name="raw.access_logs", data_path="/tmp/fake.parquet")

    insert_target = client.insert_df.call_args.args[0]
    assert insert_target == "`raw`.`access_logs`"
    assert result["rows_inserted"] == 1
