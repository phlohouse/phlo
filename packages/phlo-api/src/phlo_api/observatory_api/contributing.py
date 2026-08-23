"""Contributing rows API router.

Moves row-provenance query construction and execution behind phlo-api so
Observatory does not talk to Trino directly for this flow.

This module provides endpoints for tracing data lineage at the row level,
enabling users to identify upstream rows that contributed to downstream
aggregated or transformed records.

Key Endpoints:
    POST /query: Generate SQL query for contributing rows.
    POST /page: Execute query and return paginated results.

Example:
    Finding contributing rows:

    .. code-block:: bash

        curl -X POST http://localhost:4000/api/contributing/query \
          -H "Content-Type: application/json" \
          -d '{
            "downstream_asset_key": "marts/fct_daily_metrics",
            "upstream_asset_key": "bronze/raw_events",
            "row_data": {"date": "2024-01-15"}
          }'

"""

from __future__ import annotations

import math
from typing import Any, Literal, cast

from fastapi import APIRouter
from pydantic import BaseModel, Field

from phlo_api.observatory_api.trino import (
    QueryExecutionError,
    execute_trino_query,
    resolve_default_catalog,
    resolve_default_ref,
)
from phlo_api.observatory_api.trino_sql import quote_identifier

router = APIRouter(tags=["contributing"])

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_PAGE = 200
DEFAULT_SAMPLE_SEED = "phlo"

Primitive = str | int | float | bool | None
ContributingRowsMode = Literal["entity", "aggregate"]


def _asset_pair_is_lineage_related(upstream_asset_key: str, downstream_asset_key: str) -> bool:
    """Validate the requested pair against the authoritative lineage provider."""
    try:
        from phlo_api.observatory_api.lineage import _build_asset_graph, _resolve_lineage_sink

        _assets, edges, _details = _build_asset_graph(_resolve_lineage_sink().get_asset_graph())
    except Exception:
        return False
    return downstream_asset_key in edges.get(upstream_asset_key, ())


class ResolveTableResult(BaseModel):
    """Resolved Iceberg table metadata for contributing-row queries."""

    model_config = {"populate_by_name": True}

    schema_name: str = Field(alias="schema")
    table: str
    full_name: str
    column_types: dict[str, str]


class UpstreamTableRef(BaseModel):
    """Resolved upstream table identifier."""

    model_config = {"populate_by_name": True}

    schema_name: str = Field(alias="schema")
    table: str


class ContributingRowsQueryRequest(BaseModel):
    """Request payload for generating a contributing rows query."""

    model_config = {"extra": "forbid"}

    downstream_asset_key: str
    upstream_asset_key: str
    row_data: dict[str, Any]
    limit: int | None = None
    timeout_ms: int | None = None


class ContributingRowsQueryResponse(BaseModel):
    """Contributing rows query and resolved upstream table."""

    query: str
    upstream: UpstreamTableRef


class ContributingRowsPageRequest(BaseModel):
    """Request payload for paginated contributing rows."""

    model_config = {"extra": "forbid"}

    downstream_asset_key: str
    upstream_asset_key: str
    row_data: dict[str, Any]
    page: int | None = None
    page_size: int | None = None
    timeout_ms: int | None = None


class ContributingRowsPageResponse(BaseModel):
    """Paginated contributing rows payload."""

    mode: ContributingRowsMode
    page: int
    page_size: int
    has_more: bool
    query: str
    upstream: UpstreamTableRef
    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]


EXPLICIT_COLUMN_MAPPINGS: dict[str, dict[str, dict[str, str]]] = {
    "fct_daily_github_metrics": {
        "fct_github_events": {
            "activity_date": "event_date",
            "_phlo_partition_date": "_phlo_partition_date",
        },
    },
    "fct_repository_languages": {
        "fct_repository_stats": {
            "primary_language": "language_category",
            "_phlo_partition_date": "_phlo_partition_date",
        },
    },
    "mrt_github_activity_overview": {
        "fct_daily_github_metrics": {
            "activity_date": "activity_date",
            "_phlo_partition_date": "_phlo_partition_date",
        },
    },
    "mrt_language_distribution": {
        "fct_repository_languages": {
            "primary_language": "primary_language",
            "_phlo_partition_date": "_phlo_partition_date",
        },
    },
    "mrt_contribution_patterns": {
        "fct_github_events": {
            "hour_of_day": "hour_of_day",
            "day_of_week": "day_of_week",
            "_phlo_partition_date": "_phlo_partition_date",
        },
    },
}


