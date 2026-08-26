"""Unit tests for the row-level lineage store.

Drives ``LineageStore`` through a scripted fake psycopg2 driver: every executed
statement is recorded together with its exact bound parameters, fetch results
and affected-row counts are queued per call, and schema migration is replaced
by a call recorder. Tests assert outcomes - what was bound, what reads back,
what the driver reported - instead of mirroring SQL text.
"""

from __future__ import annotations

import json
import socket
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from phlo_lineage import store as store_module
from phlo_lineage.store import (
    LineageStore,
    generate_row_id,
    resolve_lineage_db_url,
    resolve_lineage_db_url_with_postgres_fallback,
)

LINEAGE_DSN = "postgresql://lineage-fake/test"


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
        self._fail_next_connect = False

    def connect(self, dsn: str) -> _FakeConnection:
        if self.connect_error is not None:
            raise self.connect_error
        if self._fail_next_connect:
            self._fail_next_connect = False
            raise RuntimeError("lineage database unreachable")
        connection = _FakeConnection(self)
        self.connections.append(connection)
        return connection

    def fail_next_connect(self) -> None:
        self._fail_next_connect = True

    def script_affected(self, *rowcounts: int) -> None:
        """Queue cursor.rowcount values, consumed one per execute/batch."""
        self._affected.extend(rowcounts)

    def script_fetchone(self, *results) -> None:
        self._fetchone_results.extend(results)

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


class TestGenerateRowId:
    """Tests for ULID generation."""

    def test_generates_string(self):
        """Row ID should be a string."""
        row_id = generate_row_id()
        assert isinstance(row_id, str)

    def test_generates_26_chars(self):
        """ULID should be 26 characters (Crockford's Base32)."""
        row_id = generate_row_id()
        assert len(row_id) == 26

    def test_generates_unique_ids(self):
        """Each call should generate a unique ID."""
        ids = [generate_row_id() for _ in range(1000)]
        assert len(set(ids)) == 1000

    def test_ids_are_sortable(self):
        """IDs generated in sequence should be lexicographically sortable."""
        id1 = generate_row_id()
        id2 = generate_row_id()

        # Later ID should be lexicographically greater
        assert id2 > id1


class TestSchemaBootstrap:
    """Schema-existence branches exercised through public operations only."""

    def test_existing_tables_skip_migration_but_still_serve_the_operation(
        self, driver, stub_schema_setup
    ):
        driver.script_fetchone(
            ("phlo.asset_lineage_nodes", "phlo.asset_lineage_edges", "phlo.column_lineage")
        )
        store = LineageStore(LINEAGE_DSN)

        store.record_row("01KC7SKJE0WARM", "bronze.orders", "dlt")

        assert stub_schema_setup == []  # tables exist, so no migration attempted
        assert len(driver.statements) == 2  # existence probe, then the insert
        assert driver.statements[0].params is None  # probe binds no parameters
        assert driver.statements[1].params == (
            "01KC7SKJE0WARM",
            "bronze.orders",
            "dlt",
            None,
            None,
        )

    def test_missing_tables_run_migration_once_then_the_operation_proceeds(
        self, driver, stub_schema_setup
    ):
        driver.script_fetchone((None, None, None))
        store = LineageStore(LINEAGE_DSN)

        store.record_row("01KC7SKJE0COLD", "bronze.orders", "dlt")

        assert stub_schema_setup == [store]
        assert driver.statements[1].params == (
            "01KC7SKJE0COLD",
            "bronze.orders",
            "dlt",
            None,
            None,
        )

    def test_failed_existence_probe_falls_back_to_migration(self, driver, stub_schema_setup):
        driver.fail_next_connect()
        store = LineageStore(LINEAGE_DSN)

        store.record_row("01KC7SKJE0PROBEFAIL", "bronze.orders", "dlt")

        # Unreachable probe is treated as missing schema, and the row still lands.
        assert stub_schema_setup == [store]
        assert driver.statements[0].params[0] == "01KC7SKJE0PROBEFAIL"

    def test_warm_cache_skips_probe_for_subsequent_stores(self, driver, stub_schema_setup):
        driver.script_fetchone((None, None, None))
        first = LineageStore(LINEAGE_DSN)
        first.record_row("01KC7SKJE0ONE", "bronze.orders", "dlt")

        second = LineageStore("postgresql://other-dsn")
        second.record_row("01KC7SKJE0TWO", "silver.orders", "dbt")

        assert stub_schema_setup == [first]
        # Second store opens exactly one connection (its insert), no probe.
        assert len(driver.connections) == 3
        assert driver.statements[2].params == (
            "01KC7SKJE0TWO",
            "silver.orders",
            "dbt",
            None,
            None,
        )


