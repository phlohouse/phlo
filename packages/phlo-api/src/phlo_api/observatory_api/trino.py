"""Trino API Router.

Endpoints for executing queries against Trino via HTTP API.
Enables data preview, column profiling, and table metrics in Observatory.

This module provides a comprehensive interface to Trino for data exploration,
including table previews, column profiling, row lookups, and ad-hoc queries
with guardrails. Dataset authorization is enforced when an authorization
backend is configured, and strict mode fails closed when it is not.

Endpoints cover connectivity checks, paginated table previews, column statistics,
table-level metrics, ad-hoc queries with guardrails, filtered queries, and
single-row lookups by phlo_row_id. Configuration is read from the
PHLO_QUERY_ENGINE_URL, PHLO_QUERY_CATALOG, and PHLO_DEFAULT_REF environment
variables.

Example:
    Previewing table data:

    .. code-block:: bash

        curl "http://localhost:4000/api/trino/preview/warehouse.main.events?limit=10"

"""

from __future__ import annotations

import asyncio
import os
import re
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from phlo.capabilities import resolve_capability
from phlo.capabilities.discovery import discover_capabilities
from phlo.config.env import project_env_value
from phlo.config.network import resolve_url
from phlo.logging import get_bound_correlation_context, get_logger
from phlo_api.api.authorization import check_dataset_query, check_dataset_read
from phlo_api.observatory_api.trino_sql import (
    is_probably_qualified_table,
    qualify_table_name,
    quote_identifier,
    sql_literal,
    validate_read_only_query,
)

logger = get_logger(__name__)

router = APIRouter(tags=["trino"])

_DEFAULT_QUERY_ENGINE_ENV = "PHLO_QUERY_ENGINE"
_DEFAULT_TRINO_CAPABILITY_NAME = "trino"
_QUERY_ENGINE_URL_ENV = "PHLO_QUERY_ENGINE_URL"
_DISCOVERY_SCHEMAS_ENV = "PHLO_API_DISCOVERY_SCHEMAS"
_SAFE_ROW_ID_RE = re.compile(r"^[a-zA-Z0-9_.\-:]+$")
_TRINO_USER_ENV = "TRINO_USER"
_TRINO_ROLE_ENV = "TRINO_ROLE"


def _externalize_trino_uri(uri: str, base_url: str) -> str:
    """Rewrite Trino response URIs through the configured client-facing base URL."""
    parsed_uri = urlsplit(uri)
    parsed_base = urlsplit(base_url)
    if not parsed_uri.scheme or not parsed_uri.netloc:
        return uri
    base_path = parsed_base.path.rstrip("/")
    uri_path = parsed_uri.path if parsed_uri.path.startswith("/") else f"/{parsed_uri.path}"
    return urlunsplit(
        (
            parsed_base.scheme or parsed_uri.scheme,
            parsed_base.netloc or parsed_uri.netloc,
            f"{base_path}{uri_path}",
            parsed_uri.query,
            parsed_uri.fragment,
        )
    )


def _build_trino_headers(
    *,
    catalog: str | None = None,
    schema: str | None = None,
    include_catalog_context: bool = True,
) -> dict[str, str]:
    """Build Trino request headers with scoped identity and correlation metadata."""
    correlation_id = get_bound_correlation_context().request_id
    headers = {
        "X-Trino-User": os.environ.get(_TRINO_USER_ENV, "observatory"),
    }
    if include_catalog_context and catalog:
        headers["X-Trino-Catalog"] = catalog
    if include_catalog_context and schema:
        headers["X-Trino-Schema"] = schema
    role = os.environ.get(_TRINO_ROLE_ENV)
    if role:
        headers["X-Trino-Role"] = f"system={role}"
    if correlation_id:
        headers["X-Trino-Extra-Credential"] = f"phlo.correlation_id={correlation_id}"
    return headers


def _resolve_query_engine() -> Any | None:
    """Resolve the query engine capability, returning None when none is available."""
    discover_capabilities()
    return resolve_capability(
        "query_engine",
        project_env_value(_DEFAULT_QUERY_ENGINE_ENV) or _DEFAULT_TRINO_CAPABILITY_NAME,
    )


