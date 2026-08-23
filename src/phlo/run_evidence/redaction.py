"""Redaction and canonical serialization for stored run evidence.

Deep-redacts sensitive keys, row-data payloads, and credential-bearing
text before anything is stored. canonical_json is the stable serialization
underlying payload_checksum, and safe_error_summary keeps only an error
type plus a text fingerprint — never exception message content.

Imported by the run-evidence store/report/reconciliation siblings and by the
evidence pipelines in phlo-dagster, phlo-dlt, and phlo-iceberg.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from phlo.exceptions import redact_sensitive_text
from phlo.helpers._common import is_sensitive_key

_TEXT_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|secret|client[_-]?secret|authorization|api[_-]?key|access[_-]?token|private[_-]?key)\s*[:=]\s*([^&\s/]+)"
)
_ROW_DATA_KEYS = {
    "rows",
    "records",
    "row_data",
    "raw_rows",
    "sample_rows",
    "failure_cases",
    "sample",
    "sample_failures",
}


def redact_payload(value: Any, *, key: str | None = None) -> Any:
    """Deep-redact sensitive keys and credential-bearing strings."""
    if (
        key is not None
        and key.lower() in _ROW_DATA_KEYS
        and isinstance(value, (Mapping, list, tuple, str))
    ):
        return "<redacted>" if value not in (None, "") else value
    if key is not None and is_sensitive_key(key):
        return "<redacted>" if value not in (None, "") else value
    if isinstance(value, Mapping):
        return {str(k): redact_payload(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return _TEXT_SECRET_PATTERN.sub(r"\1=<redacted>", redact_sensitive_text(value))
    return value


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def canonical_json(value: Any) -> str:
    """Return stable JSON used for checksums and compatibility tests."""
    return json.dumps(
        redact_payload(value),
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def payload_checksum(value: Any) -> str:
    """Return a full SHA-256 checksum of the redacted canonical payload."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def safe_error_summary(error: BaseException | str) -> str:
    """Return a bounded, stable error marker without retaining exception text."""
    raw = str(error)
    fingerprint = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]
    error_type = type(error).__name__[:32] if isinstance(error, BaseException) else "provider_error"
    return f"{error_type}:fingerprint:{fingerprint}"
