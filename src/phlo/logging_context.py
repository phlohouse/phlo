"""Logger acquisition and routing context without application composition.

Acquiring a logger does not configure process-wide handlers. Entrypoints use
``phlo.logging.setup_logging``; the public ``phlo.logging.get_logger`` retains
its configure-on-first-use behaviour.
"""

from __future__ import annotations

import contextvars
import sys
import traceback
from collections.abc import MutableMapping
from contextlib import contextmanager
from typing import Any

import structlog

from phlo.exceptions import redact_sensitive_text

_ROUTER_ACTIVE = contextvars.ContextVar("phlo_log_router_active", default=False)
_SENSITIVE_FIELD_TOKENS = (
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "cookie",
    "bearer",
    "private_key",
    "signing_key",
    "encryption_key",
    "cert",
    "ssh",
    "session_id",
    "session_token",
)


def get_logger(
    name: str | None = None, *, service: str | None = None
) -> structlog.stdlib.BoundLogger:
    """Acquire a logger using the entrypoint's process-wide configuration."""
    if not structlog.is_configured():
        # Early discovery logs still redact secrets before an entrypoint sets
        # handlers, levels and routing. Do not replace caller configuration.
        structlog.configure(
            processors=[_redact_sensitive_processor, *structlog.get_config()["processors"]]
        )
    logger = structlog.get_logger(name)
    return logger.bind(service=service) if service else logger


def log_event(logger: Any, level: str, event: str, **fields: Any) -> None:
    """Accept structured loggers and ordinary stdlib loggers."""
    log_method = getattr(logger, level)
    try:
        log_method(event, **fields)
    except TypeError:
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        log_method(f"{event} {details}" if fields else event)


@contextmanager
def suppress_log_routing() -> Any:
    """Prevent hook subscribers from recursively routing their own logs."""
    token = _ROUTER_ACTIVE.set(True)
    try:
        yield
    finally:
        _ROUTER_ACTIVE.reset(token)


def redact_sensitive_fields(data: MutableMapping[str, Any]) -> None:
    """Redact sensitive keys and URL credentials in-place within a mapping."""
    for key, value in list(data.items()):
        lowered = key.lower()
        if any(token in lowered for token in _SENSITIVE_FIELD_TOKENS):
            data[key] = "<redacted>"
            continue
        if key == "exc_info" and value:
            exc_info = value if isinstance(value, tuple) else sys.exc_info()
            if exc_info[0] is not None:
                data["exception"] = redact_sensitive_text(
                    "".join(traceback.format_exception(*exc_info))
                )
            data[key] = None
        elif isinstance(value, str):
            data[key] = redact_sensitive_text(value)
        elif isinstance(value, MutableMapping):
            redact_sensitive_fields(value)
        elif isinstance(value, (list, tuple)):
            sanitized_items = list(value)
            for index, item in enumerate(sanitized_items):
                if isinstance(item, MutableMapping):
                    redact_sensitive_fields(item)
                elif isinstance(item, str):
                    sanitized_items[index] = redact_sensitive_text(item)
            data[key] = tuple(sanitized_items) if isinstance(value, tuple) else sanitized_items


def _redact_sensitive_processor(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    redact_sensitive_fields(event_dict)
    return event_dict
