"""Metrics collection models and helpers.

MetricsCollector gathers run/asset/summary metrics from the
observability backends (Prometheus, Postgres, Iceberg, query engine)
behind a TTL cache keyed per backend response; psycopg2 is imported
lazily so Postgres paths raise MetricsDependencyError only when used.
Backend failures surface as MetricsCollectorError subclasses rather than
crashing callers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import Field

from phlo.capabilities import QueryEngine, resolve_capability
from phlo.capabilities.discovery import discover_capabilities
from phlo.config.base import BaseConfig
from phlo.config.network import resolve_host
from phlo.logging import get_logger

logger = get_logger(__name__)


class _TTLCache(dict[str, Any]):
    """Dict with per-key TTL and maxsize eviction for backend responses.

    Expiry uses time.monotonic(), so wall-clock adjustments never extend or
    shorten entry lifetimes. There is no internal locking.
    """

    def __init__(self, *, maxsize: int, ttl: float) -> None:
        super().__init__()
        self._maxsize = maxsize
        self._ttl = ttl
        self._expires_at: dict[str, float] = {}

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return not self._is_expired(key) and super().__contains__(key)

    def __getitem__(self, key: str) -> Any:
        if self._is_expired(key):
            raise KeyError(key)
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if len(self) >= self._maxsize and key not in self:
            oldest_key = min(self._expires_at, key=self._expires_at.__getitem__)
            self.pop(oldest_key, None)
        super().__setitem__(key, value)
        self._expires_at[key] = time.monotonic() + self._ttl

    def pop(self, key: str, default: Any = None) -> Any:
        self._expires_at.pop(key, None)
        return super().pop(key, default)

    def clear(self) -> None:
        self._expires_at.clear()
        super().clear()

    def _is_expired(self, key: str) -> bool:
        expires_at = self._expires_at.get(key)
        if expires_at is None or expires_at > time.monotonic():
            return False
        super().pop(key, None)
        self._expires_at.pop(key, None)
        return True


def _load_psycopg2() -> Any:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as exc:
        raise MetricsDependencyError(
            "Postgres metrics require the runtime extra: install phlo[runtime]."
        ) from exc
    return psycopg2


class _LazyPsycopg2:
    def __getattr__(self, name: str) -> Any:
        return getattr(_load_psycopg2(), name)


psycopg2 = _LazyPsycopg2()


class MetricsBackendSettings(BaseConfig):
    """Backend connection settings for metrics collection."""

    postgres_host: str = Field(default="postgres", description="PostgreSQL host")
    postgres_port: int = Field(default=5432, description="PostgreSQL port")
    postgres_user: str = Field(default="phlo", description="PostgreSQL username")
    postgres_password: str = Field(default="phlo", description="PostgreSQL password")
    postgres_db: str = Field(default="phlo", description="PostgreSQL database name")
    nessie_host: str = Field(default="nessie", description="Nessie host")
    nessie_port: int = Field(default=19120, description="Nessie port")
    nessie_api_version: str = Field(default="v1", description="Nessie API version")
    metrics_query_engine: str | None = Field(
        default=None,
        description="Optional query_engine capability name for table stats queries",
    )
    metrics_query_catalog: str | None = Field(
        default=None,
        description="Catalog name used for query-engine table stats lookups",
    )

    def model_post_init(self, __context: Any) -> None:
        """Resolve Postgres and Nessie hosts/ports after validation, in place."""
        host, port = resolve_host(
            self.postgres_host, self.postgres_port, port_env_var="POSTGRES_PORT"
        )
        object.__setattr__(self, "postgres_host", host)
        object.__setattr__(self, "postgres_port", port)
        nhost, nport = resolve_host(self.nessie_host, self.nessie_port, port_env_var="NESSIE_PORT")
        object.__setattr__(self, "nessie_host", nhost)
        object.__setattr__(self, "nessie_port", nport)

    def nessie_api_uri(self) -> str:
        """Return the versioned Nessie API URI."""
        return f"http://{self.nessie_host}:{self.nessie_port}/api/{self.nessie_api_version}"


class MetricsCollectorError(RuntimeError):
    """Base class for collector-specific failures."""


class MetricsDependencyError(MetricsCollectorError):
    """Raised when an external dependency is unavailable."""


class MetricsMalformedResponseError(MetricsCollectorError):
    """Raised when a backend returns an unexpected payload shape."""


@dataclass
class RunMetrics:
    """Metrics for a single asset run."""

    asset_name: str
    run_id: str
    start_time: datetime
    end_time: datetime | None = None
    duration_seconds: float | None = None
    status: str = "running"
    rows_processed: int = 0
    bytes_written: int = 0


@dataclass
class AssetMetrics:
    """Aggregated metrics for an asset."""

    asset_name: str
    last_run: RunMetrics | None = None
    last_10_runs: list[RunMetrics] = field(default_factory=list)
    average_duration: float = 0.0
    failure_rate: float = 0.0
    average_rows_per_run: float = 0.0
    data_growth_bytes: int = 0


@dataclass
class SummaryMetrics:
    """Summary metrics for the entire platform."""

    total_runs_24h: int = 0
    successful_runs_24h: int = 0
    failed_runs_24h: int = 0
    total_rows_processed_24h: int = 0
    total_bytes_written_24h: int = 0
    p50_duration_seconds: float = 0.0
    p95_duration_seconds: float = 0.0
    p99_duration_seconds: float = 0.0
    active_assets_count: int = 0
    data_growth_bytes: int = 0
    assets_by_status: dict[str, int] = field(
        default_factory=lambda: {"success": 0, "warning": 0, "failure": 0}
    )


class MetricsCollector:
    """Collect metrics from Prometheus, Iceberg, and Postgres."""

    def __init__(self) -> None:
        self.settings = MetricsBackendSettings()
        self._cache = _TTLCache(maxsize=100, ttl=30)
        self._prometheus_url: str | None = None

    @property
    def prometheus_url(self) -> str | None:
        """Get Prometheus URL from config or environment."""
        if self._prometheus_url is None:
            self._prometheus_url = "http://prometheus:9090"
        return self._prometheus_url

    def collect_summary(self, period_hours: int = 24) -> SummaryMetrics:
        """Collect summary metrics for the platform."""
        cache_key = f"summary_{period_hours}h"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Backends are independent: a backend that fails logs a warning and
        # leaves its fields zeroed rather than failing the call. The partially
        # filled result is cached like a complete one.
        metrics = SummaryMetrics()

        try:
            metrics = self._collect_from_prometheus(period_hours)
        except Exception:
            logger.warning(
                "metrics_collect_prometheus_failed", period_hours=period_hours, exc_info=True
            )

        try:
            postgres_metrics = self._collect_from_postgres(period_hours)
            metrics.total_rows_processed_24h = postgres_metrics.get("rows_processed", 0)
            metrics.total_bytes_written_24h = postgres_metrics.get("bytes_written", 0)
        except Exception:
            logger.warning(
                "metrics_collect_postgres_failed", period_hours=period_hours, exc_info=True
            )

        try:
            iceberg_metrics = self._collect_from_iceberg()
            metrics.active_assets_count = int(iceberg_metrics.get("table_count", 0) or 0)
            metrics.data_growth_bytes = int(iceberg_metrics.get("total_bytes", 0) or 0)
        except Exception:
            logger.warning(
                "metrics_collect_iceberg_failed", period_hours=period_hours, exc_info=True
            )

        self._cache[cache_key] = metrics
        return metrics

    def collect_asset(self, asset_name: str, runs: int = 10) -> AssetMetrics:
        """Collect metrics for a specific asset."""
        cache_key = f"asset_{asset_name}_{runs}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        metrics = AssetMetrics(asset_name=asset_name)

        try:
            run_records = self._get_asset_runs_from_postgres(asset_name, limit=runs)
            if run_records:
                metrics.last_10_runs = run_records
                metrics.last_run = run_records[0]

                durations = [
                    record.duration_seconds
                    for record in run_records
                    if record.duration_seconds is not None
                ]
                if durations:
                    metrics.average_duration = sum(durations) / len(durations)

                successful = sum(1 for record in run_records if record.status == "success")
                metrics.failure_rate = 1.0 - (successful / len(run_records))
                metrics.average_rows_per_run = sum(
                    record.rows_processed for record in run_records
                ) / len(run_records)
        except Exception:
            logger.warning(
                "asset_metrics_collect_failed",
                asset_name=asset_name,
                runs=runs,
                exc_info=True,
            )

        try:
            iceberg_table_name = self._resolve_asset_table_name_from_postgres(asset_name)
            iceberg_metrics = self._get_iceberg_table_stats(iceberg_table_name)
            metrics.data_growth_bytes = iceberg_metrics.get("total_bytes", 0)
        except Exception:
            logger.warning("asset_iceberg_stats_failed", asset_name=asset_name, exc_info=True)

        self._cache[cache_key] = metrics
        return metrics

    def _collect_from_prometheus(self, period_hours: int) -> SummaryMetrics:
        """Collect metrics from Prometheus."""
        metrics = SummaryMetrics()
        if not self.prometheus_url:
            return metrics

        try:
            response = httpx.get(
                f"{self.prometheus_url}/api/v1/query",
                params={
                    "query": f'increase(dagster_runs_total{{status="success"}}[{period_hours}h])'
                },
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("data", {}).get("result"):
                    value = data["data"]["result"][0].get("value", [None, "0"])
                    metrics.successful_runs_24h = int(float(value[1]))

            response = httpx.get(
                f"{self.prometheus_url}/api/v1/query",
                params={
                    "query": f'increase(dagster_runs_total{{status="failure"}}[{period_hours}h])'
                },
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("data", {}).get("result"):
                    value = data["data"]["result"][0].get("value", [None, "0"])
                    metrics.failed_runs_24h = int(float(value[1]))

            metrics.total_runs_24h = metrics.successful_runs_24h + metrics.failed_runs_24h

            for percentile in ["0.5", "0.95", "0.99"]:
                response = httpx.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={
                        "query": (
                            f"histogram_quantile({percentile}, "
                            f"dagster_run_duration_seconds[{period_hours}h])"
                        )
                    },
                    timeout=5,
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("data", {}).get("result"):
                        value = data["data"]["result"][0].get("value", [None, "0"])
                        duration = float(value[1])
                        if percentile == "0.5":
                            metrics.p50_duration_seconds = duration
                        elif percentile == "0.95":
                            metrics.p95_duration_seconds = duration
                        else:
                            metrics.p99_duration_seconds = duration
        except Exception:
            logger.debug("prometheus_collection_failed", period_hours=period_hours, exc_info=True)

        return metrics

    def _collect_from_postgres(self, period_hours: int) -> dict[str, Any]:
        """Collect metrics from Postgres."""
        metrics: dict[str, Any] = {}
        conn = None
        try:
            conn = psycopg2.connect(
                host=self.settings.postgres_host,
                port=self.settings.postgres_port,
                database=self.settings.postgres_db,
                user=self.settings.postgres_user,
                password=self.settings.postgres_password,
            )
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            since = datetime.now(UTC) - timedelta(hours=period_hours)
            cur.execute(
                """
                SELECT
                    COUNT(*) as run_count,
                    SUM(CASE WHEN event_type = 'PIPELINE_SUCCESS' THEN 1 ELSE 0 END) as success_count
                FROM dagster_event_logs
                WHERE timestamp > %s
                """,
                (since,),
            )
            row = cur.fetchone()
            if row:
                metrics["runs"] = row["run_count"] or 0
                metrics["successful_runs"] = row["success_count"] or 0
        except Exception:
            logger.debug(
                "postgres_metrics_collection_failed", period_hours=period_hours, exc_info=True
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.debug(
                        "postgres_metrics_connection_close_failed",
                        period_hours=period_hours,
                        exc_info=True,
                    )
        return metrics

    def _collect_from_iceberg(self) -> dict[str, Any]:
        """Collect metrics from Iceberg/Nessie."""
        metrics: dict[str, Any] = {}
        try:
            nessie_url = self.settings.nessie_api_uri()
            response = httpx.get(f"{nessie_url}/trees", timeout=5)
            if response.status_code == 200:
                data = response.json()
                tables_count = 0
                namespaces = data.get("trees", [])
                for namespace in namespaces:
                    ns_name = namespace.get("name")
                    if not ns_name:
                        continue
                    try:
                        ns_response = httpx.get(
                            f"{nessie_url}/namespaces/{ns_name}/tables",
                            timeout=5,
                        )
                        if ns_response.status_code == 200:
                            tables_count += len(ns_response.json().get("tables", []))
                    except Exception:
                        continue
                metrics["table_count"] = tables_count
                metrics["total_bytes"] = 0
        except Exception:
            logger.debug("iceberg_metrics_collection_failed", exc_info=True)
        return metrics

    def _get_asset_runs_from_postgres(self, asset_name: str, limit: int = 10) -> list[RunMetrics]:
        """Get past runs for an asset from Postgres."""
        try:
            conn = psycopg2.connect(
                host=self.settings.postgres_host,
                port=self.settings.postgres_port,
                database=self.settings.postgres_db,
                user=self.settings.postgres_user,
                password=self.settings.postgres_password,
            )
        except Exception as exc:
            raise MetricsDependencyError("Postgres unavailable for asset run lookup") from exc

        try:
            # Escape LIKE wildcards so asset names containing % or _ match literally.
            escaped_asset_name = (
                asset_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            with conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        r.run_id,
                        CASE
                            WHEN r.start_time IS NOT NULL THEN to_timestamp(r.start_time)
                            ELSE r.create_timestamp
                        END AS start_time,
                        CASE
                            WHEN r.end_time IS NOT NULL THEN to_timestamp(r.end_time)
                            ELSE r.update_timestamp
                        END AS end_time,
                        r.status
                    FROM runs AS r
                    WHERE r.pipeline_name = %s
                       OR EXISTS (
                           SELECT 1
                           FROM run_tags AS t
                           WHERE t.run_id = r.run_id
                             AND t.key = 'dagster/asset_selection'
                             AND t.value ILIKE %s ESCAPE '\\'
                       )
                       OR EXISTS (
                           SELECT 1
                           FROM event_logs AS e
                           WHERE e.run_id = r.run_id
                             AND e.asset_key ILIKE %s ESCAPE '\\'
                       )
                    ORDER BY start_time DESC
                    LIMIT %s
                    """,
                    (asset_name, f"%{escaped_asset_name}%", f"%{escaped_asset_name}%", limit),
                )
                rows = cur.fetchall()
        except Exception as exc:
            raise MetricsDependencyError("Failed querying Dagster run history") from exc
        finally:
            conn.close()

        runs: list[RunMetrics] = []
        for row in rows:
            run_id = row.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise MetricsMalformedResponseError("Dagster run row missing string run_id")
            start = self._coerce_datetime(row.get("start_time"), "start_time")
            assert start is not None
            end = self._coerce_datetime(row.get("end_time"), "end_time", allow_none=True)
            runs.append(
                RunMetrics(
                    asset_name=asset_name,
                    run_id=run_id,
                    start_time=start,
                    end_time=end,
                    duration_seconds=(end - start).total_seconds() if end is not None else None,
                    status=self._normalize_status(row.get("status")),
                )
            )
        return runs

    def _resolve_asset_table_name_from_postgres(self, asset_name: str) -> str:
        """Resolve a Dagster asset key to its physical table when metadata records it."""
        try:
            conn = psycopg2.connect(
                host=self.settings.postgres_host,
                port=self.settings.postgres_port,
                database=self.settings.postgres_db,
                user=self.settings.postgres_user,
                password=self.settings.postgres_password,
            )
        except Exception as exc:
            raise MetricsDependencyError("Postgres unavailable for asset table lookup") from exc

        try:
            # Escape LIKE wildcards so asset names containing % or _ match literally.
            escaped_asset_name = (
                asset_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            with conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT event
                    FROM event_logs
                    WHERE dagster_event_type = 'ASSET_MATERIALIZATION'
                      AND asset_key ILIKE %s ESCAPE '\\'
                    ORDER BY timestamp DESC
                    LIMIT 20
                    """,
                    (f"%{escaped_asset_name}%",),
                )
                rows = cur.fetchall()
        except Exception as exc:
            raise MetricsDependencyError("Failed querying asset materialization metadata") from exc
        finally:
            conn.close()

        for row in rows:
            table_name = self._extract_materialized_table_name(row.get("event"))
            if table_name:
                return table_name

        return asset_name

    def _extract_materialized_table_name(self, raw_event: Any) -> str | None:
        if not isinstance(raw_event, str) or not raw_event:
            return None
        try:
            event = json.loads(raw_event)
        except json.JSONDecodeError:
            return None

        entries = (
            event.get("dagster_event", {})
            .get("event_specific_data", {})
            .get("materialization", {})
            .get("metadata_entries", [])
        )
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if entry.get("label") != "table_name":
                continue
            text = entry.get("entry_data", {}).get("text")
            if isinstance(text, str) and text:
                return text
        return None

    def _get_iceberg_table_stats(self, table_name: str) -> dict[str, Any]:
        """Get table statistics from Iceberg."""
        query_engine = self._load_query_engine()
        catalog = self._resolve_query_engine_catalog()
        if "." in table_name:
            table_schema, raw_table = table_name.split(".", maxsplit=1)
        else:
            table_schema = self._resolve_table_schema(table_name, catalog, query_engine)
            raw_table = table_name

        safe_schema = self._validate_identifier(table_schema, "table_schema")
        safe_table = self._validate_identifier(raw_table, "table_name")
        escaped_files_table = f"{safe_table}$files"
        stats_sql = (
            f"SELECT COALESCE(SUM(file_size_in_bytes), 0), COALESCE(SUM(record_count), 0) "
            f'FROM {catalog}.{safe_schema}."{escaped_files_table}"'
        )

        try:
            row = query_engine.execute(stats_sql, schema=safe_schema)
        except Exception as exc:
            raise MetricsDependencyError(
                f"Failed querying Iceberg table stats for {table_name}"
            ) from exc

        if not isinstance(row, list) or len(row) != 1 or not isinstance(row[0], (list, tuple)):
            raise MetricsMalformedResponseError(
                f"Unexpected Iceberg table stats shape for {table_name}: {row!r}"
            )
        stats_row = row[0]
        if len(stats_row) != 2:
            raise MetricsMalformedResponseError(
                f"Unexpected Iceberg table stats shape for {table_name}: {row!r}"
            )

        return {
            "total_bytes": self._coerce_int(stats_row[0], "total_bytes"),
            "row_count": self._coerce_int(stats_row[1], "row_count"),
        }

    def _resolve_table_schema(
        self, table_name: str, catalog: str, query_engine: QueryEngine
    ) -> str:
        safe_table_name = self._validate_identifier(table_name, "table_name")
        schema_sql = (
            f"SELECT table_schema FROM {catalog}.information_schema.tables "
            f"WHERE table_name = '{safe_table_name}' ORDER BY table_schema LIMIT 1"
        )
        try:
            rows = query_engine.execute(schema_sql)
        except Exception as exc:
            raise MetricsDependencyError(f"Failed resolving schema for {table_name}") from exc

        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], tuple):
            raise MetricsMalformedResponseError(
                f"Could not resolve Iceberg schema for table {table_name}"
            )
        row = rows[0]
        if not row or not isinstance(row[0], str) or not row[0]:
            raise MetricsMalformedResponseError(
                f"Could not resolve Iceberg schema for table {table_name}"
            )
        return self._validate_identifier(row[0], "table_schema")

    def _coerce_datetime(
        self, value: Any, field_name: str, *, allow_none: bool = False
    ) -> datetime | None:
        if value is None:
            if allow_none:
                return None
            raise MetricsMalformedResponseError(f"Missing required timestamp field {field_name}")
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=UTC)
        if isinstance(value, str):
            normal = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normal)
            except ValueError as exc:
                raise MetricsMalformedResponseError(
                    f"Invalid datetime string for {field_name}: {value!r}"
                ) from exc
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        raise MetricsMalformedResponseError(
            f"Unsupported datetime value type for {field_name}: {type(value).__name__}"
        )

    def _coerce_int(self, value: Any, field_name: str) -> int:
        if isinstance(value, bool):
            raise MetricsMalformedResponseError(
                f"Invalid numeric value for {field_name}: {value!r}"
            )
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise MetricsMalformedResponseError(
                    f"Expected integer-compatible value for {field_name}, got {value!r}"
                )
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
                return int(stripped)
        raise MetricsMalformedResponseError(f"Invalid numeric value for {field_name}: {value!r}")

    def _normalize_status(self, value: Any) -> str:
        if not isinstance(value, str):
            raise MetricsMalformedResponseError(f"Invalid Dagster status value: {value!r}")
        normalized = value.lower()
        if normalized in {"success", "succeeded"}:
            return "success"
        if normalized in {"failure", "failed", "canceled", "cancelled"}:
            return "failure"
        if normalized in {"running", "started", "starting", "queued", "not_started"}:
            return "running"
        return normalized

    def _validate_identifier(self, value: str, field_name: str) -> str:
        if not value:
            raise MetricsMalformedResponseError(f"Empty identifier for {field_name}")
        if not all(part and part.replace("_", "").isalnum() for part in value.split(".")):
            raise MetricsMalformedResponseError(f"Invalid identifier for {field_name}: {value!r}")
        return value

    def _load_query_engine(self) -> QueryEngine:
        discover_capabilities()
        resolution = resolve_capability("query_engine", self.settings.metrics_query_engine)
        if resolution is None:
            target = (
                f"query_engine:{self.settings.metrics_query_engine}"
                if self.settings.metrics_query_engine
                else "query_engine"
            )
            raise MetricsDependencyError(
                f"Query engine capability unavailable for Iceberg metrics ({target})"
            )
        return resolution.provider

    def _resolve_query_engine_catalog(self) -> str:
        if self.settings.metrics_query_catalog:
            return self._validate_identifier(
                self.settings.metrics_query_catalog,
                "metrics_query_catalog",
            )
        discover_capabilities()
        resolution = resolve_capability("query_engine", self.settings.metrics_query_engine)
        if resolution is None:
            target = (
                f"query_engine:{self.settings.metrics_query_engine}"
                if self.settings.metrics_query_engine
                else "query_engine"
            )
            raise MetricsDependencyError(
                f"Query engine capability unavailable for metrics catalog resolution ({target})"
            )
        for key in ("default_catalog", "catalog", "catalog_name"):
            value = resolution.metadata.get(key)
            if isinstance(value, str) and value:
                return self._validate_identifier(value, "metrics_query_catalog")
        raise MetricsDependencyError(
            "Query engine capability does not declare a default catalog and "
            "metrics_query_catalog is not configured."
        )


_metrics_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    """Get or create the global metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector
