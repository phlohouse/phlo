"""Schema migration planning primitives.

Public surface re-exports change classification and migration planning from
phlo.schema_migration.planning plus instruction resolution from
phlo.schema_migration.instructions; no behaviour is defined here.
"""

from phlo.schema_migration.instructions import (
    MigrationInstructionError,
    default_migration_instruction_path,
    resolve_migration_instructions,
)
from phlo.schema_migration.planning import (
    GENERIC_SCHEMA_POLICY,
    SchemaMigrationInstructions,
    SchemaMigrationPlanningError,
    SchemaPlanningPolicy,
    classify_schema_change,
    plan_schema_migration,
)

__all__ = [
    "GENERIC_SCHEMA_POLICY",
    "MigrationInstructionError",
    "SchemaMigrationInstructions",
    "SchemaMigrationPlanningError",
    "SchemaPlanningPolicy",
    "classify_schema_change",
    "default_migration_instruction_path",
    "plan_schema_migration",
    "resolve_migration_instructions",
]