def escape_sql_string(value: str) -> str:
    """Escape a string for inclusion in SQL literals."""
    return value.replace("'", "''")


def to_sql_equality(column_name: str, column_type: str, value: Primitive) -> str | None:
    """Build a safe equality predicate for a typed Trino column."""
    if value is None:
        return None

    normalized_type = column_type.lower()

    # Trino renders timestamp values with exactly six fractional digits.
    # Normalize the incoming value to that shape and compare as varchar so a
    # precision mismatch on either side cannot silently break the predicate.
    if normalized_type.startswith("timestamp") or normalized_type.startswith("time"):
        raw = str(value)
        normalized = raw
        if "." in raw:
            head, tail = raw.split(".", 1)
            normalized = f"{head}.{tail[:6].ljust(6, '0')}"
        if len(normalized) == 19 and normalized[4] == "-" and normalized[10] == " ":
            normalized = f"{normalized}.000000"
        return (
            f"cast({quote_identifier(column_name)} as varchar) = '{escape_sql_string(normalized)}'"
        )

    if normalized_type.startswith("varchar") or normalized_type == "varbinary":
        return f"{quote_identifier(column_name)} = '{escape_sql_string(str(value))}'"

    if normalized_type.startswith(
        ("bigint", "integer", "smallint", "tinyint", "double", "real", "decimal")
    ):
        numeric = str(value).strip()
        try:
            parsed = float(numeric)
        except ValueError:
            return None
        if not math.isfinite(parsed):
            return None
        return f"{quote_identifier(column_name)} = {numeric}"

    if normalized_type == "boolean":
        if isinstance(value, bool):
            return f"{quote_identifier(column_name)} = {'true' if value else 'false'}"
        lower = str(value).lower()
        if lower in {"true", "false"}:
            return f"{quote_identifier(column_name)} = {lower}"
        return None

    if normalized_type == "date":
        as_string = str(value)[:10]
        if len(as_string) == 10 and as_string[4] == "-" and as_string[7] == "-":
            return f"{quote_identifier(column_name)} = date '{as_string}'"
        return None

    return f"cast({quote_identifier(column_name)} as varchar) = '{escape_sql_string(str(value))}'"


def should_use_as_dimension(column_name: str) -> bool:
    """Decide whether a row field is safe to use as a grain predicate."""
    lower = column_name.lower()
    if lower.startswith("_phlo_"):
        return True
    if lower.endswith("_date") or lower.endswith("_name") or lower.endswith("_id"):
        return True
    if any(token in lower for token in ("count", "total", "avg", "score", "ratio", "pct")):
        return False
    if "rank" in lower or lower.startswith("is_"):
        return False
    return True


def to_safe_page_size(page_size: int | None) -> int:
    """Clamp page size to the supported range."""
    if page_size is None:
        return DEFAULT_PAGE_SIZE
    # NaN compares unequal to itself, so this rejects NaN input before int().
    if page_size != page_size:
        return DEFAULT_PAGE_SIZE
    return max(1, min(MAX_PAGE_SIZE, int(page_size)))


def to_safe_page(page: int | None) -> int:
    """Clamp page number to the supported range."""
    if page is None:
        return 0
    # NaN compares unequal to itself, so this rejects NaN input before int().
    if page != page:
        return 0
    return max(0, min(MAX_PAGE, int(page)))


def build_deterministic_order_expression(column_types: dict[str, str]) -> str:
    """Build a stable pseudo-random order expression for aggregate samples."""
    seed_sql = escape_sql_string(DEFAULT_SAMPLE_SEED)
    columns = list(column_types.keys())

    if "_phlo_row_id" in column_types:
        quoted = quote_identifier("_phlo_row_id")
        return f"xxhash64(to_utf8(concat('{seed_sql}', '|', cast({quoted} as varchar))))"

    order_key_columns = sorted(
        col
        for col in columns
        if not col.lower().startswith("_phlo_")
        and not column_types[col].lower().startswith(("array(", "map(", "row(", "json"))
    )[:5]

    if not order_key_columns:
        return f"xxhash64(to_utf8('{seed_sql}'))"

    concat_parts = ", '|' , ".join(
        f"coalesce(cast({quote_identifier(col)} as varchar), '')" for col in order_key_columns
    )
    return f"xxhash64(to_utf8(concat('{seed_sql}', '|', {concat_parts})))"


def get_table_from_asset_key(asset_key: str) -> str:
    """Extract the table name from an asset key path."""
    return asset_key.split("/")[-1] or asset_key


def _result_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    return [cast(dict[str, Any], row) for row in rows if isinstance(row, dict)]


