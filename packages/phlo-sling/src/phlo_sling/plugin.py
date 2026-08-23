"""Sling service and ingestion plugins.

This module provides plugin implementations that integrate Sling replication
capabilities into the Phlo plugin system. It exposes both an AssetProviderPlugin
for discovering Sling-backed assets and an IngestionProviderPlugin for handling
Sling-based data ingestion.

Classes:
    SlingAssetProvider: Provides Sling replication assets to the Phlo runtime.
    SlingIngestionProvider: Provides Sling-based ingestion capabilities.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Contributes Sling asset and ingestion providers plus wizard workflows via phlo.capabilities.specs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from phlo.capabilities import (
    WorkflowContributionMode,
    WorkflowWizardContribution,
    WorkflowWizardField,
)
from phlo.capabilities.specs import AssetCheckSpec, AssetSpec, WorkflowAuthoringSpec
from phlo.plugins.base import AssetProviderPlugin, IngestionProviderPlugin, PluginMetadata

from phlo_sling.decorator import clear_sling_assets, get_sling_assets


def get_workflow_wizard_contributions() -> list[WorkflowWizardContribution]:
    """Return provider-neutral workflow wizard contributions for Sling."""

    return [
        WorkflowWizardContribution(
            id="sling.replication-source",
            package="phlo-sling",
            stage="source",
            label="Sling replication",
            description="Replicate database or file streams into a managed Phlo table.",
            required_capabilities=["table_store"],
            fields=[
                WorkflowWizardField(
                    name="domain",
                    label="Domain",
                    required=True,
                    description="Workflow domain, such as customers or billing.",
                ),
                WorkflowWizardField(
                    name="source_name",
                    label="Source name",
                    required=True,
                    description="Sling source connection name.",
                ),
                WorkflowWizardField(
                    name="source_stream",
                    label="Source stream",
                    required=True,
                    description="Stream, table, or file path to replicate.",
                ),
                WorkflowWizardField(
                    name="target_table",
                    label="Target table",
                    required=True,
                    description="Destination table and generated asset name.",
                ),
                WorkflowWizardField(
                    name="primary_key",
                    label="Primary key",
                    required=True,
                    default="id",
                    description="Column used for incremental replication and deduplication.",
                ),
                WorkflowWizardField(
                    name="replication_mode",
                    label="Replication mode",
                    field_type="select",
                    required=True,
                    default="incremental",
                    options=["incremental", "full-refresh", "snapshot"],
                    description="How Sling should keep the target table in sync.",
                ),
                WorkflowWizardField(
                    name="update_key",
                    label="Update key",
                    required=False,
                    description="Optional cursor column for incremental streams.",
                ),
                WorkflowWizardField(
                    name="schedule",
                    label="Schedule",
                    default="0 2 * * *",
                    description="Cron schedule for the generated replication asset.",
                ),
            ],
            modes={WorkflowContributionMode.PROPOSAL, WorkflowContributionMode.APPLY},
            metadata={"generator": "phlo-api workflow wizard Sling scaffold"},
        )
    ]


class SlingAssetProvider(AssetProviderPlugin):
    """Provide Sling-defined replication assets and checks to Phlo.

    This plugin class discovers and exposes Sling replication assets registered
    via decorators to the Phlo orchestration runtime. It manages the lifecycle
    of Sling asset registrations.

    Example:
        The plugin is automatically discovered by the Phlo plugin system::

            # No manual registration needed - discovered via entry points
            assets = SlingAssetProvider().get_assets()

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for discovery and registration."""
        return PluginMetadata(
            name="sling",
            version="0.1.0",
            description="Sling-based replication engine for Phlo",
        )

    def get_assets(self) -> Iterable[AssetSpec]:
        """Return registered Sling replication assets.

        Retrieves all Sling replication assets that have been registered
        via the @phlo_sling_replication or @phlo_sling_assets decorators.
        """
        return get_sling_assets()

    def get_checks(self) -> Iterable[AssetCheckSpec]:
        """Return asset checks exposed by this provider.

        Currently empty: Sling replication assets do not expose any
        built-in asset checks through this provider.
        """
        return []

    def clear_registries(self) -> None:
        """Clear in-memory Sling replication asset registrations.

        Typically called during testing or plugin reload scenarios.
        """
        clear_sling_assets()


class SlingIngestionProvider(IngestionProviderPlugin):
    """Sling-based ingestion provider for Phlo.

    This plugin class exposes Sling replication as an ingestion mechanism
    within the Phlo platform. It provides the decorator and asset retrieval
    functions needed to define and execute Sling-based data replication.

    Example:
        The provider exposes the replication decorator::

            decorator = SlingIngestionProvider().get_decorator()
            @decorator(stream_name="source", table_name="target", ...)
            def my_replication():
                pass

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        return PluginMetadata(
            name="sling",
            version="0.1.0",
            description="Sling-based replication provider with database replication",
        )

    def get_decorator(self) -> Callable:
        """Return the @phlo_sling_replication decorator.

        The returned decorator registers Sling-backed replication assets.
        """
        from phlo_sling import phlo_sling_replication

        return phlo_sling_replication

    def get_asset_retriever(self) -> Callable[[], list[Any]]:
        """Return callable that lists registered replication assets."""
        return get_sling_assets

    def get_workflow_wizard_contributions(self) -> list[WorkflowWizardContribution]:
        """Return workflow wizard contributions exposed by Sling."""
        return get_workflow_wizard_contributions()

    def get_workflow_authoring_providers(self) -> list[WorkflowAuthoringSpec]:
        """Return workflow authoring capabilities exposed by Sling."""
        return [
            WorkflowAuthoringSpec(
                name="sling",
                provider=SlingWorkflowAuthoringProvider(),
                metadata={"contribution_id": "sling.replication-source"},
            )
        ]


