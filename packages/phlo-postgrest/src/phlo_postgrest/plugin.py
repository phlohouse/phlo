"""Postgrest service plugin registration.

Declares the schema-generated REST API as a service plugin. The class
object is created at import time via service_plugin_class so plugin
discovery can pick it up without instantiation.
Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly; declares service metadata against phlo.plugins.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


PostgrestServicePlugin = service_plugin_class(
    "PostgrestServicePlugin",
    name="postgrest",
    version="0.1.0",
    description="RESTful API automatically generated from PostgreSQL schema",
    author="Phlo Team",
    tags=["api", "rest"],
)
