"""Tests for column-level lineage.

Covers the frozen ColumnLineage dataclass, dbt manifest column extraction
(same-name columns map lineage; no columns or no overlap yield nothing),
and store behaviour against a mocked psycopg2 connection: batch inserts and
upstream-column queries.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phlo_lineage.dbt_column_lineage import extract_column_lineage
from phlo_lineage.store import ColumnLineage, LineageStore


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
# Store methods (mocked psycopg2)
# -------------------------------------------------------------------


@pytest.fixture()
def mock_pg():
    """Provide a mocked psycopg2 connection + cursor."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestRecordColumnLineageMock:
    """Verify batch insert SQL via mocked psycopg2."""

    @patch("psycopg2.extras.execute_values")
    @patch("phlo_lineage.store.psycopg2")
    def test_batch_insert(self, mock_psycopg2, mock_execute_values, mock_pg):
        mock_conn, mock_cursor = mock_pg
        mock_psycopg2.connect.return_value = mock_conn

        store = LineageStore("postgresql://test")
        mappings = [
            ColumnLineage(
                source_asset="bronze.src",
                source_column="id",
                target_asset="silver.stg",
                target_column="id",
            ),
        ]
        count = store.record_column_lineage(mappings)

        assert count == 1
        sql = mock_execute_values.call_args[0][1]
        assert "INSERT INTO phlo.column_lineage" in sql
        assert "ON CONFLICT DO NOTHING" in sql

    @patch("phlo_lineage.store.psycopg2")
    def test_empty_returns_zero(self, mock_psycopg2):
        store = LineageStore("postgresql://test")
        assert store.record_column_lineage([]) == 0


class TestGetUpstreamColumnsMock:
    """Verify upstream query via mocked psycopg2."""

    @patch("phlo_lineage.store.psycopg2")
    def test_query_with_column(self, mock_psycopg2, mock_pg):
        mock_conn, mock_cursor = mock_pg
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchall.return_value = [
            ("bronze.src", "id", "silver.stg", "id", "dbt_heuristic", None),
        ]

        store = LineageStore("postgresql://test")
        results = store.get_upstream_columns("silver.stg", target_column="id")

        assert len(results) == 1
        assert results[0].source_asset == "bronze.src"
        assert results[0].source_column == "id"

        sql = mock_cursor.execute.call_args[0][0]
        assert "target_asset" in sql
        assert "target_column" in sql

    @patch("phlo_lineage.store.psycopg2")
    def test_query_without_column(self, mock_psycopg2, mock_pg):
        mock_conn, mock_cursor = mock_pg
        mock_psycopg2.connect.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        store = LineageStore("postgresql://test")
        results = store.get_upstream_columns("silver.stg")

        assert results == []
        sql = mock_cursor.execute.call_args[0][0]
        assert "WHERE target_asset = %s" in sql
        assert "target_column = %s" not in sql
