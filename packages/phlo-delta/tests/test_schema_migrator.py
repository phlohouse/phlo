"""Schema-only migrations preserve real Delta data and reject rewrite operations."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from phlo.capabilities.specs import FieldSpec, NormalizedSchema, SchemaChange, SchemaMigrationPlan
from phlo.hooks.bus import HookBus
from phlo_delta.schema_migrator import DeltaSchemaMigrator


@pytest.fixture
def delta_table(tmp_path, monkeypatch):
    bus = HookBus()
    bus._discovered = True
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)
    path = str(tmp_path / "events")
    write_deltalake(path, pa.table({"id": [3, 17], "value": ["alpha", "omega"]}))
    monkeypatch.setattr("phlo_delta.schema_migrator._resolve_table_uri", lambda _: path)
    monkeypatch.setattr("phlo_delta.schema_migrator._default_storage_options", lambda: {})
    return path


def test_add_columns_preserves_data_and_files(delta_table: str) -> None:
    migrator = DeltaSchemaMigrator()
    original_files = DeltaTable(delta_table).file_uris()
    desired = NormalizedSchema(
        fields=[
            FieldSpec("id", "int64"),
            FieldSpec("value", "string"),
            FieldSpec("source", "string"),
            FieldSpec("count", "int32"),
        ]
    )
    plan = migrator.diff_schema(table_name="raw.events", desired=desired)
    assert plan.classification == "safe"
    result = migrator.apply_plan(plan=plan)
    table = DeltaTable(delta_table)
    assert result["applied_count"] == 2
    assert table.to_pyarrow_table().to_pydict() == {
        "id": [3, 17],
        "value": ["alpha", "omega"],
        "source": [None, None],
        "count": [None, None],
    }
    assert table.file_uris() == original_files
    assert table.version() == 1


def test_empty_plan_does_not_commit(delta_table: str) -> None:
    result = DeltaSchemaMigrator().apply_plan(plan=SchemaMigrationPlan("raw.events", [], "safe"))
    table = DeltaTable(delta_table)
    assert result == {"status": "applied", "applied_count": 0, "changes_applied": []}
    assert table.version() == 0
    assert table.to_pyarrow_table().to_pydict() == {"id": [3, 17], "value": ["alpha", "omega"]}


def test_invalid_plans_are_rejected_before_any_change(delta_table: str) -> None:
    migrator = DeltaSchemaMigrator()
    assert migrator.supported_changes() == {"add"}
    for operation in (
        "drop",
        "rename",
        "widen_type",
        "narrow_type",
        "nullability_relaxed",
        "nullability_tightened",
        "unknown",
    ):
        plan = SchemaMigrationPlan(
            "raw.events",
            [
                SchemaChange("source", "add", new_value="string", classification="safe"),
                SchemaChange("id", operation, old_value="id", new_value="other"),
            ],
            "breaking",
            requires_approval=True,
        )
        with pytest.raises(ValueError, match="Unsupported Delta schema change"):
            migrator.apply_plan(plan=plan, approved=True)
    for field_name, dtype, message in (
        ("id", "string", "already exists"),
        ("source", "unsupported", "Unsupported dtype"),
    ):
        plan = SchemaMigrationPlan(
            "raw.events",
            [
                SchemaChange("first", "add", new_value="int64", classification="safe"),
                SchemaChange(field_name, "add", new_value=dtype, classification="safe"),
            ],
            "safe",
        )
        with pytest.raises(ValueError, match=message):
            migrator.apply_plan(plan=plan)
    table = DeltaTable(delta_table)
    assert table.version() == 0
    assert table.to_pyarrow_table().to_pydict() == {"id": [3, 17], "value": ["alpha", "omega"]}


def test_add_constraints_are_not_silently_lost(delta_table: str) -> None:
    for field in (
        FieldSpec("source", "string", nullable=False),
        FieldSpec("source", "string", default="import"),
    ):
        desired = NormalizedSchema(
            fields=[FieldSpec("id", "int64"), FieldSpec("value", "string"), field]
        )
        with pytest.raises(ValueError, match="nullable columns without defaults"):
            DeltaSchemaMigrator().diff_schema(table_name="raw.events", desired=desired)
    assert DeltaTable(delta_table).version() == 0
