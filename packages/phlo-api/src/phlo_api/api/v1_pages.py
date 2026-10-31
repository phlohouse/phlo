"""Opaque v1 page cursors bound to the resource, environment, and filters.

A cursor carries a position (an offset or an upstream cursor) together with
the scope it was issued for. Presenting it with a different scope, or
presenting a malformed cursor, is a client error rather than a silent restart
at the first page.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from phlo_api.errors import BadInputError

_MAX_CURSOR_LENGTH = 4096


def encode_cursor(scope: list[Any], position: int | str) -> str:
    """Return an opaque cursor for ``position`` within ``scope``."""
    raw = json.dumps({"scope": scope, "position": position}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, scope: list[Any]) -> int | str:
    """Return the position encoded in ``cursor``; reject foreign or malformed cursors."""
    try:
        if len(cursor) > _MAX_CURSOR_LENGTH:
            raise ValueError
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        position = value["position"]
        if value["scope"] != scope or not (
            (type(position) is int and position >= 0) or (isinstance(position, str) and position)
        ):
            raise ValueError
        return position
    except (ValueError, TypeError, KeyError) as exc:
        raise BadInputError("Cursor is malformed or was issued for another scope.") from exc


def decode_offset(cursor: str | None, scope: list[Any]) -> int:
    """Return the page offset for ``cursor``; ``None`` starts at the first page."""
    if cursor is None:
        return 0
    position = decode_cursor(cursor, scope)
    if not isinstance(position, int):
        raise BadInputError("Cursor is malformed or was issued for another scope.")
    return position


def reject_unknown_query(request: Any, allowed: frozenset[str]) -> None:
    """Reject query parameters, including ``env``, that a project-scoped read does not accept."""
    if "env" in request.query_params and "env" not in allowed:
        raise BadInputError("This resource is project-scoped; the env selector is not accepted.")
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise BadInputError(f"Unsupported query parameters: {', '.join(unknown)}.")
