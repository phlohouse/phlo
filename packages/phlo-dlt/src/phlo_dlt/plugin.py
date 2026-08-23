"""Plugin interface for Phlo DLT integration.

This module provides the plugin classes that integrate phlo-dlt with the
Phlo plugin system. It exposes DLT-based ingestion capabilities through
standardized plugin interfaces.

Plugin Classes:
    - :class:`DltAssetProvider`: Provides DLT-defined assets to Phlo
    - :class:`DLTIngestionProvider`: Provides ingestion decorator interface

Plugin Registration:
    These plugins are discovered via entry points defined in pyproject.toml:
    - ``phlo.asset_providers``: DltAssetProvider
    - ``phlo.ingestion_providers``: DLTIngestionProvider

Capabilities Exposed:
    - Ingestion asset definitions from @phlo_ingestion decorators
    - Asset check specifications for Pandera validation
    - The phlo_ingestion decorator for users

See Also:
    - :mod:`phlo.plugins.base`: Base plugin interfaces
    - :mod:`phlo.plugins.discovery`: Plugin discovery system
    - :mod:`phlo_dlt.decorator`: Asset registration source

Example:
    The plugins are auto-discovered by Phlo. Users interact with them
    via the public API:
    ```python
    import phlo

    # Uses DLTIngestionProvider internally
    @phlo.ingestion.phlo_ingestion(table_name="users", ...)
    def load_users(): ...

    # Uses DltAssetProvider internally
    assets = phlo.ingestion.get_ingestion_assets()
    ```


    dlt plugin module; its asset and ingestion providers register via phlo plugin entry points.
    Builds on phlo.capabilities.specs and the phlo.plugins.base plugin interfaces.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from phlo.capabilities import (
    NamespaceResolverSpec,
    WorkflowContributionMode,
    WorkflowWizardContribution,
    WorkflowWizardField,
)
from phlo.capabilities.specs import AssetCheckSpec, AssetSpec, WorkflowAuthoringSpec
from phlo.plugins.base import AssetProviderPlugin, IngestionProviderPlugin, PluginMetadata

from phlo_dlt.decorator import clear_ingestion_assets, get_ingestion_assets


def get_workflow_wizard_contributions() -> list[WorkflowWizardContribution]:
    """Return provider-neutral workflow wizard contributions for DLT."""

    return [
        WorkflowWizardContribution(
            id="dlt.rest-api-source",
            package="phlo-dlt",
            stage="source",
            label="REST API source",
            description="Create a DLT ingestion asset for a REST API source.",
            required_capabilities=["table_store"],
            optional_capabilities=["quality_backend"],
            fields=[
                WorkflowWizardField(
                    name="domain",
                    label="Domain",
                    required=True,
                    description="Workflow domain, such as customers or billing.",
                ),
                WorkflowWizardField(
                    name="table_name",
                    label="Table name",
                    required=True,
                    description="Destination table and generated asset name.",
                ),
                WorkflowWizardField(
                    name="unique_key",
                    label="Unique key",
                    required=True,
                    default="id",
                    description="Column used for merge/deduplication.",
                ),
                WorkflowWizardField(
                    name="api_base_url",
                    label="API base URL",
                    required=False,
                    secret=True,
                    description="Optional base URL; omit to leave a runtime placeholder.",
                ),
                WorkflowWizardField(
                    name="response_path",
                    label="Response path",
                    default="",
                    description="Optional JSON list path, such as recipes or data.items.",
                ),
                WorkflowWizardField(
                    name="pagination",
                    label="Pagination",
                    field_type="select",
                    default="none",
                    options=["none", "offset-limit", "page-number"],
                    description="Pagination strategy for list endpoints.",
                ),
                WorkflowWizardField(
                    name="auth",
                    label="Auth",
                    field_type="select",
                    default="none",
                    options=["none", "bearer-token", "api-key-header"],
                    description="Authentication shape to leave as a runtime placeholder.",
                ),
                WorkflowWizardField(
                    name="cron",
                    label="Schedule",
                    default="0 */1 * * *",
                    description="Cron schedule stored in the generated asset.",
                ),
                WorkflowWizardField(
                    name="fields",
                    label="Schema fields",
                    field_type="fields",
                    description="Additional fields as name:type entries.",
                ),
            ],
            modes={WorkflowContributionMode.PROPOSAL, WorkflowContributionMode.APPLY},
            metadata={"generator": "phlo_dlt.scaffold.create_ingestion_workflow"},
        )
    ]


class DltAssetProvider(AssetProviderPlugin):
    """Provide DLT-defined ingestion assets and checks to Phlo.

    Asset provider plugin that exposes all ingestion assets registered
    via the ``@phlo_ingestion`` decorator. Discovered by Phlo's plugin
    system and used during asset loading.

    Example:
        This class is auto-discovered by Phlo. Users don't instantiate it:
        ```python
        # In Phlo internals, this happens:
        from phlo_dlt.plugin import DltAssetProvider
        provider = DltAssetProvider()
        assets = list(provider.get_assets())
        ```

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return static plugin metadata for discovery and registration."""
        return PluginMetadata(
            name="dlt",
            version="0.1.0",
            description="DLT-based ingestion engine for Phlo",
        )

    def get_assets(self) -> Iterable[AssetSpec]:
        """Return all assets registered through the ``@phlo_ingestion`` decorator."""
        return get_ingestion_assets()

    def get_checks(self) -> Iterable[AssetCheckSpec]:
        """Return no checks; asset checks attach to individual assets, not providers."""
        return []

    def clear_registries(self) -> None:
        """Clear in-memory DLT ingestion asset registrations.

        Removes all registered assets from the internal registry.
        Called during plugin reload or testing scenarios.

        """
        clear_ingestion_assets()


class DLTIngestionProvider(IngestionProviderPlugin):
    """DLT-based ingestion provider for Phlo.

    Ingestion provider plugin that exposes DLT-based ingestion
    capabilities through the standardized ingestion provider interface.

    Example:
        This class is auto-discovered by Phlo:
        ```python
        from phlo_dlt.plugin import DLTIngestionProvider
        provider = DLTIngestionProvider()
        decorator = provider.get_decorator()
        ```

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return static plugin metadata for the DLT ingestion provider."""
        return PluginMetadata(
            name="dlt",
            version="0.1.0",
            description="DLT-based ingestion provider with pipeline orchestration",
        )

    def get_decorator(self) -> Callable:
        """Return the ``@phlo_ingestion`` decorator."""
        from phlo_dlt import phlo_ingestion

        return phlo_ingestion

    def get_asset_retriever(self) -> Callable[[], list[Any]]:
        """Return the callable that lists registered ingestion assets."""
        return get_ingestion_assets

    def get_workflow_wizard_contributions(self) -> list[WorkflowWizardContribution]:
        """Return workflow wizard contributions exposed by DLT."""
        return get_workflow_wizard_contributions()

    def get_workflow_authoring_providers(self) -> list[WorkflowAuthoringSpec]:
        """Return workflow authoring capabilities exposed by DLT."""
        return [
            WorkflowAuthoringSpec(
                name="dlt",
                provider=DltWorkflowAuthoringProvider(),
                metadata={"contribution_id": "dlt.rest-api-source"},
            )
        ]

    def get_namespace_resolvers(self) -> list[NamespaceResolverSpec]:
        """Expose DLT's default namespace through the neutral CLI capability."""
        return [NamespaceResolverSpec(name="dlt", provider=DltNamespaceResolver())]


