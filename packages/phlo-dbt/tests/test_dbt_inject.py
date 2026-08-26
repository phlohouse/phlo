"""Unit tests for dbt row ID injection.

Exercises `_phlo_row_id` injection against recording DBAPI-style fakes: tables
that already carry the column are skipped without any mutation statement,
row counts flow from scripted fetch results into the returned summary, schemas
are inferred from model name prefixes, and per-table errors are captured
without failing the batch. Statement assertions use only the leading verb
token (the statement type); the SQL text itself is never asserted.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import phlo_dbt.dbt_inject as dbt_inject
from phlo_dbt.dbt_inject import inject_row_ids_for_dbt_run, inject_row_ids_to_table

FQTN = "iceberg.silver.test_table"


class _Statement:
    """One executed statement: its type (leading verb) plus split tokens."""

    def __init__(self, text: str) -> None:
        self.tokens = text.split()
        self.verb = self.tokens[0].upper()


class _RecordingCursor:
    """DBAPI-style cursor that records statements and serves scripted rows.

    ``fetchall`` backs the DESCRIBE column listing; ``fetchone`` backs the
    COUNT(*) probe. ``fail_on`` maps a statement verb to an exception so tests
    can pin error containment at a specific stage.
    """

    def __init__(
        self,
        columns: tuple[str, ...] = (),
        count: int = 0,
        fail_on: dict[str, Exception] | None = None,
    ) -> None:
        self._columns = columns
        self._count = count
        self._fail_on = fail_on or {}
        self.statements: list[_Statement] = []

    def execute(self, statement: str) -> None:
        stmt = _Statement(statement)
        self.statements.append(stmt)
        exc = self._fail_on.get(stmt.verb)
        if exc is not None:
            raise exc

    def fetchall(self) -> list[tuple[str]]:
        return [(name,) for name in self._columns]

    def fetchone(self) -> tuple[int]:
        return (self._count,)

    def verbs(self) -> list[str]:
        return [stmt.verb for stmt in self.statements]

    def sole_statement(self, verb: str) -> _Statement:
        matches = [stmt for stmt in self.statements if stmt.verb == verb]
        assert len(matches) == 1, f"expected one {verb} statement, saw {self.verbs()}"
        return matches[0]


class _FakeTrinoConnection:
    def __init__(self, cursor: _RecordingCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _RecordingCursor:
        return self._cursor


class _RecordingLogger:
    """Structured logger double capturing (event, fields) pairs."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **fields) -> None:
        self.events.append((event, fields))

    def error(self, event: str, **fields) -> None:
        self.events.append((event, fields))


class _InjectionSpy:
    """Recording stand-in for ``inject_row_ids_to_table``.

    Stores every received (catalog, schema, table) call and derives outputs
    from them: per-table row counts from ``counts``, raised errors for tables
    listed in ``failures``.
    """

    def __init__(
        self,
        counts: dict[str, int] | None = None,
        failures: tuple[str, ...] = (),
    ) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.counts = counts or {}
        self.failures = failures

    def __call__(self, *, trino_connection, catalog, schema, table, context=None):
        self.calls.append((catalog, schema, table))
        if table in self.failures:
            raise RuntimeError(f"injection failed for {table}")
        return {"rows_updated": self.counts.get(table, 0)}


class TestInjectRowIdsToTable:
    """Tests for single table injection."""

    def _inject(self, cursor: _RecordingCursor, context=None, **overrides):
        kwargs = dict(
            trino_connection=_FakeTrinoConnection(cursor),
            catalog="iceberg",
            schema="silver",
            table="test_table",
            context=context,
        )
        kwargs.update(overrides)
        return inject_row_ids_to_table(**kwargs)

    def test_skips_if_column_exists(self):
        """Existing `_phlo_row_id` column short-circuits without mutations."""
        cursor = _RecordingCursor(columns=("id", "name", "_phlo_row_id"))

        result = self._inject(cursor)

        assert result == {"rows_updated": 0, "skipped": True}
        # Only the schema probe ran: no ALTER/UPDATE was ever issued.
        assert cursor.verbs() == ["DESCRIBE"]

    def test_adds_column_and_updates(self):
        """Missing column triggers DDL then backfill; COUNT flows into result."""
        cursor = _RecordingCursor(columns=("id", "name"), count=100)

        result = self._inject(cursor)

        assert result == {"rows_updated": 100}
        assert cursor.verbs() == ["DESCRIBE", "ALTER", "SELECT", "UPDATE"]

        describe = cursor.sole_statement("DESCRIBE")
        assert describe.tokens == ["DESCRIBE", FQTN]

        alter = cursor.sole_statement("ALTER")
        assert alter.tokens[1:3] == ["TABLE", FQTN]
        assert alter.tokens[3:6] == ["ADD", "COLUMN", "_phlo_row_id"]

        count_query = cursor.sole_statement("SELECT")
        assert count_query.tokens[-1] == FQTN

        update = cursor.sole_statement("UPDATE")
        assert update.tokens[1] == FQTN

    def test_logs_progress_with_structured_fields(self):
        """Context logger receives started/finished events with result fields."""
        cursor = _RecordingCursor(columns=("id",), count=50)
        rec_log = _RecordingLogger()

        self._inject(cursor, context=SimpleNamespace(log=rec_log))

        assert [event for event, _ in rec_log.events] == [
            "dbt_row_id_injection_started",
            "dbt_row_id_injection_finished",
        ]
        finished = rec_log.events[-1][1]
        assert finished["fqtn"] == FQTN
        assert finished["rows_updated"] == 50
        assert finished["skipped"] is False

    def test_database_errors_propagate_and_are_logged(self):
        """A failing statement surfaces untouched after being logged."""
        cursor = _RecordingCursor(
            columns=("id",),
            fail_on={"ALTER": RuntimeError("constraint violation")},
        )
        rec_log = _RecordingLogger()

        with pytest.raises(RuntimeError, match="constraint violation"):
            self._inject(cursor, context=SimpleNamespace(log=rec_log))

        # Failure stops the sequence before any backfill runs.
        assert cursor.verbs() == ["DESCRIBE", "ALTER"]
        failures = [f for f in rec_log.events if f[0] == "dbt_row_id_injection_failed"]
        assert len(failures) == 1
        assert failures[0][1]["error"] == "constraint violation"

    def test_rejects_unsafe_identifier_before_any_statement(self):
        """Identifier validation rejects hostile names before touching the wire."""
        cursor = _RecordingCursor(columns=("id",))

        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            self._inject(cursor, table="orders; drop table x")

        assert cursor.verbs() == []


