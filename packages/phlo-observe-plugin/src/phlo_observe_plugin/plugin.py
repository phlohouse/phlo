"""phlo-observer service plugin registration.

Declares the phlo-observer ingestion/query service and its one-shot database
provisioning helper through the shared service plugin factory; behavior lives
in the generic plugin machinery. Loaded through the phlo plugin entry-point
mechanism at startup rather than imported directly.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class

PhloObserverServicePlugin = service_plugin_class(
    "PhloObserverServicePlugin",
    name="phlo-observer",
    version="0.1.0",
    description="Phlo observability ingestion and query service",
    author="Phlo Team",
    tags=["observability", "phlo-observe"],
)

PhloObserverDbSetupPlugin = service_plugin_class(
    "PhloObserverDbSetupPlugin",
    name="phlo-observer-db-setup",
    version="0.1.0",
    description="Provision the phlo-observer PostgreSQL database",
    author="Phlo Team",
    tags=["observability", "phlo-observe", "setup"],
    service_definition_file="db-setup.yaml",
)
