"""Clickstack service plugin registration.

Declares the ClickStack observability backend (logs, metrics, traces)
as a service plugin. The class object is created at import time via
service_plugin_class so plugin discovery can pick it up without
instantiation.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Registers the clickstack observability backend service through phlo.plugins.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


ClickStackServicePlugin = service_plugin_class(
    "ClickStackServicePlugin",
    name="clickstack",
    version="0.1.0",
    description="ClickStack all-in-one observability backend",
    author="Phlo Team",
    tags=["observability", "logs", "metrics", "traces"],
)
