"""Data migration specs and execution result models.

Frozen dataclasses describing a migration (source, destination, options,
column mapping) and its execution outcome. All models are plain data with
no execution logic; runners consume these specs as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MigrationSource:
    """Source configuration for a migration."""

    type: str
    connection: str | None = None
    query: str | None = None
    table: str | None = None
    path: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MigrationDestination:
    """Destination configuration for a migration."""

    table: str
    write_mode: str = "append"
    unique_key: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationOptions:
    """Execution options for a migration."""

    chunk_size: int = 50_000
    parallelism: int = 1
    validate: bool = True
    dry_run: bool = False
    quality_schema: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    """Parsed migration specification."""

    name: str
    version: str
    description: str
    source: MigrationSource
    destination: MigrationDestination
    options: MigrationOptions = field(default_factory=MigrationOptions)
    column_mapping: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Outcome of a migration execution."""

    name: str
    status: str
    rows_read: int
    rows_written: int
    rows_rejected: int
    chunks_processed: int
    duration_seconds: float
    validation_passed: bool | None
    metadata: dict[str, Any] = field(default_factory=dict)
