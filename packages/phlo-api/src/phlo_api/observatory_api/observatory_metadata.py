"""Shared metadata sanitization for Observatory contracts.

safe_metadata() strips secret-bearing keys and provider URLs by token match
and value patterns, returning deterministic metadata safe to expose to the
browser. Unsafe values are dropped, never redacted in place.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

_PRIVATE_METADATA_TOKENS = (
    "url",
    "uri",
    "dsn",
    "endpoint",
    "connection",
    "password",
    "secret",
    "token",
    "key",
)
_PRIVATE_METADATA_VALUE_PATTERNS = (
    re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE),
    re.compile(r"\b(?:token|password|secret|apikey|api_key|access_key|session)=\S+", re.IGNORECASE),
    re.compile(r"\b(?:bearer|basic)\s+\S+", re.IGNORECASE),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^@\s]+@[^/\s]+", re.IGNORECASE),
)
_UNSAFE_METADATA = object()


def safe_metadata(value: Any) -> dict[str, Any]:
    """Return deterministic non-secret, non-provider-URL metadata."""
    if not isinstance(value, Mapping):
        return {}

    safe: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if any(token in key.lower() for token in _PRIVATE_METADATA_TOKENS):
            continue
        sanitized = _safe_metadata_value(raw_value)
        if sanitized is not _UNSAFE_METADATA:
            safe[key] = sanitized
    return safe


def _safe_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return safe_metadata(value)
    if isinstance(value, list):
        return [
            item
            for item in (_safe_metadata_value(raw_item) for raw_item in value)
            if item is not _UNSAFE_METADATA
        ]
    if isinstance(value, str | int | float | bool) or value is None:
        if isinstance(value, str) and _looks_private_metadata_value(value):
            return _UNSAFE_METADATA
        return value
    return _UNSAFE_METADATA


def _looks_private_metadata_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _PRIVATE_METADATA_VALUE_PATTERNS)