class SlingWorkflowAuthoringProvider:
    """Create Sling-backed replication workflow files."""

    def create_workflow(self, *, project_root: Path, request: dict[str, Any]) -> dict[str, Any]:
        """Write the Sling ingestion asset module and replication config for
        a new workflow, returning created files and next steps.

        Raises: ValueError when required values are missing or the requested
        contribution is not Sling's; FileExistsError when target files exist.
        """
        values = dict(request.get("values") or {})
        contribution_id = request.get("contribution_id")
        if contribution_id not in {None, "", "sling.replication-source"}:
            raise ValueError(f"Sling cannot author contribution {contribution_id!r}")

        domain = _slug(str(values.get("domain") or request.get("domain") or ""))
        table = _slug(
            str(values.get("target_table") or values.get("table") or request.get("table") or "")
        )
        source_name = str(values.get("source_name") or "")
        source_stream = str(values.get("source_stream") or table)
        primary_key = str(
            values.get("primary_key") or values.get("unique_key") or request.get("unique_key") or ""
        )
        replication_mode = str(values.get("replication_mode") or "incremental")
        update_key = str(values.get("update_key") or "")
        cron = str(values.get("schedule") or request.get("cron") or "0 2 * * *")

        if not domain or not table or not source_name or not source_stream or not primary_key:
            raise ValueError(
                "Sling workflow creation requires domain, target_table, source_name, source_stream, and primary_key."
            )

        asset_path = project_root / "workflows" / "ingestion" / domain / f"{table}_sling.py"
        config_path = project_root / "workflows" / "ingestion" / domain / f"{table}_sling.yml"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        existing = [path for path in (asset_path, config_path) if path.exists()]
        if existing:
            raise FileExistsError(
                "Files already exist:\n" + "\n".join(f"  - {path}" for path in existing)
            )

        asset_path.write_text(
            _render_sling_asset(
                domain, table, primary_key, source_name, source_stream, replication_mode, cron
            ),
            encoding="utf-8",
        )
        config_path.write_text(
            _render_sling_config(
                table, primary_key, source_name, source_stream, replication_mode, update_key
            ),
            encoding="utf-8",
        )
        files = [
            str(asset_path.relative_to(project_root)),
            str(config_path.relative_to(project_root)),
        ]
        return {
            "workflow_type": "ingestion",
            "provider": "sling",
            "domain": domain,
            "table": table,
            "files": files,
            "next_steps": [
                f"Review workflow: {files[0]}",
                f"Review replication config: {files[1]}",
                "Restart active services if needed: phlo services restart",
                f"Materialize: phlo materialize sling_{table}",
                "Inspect status: phlo status",
            ],
        }


def _render_sling_asset(
    domain: str,
    table: str,
    primary_key: str,
    source_name: str,
    source_stream: str,
    replication_mode: str,
    cron: str,
) -> str:
    return f'''"""Sling replication asset for {domain}.{table}."""

from phlo_sling import phlo_sling_replication


@phlo_sling_replication(
    stream_name="{source_stream}",
    table_name="{table}",
    source_conn="{source_name}",
    group="{domain}",
    mode="{replication_mode}",
    primary_key="{primary_key}",
    cron="{cron}",
)
def {table}_sling():
    return "{table}_sling.yml"
'''


def _render_sling_config(
    table: str,
    primary_key: str,
    source_name: str,
    source_stream: str,
    replication_mode: str,
    update_key: str,
) -> str:
    update_key_line = f'    update_key: "{update_key}"\n' if update_key else ""
    return f"""source: {source_name}
streams:
  {source_stream}:
    object: {table}
    mode: {replication_mode}
    primary_key: "{primary_key}"
{update_key_line}"""


def _slug(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    return slug or "workflow"
