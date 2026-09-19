"""Browser request-origin policy shared by CORS setup and the CSRF guard.

``PHLO_API_CORS_ORIGINS`` names the browser origins allowed to make
credentialed requests; the loopback regex keeps local Observatory dev servers
working without extra configuration. The same policy must answer both "may a
browser fetch cross-origin" (CORS middleware) and "may a cookie-backed
mutation claim this Origin" (the manifest CSRF guard), so both read it here.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

CORS_ORIGINS_ENV = "PHLO_API_CORS_ORIGINS"

_DEFAULT_ORIGINS_RAW = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001,"
    "http://localhost:3005,http://127.0.0.1:3005,"
    "http://localhost:4000,http://127.0.0.1:4000"
)

DEV_ORIGIN_PATTERN = re.compile(r"^https?://(localhost|127\.0\.0\.1):\d+$")


def allowed_origins() -> list[str]:
    """Return the configured browser origin allow-list."""
    raw = os.environ.get(CORS_ORIGINS_ENV, _DEFAULT_ORIGINS_RAW)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def allow_credentials() -> bool:
    """Browsers reject credentialed CORS responses when origins are `*`."""
    return allowed_origins() != ["*"]


def is_origin_allowed(origin: str) -> bool:
    """Whether a browser Origin may make credentialed requests to this API."""
    origins = allowed_origins()
    if "*" in origins or DEV_ORIGIN_PATTERN.match(origin):
        return True
    return origin in origins


def is_same_origin(origin: str, host: str) -> bool:
    """Whether Origin's netloc matches the request's Host header exactly."""
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(netloc) and netloc.lower() == host.lower()
