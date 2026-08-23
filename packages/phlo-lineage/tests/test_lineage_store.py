"""Unit tests for the row-level lineage store.

Mocks the backing connection to exercise row-id generation, batch recording,
URL resolution, and asset-level lineage lookups in isolation.
"""

import socket
from unittest.mock import MagicMock, patch

import pytest

from phlo_lineage.store import (
    LineageStore,
    generate_row_id,
    resolve_lineage_db_url,
    resolve_lineage_db_url_with_postgres_fallback,
)


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
        import time

        id1 = generate_row_id()
        time.sleep(0.01)  # Small delay to ensure different timestamp
        id2 = generate_row_id()

        # Later ID should be lexicographically greater
        assert id2 > id1


class TestLineageStore:
    """Tests for LineageStore class."""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock psycopg2 connection."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return mock_conn, mock_cursor

    def test_init_stores_connection_string(self):
        """Store should store connection string."""
        store = LineageStore("postgresql://test")
        assert store.connection_string == "postgresql://test"

    @patch("phlo_lineage.store.psycopg2")
    def test_schema_exists_returns_true_when_tables_are_present(
        self, mock_psycopg2, mock_connection
    ):
        """Schema existence probe should detect initialized lineage tables."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (
            "phlo.asset_lineage_nodes",
            "phlo.asset_lineage_edges",
            "phlo.column_lineage",
        )

        store = LineageStore("postgresql://test")

        assert store._schema_exists() is True

    @patch("phlo_lineage.store.psycopg2")
    def test_ensure_schema_skips_setup_when_schema_already_exists(
        self, mock_psycopg2, mock_connection
    ):
        """Schema setup should be skipped when another process already created it."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = (
            "phlo.asset_lineage_nodes",
            "phlo.asset_lineage_edges",
            "phlo.column_lineage",
        )

        store = LineageStore("postgresql://test")
        with patch.object(LineageStore, "setup_schema") as mock_setup:
            store._ensure_schema()

        mock_setup.assert_not_called()

    @patch("phlo_lineage.store.psycopg2")
    def test_record_row_executes_insert(self, mock_psycopg2, mock_connection):
        """record_row should execute INSERT statement."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn

        store = LineageStore("postgresql://test")
        store.record_row(
            row_id="01KC7SKJE0TEST",
            table_name="bronze.test_table",
            source_type="dlt",
        )

        calls = [args[0][0] for args in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO phlo.row_lineage" in sql for sql in calls)

    @patch("phlo_lineage.store.psycopg2")
    def test_record_row_with_parents(self, mock_psycopg2, mock_connection):
        """record_row should handle parent_row_ids."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn

        store = LineageStore("postgresql://test")
        store.record_row(
            row_id="01KC7SKJE0CHILD",
            table_name="silver.test_table",
            source_type="dbt",
            parent_row_ids=["01KC7SKJE0PARENT1", "01KC7SKJE0PARENT2"],
        )

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO phlo.row_lineage" in call for call in calls)
        assert any("01KC7SKJE0PARENT1" in call for call in calls)

    @patch("phlo_lineage.store.psycopg2")
    def test_record_row_with_metadata(self, mock_psycopg2, mock_connection):
        """record_row should serialize metadata as JSON."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn

        store = LineageStore("postgresql://test")
        store.record_row(
            row_id="01KC7SKJE0TEST",
            table_name="bronze.test_table",
            source_type="dlt",
            metadata={"run_id": "abc123", "partition": "2024-01-01"},
        )

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO phlo.row_lineage" in call for call in calls)

    @patch("phlo_lineage.store.psycopg2")
    def test_get_row_returns_none_when_not_found(self, mock_psycopg2, mock_connection):
        """get_row should return None when row doesn't exist."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        store = LineageStore("postgresql://test")
        result = store.get_row("nonexistent")

        assert result is None


class TestResolveLineageDbUrl:
    """Tests for lineage DB URL resolution."""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock psycopg2 connection."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return mock_conn, mock_cursor

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

    @patch("phlo_lineage.store.psycopg2")
    def test_get_row_returns_dict_when_found(self, mock_psycopg2, mock_connection):
        """get_row should return dict with row data."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn

        from datetime import datetime

        mock_cursor.fetchone.return_value = (
            "01KC7SKJE0TEST",
            "bronze.test",
            "dlt",
            None,
            datetime(2024, 1, 1, 12, 0, 0),
            {"run_id": "abc"},
        )

        store = LineageStore("postgresql://test")
        result = store.get_row("01KC7SKJE0TEST")

        assert result is not None
        assert result["row_id"] == "01KC7SKJE0TEST"
        assert result["table_name"] == "bronze.test"
        assert result["source_type"] == "dlt"

    @patch("phlo_lineage.store.psycopg2")
    def test_get_ancestors_uses_recursive_cte(self, mock_psycopg2, mock_connection):
        """get_ancestors should use recursive CTE query."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        store = LineageStore("postgresql://test")
        store.get_ancestors("01KC7SKJE0TEST")

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert "WITH RECURSIVE" in call_args[0][0]

    @patch("phlo_lineage.store.psycopg2")
    def test_get_descendants_uses_recursive_cte(self, mock_psycopg2, mock_connection):
        """get_descendants should use recursive CTE query."""
        mock_conn, mock_cursor = mock_connection
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        store = LineageStore("postgresql://test")
        store.get_descendants("01KC7SKJE0TEST")

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert "WITH RECURSIVE" in call_args[0][0]


class TestRecordRowsBatch:
    """Tests for batch recording."""

    @patch("phlo_lineage.store.psycopg2")
    def test_batch_returns_zero_for_empty_list(self, mock_psycopg2):
        """Batch recording should return 0 for empty input."""
        store = LineageStore("postgresql://test")
        result = store.record_rows_batch([], "bronze.test", "dlt")
        assert result == 0

    def test_batch_skips_rows_without_row_id(self):
        """Batch recording should skip rows missing _phlo_row_id."""
        with patch("phlo_lineage.store.psycopg2") as mock_psycopg2:
            with patch("psycopg2.extras.execute_values"):
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
                mock_psycopg2.connect.return_value = mock_conn

                rows = [
                    {"name": "test1"},  # Missing _phlo_row_id
                    {"_phlo_row_id": "01KC7SKJE0TEST", "name": "test2"},
                ]

                store = LineageStore("postgresql://test")
                result = store.record_rows_batch(rows, "bronze.test", "dlt")

                assert result == 1  # Only one row had _phlo_row_id


class TestAssetLineageStore:
    """Tests for asset-level lineage storage."""

    def test_record_asset_edges_executes_insert(self):
        """record_asset_edges should insert into asset lineage tables."""
        with patch("phlo_lineage.store.psycopg2") as mock_psycopg2:
            with patch("psycopg2.extras.execute_values") as mock_execute_values:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
                mock_psycopg2.connect.return_value = mock_conn

                store = LineageStore("postgresql://test")
                store.record_asset_edges(
                    [("raw.posts", "raw_marts.posts_mart")],
                    metadata={"source": "dbt"},
                    tags={"env": "test"},
                )

                sql_calls = [call.args[1] for call in mock_execute_values.call_args_list]
                assert any("INSERT INTO phlo.asset_lineage_nodes" in sql for sql in sql_calls)
                assert any("INSERT INTO phlo.asset_lineage_edges" in sql for sql in sql_calls)
