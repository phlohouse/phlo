"""Dagster service plugins for Phlo infrastructure management.

This module provides ServicePlugin implementations that register Dagster
services with Phlo's infrastructure management system. It handles service
definition loading from YAML files and provides metadata for the
Dagster webserver and daemon components.

Service Components:
    - DagsterServicePlugin: Main webserver service
    - DagsterDaemonServicePlugin: Background scheduler/sensor daemon

Service Definitions:
    Services are defined in YAML files (service.yaml, dagster-daemon.yaml)
    that specify Docker Compose configuration, ports, dependencies, and
    startup behavior. These files are loaded from the package resources.

Plugin Registration:
    Plugins are auto-discovered via entry_points (group: phlo.plugins.services)
    and contribute service definitions to the infrastructure orchestrator.

Service Responsibilities:
    - Dagster Webserver: Serves UI, handles GraphQL queries, executes runs
    - Dagster Daemon: Runs schedules, sensors, and daemon loops

Example:
    Service definition structure (service.yaml)::

        service:
          name: dagster
          description: Data orchestration platform
          ports:
            - "3000:3000"
          depends_on:
            - postgres
            - trino


Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly.
"""

from __future__ import annotations

from phlo.capabilities import (
    WorkflowContributionMode,
    WorkflowWizardContribution,
    WorkflowWizardField,
)
from phlo.plugins import service_plugin_class


def get_workflow_wizard_contributions() -> list[WorkflowWizardContribution]:
    """Return provider-neutral workflow wizard contributions for Dagster."""

    return [
        WorkflowWizardContribution(
            id="dagster.orchestration",
            package="phlo-dagster",
            stage="publish",
            label="Dagster orchestration",
            description="Create a Dagster definitions module that wires generated assets into a scheduled job.",
            required_capabilities=["orchestrator"],
            optional_capabilities=["asset_observability"],
            fields=[
                WorkflowWizardField(
                    name="job_name",
                    label="Job name",
                    required=True,
                    description="Dagster job name for this workflow.",
                ),
                WorkflowWizardField(
                    name="asset_group",
                    label="Asset group",
                    required=True,
                    description="Group name for generated workflow assets.",
                ),
                WorkflowWizardField(
                    name="schedule",
                    label="Schedule",
                    required=True,
                    default="0 2 * * *",
                    description="Cron schedule for the generated Dagster schedule.",
                ),
                WorkflowWizardField(
                    name="include_sensor",
                    label="Include sensor",
                    field_type="select",
                    required=True,
                    default="no",
                    options=["no", "yes"],
                    description="Whether to scaffold a placeholder sensor for external events.",
                ),
            ],
            modes={WorkflowContributionMode.PROPOSAL, WorkflowContributionMode.APPLY},
            metadata={"generator": "phlo-api workflow wizard Dagster scaffold"},
        )
    ]


DagsterServicePlugin = service_plugin_class(
    "DagsterServicePlugin",
    name="dagster",
    version="0.1.0",
    description="Data orchestration platform for workflows and pipelines",
    author="Phlo Team",
    tags=["orchestration", "core"],
)


DagsterDaemonServicePlugin = service_plugin_class(
    "DagsterDaemonServicePlugin",
    name="dagster-daemon",
    version="0.1.0",
    description="Dagster daemon for background scheduling and sensors",
    author="Phlo Team",
    tags=["orchestration", "core"],
    service_definition_file="dagster-daemon.yaml",
)
