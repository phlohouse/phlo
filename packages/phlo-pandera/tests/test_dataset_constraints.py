"""Public neutral constraints against real SQL, including scope and evidence."""

from contextlib import closing
from datetime import UTC, datetime
from types import SimpleNamespace

import duckdb
import pytest

from phlo import SLA, grain, range_between, reconcile_aggregate
from phlo.capabilities import AssetSpec
from phlo.capabilities.registry import CapabilityRegistry
from phlo.logging import get_logger
from phlo.quality import rules
from phlo.references import ref
from phlo_pandera import clear_quality_checks, get_quality_checks


@pytest.fixture(autouse=True)
def clear_checks():
    clear_quality_checks()
    yield
    clear_quality_checks()


def run(connection, declared, **options):
    @rules(table="target", rules=declared, backend="duckdb", **options)
    def quality():
        pass

    runtime = SimpleNamespace(
        resources={"duckdb": connection},
        tags={},
        run_id="constraints",
        partition_key="2026-10-25",
        logger=get_logger("constraints"),
    )
    return get_quality_checks()[-1].fn(runtime)


@pytest.mark.parametrize(
    ("rows", "passed"),
    [
        ("(1, 1), (1, 2), (2, 1)", True),
        ("(1, 1), (1, 2), (1, 1)", False),
        ("(1, 1), (NULL, 2)", False),
        ("(1, 1), (2, NULL)", False),
    ],
)
def test_composite_grain_requires_present_joint_identity(rows, passed):
    with closing(duckdb.connect()) as connection:
        connection.execute(f"CREATE TABLE target AS SELECT * FROM (VALUES {rows}) t(tenant, id)")
        result = run(connection, [grain("tenant", "id")], full_table=True)
        assert result.passed is passed


@pytest.mark.parametrize(
    ("source_rows", "target_rows", "aggregate", "tolerance", "passed"),
    [
        ("(11), (23)", "(34)", "sum", 0, True),
        ("(11), (23)", "(45)", "sum", 0, False),
        ("(0)", "(0)", "sum", 0, True),
        ("(NULL)", "(NULL)", "sum", 0, False),
        ("(NULL)", "(0)", "sum", 0, False),
        ("(0)", "(NULL)", "sum", 0, False),
        ("(9007199254740993)", "(9007199254740992)", "sum", 0, False),
        ("(CAST('NaN' AS DOUBLE))", "(0)", "sum", 0, False),
        ("(0)", "(CAST('Infinity' AS DOUBLE))", "sum", 0, False),
        ("(11), (23)", "(35)", "sum", 1, True),
        ("(11), (23)", "(36)", "sum", 1, False),
        ("(NULL)", "(NULL)", "count", 0, True),
        ("(11), (23)", "(17)", "avg", 0, True),
        ("(11), (23)", "(11)", "min", 0, True),
        ("(11), (23)", "(23)", "max", 0, True),
    ],
)
def test_aggregate_values_nulls_zero_and_inclusive_tolerance(
    source_rows,
    target_rows,
    aggregate,
    tolerance,
    passed,
):
    with closing(duckdb.connect()) as connection:
        connection.execute(f"CREATE TABLE source AS SELECT * FROM (VALUES {source_rows}) t(amount)")
        connection.execute(
            f"CREATE TABLE target AS SELECT * FROM (VALUES {target_rows}) t(revenue)"
        )
        result = run(
            connection,
            [
                reconcile_aggregate(
                    name="revenue_conserved",
                    source="source",
                    source_column="amount",
                    target_column="revenue",
                    aggregate=aggregate,
                    absolute_tolerance=tolerance,
                )
            ],
            full_table=True,
        )
        assert result.passed is passed


@pytest.mark.parametrize(("aggregate", "passed"), [("sum", False), ("count", True)])
def test_empty_target_does_not_skip_reconciliation(aggregate, passed):
    with closing(duckdb.connect()) as connection:
        connection.execute("CREATE TABLE source(amount INTEGER)")
        connection.execute("CREATE TABLE target(revenue INTEGER)")
        result = run(
            connection,
            [
                reconcile_aggregate(
                    name="empty",
                    source="source",
                    source_column="amount",
                    target_column="revenue",
                    aggregate=aggregate,
                )
            ],
            full_table=True,
        )
        assert result.passed is passed
        assert "No data available" not in str(result.metadata)


