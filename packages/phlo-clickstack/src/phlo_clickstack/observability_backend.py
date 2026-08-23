"""ClickStack-backed observability capability provider.

Span queries post SQL to ClickStack's HTTP endpoint and decode its
newline-delimited JSON reply into TraceSpans. The endpoint resolves from
CLICKSTACK_QUERY_URL, falling back to the in-container service address or
localhost depending on environment.
"""

from __future__ import annotations

import json
import os

import requests

from phlo.capabilities import (
    CapabilitySupport,
    DefaultObservabilityBackend,
    ObservabilityBackendSpec,
    TraceSpan,
    TraceSpanFilter,
)

_CLICKSTACK_QUERY_URL_ENV = "CLICKSTACK_QUERY_URL"
_CLICKSTACK_QUERY_USER_ENV = "CLICKSTACK_QUERY_USER"
_CLICKSTACK_QUERY_PASSWORD_ENV = "CLICKSTACK_QUERY_PASSWORD"
_CLICKSTACK_HTTP_PORT_ENV = "CLICKSTACK_HTTP_PORT"
_DEFAULT_CLICKSTACK_HTTP_PORT = "8123"
_CONTAINER_CLICKSTACK_QUERY_URL = f"http://clickstack:{_DEFAULT_CLICKSTACK_HTTP_PORT}"


class ClickStackObservabilityBackend(DefaultObservabilityBackend):
    """Observability backend with OTEL span queries backed by ClickStack."""

    def run_trace_spans(self, run_id: str, limit: int = 500) -> list[TraceSpan]:
        """Return trace spans for a single run id, bounded by limit."""
        return self.trace_spans(TraceSpanFilter(run_id=run_id, limit=limit))

    def trace_spans(self, filters: TraceSpanFilter) -> list[TraceSpan]:
        """Post the span query to ClickStack and decode newline-delimited JSON into TraceSpans.

        Raises requests.HTTPError when the query request fails.
        """
        query_url = self._resolve_clickstack_query_url()
        query = _build_trace_spans_query(filters)
        response = requests.post(
            query_url,
            data=query.encode("utf-8"),
            auth=self._resolve_clickstack_query_auth(),
            timeout=10,
        )
        response.raise_for_status()
        spans: list[TraceSpan] = []
        for line in response.text.splitlines():
            if not line.strip():
                continue
            spans.append(TraceSpan(**json.loads(line)))
        return spans

    def _resolve_clickstack_query_url(self) -> str:
        query_url = os.environ.get(_CLICKSTACK_QUERY_URL_ENV)
        if query_url:
            return query_url.rstrip("/")
        if _running_in_container():
            return _CONTAINER_CLICKSTACK_QUERY_URL
        port = os.environ.get(_CLICKSTACK_HTTP_PORT_ENV, _DEFAULT_CLICKSTACK_HTTP_PORT)
        return f"http://127.0.0.1:{port}"

    def _resolve_clickstack_query_auth(self) -> tuple[str, str] | None:
        user = os.environ.get(_CLICKSTACK_QUERY_USER_ENV)
        password = os.environ.get(_CLICKSTACK_QUERY_PASSWORD_ENV)
        if user is None:
            return None
        return (user, password or "")


def _build_trace_spans_query(filters: TraceSpanFilter) -> str:
    where_clauses = _trace_where_clauses(filters)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    limit = _bounded_limit(filters.limit)
    return f"""
SELECT
    toString(Timestamp) AS timestamp,
    TraceId AS trace_id,
    SpanId AS span_id,
    nullIf(ParentSpanId, '') AS parent_span_id,
    SpanName AS span_name,
    ServiceName AS service_name,
    SpanKind AS span_kind,
    round(Duration / 1000000, 3) AS duration_ms,
    StatusCode AS status_code,
    SpanAttributes AS span_attributes,
    ResourceAttributes AS resource_attributes
FROM default.otel_traces
{where_sql}
ORDER BY Timestamp ASC
LIMIT {limit}
FORMAT JSONEachRow
""".strip()


def _trace_where_clauses(filters: TraceSpanFilter) -> list[str]:
    clauses: list[str] = []
    _append_span_attribute_clause(clauses, "phlo.run_id", filters.run_id)
    _append_span_attribute_clause(clauses, "phlo.asset_key", filters.asset_key)
    _append_span_attribute_clause(clauses, "phlo.job_name", filters.job_name)
    _append_exact_clause(clauses, "ServiceName", filters.service_name)
    _append_exact_clause(clauses, "SpanName", filters.span_name)
    _append_exact_clause(clauses, "StatusCode", filters.status_code)
    if filters.start_time:
        clauses.append(
            f"Timestamp >= parseDateTimeBestEffort('{_escape_clickhouse_string(filters.start_time)}')"
        )
    if filters.end_time:
        clauses.append(
            f"Timestamp <= parseDateTimeBestEffort('{_escape_clickhouse_string(filters.end_time)}')"
        )
    return clauses


def _append_span_attribute_clause(clauses: list[str], key: str, value: str | None) -> None:
    if value:
        clauses.append(f"SpanAttributes['{key}'] = '{_escape_clickhouse_string(value)}'")


def _append_exact_clause(clauses: list[str], column: str, value: str | None) -> None:
    if value:
        clauses.append(f"{column} = '{_escape_clickhouse_string(value)}'")


def _bounded_limit(limit: int) -> int:
    return min(max(limit, 1), 5000)


def build_clickstack_observability_spec() -> ObservabilityBackendSpec:
    """Build the ClickStack observability capability spec."""
    return ObservabilityBackendSpec(
        name="default",
        provider=ClickStackObservabilityBackend(),
        metadata={
            "default_stack": ["phlo-otel", "phlo-clickstack"],
            "service_dependencies": ["clickstack"],
            "backend": "clickstack",
        },
        support=CapabilitySupport(
            supports_metrics=True,
            supports_logs=True,
            supports_dashboards=True,
            supports_alerts=True,
        ),
    )


def _escape_clickhouse_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _running_in_container() -> bool:
    return os.path.exists("/.dockerenv")
