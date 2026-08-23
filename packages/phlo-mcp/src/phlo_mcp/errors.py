"""Structured error envelopes for MCP tool results.

Maps httpx failures onto stable, agent-readable error codes with retryability
flags so callers can react to transport problems without parsing exception text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class PhloMcpError(Exception):
    """Structured MCP error with machine-readable code and optional guidance."""

    code: str
    message: str
    hint: str | None = None
    docs_url: str | None = None
    retryable: bool = False

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serializable payload for API responses."""
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "docs_url": self.docs_url,
            "retryable": self.retryable,
        }

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def map_httpx_error(exc: httpx.HTTPError) -> PhloMcpError:
    """Map httpx failures into stable agent-readable error codes."""
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        detail = _http_error_detail(exc.response)
        code = {
            400: "phlo.api.bad_request",
            401: "phlo.auth.unauthorized",
            403: "phlo.auth.forbidden",
            404: "phlo.resource.not_found",
            409: "phlo.api.conflict",
            422: "phlo.api.validation_failed",
            429: "phlo.api.rate_limited",
            500: "phlo.api.internal_error",
            502: "phlo.api.bad_gateway",
            503: "phlo.api.unavailable",
            504: "phlo.api.timeout",
        }.get(status_code, "phlo.api.http_error")
        return PhloMcpError(
            code=code,
            message=detail.get("message") or f"phlo-api returned HTTP {status_code}",
            hint=detail.get("hint")
            or detail.get("error")
            or "Check phlo-api logs and the requested resource identifier.",
            retryable=status_code in {429, 500, 502, 503, 504},
        )
    if isinstance(exc, httpx.TimeoutException):
        return PhloMcpError(
            code="phlo.api.timeout",
            message="Timed out calling phlo-api",
            hint="Retry or check whether phlo-api and its backends are healthy.",
            retryable=True,
        )
    if isinstance(exc, httpx.ConnectError):
        return PhloMcpError(
            code="phlo.api.unreachable",
            message="Could not connect to phlo-api",
            hint="Start phlo-api or update PHLO_MCP_API_BASE_URL.",
            retryable=True,
        )
    return PhloMcpError(
        code="phlo.api.unknown",
        message=str(exc),
        hint="Inspect the MCP server logs for the original exception.",
        retryable=False,
    )


def _http_error_detail(response: httpx.Response) -> dict[str, str]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        return {key: str(value) for key, value in detail.items() if value is not None}
    if isinstance(detail, str):
        return {"message": detail}
    return {}