class TestRecordRow:
    def test_binds_every_value_exactly_as_given(self, ready_store, driver):
        ready_store.record_row(
            row_id="01KC7SKJE0BOUND",
            table_name="bronze.orders",
            source_type="dlt",
            parent_row_ids=["01KC7SKJE0PA", "01KC7SKJE0PB"],
            metadata={"run_id": "run-42", "partition": "2026-05-01"},
        )

        assert len(driver.statements) == 1
        assert driver.statements[0].params == (
            "01KC7SKJE0BOUND",
            "bronze.orders",
            "dlt",
            ["01KC7SKJE0PA", "01KC7SKJE0PB"],
            '{"run_id": "run-42", "partition": "2026-05-01"}',
        )

    def test_defaults_bind_null_parents_and_null_metadata(self, ready_store, driver):
        ready_store.record_row("01KC7SKJE0BARE", "gold.fact", source_type="external")

        assert driver.statements[0].params == (
            "01KC7SKJE0BARE",
            "gold.fact",
            "external",
            None,
            None,
        )

    def test_written_row_reads_back_intact(self, ready_store, driver):
        ready_store.record_row(
            row_id="01KC7SKJE0RT",
            table_name="silver.stg_orders",
            source_type="dbt",
            parent_row_ids=["01KC7SKJE0SRC"],
            metadata={"run_id": "rt-1"},
        )
        assert driver.connections[0].commits == 1

        bound = driver.statements[0].params
        written_at = datetime(2026, 5, 1, 12, 30, 45)
        # Serve the database row back in physical column order using exactly
        # the values the INSERT bound.
        driver.script_fetchone(
            (bound[0], bound[1], bound[2], bound[3], written_at, json.loads(bound[4]))
        )

        row = ready_store.get_row("01KC7SKJE0RT")

        assert row == {
            "row_id": "01KC7SKJE0RT",
            "table_name": "silver.stg_orders",
            "source_type": "dbt",
            "parent_row_ids": ["01KC7SKJE0SRC"],
            "created_at": "2026-05-01T12:30:45",
            "metadata": {"run_id": "rt-1"},
        }
        assert driver.statements[-1].params == ("01KC7SKJE0RT",)

    def test_same_row_id_twice_rebinds_updated_values(self, ready_store, driver):
        driver.script_affected(1, 1)  # upsert touches the single row each time

        ready_store.record_row("01KC7SKJE0UP", "bronze.orders", "dlt", metadata={"attempt": 1})
        ready_store.record_row(
            "01KC7SKJE0UP",
            "bronze.orders",
            "dbt",
            parent_row_ids=["01KC7SKJE0PA"],
            metadata={"attempt": 2},
        )

        assert driver.total_affected == 2
        assert driver.statements[1].params == (
            "01KC7SKJE0UP",
            "bronze.orders",
            "dbt",
            ["01KC7SKJE0PA"],
            '{"attempt": 2}',
        )

    def test_database_failure_propagates_after_logging(self, ready_store, driver):
        driver.connect_error = RuntimeError("lineage database offline")

        with pytest.raises(RuntimeError, match="lineage database offline"):
            ready_store.record_row("01KC7SKJE0ERR", "bronze.orders", "dlt")


class TestRecordRowsBatch:
    def test_empty_input_touches_nothing(self, ready_store, driver):
        assert ready_store.record_rows_batch([], "bronze.orders") == 0
        assert driver.statements == []
        assert driver.batches == []
        assert driver.connections == []

    def test_rows_without_phlo_row_id_are_skipped(self, ready_store, driver):
        rows = [{"name": "orphan"}, {"_phlo_row_id": "01KC7SKJE0KEEP", "name": "kept"}]

        assert ready_store.record_rows_batch(rows, "bronze.orders", "dlt") == 1
        assert len(driver.batches) == 1
        assert driver.batches[0].params == (("01KC7SKJE0KEEP", "bronze.orders", "dlt", None, None),)

    def test_all_rows_missing_ids_sends_nothing_to_the_database(self, ready_store, driver):
        rows = [{"name": "a"}, {"name": "b"}]

        assert ready_store.record_rows_batch(rows, "bronze.orders") == 0
        assert driver.batches == []
        assert driver.connections == []

    def test_batch_metadata_is_bound_once_per_row(self, ready_store, driver):
        rows = [{"_phlo_row_id": "01KC7SKJE0M1"}, {"_phlo_row_id": "01KC7SKJE0M2"}]

        ready_store.record_rows_batch(rows, "bronze.orders", "dlt", metadata={"run_id": "b-7"})

        assert driver.batches[0].params == (
            ("01KC7SKJE0M1", "bronze.orders", "dlt", None, '{"run_id": "b-7"}'),
            ("01KC7SKJE0M2", "bronze.orders", "dlt", None, '{"run_id": "b-7"}'),
        )

    def test_duplicate_row_ids_neither_duplicate_nor_raise(self, ready_store, driver):
        rows = [
            {"_phlo_row_id": "01KC7SKJE0D1"},
            {"_phlo_row_id": "01KC7SKJE0D2"},
            {"_phlo_row_id": "01KC7SKJE0D3"},
        ]
        driver.script_affected(3, 0)  # first insert lands three rows; replay conflicts away

        first = ready_store.record_rows_batch(rows, "bronze.orders")
        second = ready_store.record_rows_batch(rows, "bronze.orders")

        assert (first, second) == (3, 3)
        assert driver.batches[0].params == driver.batches[1].params  # same key resubmitted
        assert driver.total_affected == 3  # the replay inserted nothing new

    def test_batch_failure_propagates_to_the_caller(self, ready_store, driver):
        driver.connect_error = RuntimeError("warehouse down")

        with pytest.raises(RuntimeError, match="warehouse down"):
            ready_store.record_rows_batch([{"_phlo_row_id": "01KC7SKJE0X"}], "bronze.orders")


