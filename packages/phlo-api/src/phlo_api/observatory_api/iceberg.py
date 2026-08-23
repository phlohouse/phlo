"""Iceberg Catalog API Router.

Endpoints for querying Iceberg tables via Trino.
Provides table listing, schema info, and metadata.

This module enables data exploration by exposing Iceberg table metadata
through Trino, including table listings, column schemas, row counts,
and storage metrics. Tables are classified by medallion layer (bronze,
silver, gold, publish) based on naming conventions.

Key Endpoints:
    GET /tables: List all tables in the catalog.
    GET /tables/{table}/schema: Get column schema for a table.
    GET /tables/{table}/row-count: Get estimated row count.
    GET /tables/{table}/metadata: Get combined table metadata.

Environment Variables:
    PHLO_QUERY_CATALOG: Default Trino catalog.
    PHLO_DEFAULT_REF: Default schema/branch.

Example:
    Listing tables in the warehouse:

    .. code-block:: bash

        curl "http://localhost:4000/api/iceberg/tables?branch=main"

"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from phlo.logging import get_logger
from phlo_api.observatory_api.trino import (
    QueryExecutionError,
    execute_trino_query,
    quote_identifier,
    resolve_default_catalog,
    resolve_default_ref,
    resolve_table_discovery_schemas,
)

logger = get_logger(__name__)

router = APIRouter(tags=["iceberg"])

# Simple in-memory cache (can be replaced with Redis later)
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL_TABLES = 60.0  # 1 minute
CACHE_TTL_SCHEMA = 300.0  # 5 minutes


def _cache_get(key: str, ttl: float) -> Any | None:
    """Get a cached value when still valid.

    Returns the cached value for ``key`` when younger than ``ttl``
    seconds; expired entries are evicted and None returned.

    """
    entry = _cache.get(key)
    if not entry:
        return None
    timestamp, value = entry
    if time.time() - timestamp > ttl:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    """Store a value in the in-memory cache.

    Records ``value`` under ``key`` with the current timestamp.
    """
    _cache[key] = (time.time(), value)


# --- Pydantic Models ---

Layer = Literal["bronze", "silver", "gold", "publish", "unknown"]


class IcebergTable(BaseModel):
    """Represents an Iceberg table exposed by Trino.

    Fields: ``catalog`` (Trino catalog name), ``schema_name`` (Trino
    schema), ``name``, the fully qualified ``full_name``, and the inferred
    medallion ``layer``.
    """

    catalog: str
    schema_name: str  # 'schema' is reserved in Pydantic
    name: str
    full_name: str
    layer: Layer


class TableColumn(BaseModel):
    """Represents a column in a table schema.

    Fields: column ``name``, SQL ``type`` string, ``nullable`` flag, and an
    optional ``comment`` from catalog metadata.
    """

    name: str
    type: str
    nullable: bool
    comment: str | None = None


class TableMetadata(BaseModel):
    """Represents table metadata for observatory responses.

    Fields: the table descriptor, its column definitions, and optional row
    count and last-modified timestamp when available.
    """

    table: IcebergTable
    columns: list[TableColumn]
    row_count: int | None = None
    last_modified: str | None = None


class IcebergCompatibility(BaseModel):
    """Compatibility contract for Phlo's Iceberg 1.11 lakehouse surface."""

    target: str
    rest_catalog: dict[str, str]
    engines: dict[str, dict[str, Any]]
    checks: list[str]


# --- Layer Inference ---


def _load_capability_registry() -> Any | None:
    """Load the capability registry when the core package is available."""
    try:
        from phlo.capabilities import get_capability_registry
    except Exception:  # noqa: BLE001 - optional in stripped API deployments
        return None
    return get_capability_registry()


