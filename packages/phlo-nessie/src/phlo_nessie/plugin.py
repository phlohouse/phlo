"""Nessie service plugin registration.

Declares the core Nessie catalog service plugin through the shared service
plugin factory; the module carries metadata only.

Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


NessieServicePlugin = service_plugin_class(
    "NessieServicePlugin",
    name="nessie",
    version="0.1.0",
    description="Git-like catalog for Iceberg tables with branch/merge support",
    author="Phlo Team",
    tags=["core", "catalog", "iceberg"],
)
