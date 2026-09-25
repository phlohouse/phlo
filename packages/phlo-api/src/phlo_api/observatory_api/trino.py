"""Helpers for executing queries against Trino via its HTTP API."""

from __future__ import annotations

import asyncio
import os
import re
from math import isfinite
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel

from phlo.capabilities import resolve_capability
from phlo.capabilities.discovery import discover_capabilities
from phlo.cli.sql import strip_sql_literals_and_comments
from phlo.config.env import project_env_value
from phlo.config.network import resolve_url
from phlo.logging import get_bound_correlation_context, get_logger
from phlo_api.observatory_api.http_client import backend_client

logger = get_logger(__name__)

_DEFAULT_QUERY_ENGINE_ENV = "PHLO_QUERY_ENGINE"
_DEFAULT_TRINO_CAPABILITY_NAME = "trino"
_QUERY_ENGINE_URL_ENV = "PHLO_QUERY_ENGINE_URL"
_TRINO_USER_ENV = "TRINO_USER"
_TRINO_ROLE_ENV = "TRINO_ROLE"
_FORBIDDEN_READ_ONLY_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "MERGE",
    "CALL",
    "GRANT",
    "REVOKE",
)
_FORBIDDEN_READ_ONLY_PATTERN = re.compile(rf"\b({'|'.join(_FORBIDDEN_READ_ONLY_KEYWORDS)})\b")


def quote_identifier(identifier: str) -> str:
    """Quote an SQL identifier safely for Trino.

    Raises: ValueError if identifier is empty or contains NUL bytes.
    """
    if not identifier:
        raise ValueError("Identifier cannot be empty")
    if "\x00" in identifier:
        raise ValueError("Identifier cannot contain NUL bytes")
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def qualify_table_name(catalog: str, schema: str, table: str) -> str:
    """Build a fully qualified table name with proper quoting.

    Raises: ValueError if any identifier is invalid.
    """
    return f"{quote_identifier(catalog)}.{quote_identifier(schema)}.{quote_identifier(table)}"


def quote_qualified_table(table: str) -> str:
    """Quote exactly three dot-separated SQL identifiers; reject SQL fragments."""
    parts: list[str] = []
    i = 0
    while i < len(table):
        if table[i] == '"':
            i += 1
            part = ""
            while i < len(table):
                if table[i] == '"':
                    if i + 1 < len(table) and table[i + 1] == '"':
                        part += '"'
                        i += 2
                        continue
                    i += 1
                    break
                part += table[i]
                i += 1
            else:
                raise ValueError("Unclosed quoted identifier")
        else:
            start = i
            while i < len(table) and (table[i].isalnum() or table[i] == "_"):
                i += 1
            part = table[start:i]
        if not part or "\x00" in part:
            raise ValueError("Invalid qualified table identifier")
        parts.append(part)
        if i == len(table):
            break
        if table[i] != ".":
            raise ValueError("Invalid qualified table separator")
        i += 1
    if len(parts) != 3:
        raise ValueError("Expected catalog.schema.table")
    return qualify_table_name(*parts)


def sql_literal(value: object) -> str:
    """Convert a Python value to a safe SQL literal.

    Raises: ValueError if value is None, non-finite float, or unsupported type.
    """
    if value is None:
        raise ValueError("Use IS NULL for null filters")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Non-finite float values are not supported")
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    raise ValueError(f"Unsupported filter value type: {type(value).__name__}")


def validate_read_only_query(query: str) -> str | None:
    """Validate a query is read-only and a single statement.

    Checks for forbidden keywords (INSERT, UPDATE, DELETE, etc.) and
    ensures only a single statement is present.

    No exceptions raised directly.
    """
    cleaned = strip_sql_literals_and_comments(query)
    trimmed = cleaned.strip()
    if not trimmed:
        return "Query cannot be empty"

    while trimmed.endswith(";"):
        trimmed = trimmed[:-1].rstrip()
    if ";" in trimmed:
        return "Multiple statements are not allowed in read-only mode"

    match = _FORBIDDEN_READ_ONLY_PATTERN.search(trimmed.upper())
    if match:
        return f"{match.group(1)} statements are not allowed in read-only mode"

    return None


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


def resolve_trino_url() -> str:
    """Resolve the query-engine URL from environment or capability metadata.

    Raises: RuntimeError when no query-engine URL is configured.
    """
    env_url = project_env_value(_QUERY_ENGINE_URL_ENV) or project_env_value("TRINO_URL")
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


class QueryExecutionError(BaseModel):
    """Structured query execution failure payload."""

    ok: bool = False
    error: str
    kind: str  # 'timeout', 'trino', 'validation'


async def execute_trino_query(
    query: str,
    catalog: str | None = None,
    schema: str | None = None,
    timeout_ms: int = 30000,
    max_rows: int = 5000,
) -> dict[str, Any] | QueryExecutionError:
    """Execute a query against Trino and wait for results.

    Submits the query, polls until completion, and returns a dictionary with columns,
    column_types, and rows, or a QueryExecutionError on failure. Raises: RuntimeError
    when URL resolution fails; httpx.TimeoutException when the query times out.
    """
    try:
        if max_rows < 1:
            return QueryExecutionError(error="max_rows must be positive", kind="validation")
        url = resolve_trino_url()
        timeout = timeout_ms / 1000.0
        effective_catalog = catalog or resolve_default_catalog()
        effective_schema = schema or resolve_default_ref()
        start_time = monotonic()
        async with backend_client(timeout) as client:
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
                timeout=timeout,
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

            while True:
                if result.get("columns") and not columns:
                    columns = [c["name"] for c in result["columns"]]
                    column_types = [c["type"] for c in result["columns"]]
                if result.get("error"):
                    return QueryExecutionError(
                        error=result["error"].get("message", "Query failed"), kind="trino"
                    )
                all_data.extend(result.get("data", [])[: max_rows + 1 - len(all_data)])
                if len(all_data) > max_rows or not result.get("nextUri") or polls >= max_polls:
                    break
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

            # Convert to row dicts
            rows = [
                {col: row[idx] for idx, col in enumerate(columns)} for row in all_data[:max_rows]
            ]

            return {
                "columns": columns,
                "column_types": column_types,
                "rows": rows,
                "has_more": len(all_data) > max_rows or bool(result.get("nextUri")),
            }

    except RuntimeError as exc:
        return QueryExecutionError(error=str(exc), kind="validation")
    except httpx.TimeoutException:
        return QueryExecutionError(error="Query timed out", kind="timeout")
    except Exception as e:
        logger.exception("Trino query failed")
        return QueryExecutionError(error=str(e), kind="trino")