class TestInjectRowIdsForDbtRun:
    """Tests for batch injection from dbt run results."""

    @staticmethod
    def _run(spy: _InjectionSpy, run_results: dict) -> dict:
        return inject_row_ids_for_dbt_run(
            trino_connection=object(),
            run_results=run_results,
        )

    def test_skips_non_success_and_unnamed_results(self, monkeypatch):
        """Non-success statuses and blank unique_ids never reach the connection."""
        spy = _InjectionSpy()
        monkeypatch.setattr(dbt_inject, "inject_row_ids_to_table", spy)
        run_results = {
            "results": [
                {"status": "error", "unique_id": "model.project.stg_failed"},
                {"status": "skipped", "unique_id": "model.project.stg_skipped"},
                {"status": "success", "unique_id": ""},
            ]
        }

        results = self._run(spy, run_results)

        assert results == {}
        assert spy.calls == []

    def test_processes_successful_models_with_derived_counts(self, monkeypatch):
        """Each successful model gets injected once; counts flow into results."""
        spy = _InjectionSpy(counts={"stg_events": 12, "fct_daily": 34})
        monkeypatch.setattr(dbt_inject, "inject_row_ids_to_table", spy)
        run_results = {
            "results": [
                {"status": "success", "unique_id": "model.github_stats.stg_events"},
                {"status": "success", "unique_id": "model.github_stats.fct_daily"},
            ]
        }

        results = self._run(spy, run_results)

        assert spy.calls == [("iceberg", "silver", "stg_events"), ("iceberg", "gold", "fct_daily")]
        assert results == {
            "stg_events": {"rows_updated": 12},
            "fct_daily": {"rows_updated": 34},
        }

    def test_infers_schema_from_model_name(self, monkeypatch):
        """Prefix routing covers stg/dim/fct/mrt and defaults unknown prefixes."""
        spy = _InjectionSpy()
        monkeypatch.setattr(dbt_inject, "inject_row_ids_to_table", spy)
        run_results = {
            "results": [
                {"status": "success", "unique_id": "model.p.stg_users"},
                {"status": "success", "unique_id": "model.p.dim_users"},
                {"status": "success", "unique_id": "model.p.fct_events"},
                {"status": "success", "unique_id": "model.p.mrt_summary"},
                {"status": "success", "unique_id": "model.p.plain_report"},
            ]
        }

        results = self._run(spy, run_results)

        assert spy.calls == [
            ("iceberg", "silver", "stg_users"),
            ("iceberg", "gold", "dim_users"),
            ("iceberg", "gold", "fct_events"),
            ("iceberg", "marts", "mrt_summary"),
            ("iceberg", "silver", "plain_report"),
        ]
        assert sorted(results) == sorted(
            ["stg_users", "dim_users", "fct_events", "mrt_summary", "plain_report"]
        )

    def test_captures_errors_without_failing(self, monkeypatch):
        """Per-table failure records an error entry; siblings still complete."""
        spy = _InjectionSpy(counts={"stg_good": 7}, failures=("stg_bad",))
        monkeypatch.setattr(dbt_inject, "inject_row_ids_to_table", spy)
        run_results = {
            "results": [
                {"status": "success", "unique_id": "model.p.stg_good"},
                {"status": "success", "unique_id": "model.p.stg_bad"},
            ]
        }

        results = self._run(spy, run_results)

        assert results["stg_good"] == {"rows_updated": 7}
        assert results["stg_bad"] == {"error": "injection failed for stg_bad"}
