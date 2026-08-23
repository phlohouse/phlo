"""Opaque cursor pagination helpers for API list envelopes.

Cursors encode a plain offset, so pages are cheap but not stable: items
inserted or removed between requests shift later page boundaries.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def encode_cursor(offset: int) -> str:
    """Encode a page offset as an opaque, URL-safe cursor string."""
    payload = json.dumps({"offset": max(0, offset)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_cursor(cursor: str | None) -> int:
    """Decode a cursor to its page offset; missing or malformed cursors resolve to 0."""
    if not cursor:
        return 0
    # A malformed cursor falls back to offset 0 rather than a client error;
    # callers always receive a valid page from the start of the list.
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return 0
    offset = payload.get("offset", 0)
    return offset if isinstance(offset, int) and offset >= 0 else 0


def paginate_items(
    items: Sequence[T], *, limit: int, cursor: str | None = None
) -> tuple[list[T], str | None]:
    """Slice one page of items and return it with the next cursor, or None when done."""
    safe_limit = max(1, min(limit, 500))
    offset = decode_cursor(cursor)
    page = list(items[offset : offset + safe_limit])
    next_offset = offset + len(page)
    next_cursor = encode_cursor(next_offset) if next_offset < len(items) else None
    return page, next_cursor
