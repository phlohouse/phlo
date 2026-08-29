"""Tests for the schema registry module.

Covers deterministic canonical schema serialization and hashing (field
order independent), schema round-trips through deserialization, and
persistence behavior including treating "already exists" as initialized.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from phlo.capabilities.specs import FieldSpec, NormalizedSchema
from phlo.schema_registry import (
    SchemaRegistry,
    _canonical_schema_json,
    _schema_hash,
    deserialize_schema,
)


def _schema(*fields: tuple[str, str, bool]) -> NormalizedSchema:
    """Helper to build a NormalizedSchema from (name, dtype, nullable) tuples."""
    return NormalizedSchema(
        fields=[FieldSpec(name=n, dtype=d, nullable=nl) for n, d, nl in fields],
    )


class _RegistryDatabase:
    """Record public registry setup and snapshot statements at a DB-API boundary."""

    def __init__(self, *, fail_setup_times: int = 0, setup_started: threading.Event | None = None):
        self.fail_setup_times = fail_setup_times
        self.setup_started = setup_started
        self.setup_calls: list[str] = []
        self.snapshot_calls: list[str] = []
        self._lock = threading.Lock()

    def connect(self, connection_string: str):
        """Return a connection that records statements for one public registry call."""
        return _RegistryConnection(self, connection_string)


class _RegistryConnection:
    """Provide the DB-API context-manager shape used by SchemaRegistry."""

    def __init__(self, database: _RegistryDatabase, connection_string: str):
        self.database = database
        self.connection_string = connection_string

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self):
        return _RegistryCursor(self.database, self.connection_string)

    def commit(self) -> None:
        return None


class _RegistryCursor:
    """Record setup and snapshot statements while returning deterministic snapshot IDs."""

    def __init__(self, database: _RegistryDatabase, connection_string: str):
        self.database = database
        self.connection_string = connection_string
        self.snapshot_id: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, _params: object = None) -> None:
        if "INSERT INTO phlo.schema_snapshots" in statement:
            with self.database._lock:
                self.database.snapshot_calls.append(self.connection_string)
                self.snapshot_id = f"snapshot-{len(self.database.snapshot_calls)}"
            return
        with self.database._lock:
            self.database.setup_calls.append(self.connection_string)
            should_fail = self.database.fail_setup_times > 0
            if should_fail:
                self.database.fail_setup_times -= 1
        if self.database.setup_started is not None:
            self.database.setup_started.set()
        if should_fail:
            raise RuntimeError("setup unavailable")

    def fetchone(self) -> tuple[str] | None:
        return (self.snapshot_id,) if self.snapshot_id is not None else None


@pytest.fixture
def _isolated_schema_registry_state(monkeypatch: pytest.MonkeyPatch):
    """Reset process-global schema setup state for each public-path regression."""
    monkeypatch.setattr(SchemaRegistry, "_initialized_connections", set())
    monkeypatch.setattr(SchemaRegistry, "_initialization_lock", threading.Lock())


class TestCanonicalSchemaJson:
    def test_deterministic_serialization(self) -> None:
        schema_a = _schema(("b", "int64", True), ("a", "string", False))
        schema_b = _schema(("a", "string", False), ("b", "int64", True))
        assert _canonical_schema_json(schema_a) == _canonical_schema_json(schema_b)

    def test_output_is_valid_json(self) -> None:
        schema = _schema(("x", "float64", True))
        result = json.loads(_canonical_schema_json(schema))
        assert "fields" in result
        assert result["fields"][0]["name"] == "x"


class TestSchemaHash:
    def test_hash_stability(self) -> None:
        canonical = _canonical_schema_json(_schema(("id", "int64", False)))
        h1 = _schema_hash(canonical)
        h2 = _schema_hash(canonical)
        assert h1 == h2
        assert len(h1) == 16

    def test_different_schemas_different_hashes(self) -> None:
        c1 = _canonical_schema_json(_schema(("id", "int64", False)))
        c2 = _canonical_schema_json(_schema(("id", "string", False)))
        assert _schema_hash(c1) != _schema_hash(c2)


class TestDeserializeSchema:
    def test_roundtrip(self) -> None:
        original = _schema(("id", "int64", False), ("name", "string", True))
        canonical = _canonical_schema_json(original)
        restored = deserialize_schema(canonical)
        assert len(restored.fields) == len(original.fields)
        restored_by_name = {f.name: f for f in restored.fields}
        for field in original.fields:
            assert field.name in restored_by_name
            assert restored_by_name[field.name].dtype == field.dtype
            assert restored_by_name[field.name].nullable == field.nullable

    def test_roundtrip_preserves_default(self) -> None:
        original = NormalizedSchema(
            fields=[
                FieldSpec(
                    name="email", dtype="string", nullable=False, default="unknown@example.com"
                )
            ]
        )
        canonical = _canonical_schema_json(original)
        restored = deserialize_schema(canonical)
        assert restored.fields[0].default == "unknown@example.com"


class TestSchemaRegistryPersistence:
    def test_snapshot_schema_initializes_equivalent_urls_once_under_concurrency(
        self, monkeypatch: pytest.MonkeyPatch, _isolated_schema_registry_state
    ) -> None:
        """Concurrent public snapshots share setup for equivalent database identities."""
        setup_started = threading.Event()
        release_setup = threading.Event()
        both_invoked = threading.Event()
        start = threading.Barrier(3)
        database = _RegistryDatabase(setup_started=setup_started)
        original_execute = _RegistryCursor.execute

        def blocking_execute(self, statement: str, params: object = None) -> None:
            original_execute(self, statement, params)
            if "INSERT INTO phlo.schema_snapshots" not in statement:
                assert setup_started.wait(timeout=1)
                assert release_setup.wait(timeout=1)

        monkeypatch.setattr(_RegistryCursor, "execute", blocking_execute)
        monkeypatch.setattr("phlo.schema_registry.psycopg2.connect", database.connect)
        registries = [
            SchemaRegistry("postgresql://alice:secret@EXAMPLE.test/db?sslmode=require"),
            SchemaRegistry("postgres://bob:other@example.test:5432/db"),
        ]
        snapshot_ids: list[str] = []
        failures: list[BaseException] = []
        invoked = 0
        invoked_lock = threading.Lock()

        def snapshot(registry: SchemaRegistry) -> None:
            nonlocal invoked
            start.wait()
            with invoked_lock:
                invoked += 1
                if invoked == len(registries):
                    both_invoked.set()
            try:
                snapshot_ids.append(
                    registry.snapshot_schema("raw.events", _schema(("id", "int", False)))
                )
            except BaseException as exc:  # pragma: no cover - asserted by the caller
                failures.append(exc)

        workers = [threading.Thread(target=snapshot, args=(registry,)) for registry in registries]
        for worker in workers:
            worker.start()
        start.wait()
        assert setup_started.wait(timeout=1)
        assert both_invoked.wait(timeout=1)
        release_setup.set()
        for worker in workers:
            worker.join(timeout=1)

        assert failures == []
        assert sorted(snapshot_ids) == ["snapshot-1", "snapshot-2"]
        assert len(database.setup_calls) == 1
        assert len(database.snapshot_calls) == 2

    def test_snapshot_schema_initializes_distinct_databases_and_skips_repeats(
        self, monkeypatch: pytest.MonkeyPatch, _isolated_schema_registry_state
    ) -> None:
        """Public snapshots initialize each database once and reuse that setup."""
        database = _RegistryDatabase()
        monkeypatch.setattr("phlo.schema_registry.psycopg2.connect", database.connect)
        first = SchemaRegistry("postgresql://user:secret@example.test/db-a")
        first_alias = SchemaRegistry("postgres://other:credential@EXAMPLE.test:5432/db-a?x=1")
        second = SchemaRegistry("postgresql://user:secret@example.test/db-b")

        assert first.snapshot_schema("raw.a", _schema(("id", "int", False))) == "snapshot-1"
        assert first_alias.snapshot_schema("raw.a", _schema(("id", "int", False))) == "snapshot-2"
        assert second.snapshot_schema("raw.b", _schema(("id", "int", False))) == "snapshot-3"

        assert database.setup_calls == [
            "postgresql://user:secret@example.test/db-a",
            "postgresql://user:secret@example.test/db-b",
        ]
        assert len(database.snapshot_calls) == 3

    def test_snapshot_schema_keeps_username_default_databases_distinct(
        self, monkeypatch: pytest.MonkeyPatch, _isolated_schema_registry_state
    ) -> None:
        """URL users select distinct default databases when no path or dbname exists."""
        database = _RegistryDatabase()
        monkeypatch.setattr("phlo.schema_registry.psycopg2.connect", database.connect)

        assert (
            SchemaRegistry("postgresql://alice:secret@example.test").snapshot_schema(
                "raw.events", _schema(("id", "int", False))
            )
            == "snapshot-1"
        )
        assert (
            SchemaRegistry("postgresql://bob:secret@example.test").snapshot_schema(
                "raw.events", _schema(("id", "int", False))
            )
            == "snapshot-2"
        )

        assert len(database.setup_calls) == 2

    def test_snapshot_schema_uses_query_user_for_default_database_identity(
        self, monkeypatch: pytest.MonkeyPatch, _isolated_schema_registry_state
    ) -> None:
        """libpq's query user selects the default database without a dbname."""
        database = _RegistryDatabase()
        monkeypatch.setattr("phlo.schema_registry.psycopg2.connect", database.connect)

        query_alice = SchemaRegistry("postgresql://example.test?user=alice")
        query_bob = SchemaRegistry("postgres://EXAMPLE.test:5432?user=bob")
        explicit_database = SchemaRegistry("postgresql://example.test?user=carol&dbname=shared")
        same_explicit_database = SchemaRegistry(
            "postgres://example.test:5432?user=dave&dbname=shared&sslmode=require"
        )
        path_database = SchemaRegistry("postgresql://example.test/path-shared?user=erin")
        same_path_database = SchemaRegistry(
            "postgres://example.test:5432/path-shared?user=frank&sslmode=require"
        )

        assert (
            query_alice.snapshot_schema("raw.events", _schema(("id", "int", False))) == "snapshot-1"
        )
        assert (
            query_bob.snapshot_schema("raw.events", _schema(("id", "int", False))) == "snapshot-2"
        )
        assert (
            explicit_database.snapshot_schema("raw.events", _schema(("id", "int", False)))
            == "snapshot-3"
        )
        assert (
            same_explicit_database.snapshot_schema("raw.events", _schema(("id", "int", False)))
            == "snapshot-4"
        )
        assert (
            path_database.snapshot_schema("raw.events", _schema(("id", "int", False)))
            == "snapshot-5"
        )
        assert (
            same_path_database.snapshot_schema("raw.events", _schema(("id", "int", False)))
            == "snapshot-6"
        )

        assert len(database.setup_calls) == 4

    def test_snapshot_schema_ignores_credentials_and_non_identity_options(
        self, monkeypatch: pytest.MonkeyPatch, _isolated_schema_registry_state
    ) -> None:
        """Equivalent default-database URLs share setup without retaining credentials."""
        database = _RegistryDatabase()
        monkeypatch.setattr("phlo.schema_registry.psycopg2.connect", database.connect)
        first = SchemaRegistry(
            "postgresql://alice:first-secret@EXAMPLE.test?sslmode=require&application_name=one"
        )
        equivalent = SchemaRegistry(
            "postgres://alice:second-secret@example.test:5432?options=-c%20work_mem%3D1"
        )

        assert first.snapshot_schema("raw.events", _schema(("id", "int", False))) == "snapshot-1"
        assert (
            equivalent.snapshot_schema("raw.events", _schema(("id", "int", False))) == "snapshot-2"
        )

        assert len(database.setup_calls) == 1

    def test_snapshot_schema_uses_query_dbname_and_host_port_overrides(
        self, monkeypatch: pytest.MonkeyPatch, _isolated_schema_registry_state
    ) -> None:
        """libpq query target selectors split databases but ignore option ordering."""
        database = _RegistryDatabase()
        monkeypatch.setattr("phlo.schema_registry.psycopg2.connect", database.connect)
        analytics = SchemaRegistry(
            "postgresql://user:secret@ignored.test/path?dbname=analytics&host=db.test&port=5544"
        )
        analytics_equivalent = SchemaRegistry(
            "postgres://other:credential@ignored.test/other?sslmode=require&port=5544"
            "&host=DB.test&dbname=analytics"
        )
        warehouse = SchemaRegistry(
            "postgresql://user:secret@ignored.test/path?host=db.test&port=5544&dbname=warehouse"
        )

        assert (
            analytics.snapshot_schema("raw.events", _schema(("id", "int", False))) == "snapshot-1"
        )
        assert (
            analytics_equivalent.snapshot_schema("raw.events", _schema(("id", "int", False)))
            == "snapshot-2"
        )
        assert (
            warehouse.snapshot_schema("raw.events", _schema(("id", "int", False))) == "snapshot-3"
        )

        assert len(database.setup_calls) == 2

    def test_snapshot_schema_retries_setup_after_a_failed_public_call(
        self, monkeypatch: pytest.MonkeyPatch, _isolated_schema_registry_state
    ) -> None:
        """A failed setup is retried before the next public snapshot statement."""
        database = _RegistryDatabase(fail_setup_times=1)
        monkeypatch.setattr("phlo.schema_registry.psycopg2.connect", database.connect)
        registry = SchemaRegistry("postgresql://user:secret@example.test/retry")

        assert registry.snapshot_schema("raw.events", _schema(("id", "int", False))) == "snapshot-1"
        assert registry.snapshot_schema("raw.events", _schema(("id", "int", False))) == "snapshot-2"

        assert database.setup_calls == [
            "postgresql://user:secret@example.test/retry",
            "postgresql://user:secret@example.test/retry",
        ]
        assert len(database.snapshot_calls) == 2

    def test_ensure_schema_treats_already_exists_as_initialized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = SchemaRegistry("postgresql://example")
        monkeypatch.setattr(SchemaRegistry, "_initialized_connections", set())

        def _raise_already_exists() -> None:
            raise RuntimeError("schema already exists")

        monkeypatch.setattr(registry, "_setup_schema", _raise_already_exists)

        registry._ensure_schema()

        assert len(SchemaRegistry._initialized_connections) == 1

    def test_ensure_schema_leaves_cache_unset_after_other_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = SchemaRegistry("postgresql://example")
        monkeypatch.setattr(SchemaRegistry, "_initialized_connections", set())

        def _raise_permission_denied() -> None:
            raise RuntimeError("permission denied")

        monkeypatch.setattr(registry, "_setup_schema", _raise_permission_denied)

        registry._ensure_schema()

        assert SchemaRegistry._initialized_connections == set()

    def test_ensure_schema_initializes_each_database_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SchemaRegistry, "_initialized_connections", set())
        first = SchemaRegistry("postgresql://USER:secret@Example/db/")
        same_database = SchemaRegistry("postgresql://USER:secret@example/db")
        second = SchemaRegistry("postgresql://USER:secret@example/other")
        initialized: list[str] = []
        monkeypatch.setattr(first, "_setup_schema", lambda: initialized.append("first"))
        monkeypatch.setattr(same_database, "_setup_schema", lambda: initialized.append("same"))
        monkeypatch.setattr(second, "_setup_schema", lambda: initialized.append("second"))

        first._ensure_schema()
        same_database._ensure_schema()
        second._ensure_schema()

        assert initialized == ["first", "second"]

    def test_snapshot_schema_uses_conflict_update(self) -> None:
        registry = SchemaRegistry("postgresql://example")
        registry._ensure_schema = lambda: None

        connection = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = ("persisted-id",)
        connection.cursor.return_value.__enter__.return_value = cursor
        mock_connect = MagicMock()
        mock_connect.return_value.__enter__.return_value = connection

        with patch("phlo.schema_registry.psycopg2.connect", mock_connect):
            snapshot_id = registry.snapshot_schema("raw.users", _schema(("id", "int64", False)))

        assert snapshot_id == "persisted-id"
        executed_sql = cursor.execute.call_args.args[0]
        assert "ON CONFLICT (table_name, schema_hash) DO UPDATE" in executed_sql

    def test_get_latest_snapshots_hydrates_rows(self) -> None:
        class _CreatedAt:
            def isoformat(self) -> str:
                return "2026-04-09T10:30:00+00:00"

        registry = SchemaRegistry("postgresql://example")
        registry._ensure_schema = lambda: None

        connection = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (
                "snapshot-1",
                "raw.users",
                {"fields": [{"name": "id", "dtype": "int64", "nullable": False}]},
                "hash-1",
                _CreatedAt(),
                "run-1",
                "materialization",
            )
        ]
        connection.cursor.return_value.__enter__.return_value = cursor
        mock_connect = MagicMock()
        mock_connect.return_value.__enter__.return_value = connection

        with patch("phlo.schema_registry.psycopg2.connect", mock_connect):
            snapshots = registry.get_latest_snapshots("raw.users", limit=1)

        assert len(snapshots) == 1
        snapshot = snapshots[0]
        assert snapshot.snapshot_id == "snapshot-1"
        assert snapshot.table_name == "raw.users"
        assert json.loads(snapshot.schema_json)["fields"][0]["name"] == "id"
        assert snapshot.created_at == "2026-04-09T10:30:00+00:00"
        assert snapshot.run_id == "run-1"
        assert snapshot.source == "materialization"
        cursor.execute.assert_called_once()
        assert cursor.execute.call_args.args[1] == ("raw.users", 1)