def resolve_trino_url(override: str | None = None) -> str:
    """Resolve the query-engine URL from override, environment, or capability metadata.

    Raises: RuntimeError when no query-engine URL is configured.
    """
    env_url = project_env_value(_QUERY_ENGINE_URL_ENV) or project_env_value("TRINO_URL")
    if override and override.strip():
        return resolve_url(override, port_env_var="TRINO_PORT")
    if env_url:
        return resolve_url(env_url, port_env_var="TRINO_PORT")

    resolution = _resolve_query_engine()
    if resolution is not None:
        for key in ("url", "http_url", "endpoint"):
            value = resolution.metadata.get(key)
            if isinstance(value, str) and value:
                return resolve_url(value, port_env_var="TRINO_PORT")

        host = resolution.metadata.get("host")
        port = resolution.metadata.get("port")
        scheme = (
            resolution.metadata.get("scheme") or resolution.metadata.get("http_scheme") or "http"
        )
        if isinstance(host, str) and host and port is not None:
            return resolve_url(f"{scheme}://{host}:{port}", port_env_var="TRINO_PORT")

    raise RuntimeError(
        "No query-engine URL is configured. Set PHLO_QUERY_ENGINE_URL or TRINO_URL, "
        "or expose query_engine capability metadata with host/port or a URL."
    )


def resolve_default_catalog() -> str:
    """Resolve the default catalog from query-engine capability metadata or environment.

    Raises: RuntimeError when no default catalog is configured.
    """
    env_catalog = project_env_value("PHLO_QUERY_CATALOG") or project_env_value("TRINO_CATALOG")
    if env_catalog:
        return env_catalog

    resolution = _resolve_query_engine()
    if resolution is not None:
        for key in ("default_catalog", "catalog", "catalog_name"):
            value = resolution.metadata.get(key)
            if isinstance(value, str) and value:
                return value
    raise RuntimeError(
        "No default query catalog is configured. Set PHLO_QUERY_CATALOG or expose "
        "query_engine capability metadata with a default_catalog."
    )


def resolve_default_ref() -> str:
    """Resolve the default ref/schema context from metadata or environment.

    Raises: RuntimeError when no default ref is configured.
    """
    env_ref = project_env_value("PHLO_DEFAULT_REF") or project_env_value("NESSIE_DEFAULT_REF")
    if env_ref:
        return env_ref

    resolution = _resolve_query_engine()
    if resolution is not None:
        for key in ("default_ref", "ref", "catalog_ref"):
            value = resolution.metadata.get(key)
            if isinstance(value, str) and value:
                return value
    raise RuntimeError(
        "No default ref is configured. Set PHLO_DEFAULT_REF or expose query_engine "
        "capability metadata with a default_ref."
    )


def resolve_table_discovery_schemas(
    preferred_schema: str | None = None,
    branch: str | None = None,
) -> list[str]:
    """Resolve schemas to query when listing catalog tables.

    Prefers an explicit schema, then the branch as a fallback schema, then configured
    discovery schemas. Raises: RuntimeError when no table-discovery schemas are configured.
    """
    if preferred_schema and preferred_schema.strip():
        return [preferred_schema.strip()]

    env_schemas = project_env_value(_DISCOVERY_SCHEMAS_ENV)
    if env_schemas:
        schemas = [schema.strip() for schema in env_schemas.split(",") if schema.strip()]
        if schemas:
            return schemas

    resolution = _resolve_query_engine()
    if resolution is not None:
        for key in ("discovery_schemas", "table_schemas", "schemas"):
            value = resolution.metadata.get(key)
            if isinstance(value, list):
                schemas = [item.strip() for item in value if isinstance(item, str) and item.strip()]
                if schemas:
                    return schemas

    if branch and branch.strip():
        return [branch.strip()]

    try:
        return [resolve_default_ref()]
    except RuntimeError as exc:
        raise RuntimeError(
            "No table-discovery schemas are configured. Set PHLO_API_DISCOVERY_SCHEMAS, "
            "pass a branch/preferred_schema, or expose query_engine capability metadata "
            "with discovery_schemas."
        ) from exc


# --- Pydantic Models ---


class TrinoConnectionStatus(BaseModel):
    """Connection health and version details for the Trino endpoint."""

    connected: bool
    error: str | None = None
    cluster_version: str | None = None


class DataRow(BaseModel):
    """A row of data with dynamic columns."""

    model_config = {"extra": "allow"}


class DataPreviewResult(BaseModel):
    """Paginated table preview payload returned to the UI."""

    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]
    total_rows: int | None = None
    has_more: bool


class ColumnProfile(BaseModel):
    """Per-column profiling metrics for a sampled table."""

    column: str
    type: str
    null_count: int
    null_percentage: float
    distinct_count: int
    min_value: str | None = None
    max_value: str | None = None
    sample_values: list[str] | None = None


