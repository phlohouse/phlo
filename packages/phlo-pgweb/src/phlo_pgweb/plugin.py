"""Pgweb service plugin registration.

PgwebServicePlugin is built via service_plugin_class so discovery offers pgweb
as a managed PostgreSQL browser service.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Registers the pgweb PostgreSQL browser service through the phlo.plugins factory.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


PgwebServicePlugin = service_plugin_class(
    "PgwebServicePlugin",
    name="pgweb",
    version="0.1.0",
    description="Web-based PostgreSQL database browser",
    author="Phlo Team",
    tags=["admin", "postgres", "ui"],
)
