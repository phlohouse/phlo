"""Dagster orchestrator adapter for Phlo capability specs.

This module provides the core translation layer between Phlo's capability-based
architecture and Dagster's asset-centric execution model. It converts capability
specifications (AssetSpec, AssetCheckSpec, ResourceSpec) into Dagster definitions.

Translation Mapping:
    - AssetSpec → @asset decorated functions
    - AssetCheckSpec → @asset_check decorated functions
    - ResourceSpec → Dagster ResourceDefinition
    - Partitions → Dagster PartitionsDefinition
    - Cron schedules → Dagster AutomationCondition
    - Freshness windows → Dagster FreshnessPolicy

Key Components:
    - DagsterOrchestratorAdapter: Main adapter implementing OrchestratorAdapterPlugin
    - DagsterRuntime: Runtime context wrapper providing Dagster integration
    - Metadata conversion helpers for Dagster-compatible types

Dagster Integration Points:
    - AssetExecutionContext: Wrapped to provide Phlo RuntimeContext interface
    - MaterializeResult/CheckResult: Converted to Dagster result types
    - Retry policies and op tags from spec configuration
    - Dependencies mapped to AssetKey relationships

Example:
    Adapter instantiation::

        from phlo_dagster.adapter import DagsterOrchestratorAdapter
        from phlo.capabilities.discovery import discover_capabilities

        # Discover capabilities from user code
        discover_capabilities()

        # Build Dagster definitions
        adapter = DagsterOrchestratorAdapter()
        defs = adapter.build_definitions(
            assets=registry.list("asset"),
            checks=registry.list("check"),
            resources=registry.list("resource"),
        )

"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable, Mapping

import dagster as dg

from phlo._correlation import resolve_project_identity
from phlo.capabilities.runtime import (
    RuntimeContext,
    RuntimeRouting,
    attempt_from_tags,
    capability_overrides_from_tags,
    runtime_ref_from_tags,
)
from phlo.config import get_settings
from phlo.capabilities.specs import (
    AssetCheckSpec,
    AssetSpec,
    CheckResult,
    EvidenceProfileContributionSpec,
    MaterializeResult,
    ResourceSpec,
)
from phlo.logging import get_logger
from phlo.plugins.base import OrchestratorAdapterPlugin, PluginMetadata
from phlo_dagster.framework.asset_diagnostics import raise_duplicate_asset_specs_if_present

logger = get_logger(__name__)


def _asset_key_from_string(key: str) -> dg.AssetKey:
    """Convert a dotted asset key string into a Dagster asset key."""
    if "." in key:
        return dg.AssetKey(key.split("."))
    return dg.AssetKey([key])


def _metadata_value(value: Any) -> dg.MetadataValue:
    """Convert a Python value into a Dagster metadata value."""
    if isinstance(value, dg.MetadataValue):
        return value
    if isinstance(value, dg.TableSchema):
        return dg.MetadataValue.table_schema(value)
    if isinstance(value, bool):
        return dg.MetadataValue.bool(value)
    if isinstance(value, int):
        return dg.MetadataValue.int(value)
    if isinstance(value, float):
        return dg.MetadataValue.float(value)
    if isinstance(value, str):
        return dg.MetadataValue.text(value)
    try:
        return dg.MetadataValue.json(value)
    except TypeError:
        return dg.MetadataValue.text(repr(value))


def _convert_metadata(metadata: dict[str, Any]) -> dict[str, dg.MetadataValue]:
    """Normalize metadata keys and values for Dagster materializations."""
    converted: dict[str, dg.MetadataValue] = {}
    for key, value in metadata.items():
        if key == "phlo/column_schema" and isinstance(value, list):
            columns: list[dg.TableColumn] = []
            for column in value:
                if not isinstance(column, dict):
                    continue
                columns.append(
                    dg.TableColumn(
                        name=str(column.get("name", "")),
                        type=str(column.get("type", "")),
                        description=str(column.get("description", "")),
                    )
                )
            if columns:
                converted["dagster/column_schema"] = _metadata_value(
                    dg.TableSchema(columns=columns)
                )
            continue
        converted[key] = _metadata_value(value)
    return converted


def _severity_from_string(value: str | None) -> dg.AssetCheckSeverity | None:
    """Map a string severity label to a Dagster severity, or ``None`` if unrecognized."""
    if not value:
        return None
    # Dagster has no INFO severity; informational checks surface as WARN.
    normalized = value.strip().lower()
    if normalized in {"info", "informational"}:
        return dg.AssetCheckSeverity.WARN
    if normalized in {"warn", "warning"}:
        return dg.AssetCheckSeverity.WARN
    if normalized in {"error", "critical"}:
        return dg.AssetCheckSeverity.ERROR
    return None


@dataclass(frozen=True)
class DagsterRuntime(RuntimeContext):
    """Runtime context wrapper around ``dagster.AssetExecutionContext``."""

    context: dg.AssetExecutionContext
    asset_capability_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def run_id(self) -> str | None:
        """Return the current Dagster run identifier when available."""
        return self.context.run.run_id

    @property
    def partition_key(self) -> str | None:
        """Return the active partition key for partitioned runs."""
        return self.context.partition_key if self.context.has_partition_key else None

    @property
    def tags(self) -> dict[str, str]:
        """Return run tags from the best available context attribute."""
        direct_tags = getattr(self.context, "tags", None)
        if isinstance(direct_tags, Mapping):
            return {str(key): str(value) for key, value in direct_tags.items()}

        run_tags = getattr(self.context, "run_tags", None)
        if isinstance(run_tags, Mapping):
            return {str(key): str(value) for key, value in run_tags.items()}

        run = getattr(self.context, "run", None)
        run_level_tags = getattr(run, "tags", None) if run is not None else None
        if isinstance(run_level_tags, Mapping):
            return {str(key): str(value) for key, value in run_level_tags.items()}

        return {}

    @property
    def logger(self) -> Any:
        """Expose Dagster logger for capability runtime hooks."""
        return self.context.log

    @property
    def resources(self) -> dict[str, Any]:
        """Return resources as a plain mapping for runtime consumers."""
        resources = getattr(self.context, "resources", None)
        if resources is None:
            return {}
        if isinstance(resources, dict):
            return dict(resources)
        if hasattr(resources, "_asdict"):
            return dict(resources._asdict())
        if hasattr(resources, "__dict__"):
            return {
                name: value for name, value in vars(resources).items() if not name.startswith("_")
            }
        resource_map: dict[str, Any] = {}
        for name in dir(resources):
            if name.startswith("_"):
                continue
            try:
                value = getattr(resources, name)
            except Exception:
                continue
            if callable(value):
                continue
            resource_map[name] = value
        return resource_map

    @property
    def routing(self) -> RuntimeRouting:
        """Return canonical runtime routing information."""
        tags = self.tags
        feature_flags = {
            key.removeprefix("feature/"): value
            for key, value in tags.items()
            if key.startswith("feature/")
        }
        capability_overrides = capability_overrides_from_tags(tags)
        attempt, attempt_error = attempt_from_tags(tags)
        project = resolve_project_identity(tags, get_settings().phlo_project)
        for capability_type, provider_name in self.asset_capability_overrides.items():
            capability_overrides.setdefault(capability_type, provider_name)
        return RuntimeRouting(
            environment=tags.get("environment") or tags.get("env"),
            ref=runtime_ref_from_tags(tags),
            partition_key=self.partition_key,
            run_id=(
                tags.get("phlo/run_id")
                or getattr(self.context.run, "root_run_id", None)
                or self.run_id
            ),
            project_id=project.project_id,
            project_error=project.error,
            attempt=attempt,
            attempt_error=attempt_error,
            resources=self.resources,
            feature_flags=feature_flags,
            capability_overrides=capability_overrides,
        )

    def get_resource(self, name: str) -> Any:
        """Return a named Dagster resource from execution context."""
        return getattr(self.context.resources, name)


class DagsterOrchestratorAdapter(OrchestratorAdapterPlugin):
    def get_evidence_profile_contributions(self) -> list[EvidenceProfileContributionSpec]:
        """Declare this provider's blessed run-evidence contribution."""
        from phlo.run_evidence.profiles import EvidenceProfileContribution
        from phlo.run_evidence.reconciliation import RequiredEvidenceRecord, RequiredEvidenceStage

        contribution = EvidenceProfileContribution(
            contribution_id="dagster.terminal",
            provider="dagster",
            profile_id="wap",
            profile_version="1",
            stages=(RequiredEvidenceStage(stage_type="lineage", provider="dagster"),),
            required_records=(RequiredEvidenceRecord(family="resource", minimum=1),),
        )
        return [EvidenceProfileContributionSpec(name="dagster.terminal", provider=contribution)]

    """Translate capability specs into Dagster definitions."""

    def exec_service_name(self) -> str | None:
        """Return the service container used for orchestrator-scoped CLI execution."""
        return "dagster"

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata used by capability discovery."""
        return PluginMetadata(
            name="dagster",
            version="0.1.0",
            description="Dagster orchestrator adapter for Phlo capability specs",
        )

    def build_definitions(
        self,
        *,
        assets: Iterable[AssetSpec],
        checks: Iterable[AssetCheckSpec],
        resources: Iterable[ResourceSpec],
    ) -> dg.Definitions:
        """Build Dagster definitions from asset, check, and resource capability specs."""
        assets_list = list(assets)
        checks_list = list(checks)
        resources_list = list(resources)
        logger.info(
            "dagster_adapter_build_definitions_started",
            asset_spec_count=len(assets_list),
            check_spec_count=len(checks_list),
            resource_spec_count=len(resources_list),
        )
        raise_duplicate_asset_specs_if_present(assets_list)

        resources_map: dict[str, Any] = {}
        for resource in resources_list:
            value = resource.resource
            if isinstance(value, dg.ResourceDefinition):
                resources_map[resource.name] = value
            else:
                resources_map[resource.name] = dg.ResourceDefinition.hardcoded_resource(value)

        asset_defs = [self._build_asset(spec) for spec in assets_list if spec.run is not None]
        check_defs = [self._build_check(check) for check in checks_list if check.fn is not None]

        logger.info(
            "dagster_adapter_build_definitions_completed",
            asset_definition_count=len(asset_defs),
            check_definition_count=len(check_defs),
            resource_definition_count=len(resources_map),
        )

        return dg.Definitions(
            assets=asset_defs,
            asset_checks=check_defs,
            resources=resources_map,
        )

    def _build_asset(self, spec: AssetSpec) -> dg.AssetsDefinition:
        """Create a Dagster asset definition from a capability asset spec."""
        # Declarative checks (no callable) attach to the asset as check specs;
        # checks with a fn become standalone definitions via _build_check.
        check_specs = [
            dg.AssetCheckSpec(
                name=check.name,
                asset=_asset_key_from_string(check.asset_key),
                blocking=check.blocking,
                description=check.description,
            )
            for check in spec.checks
            if check.fn is None
        ]

        partitions_def = None
        if spec.partitions and spec.partitions.kind == "daily":
            from phlo_dagster.partitions import daily_partition

            partitions_def = daily_partition

        op_tags: dict[str, str] = {}
        if spec.run and spec.run.max_runtime_seconds:
            op_tags["dagster/max_runtime"] = str(spec.run.max_runtime_seconds)

        retry_policy = None
        if spec.run and spec.run.max_retries:
            retry_policy = dg.RetryPolicy(
                max_retries=spec.run.max_retries,
                delay=spec.run.retry_delay_seconds or 30,
            )

        automation_condition = None
        if spec.run and spec.run.cron:
            automation_condition = dg.AutomationCondition.on_cron(spec.run.cron)

        freshness_policy = None
        if spec.run and spec.run.freshness_hours:
            freshness_policy = dg.FreshnessPolicy.time_window(
                warn_window=timedelta(hours=spec.run.freshness_hours[0]),
                fail_window=timedelta(hours=spec.run.freshness_hours[1]),
            )

        asset_key = _asset_key_from_string(spec.key)
        deps = [_asset_key_from_string(dep) for dep in spec.deps]
        required_resources = set(spec.resources)
        asset_metadata = _convert_metadata(spec.metadata) if spec.metadata else None

        name = asset_key.path[-1]
        key_prefix = asset_key.path[:-1] or None

        @dg.asset(
            name=name,
            key_prefix=key_prefix,
            group_name=spec.group,
            description=spec.description,
            kinds=spec.kinds,
            tags=spec.tags,
            metadata=asset_metadata,
            partitions_def=partitions_def,
            deps=deps,
            check_specs=check_specs or None,
            required_resource_keys=required_resources or None,
            op_tags=op_tags or None,
            retry_policy=retry_policy,
            automation_condition=automation_condition,
            freshness_policy=freshness_policy,
        )
        def _asset_fn(context) -> Iterable[Any]:
            """Execute capability asset logic and yield materializations or check results."""
            runtime = DagsterRuntime(
                context, asset_capability_overrides=dict(spec.capability_overrides)
            )
            results = spec.run.fn(runtime) if spec.run else []
            if results is None:
                return
            for result in results:
                if isinstance(result, MaterializeResult):
                    metadata = _convert_metadata(result.metadata)
                    if result.status:
                        metadata.setdefault("status", dg.MetadataValue.text(result.status))
                    # An in-band failure status must surface as a real step
                    # failure, not a successful materialization with bad
                    # metadata, so retry policies and failure alerts apply.
                    status = str(result.status or "").lower()
                    if status in {"failure", "failed", "error"}:
                        logger.warning(
                            "dagster_adapter_asset_materialization_failed_status",
                            asset_key=spec.key,
                            status=result.status,
                            run_id=runtime.run_id,
                            partition_key=runtime.partition_key,
                        )
                        raise dg.Failure(
                            description=f"Asset run reported status '{result.status}'",
                            metadata=metadata,
                        )
                    yield dg.MaterializeResult(metadata=metadata)
                elif isinstance(result, CheckResult):
                    severity = _severity_from_string(result.severity) or dg.AssetCheckSeverity.ERROR
                    asset_check_key = _asset_key_from_string(result.asset_key)
                    metadata = _convert_metadata(result.metadata)
                    yield dg.AssetCheckResult(
                        passed=result.passed,
                        check_name=result.check_name,
                        asset_key=asset_check_key,
                        metadata=metadata,
                        severity=severity,
                    )

        return _asset_fn

    def _build_check(self, spec: AssetCheckSpec) -> dg.AssetChecksDefinition:
        """Create a Dagster asset check definition from a capability check spec."""
        asset_key = _asset_key_from_string(spec.asset_key)
        # A failing non-blocking check must surface as WARN so the WAP
        # promotion sensor treats it as warning evidence instead of gating.
        fallback = dg.AssetCheckSeverity.ERROR if spec.blocking else dg.AssetCheckSeverity.WARN
        default_severity = _severity_from_string(spec.severity) or fallback

        @dg.asset_check(
            name=spec.name,
            asset=asset_key,
            blocking=spec.blocking,
            description=spec.description,
        )
        def _check_fn(context) -> dg.AssetCheckResult:
            """Execute capability check logic and return the Dagster check result."""
            runtime = DagsterRuntime(context)
            result = spec.fn(runtime) if spec.fn else None
            if result is None:
                return dg.AssetCheckResult(passed=True, check_name=spec.name, asset_key=asset_key)
            metadata = _convert_metadata(result.metadata)
            result_severity = _severity_from_string(result.severity)
            severity = result_severity or default_severity
            return dg.AssetCheckResult(
                passed=result.passed,
                check_name=result.check_name,
                asset_key=asset_key,
                metadata=metadata,
                severity=severity,
            )

        return _check_fn
