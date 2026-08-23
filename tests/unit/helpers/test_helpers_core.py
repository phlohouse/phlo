"""Regression tests for core phlo helpers: schema normalization, SQL
building, connection URLs, partition comparison, SLA/freshness rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import phlo
from phlo.capabilities import FieldSpec, NormalizedSchema
from phlo.contracts import SLA
from phlo.exceptions import PhloConfigError
from phlo.helpers import (
    as_dbt_profile,
    as_sling_connection,
    as_sqlalchemy_url,
    branch_for_run,
    build_backfill_plan,
    classify_columns,
    compare_partitions,
    connection_from_url,
    expected_partitions,
    flatten_json_records,
    freshness_rule_from_sla,
    limit_sql,
    list_partition_files,
    maintenance_recommendations,
    normalized_schema,
    object_store_url,
    parse_table_name,
    partition_range,
    partition_scope,
    partition_where_clause,
    previous_partition,
    read_dataframe,
    reconcile_counts,
    redacted_url,
    render_partition_predicate,
    required_field_null_rules,
    resolve_watermark,
    row_checksum,
    stage_path_for_run,
    synthetic_key,
    table_exists,
    table_ref_sql,
    table_stats,
    unique_key_rule,
    validate_read_only_sql,
    validate_unique_key_rows,
    watermark_where_clause,
    where_and,
)
from phlo.helpers.tables import load_table_schema, merge_batch
from phlo.schema_migration.planning import plan_schema_migration


def test_connection_helpers_redact_and_render_profiles() -> None:
    conn = connection_from_url("postgresql://user:secret@localhost:5432/app", name="warehouse")

    assert conn.name == "warehouse"
    assert conn.redacted()["password"] == "<redacted>"
    assert redacted_url(conn.config["url"]) == "postgresql://user:<redacted>@localhost:5432/app"
    assert as_sqlalchemy_url(conn) == conn.config["url"]
    assert as_sling_connection(conn)["type"] == "postgresql"
    assert as_dbt_profile(conn)["outputs"]["dev"]["dbname"] == "app"


def test_partition_helpers_build_scopes_and_ranges() -> None:
    assert partition_range("2026-05-01", "2026-05-03") == [
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
    ]
    assert previous_partition("2026-05-03") == "2026-05-02"
    summary = expected_partitions(start="2026-05-01", end="2026-05-03", existing=["2026-05-02"])
    assert summary["missing"] == ["2026-05-01", "2026-05-03"]

    scope = partition_scope("2026-05-01", partition_column="ds")
    assert render_partition_predicate(scope) == "ds = '2026-05-01'"
    assert partition_where_clause(scope) == "WHERE ds = '2026-05-01'"


def test_sql_helpers_validate_read_only_queries() -> None:
    assert table_ref_sql("raw", "events") == "raw.events"
    assert where_and("a = 1", None, "b = 2") == "(a = 1) AND (b = 2)"
    assert limit_sql("SELECT * FROM raw.events", limit=10).endswith("LIMIT 10")
    assert validate_read_only_sql("SELECT 1;") == "SELECT 1"

    with pytest.raises(PhloConfigError):
        validate_read_only_sql("DROP TABLE raw.events")


def test_read_dataframe_delegates_to_query_engine() -> None:
    class FakeQueryEngine:
        def read_dataframe(self, query, *, params=None, schema=None, schema_class=None):
            return {
                "query": query,
                "params": params,
                "schema": schema,
                "schema_class": schema_class,
            }

    class Schema:
        pass

    result = read_dataframe(
        "SELECT * FROM raw.events WHERE id > ?",
        params=[10],
        query_engine=FakeQueryEngine(),
        schema="raw",
        schema_class=Schema,
    )

    assert result == {
        "query": "SELECT * FROM raw.events WHERE id > ?",
        "params": [10],
        "schema": "raw",
        "schema_class": Schema,
    }


def test_read_dataframe_reports_query_engines_without_dataframe_support() -> None:
    class RowOnlyQueryEngine:
        def execute(self, sql):
            return []

    with pytest.raises(PhloConfigError, match="does not support DataFrame reads"):
        read_dataframe("SELECT 1", query_engine=RowOnlyQueryEngine())


def test_read_dataframe_is_exported_from_top_level_phlo() -> None:
    assert phlo.read_dataframe is read_dataframe


def test_synthetic_key_renders_oracle_sha256_expression() -> None:
    assert synthetic_key(
        dialect="oracle",
        namespace="crm.customer",
        fields=["customer_id", "updated_at"],
    ) == (
        "STANDARD_HASH("
        "'NS:12:crm.customer' || "
        "(CASE WHEN customer_id IS NULL THEN 'N:' ELSE 'V:' || "
        "TO_CHAR(LENGTH(CAST(customer_id AS VARCHAR2(4000)))) || ':' || "
        "CAST(customer_id AS VARCHAR2(4000)) END) || "
        "(CASE WHEN updated_at IS NULL THEN 'N:' ELSE 'V:' || "
        "TO_CHAR(LENGTH(CAST(updated_at AS VARCHAR2(4000)))) || ':' || "
        "CAST(updated_at AS VARCHAR2(4000)) END), "
        "'SHA256')"
    )


def test_synthetic_key_renders_sqlserver_sha256_expression() -> None:
    assert synthetic_key(dialect="sqlserver", fields=["customer_id", "updated_at"]) == (
        "CONVERT(varchar(64), HASHBYTES('SHA2_256', "
        "CONCAT("
        "(CASE WHEN customer_id IS NULL THEN 'N:' ELSE CONCAT('V:', "
        "CAST(LEN(CAST(customer_id AS nvarchar(max))) AS varchar(20)), ':', "
        "CAST(customer_id AS nvarchar(max))) END), "
        "(CASE WHEN updated_at IS NULL THEN 'N:' ELSE CONCAT('V:', "
        "CAST(LEN(CAST(updated_at AS nvarchar(max))) AS varchar(20)), ':', "
        "CAST(updated_at AS nvarchar(max))) END)"
        ")), 2)"
    )


def test_synthetic_key_namespace_prefix_is_optional_and_length_prefixed() -> None:
    assert synthetic_key(dialect="oracle", namespace="src", fields=["id"]).startswith(
        "STANDARD_HASH('NS:3:src' || "
    )
    assert synthetic_key(dialect="oracle", fields=["id"]).startswith(
        "STANDARD_HASH((CASE WHEN id IS NULL"
    )


def test_synthetic_key_is_exported_from_top_level_phlo() -> None:
    assert phlo.synthetic_key(dialect="oracle", fields=["id"]) == synthetic_key(
        dialect="oracle",
        fields=["id"],
    )


def test_synthetic_key_rejects_empty_fields_invalid_dialects_and_unsafe_identifiers() -> None:
    with pytest.raises(PhloConfigError, match="fields cannot be empty"):
        synthetic_key(dialect="oracle", fields=[])

    with pytest.raises(PhloConfigError, match="Unsupported synthetic key SQL dialect"):
        synthetic_key(dialect="postgres", fields=["id"])

    with pytest.raises(PhloConfigError, match="Unsafe synthetic key field"):
        synthetic_key(dialect="oracle", fields=["raw.id"])


def test_table_and_schema_helpers() -> None:
    parsed = parse_table_name("iceberg.raw.events")
    assert parsed.catalog == "iceberg"
    assert parsed.namespace_table == "raw.events"

    current = normalized_schema({"id": "int64", "name": "string"}, required={"id"})
    desired = NormalizedSchema(
        fields=[
            FieldSpec("id", "int64", nullable=False),
            FieldSpec("name", "string", nullable=True),
            FieldSpec("email", "string", nullable=True),
        ]
    )
    plan = plan_schema_migration(table_name="raw.users", current=current, desired=desired)
    assert plan.classification == "safe"
    assert [change.change_type for change in plan.changes] == ["add"]


def test_table_helpers_support_catalog_backed_resources() -> None:
    class FakeTable:
        def schema(self):
            return {"fields": ["id"]}

        def snapshots(self):
            return [{"snapshot_id": 1}]

        def current_snapshot(self):
            return type("Snapshot", (), {"snapshot_id": 1})()

    class FakeCatalog:
        def load_table(self, table_name):
            assert table_name == "raw.events"
            return FakeTable()

    class FakeResource:
        def get_catalog(self):
            return FakeCatalog()

    resource = FakeResource()

    assert table_exists("raw.events", table_store=resource) is True
    assert load_table_schema("raw.events", table_store=resource) == {"fields": ["id"]}
    assert table_stats("raw.events", table_store=resource) == {
        "table_name": "raw.events",
        "snapshot_count": 1,
        "current_snapshot_id": 1,
    }


def test_merge_batch_preserves_updated_rows() -> None:
    class FakeTableStore:
        def merge_parquet(self, **kwargs):
            return {"rows_inserted": 2, "rows_deleted": 1, "rows_updated": 3}

    result = merge_batch(
        "raw.events",
        "/tmp/events.parquet",
        unique_key="id",
        table_store=FakeTableStore(),
    )

    assert result.rows_inserted == 2
    assert result.rows_deleted == 1
    assert result.rows_updated == 3


def test_quality_and_reconciliation_helpers() -> None:
    schema = normalized_schema({"id": "int64", "email": "string"}, required={"id"})
    assert required_field_null_rules(schema)[0].columns == ["id"]
    assert unique_key_rule(["id", "email"]).columns == ["id", "email"]
    assert freshness_rule_from_sla(SLA(freshness_hours=24)).parameters["max_age_hours"] == 24

    uniqueness = validate_unique_key_rows([{"id": 1}, {"id": 1}], "id")
    assert uniqueness["passed"] is False
    assert reconcile_counts(10, 11, tolerance=1).passed is True
    assert compare_partitions(["a", "b"], ["b", "c"])["missing_in_target"] == ["a"]
    assert row_checksum({"b": 2, "a": 1}) == row_checksum({"a": 1, "b": 2})


def test_incremental_backfill_ingestion_governance_wap_helpers() -> None:
    watermark = resolve_watermark(
        column="updated_at",
        stored_value=datetime(2026, 5, 16, tzinfo=UTC),
        lookback=timedelta(hours=1),
    )
    assert watermark_where_clause(watermark) == "updated_at > '2026-05-15 23:00:00+00:00'"

    plan = build_backfill_plan("asset", start="2026-05-01", end="2026-05-02")
    assert plan.partition_count == 2
    assert flatten_json_records({"user": {"id": 1}, "ok": True}) == {"user_id": 1, "ok": True}
    assert classify_columns(["email", "api_token"]) == {"pii": ["email"], "secrets": ["api_token"]}
    assert branch_for_run("raw.events", "run/1") == "phlo/raw_events/run_1"


def test_storage_and_maintenance_helpers(tmp_path) -> None:
    assert object_store_url("lake", "warehouse", "raw") == "s3://lake/warehouse/raw"
    assert (
        stage_path_for_run("raw.events", run_id="run/1", partition_key="2026-05-16")
        == "s3://lake/stage/raw_events/partition=2026-05-16/run=run_1"
    )

    partition_dir = tmp_path / "partition=2026-05-16"
    partition_dir.mkdir()
    data_file = partition_dir / "data.parquet"
    data_file.write_text("x")
    assert list_partition_files(tmp_path, partition_key="2026-05-16") == [data_file]

    assert maintenance_recommendations(
        {"file_count": 20, "row_count": 100, "snapshot_count": 10, "orphan_count": 1}
    ) == ["compact_small_files", "expire_old_snapshots", "cleanup_orphan_files"]