class TestAssetEdges:
    def test_persists_nodes_before_edges_with_exact_values(self, ready_store, driver):
        count = ready_store.record_asset_edges(
            [("raw.posts", "raw_marts.posts_mart")],
            metadata={"source": "dbt"},
            tags={"env": "test"},
        )

        assert count == 1
        nodes, edges = driver.batches
        assert nodes.params == (
            ("raw.posts", None, None, None, '{"source": "dbt"}', '{"env": "test"}'),
            ("raw_marts.posts_mart", None, None, None, '{"source": "dbt"}', '{"env": "test"}'),
        )
        assert edges.params == (
            ("raw.posts", "raw_marts.posts_mart", '{"source": "dbt"}', '{"env": "test"}'),
        )

    def test_deduplicates_shared_endpoints_and_preserves_edge_direction(self, ready_store, driver):
        count = ready_store.record_asset_edges(
            [
                ("bronze.events", "silver.stg_events"),
                ("silver.stg_events", "gold.fct_sessions"),
            ]
        )

        assert count == 2
        nodes, edges = driver.batches
        assert [row[0] for row in nodes.params] == [
            "bronze.events",
            "gold.fct_sessions",
            "silver.stg_events",
        ]
        assert edges.params == (
            ("bronze.events", "silver.stg_events", None, None),
            ("silver.stg_events", "gold.fct_sessions", None, None),
        )

    def test_no_edges_and_no_keys_persists_nothing(self, ready_store, driver):
        assert ready_store.record_asset_edges([]) == 0
        assert driver.batches == []
        assert driver.connections == []

    def test_explicit_keys_alone_create_nodes_without_edges(self, ready_store, driver):
        count = ready_store.record_asset_edges([], asset_keys=["sandbox.demo"])

        assert count == 0
        assert len(driver.batches) == 1
        assert driver.batches[0].params == (("sandbox.demo", None, None, None, None, None),)


