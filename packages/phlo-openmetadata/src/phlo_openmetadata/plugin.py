"""OpenMetadata service plugin.

Provides OpenMetadata as a managed service within the Phlo plugin framework.
This plugin exposes service configuration and metadata for the OpenMetadata
data catalog and governance platform.

Example:
    >>> from phlo_openmetadata.plugin import OpenMetadataServicePlugin
    >>> plugin = OpenMetadataServicePlugin()
    >>> plugin.metadata.name
    'openmetadata'

Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Registers the openmetadata service and wizard contributions via phlo.capabilities.
"""

from __future__ import annotations

from phlo.capabilities import (
    WorkflowContributionMode,
    WorkflowWizardContribution,
    WorkflowWizardField,
)
from phlo.plugins import service_plugin_class


def get_workflow_wizard_contributions() -> list[WorkflowWizardContribution]:
    """Return provider-neutral workflow wizard contributions for OpenMetadata."""

    return [
        WorkflowWizardContribution(
            id="openmetadata.catalog",
            package="phlo-openmetadata",
            stage="publish",
            label="OpenMetadata catalog",
            description="Create catalog metadata for generated tables, owners, tags, and lineage handoff.",
            required_capabilities=["metadata_catalog"],
            fields=[
                WorkflowWizardField(
                    name="service_name",
                    label="Service name",
                    required=True,
                    default="phlo",
                    description="OpenMetadata service that owns the generated table.",
                ),
                WorkflowWizardField(
                    name="database",
                    label="Database",
                    required=True,
                    default="warehouse",
                    description="Catalog database name.",
                ),
                WorkflowWizardField(
                    name="schema",
                    label="Schema",
                    required=True,
                    description="Catalog schema or namespace.",
                ),
                WorkflowWizardField(
                    name="owner",
                    label="Owner",
                    required=False,
                    description="Team or user owner to assign.",
                ),
                WorkflowWizardField(
                    name="tags",
                    label="Tags",
                    field_type="fields",
                    required=False,
                    description="One tag per line.",
                ),
                WorkflowWizardField(
                    name="description",
                    label="Description",
                    field_type="textarea",
                    required=False,
                    description="Human-readable catalog description.",
                ),
            ],
            modes={WorkflowContributionMode.PROPOSAL, WorkflowContributionMode.APPLY},
            metadata={"generator": "phlo-api workflow wizard OpenMetadata scaffold"},
        )
    ]


OpenMetadataServicePlugin = service_plugin_class(
    "OpenMetadataServicePlugin",
    name="openmetadata",
    version="0.1.0",
    description="OpenMetadata data catalog and governance",
    author="Phlo Team",
    tags=["catalog", "governance", "metadata"],
)
