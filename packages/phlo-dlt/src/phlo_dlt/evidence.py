"""dlt-side normalization of observed source, staged, and Iceberg metadata.

Derives credential-free source identities (secrets stripped before hashing),
inventories staged files without reading row values, and reports only
explicitly observed dlt metrics so evidence never contains guessed values.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from phlo.capabilities.interfaces import TableStateObserver, TableStore
from phlo.run_evidence.redaction import canonical_json, redact_payload


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_source_identity(source: Any, explicit: str | None = None) -> str | None:
    """Return a credential-free, stable identity for a source object."""
    candidate = explicit
    if candidate is None:
        for name in ("source_name", "name", "section"):
            value = getattr(source, name, None)
            if isinstance(value, str) and value.strip():
                candidate = value.strip()
                break
    if not candidate:
        return None
    parts = urlsplit(candidate)
    if parts.scheme and parts.netloc:
        try:
            host = parts.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            netloc = f"{host}:{parts.port}" if parts.port is not None else host
            return urlunsplit((parts.scheme.lower(), netloc, parts.path, "", ""))
        except ValueError:
            return str(redact_payload(candidate))
    redacted = redact_payload(candidate)
    return str(redacted)


def staged_object_inventory(paths: list[Path]) -> list[dict[str, Any]]:
    """Inventory staged files without reading row values."""
    inventory: list[dict[str, Any]] = []
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        item: dict[str, Any] = {
            "identity": f"sha256:{digest.hexdigest()}",
            "checksum": digest.hexdigest(),
            "byte_count": path.stat().st_size,
        }
        try:
            import pyarrow.parquet as pq

            item["record_count"] = pq.ParquetFile(path).metadata.num_rows
        except Exception:
            item["record_count"] = None
            item["metadata"] = {"record_count": {"status": "unavailable"}}
        inventory.append(item)
    return inventory


def dlt_execution_identity(
    pipeline: Any,
    source: Any,
    parameters: dict[str, Any],
    staged_objects: list[dict[str, Any]],
) -> tuple[str | None, bool]:
    """Resolve a provider execution/load identity without guessing one."""
    for key in ("execution_id", "load_id", "evidence_event_id"):
        value = parameters.get(key)
        if isinstance(value, str) and value:
            return value, True
    for candidate in (getattr(source, "load_id", None), getattr(source, "_load_id", None)):
        if isinstance(candidate, str) and candidate:
            return candidate, True
    load_info = getattr(pipeline, "_phlo_last_load_info", None)
    packages = getattr(load_info, "load_packages", None) if load_info is not None else None
    for package in packages or ():
        for attr in ("load_id", "load_package_id", "job_id"):
            candidate = getattr(package, attr, None)
            if isinstance(candidate, str) and candidate:
                return candidate, True
    # A staged-content fingerprint is only a replay aid, never presented as a
    # provider execution ID; identical executions remain explicitly unknown.
    if staged_objects:
        return _hash([item.get("checksum") for item in staged_objects]), False
    return None, False


def dlt_observed_metrics(pipeline: Any) -> dict[str, int]:
    """Extract only explicitly reported dlt read metrics."""
    load_info = getattr(pipeline, "_phlo_last_load_info", None)
    raw = getattr(load_info, "metrics", None)
    if not isinstance(raw, dict):
        return {}
    metrics: dict[str, int] = {}
    for output_name, candidates in {
        "records_read": ("records_read", "rows_read", "row_count"),
        "bytes_read": ("bytes_read", "bytes"),
    }.items():
        for key in candidates:
            value = raw.get(key)
            if isinstance(value, int) and value >= 0:
                metrics[output_name] = value
                break
    return metrics


def table_state(table_store: TableStore, table_name: str, ref: str) -> dict[str, Any]:
    """Read normalized table state through the optional neutral capability."""
    if not isinstance(table_store, TableStateObserver):
        return {
            "state": "unavailable",
            "snapshot_id": None,
            "schema_hash": None,
            "metadata": {"state": {"status": "unavailable"}},
        }
    try:
        observed = table_store.observe_table_state(table_name=table_name, override_ref=ref)
        if isinstance(observed, dict):
            return {
                "state": observed.get("state", "unavailable"),
                "snapshot_id": observed.get("snapshot_id", observed.get("revision")),
                "schema_hash": observed.get("schema_hash"),
                "metadata": observed.get("metadata", {}),
            }
        return {
            "state": observed.state,
            "snapshot_id": observed.revision,
            "schema_hash": observed.schema_hash,
            "metadata": observed.metadata,
        }
    except Exception as exc:
        return {
            "state": "unavailable",
            "snapshot_id": None,
            "schema_hash": None,
            "metadata": {
                "state": {"status": "unavailable"},
                "error_type": type(exc).__name__,
            },
        }