class TestRowQueries:
    def test_get_row_maps_physical_columns_in_order(self, ready_store, driver):
        driver.script_fetchone(
            (
                "01KC7SKJE0F1",
                "gold.fct_orders",
                "dbt",
                ["01KC7SKJE0U1", "01KC7SKJE0U2"],
                datetime(2026, 5, 1, 8, 15),
                {"job": "nightly"},
            )
        )

        assert ready_store.get_row("01KC7SKJE0F1") == {
            "row_id": "01KC7SKJE0F1",
            "table_name": "gold.fct_orders",
            "source_type": "dbt",
            "parent_row_ids": ["01KC7SKJE0U1", "01KC7SKJE0U2"],
            "created_at": "2026-05-01T08:15:00",
            "metadata": {"job": "nightly"},
        }
        assert driver.statements[-1].params == ("01KC7SKJE0F1",)

    def test_get_row_coerces_missing_parents_to_an_empty_list(self, ready_store, driver):
        driver.script_fetchone(("01KC7SKJE0ROOT", "bronze.raw", "dlt", None, None, None))

        row = ready_store.get_row("01KC7SKJE0ROOT")

        assert row["parent_row_ids"] == []
        assert row["created_at"] is None
        assert row["metadata"] is None

    def test_get_row_returns_none_for_unknown_ids(self, ready_store, driver):
        assert ready_store.get_row("01KC7SKJE0GHOST") is None

    def test_get_ancestors_binds_seed_and_depth_and_maps_rows(self, ready_store, driver):
        driver.script_fetchall(
            [
                (
                    "01KC7SKJE0GP",
                    "bronze.raw_events",
                    "dlt",
                    None,
                    datetime(2026, 4, 30, 22, 0),
                    None,
                ),
                (
                    "01KC7SKJE0P",
                    "silver.stg_events",
                    "dbt",
                    ["01KC7SKJE0GP"],
                    datetime(2026, 5, 1, 1, 0),
                    {"layer": "silver"},
                ),
            ]
        )

        ancestors = ready_store.get_ancestors("01KC7SKJE0ME", max_depth=4)

        assert driver.statements[-1].params == ("01KC7SKJE0ME", 4)
        assert ancestors == [
            {
                "row_id": "01KC7SKJE0GP",
                "table_name": "bronze.raw_events",
                "source_type": "dlt",
                "parent_row_ids": [],
                "created_at": "2026-04-30T22:00:00",
                "metadata": None,
            },
            {
                "row_id": "01KC7SKJE0P",
                "table_name": "silver.stg_events",
                "source_type": "dbt",
                "parent_row_ids": ["01KC7SKJE0GP"],
                "created_at": "2026-05-01T01:00:00",
                "metadata": {"layer": "silver"},
            },
        ]

    def test_get_descendants_defaults_depth_to_ten_and_maps_rows(self, ready_store, driver):
        driver.script_fetchall(
            [
                (
                    "01KC7SKJE0C1",
                    "gold.dashboard",
                    "publish",
                    ["01KC7SKJE0ME"],
                    datetime(2026, 5, 2, 6, 0),
                    {"viewer": "ops"},
                ),
                ("01KC7SKJE0C2", "gold.alerts", "publish", ["01KC7SKJE0ME"], None, None),
            ]
        )

        descendants = ready_store.get_descendants("01KC7SKJE0ME")

        assert driver.statements[-1].params == ("01KC7SKJE0ME", 10)
        assert [entry["row_id"] for entry in descendants] == [
            "01KC7SKJE0C1",
            "01KC7SKJE0C2",
        ]
        assert descendants[0]["created_at"] == "2026-05-02T06:00:00"
        assert descendants[1]["parent_row_ids"] == ["01KC7SKJE0ME"]

    def test_get_table_rows_binds_table_and_limit(self, ready_store, driver):
        driver.script_fetchall(
            [
                (
                    "01KC7SKJE0R9",
                    "silver.stg_orders",
                    "dbt",
                    None,
                    datetime(2026, 5, 3, 10, 0),
                    {"wave": "9"},
                ),
            ]
        )

        rows = ready_store.get_table_rows("silver.stg_orders", limit=25)

        assert driver.statements[-1].params == ("silver.stg_orders", 25)
        assert rows == [
            {
                "row_id": "01KC7SKJE0R9",
                "table_name": "silver.stg_orders",
                "source_type": "dbt",
                "parent_row_ids": [],
                "created_at": "2026-05-03T10:00:00",
                "metadata": {"wave": "9"},
            }
        ]


class TestResolveLineageDbUrl:
    """Tests for lineage DB URL resolution."""

    def test_prefers_explicit_env_url(self, monkeypatch) -> None:
        monkeypatch.setenv("PHLO_LINEAGE_DB_URL", "postgresql://explicit")

        assert resolve_lineage_db_url() == "postgresql://explicit"

    def test_returns_none_without_explicit_lineage_env(self, monkeypatch) -> None:
        monkeypatch.delenv("LINEAGE_DB_URL", raising=False)
        monkeypatch.delenv("PHLO_LINEAGE_DB_URL", raising=False)
        monkeypatch.delenv("DAGSTER_PG_DB_CONNECTION_STRING", raising=False)
        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        monkeypatch.delenv("POSTGRES_PORT", raising=False)
        monkeypatch.delenv("POSTGRES_USER", raising=False)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)

        assert resolve_lineage_db_url() is None

    @patch("phlo.config.network.socket.gethostbyname", side_effect=socket.gaierror())
    def test_falls_back_to_localhost_for_unresolvable_postgres_host(
        self, _mock_resolve, monkeypatch
    ) -> None:
        monkeypatch.delenv("LINEAGE_DB_URL", raising=False)
        monkeypatch.delenv("PHLO_LINEAGE_DB_URL", raising=False)
        monkeypatch.delenv("DAGSTER_PG_DB_CONNECTION_STRING", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "postgres")
        monkeypatch.setenv("POSTGRES_PORT", "15432")
        monkeypatch.setenv("POSTGRES_USER", "phlo")
        monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
        monkeypatch.setenv("POSTGRES_DB", "warehouse")

        assert (
            resolve_lineage_db_url_with_postgres_fallback()
            == "postgresql://phlo:secret@localhost:15432/warehouse"
        )
