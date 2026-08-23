"""Tests for the shared schema migration planner.

Covers neutral change detection and policy-driven classification overrides,
plus rename instruction handling: explicit renames consume drop/add pairs
while remaining type/nullability changes are still detected, and invalid,
chained, duplicate, or cyclic renames raise.
"""

from __future__ import annotations

import pytest

from phlo.capabilities.specs import FieldSpec, NormalizedSchema
from phlo.schema_migration.instructions import (
    MigrationInstructionError,
    resolve_migration_instructions,
)
from phlo.schema_migration.planning import (
    SchemaMigrationInstructions,
    SchemaMigrationPlanningError,
    SchemaPlanningPolicy,
    plan_schema_migration,
)


def _schema(*fields: tuple[str, str, bool]) -> NormalizedSchema:
    return NormalizedSchema(
        fields=[
            FieldSpec(name=name, dtype=dtype, nullable=nullable) for name, dtype, nullable in fields
        ]
    )


def test_plan_schema_migration_detects_neutral_changes() -> None:
    current = _schema(
        ("id", "int32", False),
        ("email", "string", True),
        ("legacy", "string", True),
        ("score", "int64", True),
    )
    desired = _schema(
        ("id", "int64", False),
        ("email", "string", False),
        ("created_at", "timestamptz", True),
        ("score", "int32", True),
    )

    plan = plan_schema_migration(table_name="raw.users", current=current, desired=desired)

    changes = {(change.field_name, change.change_type): change for change in plan.changes}
    assert changes[("id", "widen_type")].classification == "safe"
    assert changes[("email", "nullability_tightened")].classification == "breaking"
    assert changes[("created_at", "add")].classification == "safe"
    assert changes[("legacy", "drop")].classification == "breaking"
    assert changes[("score", "narrow_type")].classification == "breaking"
    assert plan.classification == "breaking"
    assert plan.requires_approval is True


def test_policy_overrides_change_classification_and_recommendations() -> None:
    current = _schema(("legacy", "string", True))
    desired = _schema()
    policy = SchemaPlanningPolicy(
        change_classifications={"drop": "warning"},
        recommendations={"drop": "Dropped columns are recoverable via snapshots."},
    )

    plan = plan_schema_migration(
        table_name="raw.users",
        current=current,
        desired=desired,
        policy=policy,
    )

    assert plan.classification == "warning"
    assert plan.requires_approval is False
    assert plan.changes[0].classification == "warning"
    assert plan.recommendations == ["Dropped columns are recoverable via snapshots."]


def test_explicit_rename_consumes_drop_and_add() -> None:
    current = _schema(("customer_email", "string", True), ("id", "int64", False))
    desired = _schema(("email", "string", True), ("id", "int64", False))

    plan = plan_schema_migration(
        table_name="raw.customers",
        current=current,
        desired=desired,
        instructions=SchemaMigrationInstructions(renames={"customer_email": "email"}),
    )

    assert [(change.field_name, change.change_type) for change in plan.changes] == [
        ("customer_email", "rename")
    ]
    assert plan.changes[0].old_value == "customer_email"
    assert plan.changes[0].new_value == "email"
    assert plan.classification == "warning"


def test_explicit_rename_still_detects_type_and_nullability_changes() -> None:
    current = _schema(("customer_id", "int32", True))
    desired = _schema(("id", "int64", False))

    plan = plan_schema_migration(
        table_name="raw.customers",
        current=current,
        desired=desired,
        instructions=SchemaMigrationInstructions(renames={"customer_id": "id"}),
    )

    assert [(change.field_name, change.change_type) for change in plan.changes] == [
        ("customer_id", "rename"),
        ("id", "widen_type"),
        ("id", "nullability_tightened"),
    ]


def test_invalid_rename_instruction_raises() -> None:
    current = _schema(("customer_email", "string", True))
    desired = _schema(("email", "string", True))

    with pytest.raises(SchemaMigrationPlanningError, match="current schema has no field"):
        plan_schema_migration(
            table_name="raw.customers",
            current=current,
            desired=desired,
            instructions=SchemaMigrationInstructions(renames={"missing": "email"}),
        )


