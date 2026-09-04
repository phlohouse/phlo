"""Dagster orchestration adapter package for Phlo.

This package provides the Dagster-based orchestration layer for Phlo data pipelines.
It bridges Phlo's capability-based architecture with Dagster's asset-centric execution model.

Key Components:
    - DagsterOrchestratorAdapter: Translates Phlo capability specs into Dagster definitions
    - DagsterServicePlugin: Manages Dagster webserver and daemon services
    - DagsterExtensionPlugin: Extensibility interface for custom Dagster plugins
    - Framework definitions: Entry point for user workflow discovery
    - DagsterRegulatedSurfaceAdapter: Regulated surface adapter for Dagster GraphQL API
    - DagsterGraphQLAuthorizationMiddleware: GraphQL middleware for authorization enforcement

Integration Points:
    - Translates AssetSpec objects into @asset decorated functions
    - Converts AssetCheckSpec into Dagster asset checks
    - Maps ResourceSpec to Dagster resources
    - Supports partitioned assets (daily, etc.)
    - Handles Dagster-specific configuration (freshness policies, automation conditions)

Example:
    Basic usage within a Phlo project::

        from phlo_dagster import DagsterServicePlugin

        # Service plugin handles container orchestration
        plugin = DagsterServicePlugin()

    Framework definitions entry point::

        # In workspace.yaml
        load_from:
          - python_module:
              module_name: phlo_dagster.framework.definitions

    Regulated surface adapter::

        from phlo_dagster.authorization import get_adapter

        adapter = get_adapter()
        adapter.install(dagster_webserver_instance)
"""

from phlo_dagster.authorization import get_adapter
from phlo_dagster.authorization_middleware import DagsterGraphQLAuthorizationMiddleware
from phlo_dagster.dagster_ext import DagsterExtensionPlugin, IngestionEnginePlugin
from phlo_dagster.daemon_identity import (
    PhloQueuedRunCoordinator,
    authorize_daemon_run,
    create_daemon_principal,
)
from phlo_dagster.plugin import DagsterServicePlugin
from phlo_dagster.run_evidence import DagsterRunEvidenceSource
from phlo_dagster.settings import DagsterSettings, get_settings

__all__ = [
    "DagsterServicePlugin",
    "DagsterExtensionPlugin",
    "IngestionEnginePlugin",
    "DagsterSettings",
    "get_settings",
    "get_adapter",
    "DagsterGraphQLAuthorizationMiddleware",
    "DagsterRunEvidenceSource",
    "PhloQueuedRunCoordinator",
    "authorize_daemon_run",
    "create_daemon_principal",
]
from importlib.metadata import version

__version__ = version("phlo-dagster")