def test_logical_relations_independent_filters_and_partition_boundaries():
    registry = CapabilityRegistry()
    registry.register(
        "asset",
        AssetSpec(
            key="orders",
            group="sales",
            description=None,
            metadata={"catalog": "main_catalog", "table": "source"},
        ),
    )
    source = ref("orders", registry=registry, discover=False)
    with closing(duckdb.connect()) as connection:
        connection.execute("""
            CREATE TABLE source(amount INTEGER, status VARCHAR, sold_on DATE);
            INSERT INTO source VALUES (11, 'can''t', '2026-10-25'),
                (23, 'can''t', '2026-10-25'), (900, 'void', '2026-10-25'),
                (700, 'can''t', '2026-10-24');
            CREATE TABLE target(revenue INTEGER, segment VARCHAR, day DATE);
            INSERT INTO target VALUES (34, 'retail', '2026-10-25'),
                (800, 'wholesale', '2026-10-25'), (600, 'retail', '2026-10-26');
        """)
        result = run(
            connection,
            [
                reconcile_aggregate(
                    name="scoped_revenue",
                    source=source,
                    source_column="amount",
                    target_column="revenue",
                    source_filters={"status": "can't"},
                    target_filters={"segment": "retail"},
                    source_partition_column="sold_on",
                )
            ],
            partition_column="day",
            rolling_window_days=None,
        )
        assert result.passed


@pytest.mark.parametrize("source,column", [("missing", "amount"), ("source", "missing")])
def test_missing_relation_or_aggregate_fails_explicitly(source, column):
    with closing(duckdb.connect()) as connection:
        connection.execute(
            "CREATE TABLE source(amount INTEGER); CREATE TABLE target(revenue INTEGER)"
        )
        result = run(
            connection,
            [
                reconcile_aggregate(
                    name="missing",
                    source=source,
                    source_column=column,
                    target_column="revenue",
                )
            ],
            full_table=True,
        )
        assert not result.passed
        assert "Check execution failed" in str(result.metadata)


def test_unresolved_and_catalog_pinned_relations_fail_at_declaration():
    for source in [ref("unknown", discover=False), "main.gold.source"]:
        with pytest.raises(ValueError):
            reconcile_aggregate(name="invalid", source=source, source_column="x", target_column="y")
    with pytest.raises(ValueError):
        grain()
    with pytest.raises(ValueError, match="query overrides"):
        rules(table="target", rules=[grain("id")], query="SELECT * FROM other.target")
    with pytest.raises(ValueError, match="distinct evidence names"):
        rules(table="target", rules=[grain("id"), grain("id")])
    with pytest.raises(ValueError, match="distinct evidence names"):
        rules(
            table="target",
            rules=[
                range_between("revenue", min_value=0),
                reconcile_aggregate(
                    name="range_check",
                    source="source",
                    source_column="amount",
                    target_column="revenue",
                ),
            ],
        )


@pytest.mark.parametrize("columns,passed", [(["id"], True), (["missing"], False)])
def test_grain_empty_or_missing_columns(columns, passed):
    with closing(duckdb.connect()) as connection:
        connection.execute("CREATE TABLE target(id INTEGER)")
        result = run(connection, [grain(*columns)], full_table=True)
        assert result.passed is passed


@pytest.mark.parametrize(
    ("rows", "passed", "invalid_keys", "count_delta"),
    [
        ([], False, 0, "2"),
        ([(1, "2025-01-15T11:00:00Z"), (2, "2025-01-15T11:30:00Z")], True, 0, "0"),
        ([(1, "2025-01-15T11:00:00Z"), (1, "2025-01-15T11:30:00Z")], False, 1, "0"),
        ([(1, "2025-01-15T11:00:00Z")], False, 0, "1"),
        ([(1, "2025-01-14T11:00:00Z"), (2, "2025-01-14T11:30:00Z")], False, 0, "0"),
    ],
)
def test_explicit_sla_and_constraints_evaluate_together(rows, passed, invalid_keys, count_delta):
    with closing(duckdb.connect()) as connection:
        connection.execute("CREATE TABLE source AS SELECT * FROM (VALUES (1), (2)) t(id)")
        connection.execute("CREATE TABLE target(id INTEGER, event_time VARCHAR)")
        if rows:
            connection.executemany("INSERT INTO target VALUES (?, ?)", rows)
        result = run(
            connection,
            [
                grain("id"),
                reconcile_aggregate(
                    name="row_count",
                    source="source",
                    source_column="id",
                    target_column="id",
                    aggregate="count",
                ),
            ],
            full_table=True,
            sla=SLA(
                2,
                freshness_column="event_time",
                freshness_reference_time=datetime(2025, 1, 15, 12, tzinfo=UTC),
            ),
        )
        assert result.passed is passed
        assert result.metadata["checks_executed"] == 3
        assert result.metadata["grain_id_invalid_identity_groups"] == invalid_keys
        assert result.metadata["row_count_value"]["delta"] == count_delta
        assert result.metadata["freshness_check_event_time_reference_mode"] == "explicit"
