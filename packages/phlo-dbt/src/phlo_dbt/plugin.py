"""Phlo plugin implementations for dbt integration.

This module provides the Phlo plugin classes that register dbt capabilities
with the Phlo platform. It includes both an AssetProviderPlugin (for exposing
dbt models as Phlo assets) and a TransformationProviderPlugin (for dbt-based
data transformations).

Example:
    >>> from phlo_dbt.plugin import DbtAssetProvider, DbtTransformationProvider
    >>>
    >>> # Get dbt assets
    >>> asset_provider = DbtAssetProvider()
    >>> assets = asset_provider.get_assets()
    >>>
    >>> # Get transformation provider
    >>> transform_provider = DbtTransformationProvider()
    >>> cli_plugin = transform_provider.get_cli_plugin()


    dbt plugin module; its asset and transformation providers register via phlo plugin entry points.
    Builds on phlo.capabilities.specs and the phlo.plugins.base plugin interfaces.
"""

from __future__ import annotations

from collections.abc import Iterable

from phlo.capabilities import (
    EvidenceProfileContributionSpec,
    WorkflowContributionMode,
    WorkflowWizardContribution,
    WorkflowWizardField,
)
from phlo.capabilities.specs import AssetSpec
from phlo.plugins.base import (
    AssetProviderPlugin,
    PluginMetadata,
    TransformationProviderPlugin,
)

from phlo_dbt.assets import build_dbt_asset_specs


def get_workflow_wizard_contributions() -> list[WorkflowWizardContribution]:
    """Return provider-neutral workflow wizard contributions for dbt."""

    return [
        WorkflowWizardContribution(
            id="dbt.transform",
            package="phlo-dbt",
            stage="transform",
            label="dbt transform",
            description="Configure dbt project setup, source metadata, models, tests, and transformation operations.",
            required_capabilities=["query_engine"],
            fields=[
                WorkflowWizardField(
                    name="project_name",
                    label="Project name",
                    required=True,
                    description="dbt project name to write into dbt_project.yml.",
                ),
                WorkflowWizardField(
                    name="source_name",
                    label="Source name",
                    required=True,
                    description="dbt source name, usually raw.",
                    default="raw",
                ),
                WorkflowWizardField(
                    name="source_table",
                    label="Source table",
                    required=True,
                    description="Raw table exposed to dbt.",
                ),
                WorkflowWizardField(
                    name="staging_model_name",
                    label="Staging model",
                    required=True,
                    description="Name for the generated staging model.",
                ),
                WorkflowWizardField(
                    name="staging_source_relation",
                    label="Staging source relation",
                    required=True,
                    description="Relation used by the staging model.",
                ),
                WorkflowWizardField(
                    name="enable_rename",
                    label="Rename columns",
                    field_type="select",
                    required=True,
                    description="Whether to generate a rename projection model.",
                    options=["no", "yes"],
                    default="no",
                ),
                WorkflowWizardField(
                    name="renames",
                    label="Renames",
                    field_type="fields",
                    required=False,
                    description="Optional mappings as source_name:target_name.",
                ),
                WorkflowWizardField(
                    name="enable_cast",
                    label="Cast columns",
                    field_type="select",
                    required=True,
                    description="Whether to generate a type casting model.",
                    options=["no", "yes"],
                    default="no",
                ),
                WorkflowWizardField(
                    name="casts",
                    label="Casts",
                    field_type="fields",
                    required=False,
                    description="Optional casts as column:type.",
                ),
                WorkflowWizardField(
                    name="filter_model_name",
                    label="Filter model",
                    required=True,
                    description="Name for the optional filtered model.",
                ),
                WorkflowWizardField(
                    name="where",
                    label="Where clause",
                    field_type="textarea",
                    required=False,
                    description="Optional SQL predicate without the where keyword.",
                ),
                WorkflowWizardField(
                    name="dedupe_model_name",
                    label="Clean model",
                    required=True,
                    description="Name for the optional deduplicated model.",
                ),
                WorkflowWizardField(
                    name="partition_by",
                    label="Deduplicate by",
                    required=True,
                    description="Comma-separated columns that identify duplicates.",
                ),
                WorkflowWizardField(
                    name="order_by",
                    label="Keep latest by",
                    required=True,
                    description="Column or expression used to keep the latest row.",
                ),
                WorkflowWizardField(
                    name="enable_aggregate",
                    label="Aggregate output",
                    field_type="select",
                    required=True,
                    description="Whether to generate an aggregate model.",
                    options=["no", "yes"],
                    default="no",
                ),
                WorkflowWizardField(
                    name="aggregate_model_name",
                    label="Aggregate model",
                    required=False,
                    description="Name for the optional aggregate model.",
                ),
                WorkflowWizardField(
                    name="group_by",
                    label="Group by",
                    required=False,
                    description="Comma-separated grouping columns.",
                ),
                WorkflowWizardField(
                    name="metrics",
                    label="Metrics",
                    field_type="fields",
                    required=False,
                    description="Optional metrics as name:sql_expression.",
                ),
                WorkflowWizardField(
                    name="test_model_name",
                    label="Test model",
                    required=True,
                    description="dbt model to document and test.",
                ),
                WorkflowWizardField(
                    name="unique_key",
                    label="Unique key",
                    required=True,
                    description="Column that should be unique and not null.",
                    default="id",
                ),
            ],
            modes={WorkflowContributionMode.PROPOSAL, WorkflowContributionMode.APPLY},
            metadata={"generator": "phlo-api workflow wizard dbt transform scaffold"},
        ),
    ]


