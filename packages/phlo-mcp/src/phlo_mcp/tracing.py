"""Canonical MCP operation scopes over observe-core."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from functools import wraps
from typing import Any

import phlo.telemetry as phlo_observe


class _OperationScope(AbstractContextManager["_OperationScope"]):
    """Observe-core operation carrying the MCP span attributes."""

    def __init__(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.name = name
        self.attributes = attributes or {}
        self._scope: Any = None

    def __enter__(self) -> "_OperationScope":
        self._scope = phlo_observe.observe(self.name)
        event = self._scope.__enter__()
        if self.attributes:
            event.set(**self.attributes)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        return self._scope.__exit__(exc_type, exc, traceback)


class CanonicalTracer:
    """Compatibility façade for MCP nesting while observe-core owns activity."""

    def start_as_current_span(
        self, name: str, *, attributes: dict[str, Any] | None = None
    ) -> _OperationScope:
        return _OperationScope(name, attributes)


def get_tracer() -> CanonicalTracer:
    """Return the MCP operation façade; no OTel provider is installed here."""
    return CanonicalTracer()


def trace_tool[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Trace every tool dispatch without changing its MCP signature."""

    @wraps(fn)
    def traced(*args: P.args, **kwargs: P.kwargs) -> R:
        tracer = get_tracer()
        attributes = {"mcp.tool.name": traced.__name__}
        with tracer.start_as_current_span("mcp.request", attributes=attributes):
            with tracer.start_as_current_span("mcp.tool.execute", attributes=attributes):
                return fn(*args, **kwargs)

    return traced
