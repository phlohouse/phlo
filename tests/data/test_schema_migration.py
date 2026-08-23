"""Tests for schema migration core primitives, classification, and hook events.

Covers the frozen FieldSpec/NormalizedSchema/SchemaChange value
objects, default additive-vs-breaking change classification with
worst-classification merging, registry registration of migrators and
extractors, protocol conformance checks, and SchemaMigrationEvent
emission. The capability registry is reset after every test.
"""

from __future__ import annotations

import pytest

from phlo.capabilities import (
    FieldSpec,
    NormalizedSchema,
    SchemaChange,
    SchemaExtractor,
    SchemaMigrationPlan,
    SchemaMigrationSpec,
    SchemaMigrator,
    clear_all_capabilities,
    get_capability_registry,
    register_capability,
)
from phlo.capabilities.schema import (
    default_classify_change,
    worst_classification,
)
from phlo.hooks.events import SchemaMigrationEvent
from tests.helpers import reset_capability_test_state

pytestmark = pytest.mark.core_regression


def teardown_function() -> None:
    reset_capability_test_state()


# -- FieldSpec / NormalizedSchema --


class TestFieldSpec:
    def test_defaults(self) -> None:
        f = FieldSpec(name="id", dtype="int64")
        assert f.nullable is True
        assert f.default is None
        assert f.metadata == {}

    def test_non_nullable_with_default(self) -> None:
        f = FieldSpec(name="status", dtype="string", nullable=False, default="active")
        assert f.nullable is False
        assert f.default == "active"

    def test_frozen(self) -> None:
        f = FieldSpec(name="id", dtype="int64")
        with pytest.raises(AttributeError):
            f.name = "changed"  # type: ignore[misc]


class TestNormalizedSchema:
    def test_roundtrip(self) -> None:
        fields = [
            FieldSpec(name="id", dtype="int64", nullable=False),
            FieldSpec(name="name", dtype="string"),
        ]
        schema = NormalizedSchema(fields=fields, metadata={"source": "test"})
        assert len(schema.fields) == 2
        assert schema.metadata["source"] == "test"

    def test_empty(self) -> None:
        schema = NormalizedSchema(fields=[])
        assert schema.fields == []
        assert schema.metadata == {}


# -- SchemaChange / SchemaMigrationPlan --


class TestSchemaChange:
    def test_defaults(self) -> None:
        c = SchemaChange(field_name="col", change_type="add")
        assert c.classification == "breaking"
        assert c.old_value is None
        assert c.new_value is None

    def test_with_values(self) -> None:
        c = SchemaChange(
            field_name="age",
            change_type="widen_type",
            old_value="int32",
            new_value="int64",
            classification="safe",
        )
        assert c.old_value == "int32"
        assert c.new_value == "int64"


class TestSchemaMigrationPlan:
    def test_plan_no_approval(self) -> None:
        plan = SchemaMigrationPlan(
            table_name="users",
            changes=[SchemaChange(field_name="email", change_type="add", classification="safe")],
            classification="safe",
        )
        assert plan.requires_approval is False
        assert plan.recommendations == []

    def test_plan_with_breaking(self) -> None:
        plan = SchemaMigrationPlan(
            table_name="users",
            changes=[
                SchemaChange(field_name="legacy", change_type="drop", classification="breaking"),
            ],
            classification="breaking",
            requires_approval=True,
        )
        assert plan.requires_approval is True


# -- Default Classification --


class TestDefaultClassifyChange:
    def test_add_optional(self) -> None:
        assert default_classify_change("add", nullable=True) == "safe"

    def test_add_required_with_default(self) -> None:
        assert default_classify_change("add", nullable=False, has_default=True) == "warning"

    def test_add_required_no_default(self) -> None:
        assert default_classify_change("add", nullable=False, has_default=False) == "breaking"

    def test_drop(self) -> None:
        assert default_classify_change("drop") == "breaking"

    def test_rename(self) -> None:
        assert default_classify_change("rename") == "warning"

    def test_widen_type(self) -> None:
        assert default_classify_change("widen_type") == "safe"

    def test_narrow_type(self) -> None:
        assert default_classify_change("narrow_type") == "breaking"

    def test_reorder(self) -> None:
        assert default_classify_change("reorder") == "safe"

    def test_nullability_relaxed(self) -> None:
        assert default_classify_change("nullability_relaxed") == "safe"

    def test_nullability_tightened(self) -> None:
        assert default_classify_change("nullability_tightened") == "breaking"

    def test_unknown_defaults_to_breaking(self) -> None:
        assert default_classify_change("unknown_op") == "breaking"


class TestWorstClassification:
    def test_empty(self) -> None:
        assert worst_classification([]) == "safe"

    def test_single(self) -> None:
        assert worst_classification(["warning"]) == "warning"

    def test_mixed(self) -> None:
        assert worst_classification(["safe", "warning", "safe"]) == "warning"

    def test_breaking_wins(self) -> None:
        assert worst_classification(["safe", "breaking", "warning"]) == "breaking"

    def test_all_safe(self) -> None:
        assert worst_classification(["safe", "safe"]) == "safe"


# -- Registry --


class TestSchemaMigrationRegistry:
    def test_register_and_list(self) -> None:
        spec = SchemaMigrationSpec(name="iceberg", provider=object())
        register_capability("schema_migrator", spec)

        registry = get_capability_registry()
        migrators = registry.list("schema_migrator")
        assert any(m.name == "iceberg" for m in migrators)

    def test_clear(self) -> None:
        register_capability("schema_migrator", SchemaMigrationSpec(name="delta", provider=object()))
        clear_all_capabilities()
        assert get_capability_registry().list("schema_migrator") == []


# -- Protocol structural checks --


class TestSchemaMigratorProtocol:
    def test_structural_match(self) -> None:
        class FakeMigrator:
            def supported_changes(self) -> set[str]:
                return {"add", "drop"}

            def classify_change(self, change_type: str, **details: object) -> str:
                return "safe"

            def diff_schema(self, *, table_name: str, desired: object) -> object:
                return SchemaMigrationPlan(table_name=table_name, changes=[], classification="safe")

            def apply_plan(self, *, plan: object, approved: bool = False) -> dict[str, object]:
                return {"status": "applied"}

            def get_schema_history(
                self, *, table_name: str, limit: int = 10
            ) -> list[dict[str, object]]:
                return []

        assert isinstance(FakeMigrator(), SchemaMigrator)


class TestSchemaExtractorProtocol:
    def test_structural_match(self) -> None:
        class FakeExtractor:
            def extract(self, native_schema: object) -> NormalizedSchema:
                return NormalizedSchema(fields=[])

        assert isinstance(FakeExtractor(), SchemaExtractor)


# -- Hook Event --


class TestSchemaMigrationEvent:
    def test_event_fields(self) -> None:
        event = SchemaMigrationEvent(
            event_type="schema_migration.planned",
            table_name="users",
            classification="warning",
            change_count=2,
            status="planned",
            changes=[{"field_name": "email", "change_type": "add"}],
        )
        assert event.table_name == "users"
        assert event.classification == "warning"
        assert event.change_count == 2
        assert event.status == "planned"
        assert len(event.changes) == 1

    def test_default_changes_empty(self) -> None:
        event = SchemaMigrationEvent(
            event_type="schema_migration.applied",
            table_name="orders",
            classification="safe",
            change_count=0,
            status="applied",
        )
        assert event.changes == []
