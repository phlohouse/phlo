"""Tests for column-level lineage.

Covers the frozen ColumnLineage dataclass, dbt manifest column extraction
(same-name columns map lineage; no columns or no overlap yield nothing), and
the store's column-lineage persistence and queries driven through a scripted
fake psycopg2 driver that records exact bound parameters and replays queued
fetch results.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from phlo_lineage import store as store_module
from phlo_lineage.dbt_column_lineage import extract_column_lineage
from phlo_lineage.store import ColumnLineage, LineageStore

LINEAGE_DSN = "postgresql://lineage-fake/test"


# -------------------------------------------------------------------
# ColumnLineage dataclass
# -------------------------------------------------------------------


class TestColumnLineageDataclass:
    """Tests for the ColumnLineage frozen dataclass."""

    def test_construction(self):
        cl = ColumnLineage(
            source_asset="bronze.raw_events",
            source_column="user_id",
            target_asset="silver.stg_events",
            target_column="user_id",
        )
        assert cl.source_asset == "bronze.raw_events"
        assert cl.source_column == "user_id"
        assert cl.target_asset == "silver.stg_events"
        assert cl.target_column == "user_id"
        assert cl.source_type == "dbt_heuristic"
        assert cl.metadata is None

    def test_frozen(self):
        cl = ColumnLineage(
            source_asset="a",
            source_column="b",
            target_asset="c",
            target_column="d",
        )
        with pytest.raises(AttributeError):
            cl.source_asset = "x"  # type: ignore[misc]

    def test_with_metadata(self):
        cl = ColumnLineage(
            source_asset="a",
            source_column="b",
            target_asset="c",
            target_column="d",
            metadata={"confidence": 0.9},
        )
        assert cl.metadata == {"confidence": 0.9}


# -------------------------------------------------------------------
# dbt manifest extraction
# -------------------------------------------------------------------


def _make_manifest(nodes: dict) -> dict:
    return {"nodes": nodes}


def _make_model_node(
    *,
    schema: str,
    name: str,
    columns: list[str],
    depends_on_nodes: list[str] | None = None,
    alias: str | None = None,
) -> dict:
    node: dict = {
        "resource_type": "model",
        "schema": schema,
        "name": name,
        "columns": {col: {"name": col} for col in columns},
        "depends_on": {"nodes": depends_on_nodes or []},
    }
    if alias:
        node["alias"] = alias
    return node


class TestExtractColumnLineageSameName:
    """Two models sharing column names should produce mappings."""

    def test_same_name_intersection(self):
        manifest = _make_manifest(
            {
                "model.pkg.src_events": _make_model_node(
                    schema="bronze",
                    name="src_events",
                    columns=["user_id", "event_type", "created_at"],
                ),
                "model.pkg.stg_events": _make_model_node(
                    schema="silver",
                    name="stg_events",
                    columns=["user_id", "event_type", "processed_at"],
                    depends_on_nodes=["model.pkg.src_events"],
                ),
            }
        )
        mappings = extract_column_lineage(manifest)

        assert len(mappings) == 2
        pairs = {(m.source_column, m.target_column) for m in mappings}
        assert ("user_id", "user_id") in pairs
        assert ("event_type", "event_type") in pairs

        for m in mappings:
            assert m.source_asset == "bronze.src_events"
            assert m.target_asset == "silver.stg_events"
            assert m.source_type == "dbt_heuristic"

    def test_uses_alias_when_present(self):
        manifest = _make_manifest(
            {
                "model.pkg.upstream": _make_model_node(
                    schema="raw",
                    name="upstream",
                    columns=["id"],
                    alias="raw_upstream",
                ),
                "model.pkg.downstream": _make_model_node(
                    schema="staging",
                    name="downstream",
                    columns=["id"],
                    depends_on_nodes=["model.pkg.upstream"],
                ),
            }
        )
        mappings = extract_column_lineage(manifest)
        assert len(mappings) == 1
        assert mappings[0].source_asset == "raw.raw_upstream"


class TestExtractColumnLineageNoColumns:
    """Model with no columns defined yields empty."""

    def test_empty_columns(self):
        manifest = _make_manifest(
            {
                "model.pkg.src": _make_model_node(schema="bronze", name="src", columns=["col_a"]),
                "model.pkg.stg": _make_model_node(
                    schema="silver",
                    name="stg",
                    columns=[],
                    depends_on_nodes=["model.pkg.src"],
                ),
            }
        )
        assert extract_column_lineage(manifest) == []


class TestExtractColumnLineageNoOverlap:
    """No shared column names yields empty."""

    def test_no_overlap(self):
        manifest = _make_manifest(
            {
                "model.pkg.src": _make_model_node(
                    schema="bronze", name="src", columns=["alpha", "beta"]
                ),
                "model.pkg.stg": _make_model_node(
                    schema="silver",
                    name="stg",
                    columns=["gamma", "delta"],
                    depends_on_nodes=["model.pkg.src"],
                ),
            }
        )
        assert extract_column_lineage(manifest) == []


# -------------------------------------------------------------------
# Store persistence and queries (scripted fake driver)
# -------------------------------------------------------------------


@dataclass
class _Statement:
    """One executed statement: its SQL plus the exact bound parameters."""

    sql: str
    params: tuple | None


class _FakeCursor:
    """Records statements and replays scripted results and rowcounts."""

    def __init__(self, driver: _FakeDriver) -> None:
        self._driver = driver
        self.rowcount = -1

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self._driver.statements.append(_Statement(sql=sql, params=params))
        self.rowcount = self._driver.next_affected()
        self._driver.affected_history.append(self.rowcount)

    def record_batch(self, sql: str, values: tuple) -> None:
        """Receiving end of psycopg2.extras.execute_values."""
        self._driver.batches.append(_Statement(sql=sql, params=values))
        self.rowcount = self._driver.next_affected()
        self._driver.affected_history.append(self.rowcount)

    def fetchone(self):
        return self._driver.next_fetchone()

    def fetchall(self):
        return self._driver.next_fetchall()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class _FakeConnection:
    def __init__(self, driver: _FakeDriver) -> None:
        self._driver = driver
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._driver)

    def commit(self) -> None:
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class _FakeDriver:
    """In-memory psycopg2 stand-in with scripted fetches and rowcounts."""

    def __init__(self) -> None:
        self.statements: list[_Statement] = []
        self.batches: list[_Statement] = []
        self.connections: list[_FakeConnection] = []
        self.affected_history: list[int] = []
        self.connect_error: Exception | None = None
        self._affected: deque[int] = deque()
        self._fetchone_results: deque = deque()
        self._fetchall_results: deque[list] = deque()

    def connect(self, dsn: str) -> _FakeConnection:
        if self.connect_error is not None:
            raise self.connect_error
        connection = _FakeConnection(self)
        self.connections.append(connection)
        return connection

    def script_affected(self, *rowcounts: int) -> None:
        """Queue cursor.rowcount values, consumed one per execute/batch."""
        self._affected.extend(rowcounts)

    def script_fetchall(self, *result_lists: list) -> None:
        self._fetchall_results.extend(result_lists)

    def next_affected(self) -> int:
        return self._affected.popleft() if self._affected else -1

    def next_fetchone(self):
        return self._fetchone_results.popleft() if self._fetchone_results else None

    def next_fetchall(self) -> list:
        return self._fetchall_results.popleft() if self._fetchall_results else []

    @property
    def total_affected(self) -> int:
        return sum(count for count in self.affected_history if count > 0)


@pytest.fixture
def fresh_schema_cache():
    """Reset the class-wide schema cache so bootstrap branches stay reachable."""
    LineageStore._schema_initialized = False
    yield
    LineageStore._schema_initialized = False


@pytest.fixture
def stub_schema_setup(monkeypatch) -> list[LineageStore]:
    """Replace migration execution with a call recorder."""
    calls: list[LineageStore] = []

    def _record(store: LineageStore) -> None:
        calls.append(store)

    monkeypatch.setattr(LineageStore, "setup_schema", _record)
    return calls


@pytest.fixture
def driver(monkeypatch, fresh_schema_cache, stub_schema_setup) -> _FakeDriver:
    """Install the fake psycopg2 module and batch helper into the store package."""
    fake_driver = _FakeDriver()

    def _execute_values(cur, sql, values, *args, **kwargs):
        cur.record_batch(sql, tuple(values))

    # Store methods do a local ``from psycopg2.extras import execute_values``,
    # so the real module attribute must be patched, not just the package ref.
    monkeypatch.setattr("psycopg2.extras.execute_values", _execute_values)

    monkeypatch.setattr(
        store_module,
        "psycopg2",
        SimpleNamespace(
            connect=fake_driver.connect,
            extras=SimpleNamespace(execute_values=_execute_values),
        ),
    )
    return fake_driver


@pytest.fixture
def ready_store(driver) -> LineageStore:
    """Store with a warm schema cache: operations emit only their own SQL."""
    LineageStore._schema_initialized = True
    return LineageStore(LINEAGE_DSN)


class TestRecordColumnLineage:
    """Batch insert outcomes through the fake driver."""

    def test_binds_every_mapping_field_in_submission_order(self, ready_store, driver):
        mappings = [
            ColumnLineage(
                source_asset="bronze.src_events",
                source_column="user_id",
                target_asset="silver.stg_events",
                target_column="user_id",
                metadata={"confidence": 0.9},
            ),
            ColumnLineage(
                source_asset="bronze.src_events",
                source_column="event_type",
                target_asset="silver.stg_events",
                target_column="event_type",
            ),
        ]

        count = ready_store.record_column_lineage(mappings)

        assert count == 2
        assert len(driver.batches) == 1
        assert driver.batches[0].params == (
            (
                "bronze.src_events",
                "user_id",
                "silver.stg_events",
                "user_id",
                "dbt_heuristic",
                '{"confidence": 0.9}',
            ),
            (
                "bronze.src_events",
                "event_type",
                "silver.stg_events",
                "event_type",
                "dbt_heuristic",
                None,
            ),
        )
        assert driver.connections[-1].commits == 1

    def test_resubmitting_known_mappings_duplicates_nothing_and_raises_nothing(
        self, ready_store, driver
    ):
        mappings = [
            ColumnLineage(
                source_asset="bronze.a",
                source_column="id",
                target_asset="silver.b",
                target_column="id",
            ),
            ColumnLineage(
                source_asset="bronze.a",
                source_column="ts",
                target_asset="silver.b",
                target_column="ts",
            ),
        ]
        driver.script_affected(2, 0)  # both land first time; replay conflicts away

        first = ready_store.record_column_lineage(mappings)
        second = ready_store.record_column_lineage(mappings)

        assert (first, second) == (2, 2)
        assert driver.batches[0].params == driver.batches[1].params  # same keys resubmitted
        assert driver.total_affected == 2  # the replay inserted nothing new

    def test_empty_mapping_list_is_a_database_no_op(self, ready_store, driver):
        assert ready_store.record_column_lineage([]) == 0
        assert driver.batches == []
        assert driver.connections == []

    def test_database_failure_propagates_to_the_caller(self, ready_store, driver):
        driver.connect_error = RuntimeError("catalog offline")
        mappings = [
            ColumnLineage(source_asset="a", source_column="b", target_asset="c", target_column="d")
        ]

        with pytest.raises(RuntimeError, match="catalog offline"):
            ready_store.record_column_lineage(mappings)


class TestColumnLineageQueries:
    def test_maps_every_returned_field_into_column_lineage_objects(self, ready_store, driver):
        driver.script_fetchall(
            [
                (
                    "bronze.raw_signups",
                    "user_id",
                    "silver.dim_users",
                    "user_id",
                    "dbt_heuristic",
                    {"confidence": 0.93},
                ),
                (
                    "bronze.raw_events",
                    "event_ts",
                    "silver.dim_users",
                    "last_seen_at",
                    "dbt_heuristic",
                    None,
                ),
            ]
        )

        results = ready_store.get_upstream_columns("silver.dim_users", target_column="user_id")

        assert results == [
            ColumnLineage(
                source_asset="bronze.raw_signups",
                source_column="user_id",
                target_asset="silver.dim_users",
                target_column="user_id",
                source_type="dbt_heuristic",
                metadata={"confidence": 0.93},
            ),
            ColumnLineage(
                source_asset="bronze.raw_events",
                source_column="event_ts",
                target_asset="silver.dim_users",
                target_column="last_seen_at",
                source_type="dbt_heuristic",
            ),
        ]
        assert driver.statements[-1].params == ("silver.dim_users", "user_id")

    def test_without_a_column_filter_only_the_asset_is_bound(self, ready_store, driver):
        driver.script_fetchall([])

        assert ready_store.get_upstream_columns("silver.dim_users") == []
        assert driver.statements[-1].params == ("silver.dim_users",)

    def test_downstream_query_binds_source_side_filters_and_maps_rows(self, ready_store, driver):
        driver.script_fetchall(
            [
                (
                    "bronze.raw_signups",
                    "user_id",
                    "silver.dim_users",
                    "user_id",
                    "dbt_heuristic",
                    None,
                ),
            ]
        )

        results = ready_store.get_downstream_columns("bronze.raw_signups", source_column="user_id")

        assert results == [
            ColumnLineage(
                source_asset="bronze.raw_signups",
                source_column="user_id",
                target_asset="silver.dim_users",
                target_column="user_id",
            )
        ]
        assert driver.statements[-1].params == ("bronze.raw_signups", "user_id")
