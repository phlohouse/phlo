"""Grafana service plugin registration.

Declares the Grafana metrics/dashboards service through the shared service
plugin factory; behavior lives in the generic plugin machinery.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Registers the grafana metrics service through the phlo.plugins service plugin factory.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


GrafanaServicePlugin = service_plugin_class(
    "GrafanaServicePlugin",
    name="grafana",
    version="0.1.0",
    description="Metrics visualization and dashboards",
    author="Phlo Team",
    tags=["observability", "metrics", "dashboards"],
)
