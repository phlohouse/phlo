"""Shared primitives for the public lakehouse helper layer.

Provides sensitive-key redaction, strict mapping validation, best-effort
integer coercion, and the compact OperationSummary result payload reused by
the other helper modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from phlo.exceptions import PhloConfigError

SENSITIVE_KEYWORDS = (
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "api_key",
    "api-key",
    "apikey",
    "credential",
    "private_key",
    "signing_key",
    "encryption_key",
)


def is_sensitive_key(key: str) -> bool:
    """Return whether a config key likely contains sensitive material."""
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_KEYWORDS)


def redact_value(value: Any, *, replacement: str = "<redacted>") -> Any:
    """Redact a scalar value while preserving empty values for diagnostics."""
    if value in (None, ""):
        return value
    return replacement


def redact_mapping(
    payload: Mapping[str, Any],
    *,
    replacement: str = "<redacted>",
) -> dict[str, Any]:
    """Deep-redact sensitive values in a mapping."""
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if is_sensitive_key(str(key)):
            redacted[str(key)] = redact_value(value, replacement=replacement)
        elif isinstance(value, Mapping):
            redacted[str(key)] = redact_mapping(value, replacement=replacement)
        elif isinstance(value, list):
            redacted[str(key)] = [
                redact_mapping(item, replacement=replacement) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            redacted[str(key)] = value
    return redacted


def require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    """Return a mapping or raise a Phlo configuration error."""
    if isinstance(value, Mapping):
        return value
    raise PhloConfigError(
        message=f"{label} must be a mapping",
        suggestions=[f"Pass {label} as a dict-like object."],
    )


@dataclass(frozen=True, slots=True)
class OperationSummary:
    """Standard compact result payload for helper operations."""

    status: str
    rows_inserted: int = 0
    rows_deleted: int = 0
    rows_updated: int = 0
    files_written: int = 0
    bytes_written: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Serialize the summary for MaterializeResult or telemetry metadata."""
        return {
            "status": self.status,
            "rows_inserted": self.rows_inserted,
            "rows_deleted": self.rows_deleted,
            "rows_updated": self.rows_updated,
            "files_written": self.files_written,
            "bytes_written": self.bytes_written,
            **self.metadata,
        }


def coerce_int(value: Any, *, default: int = 0) -> int:
    """Coerce a best-effort integer from backend result payloads."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