def _result_columns(payload: dict[str, Any]) -> list[str]:
    columns = payload.get("columns")
    if not isinstance(columns, list):
        return []
    return [str(column) for column in columns]


def _result_column_types(payload: dict[str, Any]) -> list[str]:
    column_types = payload.get("column_types")
    if not isinstance(column_types, list):
        return []
    return [str(column_type) for column_type in column_types]


def build_contributing_rows_query(
    downstream_table_name: str,
    upstream: ResolveTableResult,
    row_data: dict[str, Primitive],
    page_size: int,
    page: int,
) -> tuple[bool, ContributingRowsMode | None, str]:
    """Build the contributing rows SQL query."""
    upstream_cols = upstream.column_types
    predicates: list[str] = []

    row_id = row_data.get("_phlo_row_id")
    if row_id is not None and "_phlo_row_id" in upstream_cols:
        predicate = to_sql_equality("_phlo_row_id", upstream_cols["_phlo_row_id"], row_id)
        if predicate:
            predicates.append(predicate)
    else:
        mappings = EXPLICIT_COLUMN_MAPPINGS.get(downstream_table_name, {}).get(upstream.table, {})

        for down_col, up_col in mappings.items():
            value = row_data.get(down_col)
            column_type = upstream_cols.get(up_col)
            if not column_type:
                continue
            predicate = to_sql_equality(up_col, column_type, value)
            if predicate:
                predicates.append(predicate)

        for column, value in row_data.items():
            if not should_use_as_dimension(column):
                continue
            column_type = upstream_cols.get(column)
            if not column_type:
                continue
            predicate = to_sql_equality(column, column_type, value)
            if predicate:
                predicates.append(predicate)

    unique_predicates = list(dict.fromkeys(predicates))
    if not unique_predicates:
        return (
            False,
            None,
            "No safe predicates could be derived for contributing rows. "
            "Add an explicit mapping for this model pair.",
        )

    where = " and ".join(unique_predicates)
    offset = page * page_size
    limit_plus_one = page_size + 1

    mode: ContributingRowsMode = (
        "entity" if row_id is not None and "_phlo_row_id" in upstream_cols else "aggregate"
    )
    order_expr = (
        quote_identifier("_phlo_row_id")
        if mode == "entity"
        else build_deterministic_order_expression(upstream_cols)
    )
    query = (
        f"SELECT * FROM {upstream.full_name} WHERE {where} "
        f"ORDER BY {order_expr} OFFSET {offset} LIMIT {limit_plus_one}"
    )
    return True, mode, query


async def _execute_trino_or_error(
    query: str,
    catalog: str,
    schema: str,
    trino_url: str | None,
    timeout_ms: int | None,
) -> dict[str, Any] | dict[str, str]:
    result = await execute_trino_query(query, catalog, schema, trino_url, timeout_ms or 30000)
    if isinstance(result, QueryExecutionError):
        return {"error": result.error}
    return result


async def resolve_iceberg_table(
    table_name: str,
    *,
    trino_url: str | None,
    timeout_ms: int | None,
    catalog: str,
) -> ResolveTableResult | None:
    """Resolve schema and columns for an Iceberg table by name."""
    try:
        default_ref = resolve_default_ref()
    except RuntimeError:
        return None

    safe_name = escape_sql_string(table_name)
    schema_query = (
        f"select table_schema from {quote_identifier(catalog)}.information_schema.tables "
        f"where table_name = '{safe_name}'"
    )
    schemas_result = await _execute_trino_or_error(
        schema_query, catalog, default_ref, trino_url, timeout_ms
    )
    if "error" in schemas_result:
        return None

    schema_rows = _result_rows(schemas_result)
    schemas = [
        str(row["table_schema"])
        for row in schema_rows
        if row.get("table_schema") and row.get("table_schema") != "information_schema"
    ]
    if not schemas:
        return None

    preference = ["raw", "bronze", "silver", "gold", "marts", "publish", "main"]
    schemas.sort(
        key=lambda schema: (
            preference.index(schema) if schema in preference else len(preference),
            schema,
        )
    )
    schema = schemas[0]

    columns_query = (
        f"select column_name, data_type from {quote_identifier(catalog)}.information_schema.columns "
        f"where table_schema = '{escape_sql_string(schema)}' and table_name = '{safe_name}'"
    )
    columns_result = await _execute_trino_or_error(
        columns_query,
        catalog,
        default_ref,
        trino_url,
        timeout_ms,
    )
    if "error" in columns_result:
        return None

    column_rows = _result_rows(columns_result)
    column_types = {
        str(row["column_name"]): str(row["data_type"])
        for row in column_rows
        if row.get("column_name") and row.get("data_type")
    }

    return ResolveTableResult(
        schema=schema,
        table=table_name,
        full_name=".".join(
            [quote_identifier(catalog), quote_identifier(schema), quote_identifier(table_name)]
        ),
        column_types=column_types,
    )


