"""Superset service plugin registration.

Declares the Superset BI service through the shared service plugin factory;
behavior lives in the generic plugin machinery.

Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


SupersetServicePlugin = service_plugin_class(
    "SupersetServicePlugin",
    name="superset",
    version="0.1.0",
    description="Apache Superset for business intelligence and data visualization",
    author="Phlo Team",
    tags=["bi", "superset", "visualization"],
)