class DbtAssetProvider(AssetProviderPlugin):
    def get_evidence_profile_contributions(self) -> list[EvidenceProfileContributionSpec]:
        """Declare this provider's blessed run-evidence contribution."""
        from phlo.run_evidence.profiles import EvidenceProfileContribution
        from phlo.run_evidence.reconciliation import RequiredEvidenceRecord, RequiredEvidenceStage

        contribution = EvidenceProfileContribution(
            contribution_id="dbt.transform",
            provider="dbt",
            profile_id="wap",
            profile_version="1",
            stages=(RequiredEvidenceStage(stage_type="transform", provider="dbt"),),
            required_records=(RequiredEvidenceRecord(family="artifact", minimum=1),),
        )
        return [EvidenceProfileContributionSpec(name="dbt.transform", provider=contribution)]

    """Asset provider plugin exposing dbt models as Phlo assets.

    This plugin discovers dbt models from the project's manifest and exposes
    them as Phlo AssetSpec objects. This enables dbt models to participate in
    Phlo's orchestration, lineage tracking, and monitoring systems.

    The plugin uses the build_dbt_asset_specs() function to parse the dbt
    manifest and create corresponding asset specifications with proper
    dependencies, metadata, and execution configuration.

    Example:
        >>> from phlo_dbt.plugin import DbtAssetProvider
        >>> provider = DbtAssetProvider()
        >>>
        >>> # Get plugin metadata
        >>> metadata = provider.metadata
        >>> print(f"Plugin: {metadata.name} v{metadata.version}")
        >>>
        >>> # Get dbt assets
        >>> assets = provider.get_assets()
        >>> for asset in assets:
        ...     print(f"Asset: {asset.key}, Group: {asset.group}")

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata describing the dbt asset provider."""
        return PluginMetadata(
            name="dbt",
            version="0.1.0",
            description="dbt models as asset specs",
        )

    def get_assets(self) -> Iterable[AssetSpec]:
        """Return dbt-derived asset specifications."""
        return build_dbt_asset_specs()


class DbtTransformationProvider(TransformationProviderPlugin):
    """Transformation provider plugin for dbt.

    This plugin registers dbt as a transformation provider in Phlo, enabling
    dbt models to be executed as part of Phlo's data pipeline transformations.

    The provider supplies both the asset retriever (for discovering transformable
    assets) and the CLI plugin (for dbt-related CLI commands). This allows Phlo
    to integrate dbt runs into its orchestration and provide unified CLI access
    to dbt operations.

    Example:
        >>> from phlo_dbt.plugin import DbtTransformationProvider
        >>> provider = DbtTransformationProvider()
        >>>
        >>> # Get metadata
        >>> metadata = provider.metadata
        >>> print(f"Transform Provider: {metadata.name}")
        >>>
        >>> # Get asset retriever function
        >>> retriever = provider.get_asset_retriever()
        >>> assets = retriever()
        >>>
        >>> # Get CLI plugin for dbt commands
        >>> cli = provider.get_cli_plugin()
        >>> commands = cli.get_cli_commands()

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata describing the dbt transformation provider."""
        return PluginMetadata(
            name="dbt",
            version="0.1.0",
            description="dbt-based transformation provider",
        )

    def get_asset_retriever(self):
        """Return a callable that yields dbt transformation asset specs."""
        return build_dbt_asset_specs

    def get_cli_plugin(self):
        """Return the CLI plugin exposing dbt commands."""
        from phlo_dbt.cli_plugin import DbtCliPlugin

        return DbtCliPlugin
