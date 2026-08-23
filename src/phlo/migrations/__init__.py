"""Data migration primitives and execution helpers.

One import point bundling spec parsing, source adapters, executor, and history
reading for the migration subsystem.
"""

from phlo.migrations.adapters import (
    SourceAdapter,
    list_source_adapter_types,
    resolve_source_adapter,
)
from phlo.migrations.executor import (
    MigrationExecutionError,
    MigrationExecutor,
    read_migration_history,
)
from phlo.migrations.parser import MigrationSpecError, load_migration_spec
from phlo.migrations.specs import (
    MigrationDestination,
    MigrationOptions,
    MigrationResult,
    MigrationSource,
    MigrationSpec,
)

__all__ = [
    "MigrationDestination",
    "MigrationExecutionError",
    "MigrationExecutor",
    "MigrationOptions",
    "MigrationResult",
    "MigrationSource",
    "MigrationSpec",
    "MigrationSpecError",
    "SourceAdapter",
    "list_source_adapter_types",
    "load_migration_spec",
    "read_migration_history",
    "resolve_source_adapter",
]
