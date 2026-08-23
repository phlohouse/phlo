"""Delta Lake implementation of the SchemaMigrator protocol.

This module provides schema migration capabilities for Delta Lake tables,
including schema diffing, migration planning, and schema change application.
It supports add, drop, rename, type widening/narrowing, and nullability changes.

Example:
    from phlo_delta.schema_migrator import DeltaSchemaMigrator
    from phlo.capabilities.specs import NormalizedSchema

    migrator = DeltaSchemaMigrator()
    plan = migrator.diff_schema(table_name="raw.events", desired=normalized_schema)
    result = migrator.apply_plan(plan=plan, approved=True)

"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, cast

import pyarrow as pa

from phlo.capabilities.specs import FieldSpec, NormalizedSchema, SchemaChange, SchemaMigrationPlan
from phlo.hooks.emitters import SchemaMigrationEventContext, SchemaMigrationEventEmitter
from phlo.logging import get_logger
from phlo.schema_migration.planning import (
    SchemaMigrationInstructions,
    SchemaPlanningPolicy,
    classify_schema_change,
    plan_schema_migration,
)
from phlo_delta.tables import _default_storage_options, _resolve_table_uri

logger = get_logger(__name__)

_ARROW_TYPE_MAP: dict[pa.DataType, str] = {
    pa.string(): "string",
    pa.int64(): "int64",
    pa.int32(): "int32",
    pa.float64(): "float64",
    pa.float32(): "float32",
    pa.bool_(): "bool",
    pa.date32(): "date",
    pa.binary(): "binary",
}
# Mapping of PyArrow data types to canonical dtype strings.

DELTA_SCHEMA_POLICY = SchemaPlanningPolicy(
    change_classifications={"drop": "warning", "rename": "safe"},
    recommendations={"drop": "Dropped columns are recoverable via Delta Lake time travel."},
)


def _arrow_type_to_dtype(arrow_type: pa.DataType) -> str:
    """Map a PyArrow type instance to a canonical dtype string.

    Example:
        dtype = _arrow_type_to_dtype(pa.int64())
        # Returns: "int64"

    """
    dtype = _ARROW_TYPE_MAP.get(arrow_type)
    if dtype is not None:
        return dtype
    if isinstance(arrow_type, pa.TimestampType):
        if arrow_type.tz:
            return "timestamptz"
        return "timestamp"
    return str(arrow_type)


_DTYPE_TO_ARROW: dict[str, pa.DataType] = {
    "string": pa.string(),
    "int64": pa.int64(),
    "int32": pa.int32(),
    "float64": pa.float64(),
    "float32": pa.float32(),
    "bool": pa.bool_(),
    "timestamptz": pa.timestamp("us", tz="UTC"),
    "timestamp": pa.timestamp("us"),
    "date": pa.date32(),
    "binary": pa.binary(),
}
# Mapping of canonical dtype strings back to PyArrow data types.


def _dtype_to_arrow_type(dtype: str) -> pa.DataType:
    """Map a canonical dtype string back to a PyArrow type.

    Raises ValueError when the dtype is not supported.

    Example:
        arrow_type = _dtype_to_arrow_type("int64")
        # Returns: pa.int64()

    """
    result = _DTYPE_TO_ARROW.get(dtype)
    if result is None:
        raise ValueError(f"Unsupported dtype for Delta conversion: {dtype}")
    return result


@dataclass
class DeltaSchemaMigrator:
    """SchemaMigrator backed by Delta Lake tables.

    Implements schema comparison, migration planning, and change application,
    supporting field addition, removal, renaming, type changes, and
    nullability adjustments.

    Example:
        migrator = DeltaSchemaMigrator()
        supported = migrator.supported_changes()
        plan = migrator.diff_schema(table_name="raw.events", desired=schema)
        result = migrator.apply_plan(plan=plan, approved=True)

    """

    def supported_changes(self) -> set[str]:
        """Return the set of change types supported natively by Delta Lake.

        Covers add, drop, rename, widen/narrow type, and both nullability
        directions.
        """
        return {
            "add",
            "drop",
            "rename",
            "widen_type",
            "narrow_type",
            "nullability_relaxed",
            "nullability_tightened",
        }

    def classify_change(self, change_type: str, **details: Any) -> str:
        """Classify a change with Delta-specific overrides.

        Rename is safe and drop is warning (recoverable via time travel);
        everything else falls through to the default classifier and returns
        "safe", "warning", or "breaking".

        Example:
            cls = migrator.classify_change("rename")
            # Returns: "safe"

        """
        return classify_schema_change(change_type, policy=DELTA_SCHEMA_POLICY, **details)

    def diff_schema(
        self,
        *,
        table_name: str,
        desired: NormalizedSchema,
        instructions: SchemaMigrationInstructions | None = None,
    ) -> SchemaMigrationPlan:
        """Compare *desired* schema against current Delta table schema.

        Returns a plan describing every detected change with classifications
        and recommendations. Raises if the table cannot be accessed or read.

        Example:
            from phlo.capabilities.specs import NormalizedSchema, NormalizedField

            desired = NormalizedSchema(fields=[
                NormalizedField(name="id", dtype="string", nullable=False),
            ])
            plan = migrator.diff_schema(table_name="raw.events", desired=desired)

        """
        from deltalake import DeltaTable

        table_uri = _resolve_table_uri(table_name)
        opts = _default_storage_options()

        dt = DeltaTable(table_uri, storage_options=opts)
        current_schema = cast(Any, dt.schema()).to_pyarrow()

        current_fields: list[FieldSpec] = []
        for field in current_schema:
            current_fields.append(
                FieldSpec(
                    name=field.name,
                    dtype=_arrow_type_to_dtype(field.type),
                    nullable=field.nullable,
                )
            )

        plan = plan_schema_migration(
            table_name=table_name,
            current=NormalizedSchema(fields=current_fields),
            desired=desired,
            policy=DELTA_SCHEMA_POLICY,
            instructions=instructions,
        )

        emitter = SchemaMigrationEventEmitter(
            SchemaMigrationEventContext(table_name=table_name, tags={"backend": "delta"})
        )
        emitter.emit(
            status="planned",
            classification=plan.classification,
            change_count=len(plan.changes),
            changes=[asdict(c) for c in plan.changes],
        )

        return plan

    def apply_plan(self, *, plan: SchemaMigrationPlan, approved: bool = False) -> dict[str, Any]:
        """Execute a migration plan against a Delta table.

        Breaking changes require explicit approval via ``approved``; raises
        ValueError otherwise and propagates any schema operation failure.
        Returns status, applied count, and the applied changes.

        Example:
            plan = migrator.diff_schema(table_name="raw.events", desired=schema)
            if not plan.requires_approval:
                result = migrator.apply_plan(plan=plan, approved=True)
                print(f"Applied {result['applied_count']} changes")

        """
        if plan.requires_approval and not approved:
            raise ValueError(
                f"Plan for {plan.table_name} contains breaking changes and requires approval."
            )

        from deltalake import DeltaTable

        table_uri = _resolve_table_uri(plan.table_name)
        opts = _default_storage_options()

        dt = DeltaTable(table_uri, storage_options=opts)
        current_schema = cast(Any, dt.schema()).to_pyarrow()

        new_fields: list[pa.Field] = list(current_schema)
        applied: list[str] = []

        applied_changes: list[SchemaChange] = []
        for change in plan.changes:
            if change.change_type == "add":
                arrow_type = _dtype_to_arrow_type(change.new_value or "string")
                new_fields.append(pa.field(change.field_name, arrow_type, nullable=True))
                applied.append(f"add:{change.field_name}")
                applied_changes.append(change)
            elif change.change_type == "drop":
                new_fields = [f for f in new_fields if f.name != change.field_name]
                applied.append(f"drop:{change.field_name}")
                applied_changes.append(change)
            elif change.change_type == "rename":
                old_name = change.old_value or change.field_name
                new_name = change.new_value or change.field_name
                new_fields = [
                    pa.field(new_name, f.type, nullable=f.nullable) if f.name == old_name else f
                    for f in new_fields
                ]
                applied.append(f"rename:{change.field_name}")
                applied_changes.append(change)
            elif change.change_type in {"widen_type", "narrow_type"}:
                arrow_type = _dtype_to_arrow_type(change.new_value or "string")
                new_fields = [
                    pa.field(f.name, arrow_type, nullable=f.nullable)
                    if f.name == change.field_name
                    else f
                    for f in new_fields
                ]
                applied.append(f"{change.change_type}:{change.field_name}")
                applied_changes.append(change)
            elif change.change_type == "nullability_relaxed":
                new_fields = [
                    pa.field(f.name, f.type, nullable=True) if f.name == change.field_name else f
                    for f in new_fields
                ]
                applied.append(f"nullability_relaxed:{change.field_name}")
                applied_changes.append(change)
            elif change.change_type == "nullability_tightened":
                new_fields = [
                    pa.field(f.name, f.type, nullable=False) if f.name == change.field_name else f
                    for f in new_fields
                ]
                applied.append(f"nullability_tightened:{change.field_name}")
                applied_changes.append(change)

        new_schema = pa.schema(new_fields)

        empty = pa.table(
            {field.name: pa.array([], type=field.type) for field in new_schema},
            schema=new_schema,
        )

        from deltalake import write_deltalake

        # The schema change is committed by writing an empty table with
        # mode="overwrite", schema_mode="overwrite": Delta records the new
        # schema as the latest table version. Note that an overwrite commit
        # replaces the table contents, so existing rows are not carried through
        # this call.
        write_deltalake(
            table_uri,
            empty,
            mode="overwrite",
            schema_mode="overwrite",
            storage_options=opts,
        )
        logger.info(
            "delta_schema_migration_applied",
            table_name=plan.table_name,
            applied_count=len(applied),
            changes=applied,
        )

        emitter = SchemaMigrationEventEmitter(
            SchemaMigrationEventContext(table_name=plan.table_name, tags={"backend": "delta"})
        )
        emitter.emit(
            status="applied",
            classification=plan.classification,
            change_count=len(applied),
            changes=[asdict(c) for c in applied_changes],
        )

        return {
            "status": "applied",
            "applied_count": len(applied),
            "changes_applied": applied,
        }

    def get_schema_history(self, *, table_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return version-level history for *table_name*.

        Each entry carries version, timestamp, operation, and parameters.

        Example:
            history = migrator.get_schema_history(table_name="raw.events", limit=5)
            for entry in history:
                print(f"Version {entry['version']}: {entry['operation']}")

        """
        from phlo_delta.tables import list_table_versions

        return list_table_versions(table_name=table_name, limit=limit)