def _compatibility_from_capabilities(registry: Any | None) -> IcebergCompatibility:
    """Build lakehouse compatibility from registered provider capability metadata."""
    target = "unavailable"
    rest_catalog: dict[str, str] = {}
    engines: dict[str, dict[str, Any]] = {}
    checks: list[str] = []

    if registry is None:
        return IcebergCompatibility(
            target=target,
            rest_catalog=rest_catalog,
            engines=engines,
            checks=checks,
        )

    for family in ("table_store", "catalog", "query_engine"):
        list_specs = getattr(registry, "list", None)
        if not callable(list_specs):
            continue
        for spec in list_specs(family):
            metadata = getattr(spec, "metadata", {})
            if not isinstance(metadata, Mapping):
                continue
            compatibility = metadata.get("compatibility")
            if not isinstance(compatibility, Mapping):
                continue
            candidate_target = compatibility.get("target")
            if isinstance(candidate_target, str) and candidate_target:
                target = candidate_target
            compatibility_rest_catalog = compatibility.get("rest_catalog")
            if isinstance(compatibility_rest_catalog, Mapping):
                rest_catalog.update(
                    {
                        str(key): str(value)
                        for key, value in compatibility_rest_catalog.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
                )
            compatibility_engines = compatibility.get("engines")
            if isinstance(compatibility_engines, Mapping):
                for engine_name, engine_metadata in compatibility_engines.items():
                    if isinstance(engine_name, str) and isinstance(engine_metadata, Mapping):
                        engines[engine_name] = dict(engine_metadata)
            compatibility_checks = compatibility.get("checks")
            if isinstance(compatibility_checks, list):
                for check in compatibility_checks:
                    if isinstance(check, str) and check not in checks:
                        checks.append(check)

    return IcebergCompatibility(
        target=target,
        rest_catalog=rest_catalog,
        engines=engines,
        checks=checks,
    )


def infer_layer(name: str) -> Layer:
    """Infer the medallion layer for a table from its name prefixes.

    Recognizes dlt_/stg_/fct_/dim_/mrt_/publish_ prefixes plus "raw" and
    "staging" substrings; returns "unknown" when nothing matches.
    """
    lower = name.lower()

    # Bronze: raw ingestion tables from DLT
    if lower.startswith("dlt_"):
        return "bronze"
    # Silver: staged/cleaned tables
    if lower.startswith("stg_"):
        return "silver"
    # Gold: curated fact/dimension tables
    if lower.startswith("fct_") or lower.startswith("dim_"):
        return "gold"
    # Publish: mart tables for BI consumption
    if lower.startswith("mrt_") or lower.startswith("publish_"):
        return "publish"
    # Fallback checks
    if "raw" in lower:
        return "bronze"
    if "staging" in lower:
        return "silver"
    return "unknown"


def infer_layer_from_schema(schema: str, table_name: str) -> Layer:
    """Infer the medallion layer from the table name first, then the schema
    name (bronze/raw, silver/staging, gold/curated, publish/marts); returns
    "unknown" when neither matches.
    """
    # First try table name (most reliable)
    from_table = infer_layer(table_name)
    if from_table != "unknown":
        return from_table

    # Fall back to schema name
    s = schema.lower()
    if s in ("bronze", "raw"):
        return "bronze"
    if s in ("silver", "staging"):
        return "silver"
    if s in ("gold", "curated"):
        return "gold"
    if s in ("publish", "marts"):
        return "publish"

    return "unknown"


# --- Table Fetching ---


async def fetch_tables(
    branch: str | None,
    catalog: str,
    schemas_to_query: list[str],
    trino_url: str | None,
    timeout_ms: int,
) -> list[IcebergTable] | dict[str, str]:
    """Fetch tables from the given Iceberg schemas via Trino.

    Queries each schema in ``schemas_to_query`` under ``catalog``,
    deduplicating tables by bare name across schemas. Returns the table
    list, or an error dictionary when every schema query fails. Per-schema
    errors are collected into a combined error message.
    """
    all_tables: list[IcebergTable] = []
    seen_tables: set[str] = set()
    errors: list[str] = []

    # Deduped by bare table name across schemas: when the same name appears in
    # several queried schemas, only the first schema's entry is kept.
    for schema in schemas_to_query:
        sql = f"SHOW TABLES FROM {quote_identifier(catalog)}.{quote_identifier(schema)}"
        result = await execute_trino_query(sql, catalog, schema, trino_url, timeout_ms)

        if isinstance(result, QueryExecutionError):
            errors.append(f"{schema}: {result.error}")
            continue

        for row in result["rows"]:
            table_name = row.get("Table") or row.get("table_name") or row.get("tableName")
            if table_name and table_name not in seen_tables:
                seen_tables.add(table_name)
                all_tables.append(
                    IcebergTable(
                        catalog=catalog,
                        schema_name=schema,
                        name=table_name,
                        full_name=f"{quote_identifier(catalog)}.{quote_identifier(schema)}.{quote_identifier(table_name)}",
                        layer=infer_layer_from_schema(schema, table_name),
                    )
                )

    # If no tables found in standard schemas, try branch as schema
    if not all_tables and errors and branch and branch not in schemas_to_query:
        # Try branch as the schema name
        sql = f"SHOW TABLES FROM {quote_identifier(catalog)}.{quote_identifier(branch)}"
        result = await execute_trino_query(sql, catalog, branch, trino_url, timeout_ms)

        if isinstance(result, QueryExecutionError):
            return {"error": "; ".join(errors)}

        for row in result["rows"]:
            table_name = row.get("Table") or row.get("table_name") or row.get("tableName")
            if table_name:
                all_tables.append(
                    IcebergTable(
                        catalog=catalog,
                        schema_name=branch,
                        name=table_name,
                        full_name=f"{quote_identifier(catalog)}.{quote_identifier(branch)}.{quote_identifier(table_name)}",
                        layer=infer_layer(table_name),
                    )
                )

    if not all_tables and errors:
        return {"error": "; ".join(errors)}

    # Sort by layer then name
    layer_order = {"bronze": 0, "silver": 1, "gold": 2, "publish": 3, "unknown": 4}
    all_tables.sort(key=lambda t: (layer_order[t.layer], t.name))

    return all_tables


async def fetch_table_schema(
    table: str,
    schema: str,
    catalog: str,
    trino_url: str | None = None,
    timeout_ms: int = 30000,
) -> list[TableColumn] | dict[str, str]:
    """Fetch a table's column definitions from Trino via DESCRIBE.

    Returns the column list, or an error dictionary when the query fails.
    ``trino_url`` optionally overrides the Trino endpoint and
    ``timeout_ms`` bounds the query.
    """
    sql = (
        f"DESCRIBE {quote_identifier(catalog)}.{quote_identifier(schema)}.{quote_identifier(table)}"
    )
    result = await execute_trino_query(sql, catalog, schema, trino_url, timeout_ms)

    if isinstance(result, QueryExecutionError):
        return {"error": result.error}

    columns = []
    for row in result["rows"]:
        col_name = row.get("Column") or row.get("column_name")
        col_type = row.get("Type") or row.get("data_type") or "unknown"
        extra = row.get("Extra") or ""
        comment = row.get("Comment") or None

        if col_name:
            columns.append(
                TableColumn(
                    name=col_name,
                    type=col_type,
                    nullable="NOT NULL" not in extra.upper() if extra else True,
                    comment=comment if comment else None,
                )
            )

    return columns


# --- API Endpoints ---


@router.get("/compatibility", response_model=IcebergCompatibility)
async def get_compatibility() -> IcebergCompatibility:
    """Return lakehouse compatibility expectations from active capabilities."""
    return _compatibility_from_capabilities(_load_capability_registry())


@router.get("/tables", response_model=list[IcebergTable] | dict)
async def get_tables(
    branch: str | None = None,
    catalog: str | None = None,
    preferred_schema: str | None = None,
    trino_url: str | None = None,
    timeout_ms: int = Query(default=30000, le=120000),
) -> list[IcebergTable] | dict[str, str]:
    """Get tables from the Iceberg catalog across configured schemas,
    serving results from the in-memory table cache when fresh.

    Query parameters: ``branch`` (schema/branch fallback), ``catalog``
    (Trino override), ``preferred_schema`` (prioritized in discovery),
    ``trino_url``, and ``timeout_ms``. Configuration errors are caught and
    returned as an error dictionary rather than raised.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        effective_branch = branch
        schemas_to_query = resolve_table_discovery_schemas(preferred_schema, branch)
    except RuntimeError as exc:
        return {"error": str(exc)}

    cache_key = f"tables:{effective_catalog}:{effective_branch}:{','.join(schemas_to_query)}:{trino_url or 'default'}"
    cached = _cache_get(cache_key, CACHE_TTL_TABLES)
    if cached is not None:
        return cached

    result = await fetch_tables(
        effective_branch, effective_catalog, schemas_to_query, trino_url, timeout_ms
    )
    if not isinstance(result, dict):
        _cache_set(cache_key, result)
    return result


@router.get("/tables/{table:path}/schema", response_model=list[TableColumn] | dict)
async def get_table_schema(
    table: str,
    schema: str | None = None,
    branch: str | None = None,
    catalog: str | None = None,
    trino_url: str | None = None,
    timeout_ms: int = Query(default=30000, le=120000),
) -> list[TableColumn] | dict[str, str]:
    """Get a table's column schema with types, nullability, and comments,
    served from the in-memory schema cache when fresh.

    ``table`` may be a bare name or fully qualified path; ``schema``,
    ``branch``, and ``catalog`` resolve the effective location. Errors are
    caught and returned as an error dictionary rather than raised.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        effective_schema = schema or branch or resolve_default_ref()
    except RuntimeError as exc:
        return {"error": str(exc)}

    cache_key = f"schema:{effective_catalog}:{effective_schema}:{table}:{trino_url or 'default'}"
    cached = _cache_get(cache_key, CACHE_TTL_SCHEMA)
    if cached is not None:
        return cached

    result = await fetch_table_schema(
        table, effective_schema, effective_catalog, trino_url, timeout_ms
    )
    if not isinstance(result, dict):
        _cache_set(cache_key, result)
    return result


@router.get("/tables/{table:path}/row-count", response_model=int | dict)
async def get_table_row_count(
    table: str,
    branch: str | None = None,
    catalog: str | None = None,
    trino_url: str | None = None,
    timeout_ms: int = Query(default=30000, le=120000),
) -> int | dict[str, str]:
    """Run COUNT(*) against the table and return the row count.

    Errors are caught and returned as an error dictionary rather than
    raised.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        effective_branch = branch or resolve_default_ref()
    except RuntimeError as exc:
        return {"error": str(exc)}
    sql = f"SELECT COUNT(*) as cnt FROM {quote_identifier(effective_catalog)}.{quote_identifier(effective_branch)}.{quote_identifier(table)}"

    result = await execute_trino_query(
        sql, effective_catalog, effective_branch, trino_url, timeout_ms
    )

    if isinstance(result, QueryExecutionError):
        return {"error": result.error}

    if result["rows"]:
        return int(result["rows"][0].get("cnt", 0))
    return 0


@router.get("/tables/{table:path}/metadata", response_model=TableMetadata | dict)
async def get_table_metadata(
    table: str,
    branch: str | None = None,
    catalog: str | None = None,
    trino_url: str | None = None,
    timeout_ms: int = Query(default=30000, le=120000),
) -> TableMetadata | dict[str, str]:
    """Combine table schema with row count in a single metadata response;
    the row count is best-effort and omitted when it fails. Errors are
    caught and returned as an error dictionary rather than raised.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        effective_branch = branch or resolve_default_ref()
    except RuntimeError as exc:
        return {"error": str(exc)}

    # Get schema
    schema_result = await fetch_table_schema(
        table, effective_branch, effective_catalog, trino_url, timeout_ms
    )
    if isinstance(schema_result, dict) and "error" in schema_result:
        return schema_result

    # Get row count (optional)
    row_count = None
    try:
        count_result = await get_table_row_count(
            table, effective_branch, effective_catalog, trino_url, timeout_ms
        )
        if isinstance(count_result, int):
            row_count = count_result
    except Exception:
        pass  # Row count is optional

    return TableMetadata(
        table=IcebergTable(
            catalog=effective_catalog,
            schema_name=effective_branch,
            name=table,
            full_name=f"{quote_identifier(effective_catalog)}.{quote_identifier(effective_branch)}.{quote_identifier(table)}",
            layer=infer_layer(table),
        ),
        columns=schema_result,  # type: ignore
        row_count=row_count,
    )
