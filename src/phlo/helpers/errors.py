"""Error handling helpers for workflow code.

Classifies exceptions into coarse categories (authorization, network, schema,
not_found) that drive failure hints; with_phlo_errors wraps unexpected errors
in PhloIngestionError with suggestions, retry_transient retries only
transient classifications, and collect_errors gathers partial failures
instead of raising on the first item.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, TypeVar

from phlo.exceptions import PhloError, PhloIngestionError

T = TypeVar("T")


def classify_exception(exc: Exception) -> str:
    """Classify common lakehouse exception families."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "auth" in name or "permission" in text or "forbidden" in text:
        return "authorization"
    if "timeout" in name or "connection" in name or "network" in text:
        return "network"
    if "schema" in name or "schema" in text:
        return "schema"
    if "not found" in text or "missing" in text:
        return "not_found"
    return "unknown"


def failure_hint(exc: Exception, *, operation: str | None = None) -> list[str]:
    """Return suggested actions for a failure."""
    category = classify_exception(exc)
    if category == "authorization":
        return ["Check credentials, service identity, and authorization policy for this operation."]
    if category == "network":
        return ["Check that the target service is running and reachable from the workflow runtime."]
    if category == "schema":
        return ["Compare the source and target schemas before writing data."]
    if category == "not_found":
        return ["Check table, namespace, branch, and connection names."]
    if operation:
        return [f"Inspect logs for the failed {operation} operation."]
    return ["Inspect the underlying exception and Phlo service logs."]


@contextmanager
def with_phlo_errors(operation: str):
    """Wrap unexpected exceptions in PhloIngestionError with suggestions."""
    try:
        yield
    except PhloError:
        raise
    except Exception as exc:
        raise PhloIngestionError(
            message=f"{operation} failed",
            suggestions=failure_hint(exc, operation=operation),
            cause=exc,
        ) from exc


def retry_transient(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    delay_seconds: float = 0.25,
    retry_categories: set[str] | None = None,
) -> T:
    """Retry a callable for transient exception categories."""
    retry_categories = retry_categories or {"network"}
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if classify_exception(exc) not in retry_categories or attempt == attempts - 1:
                raise
            time.sleep(delay_seconds * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def collect_errors(items: list[Any], fn: Callable[[Any], Any]) -> dict[str, Any]:
    """Run an operation across items and collect partial failures."""
    results: list[Any] = []
    errors: list[dict[str, str]] = []
    for item in items:
        try:
            results.append(fn(item))
        except Exception as exc:
            errors.append(
                {"item": str(item), "error": str(exc), "category": classify_exception(exc)}
            )
    return {"results": results, "errors": errors, "ok": not errors}
