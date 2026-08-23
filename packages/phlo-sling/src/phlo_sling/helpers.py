"""Ergonomic helpers for Sling-backed lakehouse ingestion.

build_replication_plan turns stream names or small mappings into
SlingReplication definitions; connection summaries deliberately
over-redact, treating any key containing a secret fragment (including
"primary_key") as a secret and dropping its value. Partition windows
are rendered as quoted SQL predicates into a WHERE fragment.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from phlo_sling.connections import resolve_phlo_connections
from phlo_sling.registry import SlingReplication


SECRET_KEY_PARTS = ("password", "secret", "token", "key")
# Substring matching deliberately over-redacts: any key containing these
# fragments (including e.g. "primary_key") is treated as a secret.


def _coerce_replication_mode(
    value: object,
) -> Literal["full-refresh", "incremental", "snapshot", "backfill"] | None:
    """Validate and narrow a replication mode from a dynamic mapping."""
    if value is None:
        return None
    if value not in {"full-refresh", "incremental", "snapshot", "backfill"}:
        raise ValueError(f"Unsupported replication mode: {value}")
    if value == "full-refresh":
        return "full-refresh"
    if value == "incremental":
        return "incremental"
    if value == "snapshot":
        return "snapshot"
    return "backfill"


def _mapping_value(value: object) -> dict[str, Any]:
    """Return a string-keyed mapping from dynamic configuration data."""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True)
class ReplicationPlan:
    """A lightweight collection of Sling replication definitions."""

    replications: list[SlingReplication] = field(default_factory=list)

    def __iter__(self):
        return iter(self.replications)

    def __len__(self) -> int:
        return len(self.replications)


@dataclass(frozen=True)
class ConnectionSummary:
    """Non-secret summary of a Sling connection."""

    name: str
    type: str | None
    configured: bool
    keys: list[str] = field(default_factory=list)
    redacted_keys: list[str] = field(default_factory=list)


def build_partition_where(
    column: str,
    start: date | datetime | str | None = None,
    end: date | datetime | str | None = None,
    *,
    inclusive_end: bool = False,
    date_format: str = "%Y-%m-%d",
) -> str | None:
    """Build a SQL WHERE fragment for a partition or incremental window."""
    predicates: list[str] = []
    if start is not None:
        predicates.append(
            f"{column} >= {_quote_sql_value(_format_window_value(start, date_format))}"
        )
    if end is not None:
        op = "<=" if inclusive_end else "<"
        predicates.append(
            f"{column} {op} {_quote_sql_value(_format_window_value(end, date_format))}"
        )
    return " AND ".join(predicates) if predicates else None


def table_name_from_stream(stream_name: str) -> str:
    """Derive a stable target table name from a Sling stream identifier."""
    cleaned = stream_name.replace('"', "").replace("`", "").strip()
    leaf = cleaned.rsplit(".", 1)[-1]
    table = re.sub(r"[^0-9A-Za-z_]+", "_", leaf).strip("_").lower()
    return table or "stream"


def build_replication_plan(
    streams: Iterable[str | Mapping[str, Any] | SlingReplication],
    *,
    source_conn: str,
    target_conn: str | None = None,
    mode: Literal["full-refresh", "incremental", "snapshot", "backfill"] | None = "incremental",
    primary_key: list[str] | str | None = None,
    update_key: str | None = None,
    group_name: str | None = None,
    where: str | None = None,
) -> ReplicationPlan:
    """Build a replication plan from stream names or small stream mappings."""
    replications: list[SlingReplication] = []
    for stream in streams:
        if isinstance(stream, SlingReplication):
            replications.append(stream)
            continue

        stream_config = (
            {"stream_name": stream}
            if isinstance(stream, str)
            else {str(key): value for key, value in stream.items()}
        )
        stream_name = str(stream_config["stream_name"])
        table_name = str(stream_config.get("table_name") or table_name_from_stream(stream_name))
        replications.append(
            SlingReplication(
                stream_name=stream_name,
                table_name=table_name,
                source_conn=str(stream_config.get("source_conn") or source_conn),
                target_conn=_coalesce_str(stream_config.get("target_conn"), target_conn),
                mode=_coerce_replication_mode(stream_config.get("mode") or mode),
                primary_key=stream_config.get("primary_key", primary_key),
                update_key=_coalesce_str(stream_config.get("update_key"), update_key),
                group_name=_coalesce_str(stream_config.get("group_name"), group_name),
                object=_coalesce_str(stream_config.get("object"), None),
                select=list(stream_config.get("select") or []),
                where=_coalesce_str(stream_config.get("where"), where),
                source_options=_mapping_value(stream_config.get("source_options")),
                target_options=_mapping_value(stream_config.get("target_options")),
                description=_coalesce_str(stream_config.get("description"), None),
                owner=_coalesce_str(stream_config.get("owner"), None),
                metadata=_mapping_value(stream_config.get("metadata")),
                tags={str(k): str(v) for k, v in _mapping_value(stream_config.get("tags")).items()},
            )
        )
    return ReplicationPlan(replications=replications)


def summarize_connections(
    connections: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, ConnectionSummary]:
    """Summarize Sling connections without exposing secret values."""
    import os

    resolved: dict[str, Mapping[str, Any]] = {}
    source_env = os.environ if environ is None else environ
    for name, value in source_env.items():
        if not _is_connection_env_name(name):
            continue
        parsed = _parse_connection_json(value)
        if parsed is not None:
            resolved[name] = parsed

    if connections is None:
        resolved.update(resolve_phlo_connections())
    else:
        resolved.update(connections)

    return {
        name: ConnectionSummary(
            name=name,
            type=_optional_str(config.get("type")),
            configured=True,
            keys=sorted(str(key) for key in config),
            redacted_keys=sorted(str(key) for key in config if _is_secret_key(str(key))),
        )
        for name, config in sorted(resolved.items())
    }


def _format_window_value(value: date | datetime | str, date_format: str) -> str:
    if isinstance(value, datetime):
        return value.strftime(date_format)
    if isinstance(value, date):
        return value.strftime(date_format)
    return value


def _quote_sql_value(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parse_connection_json(value: str) -> Mapping[str, Any] | None:
    # Sling connection env vars are JSON objects with a "type" field; anything
    # else in the environment is not a connection definition.
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, Mapping) and "type" in parsed else None


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SECRET_KEY_PARTS)


def _is_connection_env_name(name: str) -> bool:
    return (
        name.startswith("PHLO_")
        or name.startswith("SLING_")
        or name.endswith("_CONN")
        or name.endswith("_CONNECTION")
    )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _coalesce_str(primary: Any, fallback: str | None) -> str | None:
    return primary if isinstance(primary, str) and primary else fallback


__all__ = [
    "ConnectionSummary",
    "ReplicationPlan",
    "build_partition_where",
    "build_replication_plan",
    "summarize_connections",
    "table_name_from_stream",
]
