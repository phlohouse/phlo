"""Unit tests for dbt row ID injection.

Exercises _phlo_row_id injection against mocked connections: tables that
already carry the column are skipped without ALTER TABLE, only successful
dbt run results are processed, schemas are inferred from model name
prefixes, and per-table errors are captured without failing the batch.
"""

from unittest.mock import MagicMock, patch

from phlo_dbt.dbt_inject import inject_row_ids_for_dbt_run, inject_row_ids_to_table


class TestInjectRowIdsToTable:
    """Tests for single table injection."""

    def test_skips_if_column_exists(self):
        """Should skip injection if _phlo_row_id column already exists."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # DESCRIBE returns columns including _phlo_row_id
        mock_cursor.fetchall.return_value = [("id",), ("name",), ("_phlo_row_id",)]

        result = inject_row_ids_to_table(
            trino_connection=mock_conn,
            catalog="iceberg",
            schema="silver",
            table="test_table",
        )

        assert result["rows_updated"] == 0
        # Should not have called ALTER TABLE
        alter_calls = [
            call for call in mock_cursor.execute.call_args_list if "ALTER TABLE" in str(call)
        ]
        assert len(alter_calls) == 0

    def test_adds_column_and_updates(self):
        """Should add column and populate with UUIDs."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # First call: DESCRIBE returns columns without _phlo_row_id
        # Second call: COUNT returns 100 rows
        mock_cursor.fetchall.return_value = [("id",), ("name",)]
        mock_cursor.fetchone.return_value = (100,)

        result = inject_row_ids_to_table(
            trino_connection=mock_conn,
            catalog="iceberg",
            schema="silver",
            table="test_table",
        )

        # Verify ALTER TABLE was called
        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("ALTER TABLE" in call for call in calls)
        assert any("_phlo_row_id" in call for call in calls)
        assert any("UPDATE" in call for call in calls)
        assert result["rows_updated"] == 100

    def test_logs_progress(self):
        """Should log progress when context is provided."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("id",)]
        mock_cursor.fetchone.return_value = (50,)

        mock_context = MagicMock()

        inject_row_ids_to_table(
            trino_connection=mock_conn,
            catalog="iceberg",
            schema="gold",
            table="fct_events",
            context=mock_context,
        )

        mock_context.log.info.assert_called()


class TestInjectRowIdsForDbtRun:
    """Tests for batch injection from dbt run results."""

    def test_skips_non_success_results(self):
        """Should skip models that didn't succeed."""
        mock_conn = MagicMock()
        run_results = {
            "results": [
                {"status": "error", "unique_id": "model.project.stg_failed"},
                {"status": "skipped", "unique_id": "model.project.stg_skipped"},
            ]
        }

        with patch("phlo_dbt.dbt_inject.inject_row_ids_to_table") as mock_inject:
            results = inject_row_ids_for_dbt_run(
                trino_connection=mock_conn,
                run_results=run_results,
            )

        mock_inject.assert_not_called()
        assert results == {}

    def test_processes_successful_models(self):
        """Should inject row IDs to successful models."""
        mock_conn = MagicMock()
        run_results = {
            "results": [
                {"status": "success", "unique_id": "model.github_stats.stg_events"},
                {"status": "success", "unique_id": "model.github_stats.fct_daily"},
            ]
        }

        with patch("phlo_dbt.dbt_inject.inject_row_ids_to_table") as mock_inject:
            mock_inject.return_value = {"rows_updated": 100}

            results = inject_row_ids_for_dbt_run(
                trino_connection=mock_conn,
                run_results=run_results,
            )

        assert mock_inject.call_count == 2
        assert "stg_events" in results
        assert "fct_daily" in results

    def test_infers_schema_from_model_name(self):
        """Should infer correct schema from model naming convention."""
        mock_conn = MagicMock()
        run_results = {
            "results": [
                {"status": "success", "unique_id": "model.p.stg_users"},
                {"status": "success", "unique_id": "model.p.fct_events"},
                {"status": "success", "unique_id": "model.p.mrt_summary"},
            ]
        }

        with patch("phlo_dbt.dbt_inject.inject_row_ids_to_table") as mock_inject:
            mock_inject.return_value = {"rows_updated": 10}

            inject_row_ids_for_dbt_run(
                trino_connection=mock_conn,
                run_results=run_results,
            )

        # Check the schema arguments
        calls = mock_inject.call_args_list
        schemas = [call.kwargs.get("schema") for call in calls]

        assert "silver" in schemas  # stg_users
        assert "gold" in schemas  # fct_events
        assert "marts" in schemas  # mrt_summary

    def test_captures_errors_without_failing(self):
        """Should capture errors for individual tables but continue."""
        mock_conn = MagicMock()
        run_results = {
            "results": [
                {"status": "success", "unique_id": "model.p.stg_good"},
                {"status": "success", "unique_id": "model.p.stg_bad"},
            ]
        }

        def side_effect(**kwargs):
            """Raise for one table to verify per-table error capture."""
            if kwargs["table"] == "stg_bad":
                raise Exception("Connection failed")
            return {"rows_updated": 10}

        with patch("phlo_dbt.dbt_inject.inject_row_ids_to_table") as mock_inject:
            mock_inject.side_effect = side_effect

            results = inject_row_ids_for_dbt_run(
                trino_connection=mock_conn,
                run_results=run_results,
            )

        assert results["stg_good"]["rows_updated"] == 10
        assert "error" in results["stg_bad"]
