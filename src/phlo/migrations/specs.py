"""Data migration specs and execution result models.

Frozen dataclasses describing a migration (source, destination, options,
column mapping) and its execution outcome. All models are plain data with
no execution logic; runners consume these specs as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class _Source(BaseModel):
    """Provider identity and provider-owned adapter options."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    type: str
    options: dict[str, Any] = Field(default_factory=dict)


class FileMigrationSource(_Source):
    kind: Literal["file"] = "file"
    path: str = Field(min_length=1)
    connection: None = None
    query: None = None
    table: None = None


class QueryMigrationSource(_Source):
    kind: Literal["query"] = "query"
    connection: str = Field(min_length=1)
    query: str = Field(min_length=1)
    path: None = None
    table: None = None


class TableMigrationSource(_Source):
    kind: Literal["table"] = "table"
    connection: str = Field(min_length=1)
    table: str = Field(min_length=1)
    path: None = None
    query: None = None


class AdapterMigrationSource(_Source):
    """A registered provider reading an adapter-specific source from options."""

    kind: Literal["adapter"] = "adapter"
    connection: str | None = None
    path: None = None
    query: None = None
    table: None = None


type MigrationSourceValue = Annotated[
    FileMigrationSource | QueryMigrationSource | TableMigrationSource | AdapterMigrationSource,
    Field(discriminator="kind"),
]


class MigrationSourceError(ValueError):
    """A source has missing or mutually exclusive selectors."""


def MigrationSource(type: str, **values: Any) -> MigrationSourceValue:
    """Legacy flat-source adapter to the discriminated source contract."""
    selectors = [key for key in ("path", "query", "table") if values.get(key) is not None]
    if len(selectors) > 1:
        raise MigrationSourceError("source requires exactly one of path, query or table")
    if type == "csv" and selectors != ["path"]:
        raise MigrationSourceError("source.path is required for csv source")
    kind = {"path": "file", "query": "query", "table": "table"}.get(
        selectors[0] if selectors else "", "adapter"
    )
    try:
        return TypeAdapter(MigrationSourceValue).validate_python(
            {"type": type, "kind": kind, **values}
        )
    except ValidationError as exc:
        raise MigrationSourceError("invalid migration source selector or connection") from exc


@dataclass(frozen=True, slots=True)
class MigrationDestination:
    """Destination configuration for a migration."""

    table: str
    write_mode: str = "append"
    unique_key: str | None = None
    schema_policy: str = "strict"


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
    source: MigrationSourceValue
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
