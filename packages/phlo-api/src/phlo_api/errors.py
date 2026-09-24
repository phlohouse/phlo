"""Typed API errors and the shared error envelope.

Mounted routes report failure by raising a ``PhloApiError`` subclass; the
exception handler registered in ``phlo_api.main`` serializes it into a single
error body shape::

    {"error": {"code": "<machine code>", "message": "<client-safe message>"}}

with the matching HTTP status. Internal exception text is logged server-side
instead of returned to clients, so exception messages set on these types must
be safe to display to callers.
"""

from __future__ import annotations

from typing import Any


class PhloApiError(Exception):
    """Base class for failures reported through the shared error envelope.

    ``status_code`` selects the HTTP status and ``code`` the stable
    machine-readable identifier written into the response body.
    """

    status_code = 500
    code = "internal_error"
    default_message = "Internal server error."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        if code is not None:
            self.code = code


class BadInputError(PhloApiError):
    """The request input is invalid (400)."""

    status_code = 400
    code = "bad_input"
    default_message = "The request is invalid."


class UnprocessableInputError(PhloApiError):
    """The request is well-formed but cannot be processed (422)."""

    status_code = 422
    code = "unprocessable_input"
    default_message = "The request could not be processed."


class NotFoundError(PhloApiError):
    """The requested resource does not exist (404)."""

    status_code = 404
    code = "not_found"
    default_message = "The requested resource was not found."


class ConflictError(PhloApiError):
    """The request conflicts with current server state (409)."""

    status_code = 409
    code = "conflict"
    default_message = "The request conflicts with current state."


class BadGatewayError(PhloApiError):
    """An upstream backend returned a failed or unreadable response (502)."""

    status_code = 502
    code = "bad_gateway"
    default_message = "An upstream backend request failed."


class BackendUnavailableError(PhloApiError):
    """A required backend is unavailable or could not be queried (503)."""

    status_code = 503
    code = "backend_unavailable"
    default_message = "A required backend is unavailable."


def error_envelope(exc: PhloApiError) -> dict[str, Any]:
    """Serialize a typed error into the shared ``{"error": {...}}`` body."""
    return {"error": {"code": exc.code, "message": exc.message}}
