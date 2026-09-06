"""Polaris service and resource plugin registrations."""

from __future__ import annotations

from phlo.plugins import service_plugin_class

PolarisServicePlugin = service_plugin_class(
    "PolarisServicePlugin",
    name="polaris",
    version="0.1.0",
    description="Apache Polaris Iceberg REST catalog with OAuth/RBAC and credential vending",
    author="Phlo Team",
    tags=["catalog", "iceberg", "polaris"],
)