def test_duplicate_rename_target_raises() -> None:
    current = _schema(("customer_email", "string", True), ("contact_email", "string", True))
    desired = _schema(("email", "string", True))

    with pytest.raises(SchemaMigrationPlanningError, match="multiple rename sources"):
        plan_schema_migration(
            table_name="raw.customers",
            current=current,
            desired=desired,
            instructions=SchemaMigrationInstructions(
                renames={"customer_email": "email", "contact_email": "email"}
            ),
        )


def test_rename_to_existing_current_field_raises() -> None:
    current = _schema(("old_email", "string", True), ("email", "string", True))
    desired = _schema(("email", "string", True), ("primary_email", "string", True))

    with pytest.raises(SchemaMigrationPlanningError, match="already exists in current schema"):
        plan_schema_migration(
            table_name="raw.customers",
            current=current,
            desired=desired,
            instructions=SchemaMigrationInstructions(renames={"old_email": "email"}),
        )


def test_chained_renames_raise() -> None:
    current = _schema(
        ("account_id", "int64", False),
        ("customer_id", "int64", False),
        ("id", "int64", False),
    )
    desired = _schema(
        ("customer_id", "int64", False),
        ("id", "int64", False),
        ("legacy_id", "int64", False),
    )

    with pytest.raises(SchemaMigrationPlanningError, match="already exists in current schema"):
        plan_schema_migration(
            table_name="raw.customers",
            current=current,
            desired=desired,
            instructions=SchemaMigrationInstructions(
                renames={
                    "account_id": "customer_id",
                    "customer_id": "id",
                    "id": "legacy_id",
                }
            ),
        )


def test_no_op_rename_instruction_raises() -> None:
    current = _schema(("id", "string", True))
    desired = _schema(("id", "string", True))

    with pytest.raises(SchemaMigrationPlanningError, match="source and target are identical"):
        plan_schema_migration(
            table_name="raw.customers",
            current=current,
            desired=desired,
            instructions=SchemaMigrationInstructions(renames={"id": "id"}),
        )


def test_cyclic_rename_instructions_raise_as_existing_targets() -> None:
    current = _schema(("a", "string", True), ("b", "string", True))
    desired = _schema(("a", "string", True), ("b", "string", True))

    with pytest.raises(SchemaMigrationPlanningError, match="already exists in current schema"):
        plan_schema_migration(
            table_name="raw.customers",
            current=current,
            desired=desired,
            instructions=SchemaMigrationInstructions(renames={"a": "b", "b": "a"}),
        )


def test_duplicate_field_names_raise() -> None:
    current = _schema(("id", "int64", False), ("id", "string", True))
    desired = _schema(("id", "int64", False))

    with pytest.raises(SchemaMigrationPlanningError, match="duplicate field name"):
        plan_schema_migration(table_name="raw.customers", current=current, desired=desired)


def test_resolve_migration_instructions_merges_yaml_and_cli_renames(tmp_path) -> None:
    migration_file = tmp_path / "warehouse__customers.yaml"
    migration_file.write_text(
        "table_name: warehouse.customers\nrenames:\n  customer_email: email\n",
        encoding="utf-8",
    )

    instructions = resolve_migration_instructions(
        table_name="warehouse.customers",
        migration_file=migration_file,
        rename_flags=("surname=last_name",),
    )

    assert instructions == SchemaMigrationInstructions(
        renames={"customer_email": "email", "surname": "last_name"}
    )


def test_resolve_migration_instructions_rejects_yaml_cli_conflicts(tmp_path) -> None:
    migration_file = tmp_path / "warehouse__customers.yaml"
    migration_file.write_text(
        "table_name: warehouse.customers\nrenames:\n  customer_email: email\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationInstructionError, match="Sort out the YAML or CLI flags"):
        resolve_migration_instructions(
            table_name="warehouse.customers",
            migration_file=migration_file,
            rename_flags=("customer_email=primary_email",),
        )