class TableMetrics(BaseModel):
    """High-level row and storage metrics for a table."""

    row_count: int
    size_bytes: int | None = None
    last_modified: str | None = None
    partition_count: int | None = None


class QueryExecutionResult(BaseModel):
    """Normalized successful query response shape."""

    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]
    has_more: bool
    effective_query: str | None = None


class QueryExecutionError(BaseModel):
    """Structured query execution failure payload."""

    ok: bool = False
    error: str
    kind: str  # 'timeout', 'trino', 'validation'


# --- Core Query Execution ---


async def execute_trino_query(
    query: str,
    catalog: str | None = None,
    schema: str | None = None,
    trino_url: str | None = None,
    timeout_ms: int = 30000,
) -> dict[str, Any] | QueryExecutionError:
    """Execute a query against Trino and wait for results.

    Submits the query, polls until completion, and returns a dictionary with columns,
    column_types, and rows, or a QueryExecutionError on failure. Raises: RuntimeError
    when URL resolution fails; httpx.TimeoutException when the query times out.
    """
    try:
        url = resolve_trino_url(trino_url)
        timeout = timeout_ms / 1000.0
        effective_catalog = catalog or resolve_default_catalog()
        effective_schema = schema or resolve_default_ref()
        start_time = monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Submit query
            response = await client.post(
                f"{url}/v1/statement",
                content=query,
                headers={
                    "Content-Type": "text/plain",
                    **_build_trino_headers(
                        catalog=effective_catalog,
                        schema=effective_schema,
                    ),
                },
            )

            if response.status_code != 200:
                return QueryExecutionError(error=f"Trino error: {response.text}", kind="trino")

            result = response.json()

            # Poll until query completes
            max_polls = 100
            polls = 0
            all_data: list[list[Any]] = []
            columns: list[str] = []
            column_types: list[str] = []

            while result.get("nextUri") and polls < max_polls:
                polls += 1
                elapsed = monotonic() - start_time
                remaining = timeout - elapsed
                if remaining <= 0:
                    return QueryExecutionError(error="Query timed out", kind="timeout")
                await asyncio.sleep(0.1)

                poll_response = await client.get(
                    _externalize_trino_uri(str(result["nextUri"]), url),
                    headers=_build_trino_headers(include_catalog_context=False),
                    timeout=remaining,
                )

                if poll_response.status_code != 200:
                    return QueryExecutionError(
                        error=f"Trino poll error: {poll_response.text}", kind="trino"
                    )

                result = poll_response.json()

                if result.get("columns") and not columns:
                    columns = [c["name"] for c in result["columns"]]
                    column_types = [c["type"] for c in result["columns"]]

                if result.get("data"):
                    all_data.extend(result["data"])

                if result.get("error"):
                    return QueryExecutionError(
                        error=result["error"].get("message", "Query failed"),
                        kind="trino",
                    )

            # Convert to row dicts
            rows = [{col: row[idx] for idx, col in enumerate(columns)} for row in all_data]

            return {"columns": columns, "column_types": column_types, "rows": rows}

    except RuntimeError as exc:
        return QueryExecutionError(error=str(exc), kind="validation")
    except httpx.TimeoutException:
        return QueryExecutionError(error="Query timed out", kind="timeout")
    except Exception as e:
        logger.exception("Trino query failed")
        return QueryExecutionError(error=str(e), kind="trino")


# --- API Endpoints ---


@router.get("/connection", response_model=TrinoConnectionStatus)
async def check_connection(trino_url: str | None = None) -> TrinoConnectionStatus:
    """Check if Trino is reachable, reporting connection state and cluster version."""
    try:
        url = resolve_trino_url(trino_url)
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/v1/info")

            if response.status_code != 200:
                return TrinoConnectionStatus(
                    connected=False,
                    error=f"HTTP {response.status_code}: {response.reason_phrase}",
                )

            info = response.json()
            return TrinoConnectionStatus(
                connected=True,
                cluster_version=info.get("nodeVersion", {}).get("version", "unknown"),
            )
    except Exception as e:
        return TrinoConnectionStatus(connected=False, error=str(e))


