"""Logical relation references for workflow authors.

``ref`` resolves an asset name through the capability registry into a
LogicalRelation; rendering quotes each physical segment with ANSI
double-quote escaping and falls back to the bare asset key when no physical
metadata is known.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from phlo.capabilities.registry import CapabilityRegistry, get_capability_registry
from phlo.capabilities.specs import AssetSpec


def quote_identifier(identifier: str) -> str:
    """Render one SQL identifier segment with ANSI double-quote escaping."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


@dataclass(frozen=True, slots=True)
class LogicalRelation:
    """A logical asset reference plus optional physical relation metadata."""

    asset_key: str
    catalog: str | None = None
    schema: str | None = None
    table: str | None = None
    relation: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_resolved(self) -> bool:
        """Whether this reference has physical relation metadata."""
        return any((self.catalog, self.schema, self.table, self.relation))

    def render(self) -> str:
        """Render a SQL relation identifier using known physical metadata."""
        if self.relation:
            return self.relation
        parts = [part for part in (self.catalog, self.schema, self.table) if part]
        if not parts:
            return self.asset_key
        return ".".join(quote_identifier(part) for part in parts)

    def __str__(self) -> str:
        return self.render()

    def __repr__(self) -> str:
        parts = [f"asset_key={self.asset_key!r}"]
        if self.catalog is not None:
            parts.append(f"catalog={self.catalog!r}")
        if self.schema is not None:
            parts.append(f"schema={self.schema!r}")
        if self.table is not None:
            parts.append(f"table={self.table!r}")
        if self.relation is not None:
            parts.append(f"relation={self.relation!r}")
        return f"{type(self).__name__}({', '.join(parts)})"


def ref(
    name: str,
    *,
    registry: CapabilityRegistry | None = None,
    discover: bool = True,
) -> LogicalRelation:
    """Create a logical reference to a named asset."""
    return _resolve_logical_relation(name, registry=registry, discover=discover)


def source(
    source_name: str,
    table_name: str,
    *,
    registry: CapabilityRegistry | None = None,
    discover: bool = True,
) -> LogicalRelation:
    """Create a logical reference to a dbt-style source table."""
    return _resolve_logical_relation(
        f"{source_name}.{table_name}", registry=registry, discover=discover
    )


def _resolve_logical_relation(
    asset_key: str,
    *,
    registry: CapabilityRegistry | None,
    discover: bool,
) -> LogicalRelation:
    # An asset_key with no registered AssetSpec resolves to an unresolved
    # LogicalRelation instead of failing; render() then falls back to the bare key.
    capability_registry = registry or get_capability_registry()
    if discover:
        _discover_capabilities()

    for spec in capability_registry.list("asset"):
        if isinstance(spec, AssetSpec) and spec.key == asset_key:
            return _relation_from_asset_spec(spec)
    return LogicalRelation(asset_key=asset_key)


def _relation_from_asset_spec(spec: AssetSpec) -> LogicalRelation:
    metadata = dict(spec.metadata)
    catalog = _first_metadata_value(metadata, "catalog", "database")
    schema = _first_metadata_value(metadata, "schema", "namespace")
    table = _first_metadata_value(metadata, "table", "table_name")
    relation = _first_metadata_value(metadata, "relation", "relation_name")
    return LogicalRelation(
        asset_key=spec.key,
        catalog=catalog,
        schema=schema,
        table=table,
        relation=relation,
        metadata=metadata,
    )


def _first_metadata_value(metadata: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _discover_capabilities() -> None:
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()