class DltWorkflowAuthoringProvider:
    """Create DLT-backed ingestion workflow files."""

    def create_workflow(self, *, project_root: Path, request: dict[str, Any]) -> dict[str, Any]:
        """Create a DLT ingestion workflow from an authoring request and return its files."""
        from phlo_dlt.scaffold import create_ingestion_workflow

        values = dict(request.get("values") or {})
        workflow_type = str(request.get("workflow_type") or "ingestion")
        if workflow_type != "ingestion":
            raise ValueError(f"Unsupported DLT workflow type: {workflow_type}")

        contribution_id = request.get("contribution_id")
        if contribution_id not in {None, "", "dlt.rest-api-source"}:
            raise ValueError(f"DLT cannot author contribution {contribution_id!r}")

        domain = str(values.get("domain") or request.get("domain") or "")
        table = str(values.get("table_name") or values.get("table") or request.get("table") or "")
        unique_key = str(
            values.get("unique_key") or values.get("primary_key") or request.get("unique_key") or ""
        )
        cron = str(
            values.get("cron") or values.get("schedule") or request.get("cron") or "0 */1 * * *"
        )
        api_base_url = values.get("api_base_url", request.get("api_base_url"))
        fields = values.get("fields", request.get("fields") or [])
        source_kind = str(values.get("source_kind") or request.get("source_kind") or "rest-api")

        if not domain or not table or not unique_key:
            raise ValueError("DLT workflow creation requires domain, table, and unique_key.")

        project_root.mkdir(parents=True, exist_ok=True)
        with _cwd(project_root):
            files = create_ingestion_workflow(
                domain=domain,
                table_name=table,
                unique_key=unique_key,
                cron=cron,
                api_base_url=str(api_base_url) if api_base_url else None,
                fields=list(fields or []),
                source_kind=source_kind,
            )

        return {
            "workflow_type": workflow_type,
            "provider": "dlt",
            "domain": domain,
            "table": table,
            "files": files,
            "next_steps": _ingestion_next_steps(files, table=table),
        }


class DltNamespaceResolver:
    """Resolve DLT table names against its configured default namespace."""

    def resolve_namespace(self, table_name: str) -> str:
        """Prefix the table name with DLT's configured default namespace."""
        from phlo_dlt.settings import get_settings

        return f"{get_settings().dlt_default_namespace}.{table_name}"


@contextmanager
def _cwd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _ingestion_next_steps(files: list[str], *, table: str) -> list[str]:
    if len(files) < 2:
        raise ValueError(
            "create_ingestion_workflow returned fewer than two files; cannot build next steps"
        )
    schema_file = files[0]
    workflow_file = files[1]
    test_file = files[2] if len(files) > 2 else None
    steps = [f"Review schema: {schema_file}", f"Review workflow: {workflow_file}"]
    if test_file:
        steps.append(f"Run generated tests: uv run pytest {test_file} -q")
    steps.extend(
        [
            "Restart active services if needed: phlo services restart",
            f"Materialize: phlo materialize dlt_{table}",
            "Inspect status: phlo status",
        ]
    )
    return steps
