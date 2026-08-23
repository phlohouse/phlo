"""Iceberg implementation of the SchemaMigrator protocol.

This module provides the ``IcebergSchemaMigrator`` class which implements
Phlo's schema migration capability for Iceberg tables. It supports detecting
schema changes, classifying their impact (safe/warning/breaking), and
applying migrations with approval workflows.

Supported change types:
    - ``add``: Add new columns (safe if nullable, breaking if required without default)
    - ``drop``: Remove columns (warning - data loss risk but recoverable via snapshots)
    - ``rename``: Rename columns (safe in Iceberg via native rename)
    - ``widen_type``: Type promotion (e.g., int32 -> int64, date -> timestamptz)
    - ``narrow_type``: Type restriction (breaking - potential data loss)
    - ``nullability_relaxed``: Make column nullable (safe)
    - ``nullability_tightened``: Make column required (breaking without default)

Example:
    Detect and apply schema migrations::

        from phlo_iceberg.schema_migrator import IcebergSchemaMigrator
        from phlo.capabilities.specs import NormalizedSchema, NormalizedField

        # Create migrator for specific branch
        migrator = IcebergSchemaMigrator(ref="main")

        # Define desired schema
        desired = NormalizedSchema(
            fields=[
                NormalizedField(name="id", dtype="int64", nullable=False),
                NormalizedField(name="name", dtype="string", nullable=True),
                NormalizedField(name="score", dtype="float64", nullable=True),
            ]
        )

        # Detect changes
        plan = migrator.diff_schema(table_name="raw.users", desired=desired)
        print(f"Changes: {len(plan.changes)}")
        print(f"Classification: {plan.classification}")

        # Apply if safe or approved
        if not plan.requires_approval:
            result = migrator.apply_plan(plan=plan)
            print(f"Applied {result['applied_count']} changes")
        else:
            print("Breaking changes require approval")
            # After review:
            # result = migrator.apply_plan(plan=plan, approved=True)

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyiceberg.types import (
    BinaryType,
    BooleanType,
    DateType,
    DecimalType,
    DoubleType,
    FloatType,
    IcebergType,
    IntegerType,
    LongType,
    StringType,
    TimestampType,
    TimestamptzType,
)

from dataclasses import asdict

from phlo.capabilities.specs import FieldSpec, NormalizedSchema, SchemaChange, SchemaMigrationPlan
from phlo.hooks.emitters import SchemaMigrationEventContext, SchemaMigrationEventEmitter
from phlo.logging import get_logger
from phlo.schema_migration.planning import (
    SchemaMigrationInstructions,
    SchemaPlanningPolicy,
    classify_schema_change,
    plan_schema_migration,
)
from phlo_iceberg.catalog import get_catalog
from phlo_iceberg.settings import get_settings

logger = get_logger(__name__)

_ICEBERG_TYPE_MAP: dict[type[IcebergType], str] = {
    StringType: "string",
    LongType: "int64",
    IntegerType: "int32",
    DoubleType: "float64",
    FloatType: "float32",
    BooleanType: "bool",
    TimestamptzType: "timestamptz",
    TimestampType: "timestamp",
    DateType: "date",
    BinaryType: "binary",
    DecimalType: "decimal",
}

_SYSTEM_METADATA_FIELDS = {
    "_dlt_load_id",
    "_dlt_id",
    "_phlo_ingested_at",
    "_phlo_row_id",
    "_phlo_partition_date",
    "_phlo_run_id",
}

ICEBERG_SCHEMA_POLICY = SchemaPlanningPolicy(
    change_classifications={"drop": "warning", "rename": "safe"},
    recommendations={"drop": "Dropped columns are recoverable via Iceberg snapshot rollback."},
)


def _iceberg_type_to_dtype(iceberg_type: IcebergType) -> str:
    """Map a PyIceberg type instance to a canonical dtype string."""
    dtype = _ICEBERG_TYPE_MAP.get(type(iceberg_type))
    if dtype is not None:
        return dtype
    return str(iceberg_type)


@dataclass
class IcebergSchemaMigrator:
    """SchemaMigrator implementation for Iceberg-backed tables.
    Detects schema differences between a desired state and current table schema,
    classifies changes by impact level, and applies migrations with optional
    approval workflows.

    Iceberg's native capabilities allow safe operations like column rename
    and time-travel recovery for dropped columns.

    Example:
        Basic migration workflow::

            migrator = IcebergSchemaMigrator(ref="main")

            # Define target schema
            desired = NormalizedSchema(
                fields=[
                    NormalizedField(name="user_id", dtype="int64", nullable=False),
                    NormalizedField(name="email", dtype="string", nullable=True),
                    NormalizedField(name="created_at", dtype="timestamptz", nullable=False),
                ]
            )

            # Detect changes
            plan = migrator.diff_schema(
                table_name="raw.users",
                desired=desired
            )

            # Review and apply
            for change in plan.changes:
                print(f"{change.change_type}: {change.field_name} ({change.classification})")

            if plan.requires_approval:
                print("WARNING: Breaking changes detected!")
            else:
                result = migrator.apply_plan(plan=plan)
                print(f"Applied {result['applied_count']} changes")

    See Also:
        Phlo capabilities system for schema migration protocols.
    """

    ref: str = field(default_factory=lambda: get_settings().iceberg_default_ref)

    def supported_changes(self) -> set[str]:
        """Return the set of change types supported by Iceberg.
        Iceberg's native schema evolution supports all common change types
        including safe renames, type widening, and nullability changes.

        Example:
            Check supported changes::

                migrator = IcebergSchemaMigrator()
                supported = migrator.supported_changes()
                print(f"Can rename columns: {'rename' in supported}")
        """
        return {
            "add",
            "drop",
            "rename",
            "widen_type",
            "narrow_type",
            "reorder",
            "nullability_relaxed",
            "nullability_tightened",
        }

    def classify_change(self, change_type: str, **details: Any) -> str:
        """Classify a schema change by impact level.
        Iceberg-specific overrides:
        - ``rename``: Always "safe" (native rename support)
        - ``drop``: "warning" (data loss risk but recoverable via snapshots)
        - Other types: Delegate to default classifier

        Example:
            Classify individual changes::

                migrator = IcebergSchemaMigrator()

                # Safe operations
                assert migrator.classify_change("rename") == "safe"

                # Warning level
                assert migrator.classify_change("drop") == "warning"

                # Breaking without default
                assert migrator.classify_change("add", nullable=False, has_default=False) == "breaking"
        """
        return classify_schema_change(change_type, policy=ICEBERG_SCHEMA_POLICY, **details)

    def diff_schema(
        self,
        *,
        table_name: str,
        desired: NormalizedSchema,
        instructions: SchemaMigrationInstructions | None = None,
    ) -> SchemaMigrationPlan:
        """Compare desired schema against current table schema.
        Detects all differences between the desired schema and the current
        table schema, classifying each change by impact level.

        Detected changes:
            - Added columns (not in current schema)
            - Dropped columns (not in desired schema)
            - Type changes (widening or narrowing)
            - Nullability changes (relaxed or tightened)

        Example:
            Detect schema drift::

                migrator = IcebergSchemaMigrator()

                # Current table has columns: id (int), name (string)
                # Desired adds: email (string), changes id to int64
                desired = NormalizedSchema(
                    fields=[
                        NormalizedField(name="id", dtype="int64", nullable=False),
                        NormalizedField(name="name", dtype="string", nullable=True),
                        NormalizedField(name="email", dtype="string", nullable=True),
                    ]
                )

                plan = migrator.diff_schema(
                    table_name="raw.users",
                    desired=desired
                )

                print(f"Changes: {len(plan.changes)}")
                for change in plan.changes:
                    print(f"  {change.field_name}: {change.change_type} ({change.classification})")

                if plan.requires_approval:
                    print("Requires approval before applying")
        """
        catalog = get_catalog(ref=self.ref)
        table = catalog.load_table(table_name)
        current_schema = table.schema()

        # Metadata columns (_dlt_*, _phlo_*) are injected by the ingestion
        # pipeline and never appear in user-supplied schemas, so exclude them
        # or every diff would report them as dropped.
        current_fields: list[FieldSpec] = []
        for f in current_schema.fields:
            if f.name in _SYSTEM_METADATA_FIELDS:
                continue
            current_fields.append(
                FieldSpec(
                    name=f.name,
                    dtype=_iceberg_type_to_dtype(f.field_type),
                    nullable=f.required is False,
                )
            )

        plan = plan_schema_migration(
            table_name=table_name,
            current=NormalizedSchema(fields=current_fields),
            desired=desired,
            policy=ICEBERG_SCHEMA_POLICY,
            instructions=instructions,
        )

        emitter = SchemaMigrationEventEmitter(
            SchemaMigrationEventContext(table_name=table_name, tags={"backend": "iceberg"})
        )
        emitter.emit(
            status="planned",
            classification=plan.classification,
            change_count=len(plan.changes),
            changes=[asdict(c) for c in plan.changes],
        )

        return plan

    def apply_plan(self, *, plan: SchemaMigrationPlan, approved: bool = False) -> dict[str, Any]:
        """Execute a migration plan against the Iceberg catalog.
        Applies all changes in the plan using Iceberg's schema update API.
        Breaking changes require explicit approval via the ``approved`` flag.

        Supported operations:
            - Add column: ``update.add_column()```
            - Drop column: ``update.delete_column()```
            - Rename column: ``update.rename_column()```
            - Type change: ``update.update_column()```
            - Nullability: ``update.set_column_optional()`` / ``set_column_required()``

        Raises: ValueError when if plan contains breaking changes and ``approved`` is False.
        Raises: Exception when any Iceberg catalog errors during update.
        Example:
            Apply safe changes automatically::

                plan = migrator.diff_schema(table_name="raw.users", desired=schema)

                if not plan.requires_approval:
                    result = migrator.apply_plan(plan=plan)
                    print(f"Applied {result['applied_count']} changes")
                else:
                    print("Manual approval required")

            Apply with approval::

                # After reviewing the plan...
                result = migrator.apply_plan(plan=plan, approved=True)
                print(f"Applied changes: {result['changes_applied']}")
        """
        if plan.requires_approval and not approved:
            raise ValueError(
                f"Plan for {plan.table_name} contains breaking changes and requires approval."
            )

        catalog = get_catalog(ref=self.ref)
        table = catalog.load_table(plan.table_name)

        applied: list[str] = []
        applied_changes: list[SchemaChange] = []
        with table.update_schema() as update:
            for change in plan.changes:
                if change.change_type == "add":
                    iceberg_type = _dtype_to_iceberg_type(change.new_value or "string")
                    update.add_column(
                        path=change.field_name,
                        field_type=iceberg_type,
                    )
                    applied.append(f"add:{change.field_name}")
                    applied_changes.append(change)
                elif change.change_type == "drop":
                    update.delete_column(path=change.field_name)
                    applied.append(f"drop:{change.field_name}")
                    applied_changes.append(change)
                elif change.change_type == "rename":
                    update.rename_column(
                        path=change.old_value or change.field_name,
                        new_name=change.new_value or change.field_name,
                    )
                    applied.append(f"rename:{change.field_name}")
                    applied_changes.append(change)
                elif change.change_type in {"widen_type", "narrow_type"}:
                    iceberg_type = _dtype_to_iceberg_type(change.new_value or "string")
                    update.update_column(
                        path=change.field_name,
                        field_type=iceberg_type,
                    )
                    applied.append(f"{change.change_type}:{change.field_name}")
                    applied_changes.append(change)
                elif change.change_type == "nullability_relaxed":
                    update.set_column_optional(path=change.field_name)
                    applied.append(f"nullability_relaxed:{change.field_name}")
                    applied_changes.append(change)
                elif change.change_type == "nullability_tightened":
                    update.set_column_required(path=change.field_name)
                    applied.append(f"nullability_tightened:{change.field_name}")
                    applied_changes.append(change)
                # Change types without an Iceberg operation (currently
                # "reorder") fall through silently rather than failing the
                # whole plan.

        logger.info(
            "iceberg_schema_migration_applied",
            table_name=plan.table_name,
            applied_count=len(applied),
            changes=applied,
        )

        emitter = SchemaMigrationEventEmitter(
            SchemaMigrationEventContext(table_name=plan.table_name, tags={"backend": "iceberg"})
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
        """Return snapshot-level schema history for a table.
        Retrieves Iceberg snapshots which capture schema state at each
        table modification. Includes metadata about operation type,
        timestamp, and parent snapshot relationships.

        Example:
            Review table history::

                history = migrator.get_schema_history(
                    table_name="raw.users",
                    limit=5
                )

                for snapshot in history:
                    ts = datetime.fromtimestamp(snapshot['timestamp_ms'] / 1000)
                    print(f"{ts}: {snapshot['summary']}")

        Note:
            Schema history is derived from Iceberg snapshots, which
            capture the entire table state including schema at each
            commit point.
        """
        catalog = get_catalog(ref=self.ref)
        table = catalog.load_table(table_name)

        snapshots = sorted(table.snapshots(), key=lambda s: s.timestamp_ms, reverse=True)
        results: list[dict[str, Any]] = []
        for snap in snapshots[:limit]:
            results.append(
                {
                    "snapshot_id": snap.snapshot_id,
                    "timestamp_ms": snap.timestamp_ms,
                    "summary": dict(snap.summary.additional_properties) if snap.summary else {},
                    "parent_id": snap.parent_snapshot_id,
                }
            )
        return results


# -- internal helpers --------------------------------------------------------

_DTYPE_TO_ICEBERG: dict[str, type[IcebergType]] = {
    "string": StringType,
    "int64": LongType,
    "int32": IntegerType,
    "float64": DoubleType,
    "float32": FloatType,
    "bool": BooleanType,
    "timestamptz": TimestamptzType,
    "timestamp": TimestampType,
    "date": DateType,
    "binary": BinaryType,
}


def _dtype_to_iceberg_type(dtype: str) -> IcebergType:
    """Map a canonical dtype string back to a PyIceberg type instance."""
    cls = _DTYPE_TO_ICEBERG.get(dtype)
    if cls is None:
        raise ValueError(f"Unsupported dtype for Iceberg conversion: {dtype}")
    return cls()