@router.post("/query", response_model=ContributingRowsQueryResponse | dict)
async def get_contributing_rows_query(
    request: ContributingRowsQueryRequest,
) -> ContributingRowsQueryResponse | dict[str, str]:
    """Generate the SQL query finding upstream rows that contributed to a downstream row.

    Returns the query and upstream reference, or an error dictionary;
    exceptions are caught and reported in the response rather than raised.

    """
    try:
        catalog = resolve_default_catalog()
    except RuntimeError as exc:
        return {"error": str(exc)}
    if not _asset_pair_is_lineage_related(request.upstream_asset_key, request.downstream_asset_key):
        return {"error": "unrelated_asset_pair"}
    upstream_table_name = get_table_from_asset_key(request.upstream_asset_key)
    downstream_table_name = get_table_from_asset_key(request.downstream_asset_key)

    upstream = await resolve_iceberg_table(
        upstream_table_name,
        trino_url=None,
        timeout_ms=request.timeout_ms,
        catalog=catalog,
    )
    if upstream is None:
        return {"error": f"Could not resolve upstream table for {upstream_table_name}"}

    row_data = {key: value for key, value in request.row_data.items()}
    ok, _mode, query_or_error = build_contributing_rows_query(
        downstream_table_name=downstream_table_name,
        upstream=upstream,
        row_data=row_data,
        page_size=max(1, min(MAX_PAGE_SIZE, int(request.limit or 100))),
        page=0,
    )
    if not ok:
        return {"error": query_or_error}

    query = (
        query_or_error.rsplit(" OFFSET ", 1)[0]
        + f" LIMIT {max(1, min(MAX_PAGE_SIZE, int(request.limit or 100)))}"
    )

    return ContributingRowsQueryResponse(
        query=query,
        upstream=UpstreamTableRef(schema=upstream.schema_name, table=upstream.table),
    )


@router.post("/page", response_model=ContributingRowsPageResponse | dict)
async def get_contributing_rows_page(
    request: ContributingRowsPageRequest,
) -> ContributingRowsPageResponse | dict[str, str]:
    """Return paginated contributing rows with a has_more flag for the selected pair.

    Executes the generated query and returns mode, rows, columns, and
    pagination info, or an error dictionary; exceptions are caught and
    reported in the response rather than raised.

    """
    try:
        catalog = resolve_default_catalog()
        default_ref = resolve_default_ref()
    except RuntimeError as exc:
        return {"error": str(exc)}
    if not _asset_pair_is_lineage_related(request.upstream_asset_key, request.downstream_asset_key):
        return {"error": "unrelated_asset_pair"}
    upstream_table_name = get_table_from_asset_key(request.upstream_asset_key)
    downstream_table_name = get_table_from_asset_key(request.downstream_asset_key)

    upstream = await resolve_iceberg_table(
        upstream_table_name,
        trino_url=None,
        timeout_ms=request.timeout_ms,
        catalog=catalog,
    )
    if upstream is None:
        return {"error": f"Could not resolve upstream table for {upstream_table_name}"}

    page_size = to_safe_page_size(request.page_size)
    page = to_safe_page(request.page)
    row_data = {key: value for key, value in request.row_data.items()}

    ok, mode, query_or_error = build_contributing_rows_query(
        downstream_table_name=downstream_table_name,
        upstream=upstream,
        row_data=row_data,
        page_size=page_size,
        page=page,
    )
    if not ok or mode is None:
        return {"error": query_or_error}

    result = await _execute_trino_or_error(
        query_or_error, catalog, default_ref, None, request.timeout_ms
    )
    if "error" in result:
        return result

    rows = _result_rows(result)
    columns = _result_columns(result)
    column_types = _result_column_types(result)
    has_more = len(rows) > page_size
    rows = rows[:page_size] if has_more else rows

    return ContributingRowsPageResponse(
        mode=mode,
        page=page,
        page_size=page_size,
        has_more=has_more,
        query=query_or_error,
        upstream=UpstreamTableRef(schema=upstream.schema_name, table=upstream.table),
        columns=columns,
        column_types=column_types,
        rows=rows,
    )
