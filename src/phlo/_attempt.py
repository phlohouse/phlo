"""Shared validation for positive execution-attempt correlation.

Normalizes attempt values from tags or payloads without ever aliasing invalid
retry metadata to attempt 1; malformed values surface as errors or explicit
"invalid" markers instead of a silent first-attempt retry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_attempt(value: Any) -> int:
    """Return a positive integer attempt or reject malformed retry metadata.

    >>> normalize_attempt("3")
    3
    >>> normalize_attempt(2)
    2
    >>> normalize_attempt(True)
    Traceback (most recent call last):
        ...
    ValueError: attempt must be a positive integer
    >>> normalize_attempt(0)
    Traceback (most recent call last):
        ...
    ValueError: attempt must be a positive integer
    """
    # bool is an int subclass, so an unguarded isinstance check would accept
    # True as attempt 1. Reject it before the int branch.
    if isinstance(value, bool):
        raise ValueError("attempt must be a positive integer")
    if isinstance(value, int):
        attempt = value
    elif isinstance(value, str) and value.strip():
        try:
            attempt = int(value)
        except ValueError as exc:
            raise ValueError("attempt must be a positive integer") from exc
    else:
        raise ValueError("attempt must be a positive integer")
    if attempt <= 0:
        raise ValueError("attempt must be a positive integer")
    return attempt


def attempt_from_tags(tags: Mapping[str, str]) -> tuple[int | None, str | None]:
    """Parse an optional retry tag without aliasing invalid values to attempt one.

    A missing tag means first attempt. A malformed tag yields ``(None,
    "invalid_attempt")`` rather than silently retrying as attempt 1, so callers
    can surface the bad retry metadata.

    >>> attempt_from_tags({})
    (1, None)
    >>> attempt_from_tags({"phlo/attempt": "4"})
    (4, None)
    >>> attempt_from_tags({"phlo/attempt": "not-a-number"})
    (None, 'invalid_attempt')
    """
    raw_attempt = tags.get("phlo/attempt")
    if raw_attempt is None:
        return 1, None
    try:
        return normalize_attempt(raw_attempt), None
    except ValueError:
        return None, "invalid_attempt"
