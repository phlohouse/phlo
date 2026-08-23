"""Tests for the schema registry module.

Covers deterministic canonical schema serialization and hashing (field
order independent), schema round-trips through deserialization, and
persistence behavior including treating "already exists" as initialized.
"""

from __future__ import annotations

import json
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
    def test_ensure_schema_treats_already_exists_as_initialized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = SchemaRegistry("postgresql://example")
        monkeypatch.setattr(SchemaRegistry, "_schema_initialized", False)

        def _raise_already_exists() -> None:
            raise RuntimeError("schema already exists")

        monkeypatch.setattr(registry, "_setup_schema", _raise_already_exists)

        registry._ensure_schema()

        assert SchemaRegistry._schema_initialized is True

    def test_ensure_schema_leaves_cache_unset_after_other_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = SchemaRegistry("postgresql://example")
        monkeypatch.setattr(SchemaRegistry, "_schema_initialized", False)

        def _raise_permission_denied() -> None:
            raise RuntimeError("permission denied")

        monkeypatch.setattr(registry, "_setup_schema", _raise_permission_denied)

        registry._ensure_schema()

        assert SchemaRegistry._schema_initialized is False

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
