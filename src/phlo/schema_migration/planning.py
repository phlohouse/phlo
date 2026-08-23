"""Shared schema migration planning over normalized schemas.

Diffing is provider-neutral: dtype widening pairs are lossless, every
other dtype change classifies as narrowing, and providers can override
risk/recommendations per change via SchemaPlanningPolicy. Explicit
renames must be injective and reference fields that exist on both
sides; violations raise SchemaMigrationPlanningError.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from phlo.capabilities.schema import default_classify_change, worst_classification
from phlo.capabilities.specs import FieldSpec, NormalizedSchema, SchemaChange, SchemaMigrationPlan

# Dtype transitions treated as lossless widening; every other dtype change is
# classified as a narrowing change.
_WIDEN_PAIRS = {
    ("int32", "int64"),
    ("float32", "float64"),
    ("int32", "float64"),
    ("int64", "float64"),
    ("date", "timestamptz"),
}


class SchemaMigrationPlanningError(ValueError):
    """Raised when explicit migration instructions are invalid."""


@dataclass(frozen=True, slots=True)
class SchemaPlanningPolicy:
    """Provider-specific risk and recommendation overrides for neutral changes."""

    change_classifications: Mapping[str, str] = field(default_factory=dict)
    recommendations: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SchemaMigrationInstructions:
    """Explicit human-authored schema migration instructions."""

    renames: Mapping[str, str] = field(default_factory=dict)


GENERIC_SCHEMA_POLICY = SchemaPlanningPolicy()


def plan_schema_migration(
    *,
    table_name: str,
    current: NormalizedSchema,
    desired: NormalizedSchema,
    policy: SchemaPlanningPolicy = GENERIC_SCHEMA_POLICY,
    instructions: SchemaMigrationInstructions | None = None,
) -> SchemaMigrationPlan:
    """Compare two normalized schemas and produce a migration plan."""
    instructions = instructions or SchemaMigrationInstructions()
    current_fields = _field_map(current)
    desired_fields = _field_map(desired)
    renames = dict(instructions.renames)
    _validate_renames(renames, current_fields=current_fields, desired_fields=desired_fields)

    changes: list[SchemaChange] = []
    # Renamed fields are emitted as explicit rename changes and excluded from
    # the add/drop/mutation passes below so they never surface as drop+add.
    renamed_sources = set(renames)
    renamed_targets = set(renames.values())

    for old_name, new_name in sorted(renames.items()):
        changes.append(
            SchemaChange(
                field_name=old_name,
                change_type="rename",
                old_value=old_name,
                new_value=new_name,
                classification=_classify("rename", policy),
            )
        )
        _append_field_mutation_changes(
            changes,
            field_name=new_name,
            current_field=current_fields[old_name],
            desired_field=desired_fields[new_name],
            policy=policy,
        )

    for name, desired_field in desired_fields.items():
        if name in renamed_targets:
            continue
        if name not in current_fields:
            changes.append(
                SchemaChange(
                    field_name=name,
                    change_type="add",
                    new_value=desired_field.dtype,
                    classification=_classify(
                        "add",
                        policy,
                        nullable=desired_field.nullable,
                        has_default=desired_field.default is not None,
                    ),
                )
            )

    for name, current_field in current_fields.items():
        if name in renamed_sources:
            continue
        if name not in desired_fields:
            changes.append(
                SchemaChange(
                    field_name=name,
                    change_type="drop",
                    old_value=current_field.dtype,
                    classification=_classify("drop", policy),
                )
            )

    for name in current_fields.keys() & desired_fields.keys():
        if name in renamed_sources or name in renamed_targets:
            continue
        current_field = current_fields[name]
        desired_field = desired_fields[name]
        _append_field_mutation_changes(
            changes,
            field_name=name,
            current_field=current_field,
            desired_field=desired_field,
            policy=policy,
        )

    classification = worst_classification([change.classification for change in changes])
    return SchemaMigrationPlan(
        table_name=table_name,
        changes=changes,
        classification=classification,
        recommendations=_recommendations(changes, classification, policy),
        requires_approval=classification == "breaking",
    )


def _field_map(schema: NormalizedSchema) -> dict[str, FieldSpec]:
    fields: dict[str, FieldSpec] = {}
    for schema_field in schema.fields:
        if schema_field.name in fields:
            raise SchemaMigrationPlanningError(
                f"Schema contains duplicate field name '{schema_field.name}'."
            )
        fields[schema_field.name] = schema_field
    return fields


def _classify(change_type: str, policy: SchemaPlanningPolicy, **details: object) -> str:
    override = policy.change_classifications.get(change_type)
    if override is not None:
        return override
    return default_classify_change(change_type, **details)


def classify_schema_change(
    change_type: str,
    *,
    policy: SchemaPlanningPolicy = GENERIC_SCHEMA_POLICY,
    **details: object,
) -> str:
    """Classify a schema change through the same policy used for planning."""
    return _classify(change_type, policy, **details)


def _append_field_mutation_changes(
    changes: list[SchemaChange],
    *,
    field_name: str,
    current_field: FieldSpec,
    desired_field: FieldSpec,
    policy: SchemaPlanningPolicy,
) -> None:
    if current_field.dtype != desired_field.dtype:
        change_type = (
            "widen_type"
            if (current_field.dtype, desired_field.dtype) in _WIDEN_PAIRS
            else "narrow_type"
        )
        changes.append(
            SchemaChange(
                field_name=field_name,
                change_type=change_type,
                old_value=current_field.dtype,
                new_value=desired_field.dtype,
                classification=_classify(change_type, policy),
            )
        )

    if current_field.nullable != desired_field.nullable:
        change_type = (
            "nullability_relaxed"
            if desired_field.nullable and not current_field.nullable
            else "nullability_tightened"
        )
        changes.append(
            SchemaChange(
                field_name=field_name,
                change_type=change_type,
                old_value=str(current_field.nullable),
                new_value=str(desired_field.nullable),
                classification=_classify(change_type, policy),
            )
        )


def _recommendations(
    changes: list[SchemaChange],
    classification: str,
    policy: SchemaPlanningPolicy,
) -> list[str]:
    recommendations: list[str] = []
    if classification == "breaking":
        recommendations.append("Breaking changes detected - requires explicit approval.")
    for change in changes:
        recommendation = policy.recommendations.get(change.change_type)
        if recommendation and recommendation not in recommendations:
            recommendations.append(recommendation)
    return recommendations


def _validate_renames(
    renames: Mapping[str, str],
    *,
    current_fields: Mapping[str, FieldSpec],
    desired_fields: Mapping[str, FieldSpec],
) -> None:
    seen_targets: dict[str, str] = {}
    for old_name, new_name in renames.items():
        if not old_name or not new_name:
            raise SchemaMigrationPlanningError("Rename instructions require non-empty field names.")
        if old_name == new_name:
            raise SchemaMigrationPlanningError(
                f"Rename instruction {old_name} -> {new_name} is invalid: "
                "source and target are identical."
            )
        if old_name not in current_fields:
            raise SchemaMigrationPlanningError(
                f"Rename instruction {old_name} -> {new_name} is invalid: "
                f"current schema has no field '{old_name}'."
            )
        if new_name not in desired_fields:
            raise SchemaMigrationPlanningError(
                f"Rename instruction {old_name} -> {new_name} is invalid: "
                f"desired schema has no field '{new_name}'."
            )
        previous_source = seen_targets.get(new_name)
        if previous_source is not None:
            raise SchemaMigrationPlanningError(
                f"Invalid rename instructions: multiple rename sources "
                f"('{previous_source}', '{old_name}') target '{new_name}'."
            )
        seen_targets[new_name] = old_name

    for old_name, new_name in renames.items():
        if new_name in current_fields:
            raise SchemaMigrationPlanningError(
                f"Rename instruction {old_name} -> {new_name} is invalid: "
                f"target field '{new_name}' already exists in current schema."
            )