@router.get("/preview/{table:path}", response_model=DataPreviewResult | dict)
async def preview_data(
    request: Request,
    table: str,
    branch: str | None = None,
    catalog: str | None = None,
    schema: str | None = None,
    limit: int = Query(default=100, le=5000),
    offset: int = Query(default=0, ge=0),
    trino_url: str | None = None,
    timeout_ms: int = Query(default=30000, le=120000),
) -> DataPreviewResult | dict[str, str]:
    """Preview data from a table with pagination.

    Returns a DataPreviewResult with columns, types, and rows, or an error dictionary
    when resolution or execution fails.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        effective_schema = schema or branch or resolve_default_ref()
    except RuntimeError as exc:
        return {"error": str(exc)}

    resolved_table = (
        table
        if is_probably_qualified_table(table)
        else qualify_table_name(effective_catalog, effective_schema, table)
    )

    check_dataset_read(request, resolved_table)

    # Build query
    if offset > 0:
        query = f"SELECT * FROM {resolved_table} OFFSET {offset} ROWS FETCH FIRST {limit} ROWS ONLY"
    else:
        query = f"SELECT * FROM {resolved_table} LIMIT {limit}"

    result = await execute_trino_query(
        query, effective_catalog, effective_schema, trino_url, timeout_ms
    )

    if isinstance(result, QueryExecutionError):
        return {"error": result.error}

    return DataPreviewResult(
        columns=result["columns"],
        column_types=result["column_types"],
        rows=result["rows"],
        has_more=len(result["rows"]) == limit,
    )


@router.get("/profile/{table:path}/{column}", response_model=ColumnProfile | dict)
async def profile_column(
    table: str,
    column: str,
    branch: str | None = None,
    catalog: str | None = None,
    schema: str | None = None,
    trino_url: str | None = None,
    timeout_ms: int = Query(default=30000, le=120000),
) -> ColumnProfile | dict[str, str]:
    """Get column statistics and profiling metrics for a table column.

    Returns a ColumnProfile with statistics, or an error dictionary when resolution
    or execution fails.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        effective_schema = schema or branch or resolve_default_ref()
    except RuntimeError as exc:
        return {"error": str(exc)}

    resolved_table = (
        table
        if is_probably_qualified_table(table)
        else qualify_table_name(effective_catalog, effective_schema, table)
    )

    query = f"""
        SELECT
            COUNT(*) as total_count,
            COUNT("{column}") as non_null_count,
            COUNT(DISTINCT "{column}") as distinct_count,
            MIN(CAST("{column}" AS VARCHAR)) as min_value,
            MAX(CAST("{column}" AS VARCHAR)) as max_value
        FROM {resolved_table}
    """

    result = await execute_trino_query(
        query, effective_catalog, effective_schema, trino_url, timeout_ms
    )

    if isinstance(result, QueryExecutionError):
        return {"error": result.error}

    if not result["rows"]:
        return {"error": "No data returned from profile query"}

    row = result["rows"][0]
    total_count = int(row.get("total_count") or 0)
    non_null_count = int(row.get("non_null_count") or 0)
    null_count = total_count - non_null_count

    return ColumnProfile(
        column=column,
        type="unknown",
        null_count=null_count,
        null_percentage=(null_count / total_count * 100) if total_count > 0 else 0,
        distinct_count=int(row.get("distinct_count") or 0),
        min_value=row.get("min_value"),
        max_value=row.get("max_value"),
    )


@router.get("/metrics/{table:path}", response_model=TableMetrics | dict)
async def get_table_metrics(
    table: str,
    branch: str | None = None,
    catalog: str | None = None,
    schema: str | None = None,
    trino_url: str | None = None,
    timeout_ms: int = Query(default=30000, le=120000),
) -> TableMetrics | dict[str, str]:
    """Get table-level metrics such as row count.

    Returns TableMetrics, or an error dictionary when resolution or execution fails.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        effective_schema = schema or branch or resolve_default_ref()
    except RuntimeError as exc:
        return {"error": str(exc)}

    resolved_table = (
        table
        if is_probably_qualified_table(table)
        else qualify_table_name(effective_catalog, effective_schema, table)
    )

    query = f"SELECT COUNT(*) as row_count FROM {resolved_table}"

    result = await execute_trino_query(
        query, effective_catalog, effective_schema, trino_url, timeout_ms
    )

    if isinstance(result, QueryExecutionError):
        return {"error": result.error}

    if not result["rows"]:
        return {"error": "No data returned from count query"}

    return TableMetrics(row_count=int(result["rows"][0].get("row_count") or 0))


@router.post("/query", response_model=QueryExecutionResult | QueryExecutionError)
async def execute_query(
    http_request: Request,
    request: ExecuteQueryRequest,
) -> QueryExecutionResult | QueryExecutionError:
    """Run an arbitrary query with guardrails, returning a result or error payload."""
    try:
        effective_catalog = request.catalog or resolve_default_catalog()
        effective_schema = request.schema_name or request.branch or resolve_default_ref()
    except RuntimeError as exc:
        return QueryExecutionError(error=str(exc), kind="validation")

    check_dataset_query(http_request, f"{effective_catalog}.{effective_schema}")

    if request.read_only_mode:
        validation_error = validate_read_only_query(request.query)
        if validation_error:
            return QueryExecutionError(error=validation_error, kind="validation")

    result = await execute_trino_query(
        request.query,
        effective_catalog,
        effective_schema,
        request.trino_url,
        request.timeout_ms,
    )

    if isinstance(result, QueryExecutionError):
        return result

    return QueryExecutionResult(
        columns=result["columns"],
        column_types=result["column_types"],
        rows=result["rows"],
        has_more=False,
        effective_query=request.query,
    )


class ExecuteQueryRequest(BaseModel):
    """Payload for executing an ad-hoc Trino query."""

    query: str
    branch: str | None = None
    catalog: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    trino_url: str | None = None
    timeout_ms: int = 30000
    read_only_mode: bool = True
    default_limit: int = 100
    max_limit: int = 5000


class QueryWithFiltersRequest(BaseModel):
    """Payload for filtered table preview queries."""

    table_name: str
    schema_name: str = Field(alias="schema")
    catalog: str | None = None
    filters: dict[str, Any]
    limit: int = 10
    trino_url: str | None = None
    timeout_ms: int = 30000


@router.post("/query-with-filters", response_model=DataPreviewResult | dict)
async def query_with_filters(
    request: QueryWithFiltersRequest,
) -> DataPreviewResult | dict[str, str]:
    """Query a table with simple equality filters.

    Returns a DataPreviewResult with the filtered rows, or an error dictionary when
    validation or execution fails.
    """
    try:
        catalog = request.catalog or resolve_default_catalog()
        table = request.table_name
        schema = request.schema_name

        if not request.filters:
            return DataPreviewResult(columns=[], column_types=[], rows=[], has_more=False)

        resolved_table = qualify_table_name(catalog, schema, table)

        where_parts: list[str] = []
        for column, value in request.filters.items():
            quoted_column = quote_identifier(column)
            if value is None:
                where_parts.append(f"{quoted_column} IS NULL")
            else:
                where_parts.append(f"{quoted_column} = {sql_literal(value)}")

        where_clause = " AND ".join(where_parts)
        limit = int(request.limit)
        if limit <= 0 or limit > 5000:
            raise ValueError("limit must be between 1 and 5000")

        query = f"SELECT * FROM {resolved_table} WHERE {where_clause} LIMIT {limit}"
        result = await execute_trino_query(
            query,
            catalog=catalog,
            schema=schema,
            trino_url=request.trino_url,
            timeout_ms=request.timeout_ms,
        )

        if isinstance(result, QueryExecutionError):
            return {"error": result.error}

        return DataPreviewResult(
            columns=result["columns"],
            column_types=result["column_types"],
            rows=result["rows"],
            has_more=False,
        )
    except ValueError as e:
        return {"error": str(e)}


@router.get("/row/{table:path}/{row_id}", response_model=DataPreviewResult | dict)
async def get_row_by_id(
    table: str,
    row_id: str,
    catalog: str | None = None,
    schema: str | None = None,
    trino_url: str | None = None,
    timeout_ms: int = Query(default=30000, le=120000),
) -> DataPreviewResult | dict[str, str]:
    """Get a single row by its _phlo_row_id.

    Returns a DataPreviewResult with the matching row, or an error dictionary when
    resolution or execution fails.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        effective_schema = schema or resolve_default_ref()
    except RuntimeError as exc:
        return {"error": str(exc)}

    resolved_table = (
        table
        if is_probably_qualified_table(table)
        else qualify_table_name(effective_catalog, effective_schema, table)
    )

    # row_id is interpolated straight into the SQL literal below; the
    # character whitelist here is what keeps that interpolation injection-safe.
    if not _SAFE_ROW_ID_RE.match(row_id):
        return {"error": "Invalid row_id: contains disallowed characters"}

    query = f"SELECT * FROM {resolved_table} WHERE \"_phlo_row_id\" = '{row_id}' LIMIT 1"

    result = await execute_trino_query(
        query, effective_catalog, effective_schema, trino_url, timeout_ms
    )

    if isinstance(result, QueryExecutionError):
        return {"error": result.error}

    if not result["rows"]:
        return {"error": "Row not found"}

    return DataPreviewResult(
        columns=result["columns"],
        column_types=result["column_types"],
        rows=result["rows"],
        has_more=False,
    )
