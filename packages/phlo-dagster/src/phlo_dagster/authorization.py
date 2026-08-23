"""Regulated surface adapter for Dagster webserver.

This adapter registers Dagster as a regulated surface with the capability registry
and provides GraphQL operation mapping to canonical actions.

GraphQL Operation Mapping:
    - assetMutation (MaterializeResult) -> asset.execute
    - launchPipelineRun / launchBackfill -> run.execute
    - GraphQL queries (assets, pipelines, runs) -> asset.read / run.read
    - Sensor/schedule mutations -> run.manage
    - Repository queries -> catalog.read

Principal Extraction:
    - Bearer token from Authorization header
    - Canonicalized via EnforcementContext.identity_bridge
    - Service tokens and verified RS256 OIDC tokens only; unsigned user headers are rejected

Route vs Operation Granularity:
    - Route-level (GraphQL endpoint) is a single entry point
    - Operation-level enforcement within the GraphQL request body
    - Operation name + selection set determine the canonical action
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from graphql import GraphQLObjectType

from phlo.capabilities import (
    RegulatedSurfaceSpec,
    register_capability,
)
from phlo.rbac.models import CanonicalAction
from phlo.security.adapters import SurfaceOperation

SURFACE_NAME = "dagster-webserver"
SURFACE_FRAMEWORK = "dagster-graphql"

ACTION_ASSET_READ = CanonicalAction.ASSET_READ.value
ACTION_ASSET_EXECUTE = CanonicalAction.ASSET_EXECUTE.value
ACTION_ASSET_MANAGE = CanonicalAction.ASSET_MANAGE.value
ACTION_RUN_READ = "run.read"
ACTION_RUN_EXECUTE = "run.execute"
ACTION_RUN_MANAGE = "run.manage"
ACTION_CATALOG_READ = CanonicalAction.CATALOG_READ.value
ACTION_CATALOG_MANAGE = CanonicalAction.CATALOG_MANAGE.value
ACTION_SERVICE_READ = CanonicalAction.SERVICE_READ.value
ACTION_ADMIN_READ = CanonicalAction.ADMIN_READ.value
ACTION_ADMIN_MANAGE = CanonicalAction.ADMIN_MANAGE.value

_DAGSTER_LOG_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def _valid_log_component(value: Any) -> bool:
    """Return whether a Dagster log-key path component is safe and non-empty."""
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
    )


def extract_dagster_run_id_from_log_key(log_key: Any) -> str | None:
    """Extract the run ID from Dagster's captured-compute-log key grammar.

    Dagster's ``build_log_key_for_run`` emits ``[run_id, "compute_logs", step_key]``.
    The first component is the only authoritative run identity; arbitrary log
    keys and path-like values are rejected rather than authorized as a field.
    """
    if not isinstance(log_key, (list, tuple)) or len(log_key) < 3:
        return None
    run_id, namespace, *components = log_key
    if (
        not isinstance(run_id, str)
        or not _DAGSTER_LOG_ID_RE.fullmatch(run_id)
        or namespace != "compute_logs"
        or not components
        or not all(_valid_log_component(component) for component in components)
    ):
        return None
    return run_id


def extract_dagster_run_id_from_log_path(path: Any) -> str | None:
    """Extract a run ID from Dagster's ``/logs/{log_key}/{io}`` URL path."""
    if not isinstance(path, str):
        return None
    parts = path.strip("/").split("/")
    if len(parts) < 4 or parts[-1] not in {"stdout", "stderr"}:
        return None
    return extract_dagster_run_id_from_log_key(parts[:-1])


@dataclass(frozen=True)
class GraphQLOperationSpec:
    """Exact classification for one or more GraphQL root fields."""

    operation: str
    fields: tuple[str, ...]
    action: str
    resource_type: str
    resource_keys: tuple[str, ...] = ()
    require_resource: bool = False
    field_resource_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def keys_for_field(self, field: str) -> tuple[str, ...]:
        """Return the authoritative resource arguments for one root field."""
        return dict(self.field_resource_keys).get(field, self.resource_keys)


_GRAPHQL_OPERATION_SPECS: tuple[GraphQLOperationSpec, ...] = (
    GraphQLOperationSpec(
        "query",
        (
            "assetBackfillPreview",
            "assetCheckExecutions",
            "assetConditionEvaluationForPartition",
            "assetConditionEvaluationRecordsOrError",
            "assetConditionEvaluationsForEvaluationId",
            "assetNodeAdditionalRequiredKeys",
            "assetNodeDefinitionCollisions",
            "assetNodeOrError",
            "assetNodes",
            "assetOrError",
            "assetRecordsOrError",
            "assetsLatestInfo",
            "assetsOrError",
            "autoMaterializeAssetEvaluationsOrError",
            "autoMaterializeEvaluationsForEvaluationId",
            "truePartitionsForAutomationConditionEvaluationNode",
        ),
        ACTION_ASSET_READ,
        "asset",
        (),
        field_resource_keys=(
            ("assetCheckExecutions", ("assetKey",)),
            ("assetConditionEvaluationForPartition", ("assetKey",)),
            ("assetConditionEvaluationRecordsOrError", ("assetKey",)),
            ("assetNodeAdditionalRequiredKeys", ("assetKeys",)),
            ("assetNodeDefinitionCollisions", ("assetKeys",)),
            ("assetNodeOrError", ("assetKey",)),
            ("assetOrError", ("assetKey",)),
            ("assetNodes", ("assetKeys",)),
            ("assetsLatestInfo", ("assetKeys",)),
            ("assetsOrError", ("assetKeys",)),
            ("autoMaterializeAssetEvaluationsOrError", ("assetKey",)),
            ("truePartitionsForAutomationConditionEvaluationNode", ("assetKey",)),
        ),
    ),
    GraphQLOperationSpec(
        "query",
        (
            "capturedLogs",
            "capturedLogsMetadata",
            "executionPlanOrError",
            "graphOrError",
            "latestDefsStateInfo",
            "logsForRun",
            "partitionBackfillOrError",
            "partitionBackfillsOrError",
            "partitionSetOrError",
            "partitionSetsOrError",
            "pipelineOrError",
            "pipelineRunOrError",
            "pipelineRunsOrError",
            "pipelineSnapshotOrError",
            "runGroupOrError",
            "runIdsOrError",
            "runOrError",
            "runTagKeysOrError",
            "runTagsOrError",
            "runsFeedCountOrError",
            "runsFeedOrError",
            "runsOrError",
        ),
        ACTION_RUN_READ,
        "run",
        (),
        field_resource_keys=(
            ("capturedLogs", ("logKey",)),
            ("capturedLogsMetadata", ("logKey",)),
            ("logsForRun", ("runId",)),
            ("pipelineRunOrError", ("runId",)),
            ("runGroupOrError", ("runId",)),
            ("runOrError", ("runId",)),
            ("partitionBackfillOrError", ("backfillId",)),
            ("partitionSetOrError", ("repositorySelector",)),
            ("partitionSetsOrError", ("repositorySelector",)),
            ("pipelineOrError", ("params",)),
            ("executionPlanOrError", ("pipeline",)),
        ),
    ),
    GraphQLOperationSpec(
        "query",
        (
            "instigationStateOrError",
            "instigationStatesOrError",
            "locationStatusesOrError",
            "scheduleOrError",
            "scheduler",
            "schedulesOrError",
            "sensorOrError",
            "sensorsOrError",
            "workspaceLocationEntryOrError",
        ),
        ACTION_SERVICE_READ,
        "service",
        (),
        field_resource_keys=(
            ("instigationStateOrError", ("id",)),
            ("instigationStatesOrError", ("repositoryID",)),
            ("scheduleOrError", ("scheduleSelector",)),
            ("schedulesOrError", ("repositorySelector",)),
            ("sensorOrError", ("sensorSelector",)),
            ("sensorsOrError", ("repositorySelector",)),
            ("workspaceLocationEntryOrError", ("name",)),
        ),
    ),
    GraphQLOperationSpec(
        "query",
        (
            "allTopLevelResourceDetailsOrError",
            "repositoriesOrError",
            "repositoryOrError",
            "resourcesOrError",
            "workspaceOrError",
        ),
        ACTION_CATALOG_READ,
        "catalog",
        (),
        field_resource_keys=(
            ("repositoryOrError", ("repositorySelector",)),
            ("repositoriesOrError", ("repositorySelector",)),
            ("resourcesOrError", ("pipelineSelector",)),
        ),
    ),
    GraphQLOperationSpec(
        "query",
        (
            "autoMaterializeTicks",
            "appManagedComponentsForLocationOrError",
            "canBulkTerminate",
            "componentsForLocationOrError",
            "componentTypesForLocationOrError",
            "instance",
            "isPipelineConfigValid",
            "permissions",
            "runConfigSchemaOrError",
            "shouldShowNux",
            "test",
            "topLevelResourceDetailsOrError",
            "utilizedEnvVarsOrError",
            "version",
        ),
        ACTION_ADMIN_READ,
        "admin",
        field_resource_keys=(
            ("isPipelineConfigValid", ("pipeline",)),
            ("runConfigSchemaOrError", ("selector",)),
        ),
    ),
    GraphQLOperationSpec(
        "mutation",
        (
            "launchMultipleRuns",
            "launchPartitionBackfill",
            "launchPipelineExecution",
            "launchPipelineReexecution",
            "launchRun",
            "launchRunReexecution",
            "reexecutePartitionBackfill",
        ),
        ACTION_RUN_EXECUTE,
        "run",
        ("jobName", "repositoryName", "repositoryLocationName"),
        require_resource=True,
        field_resource_keys=(
            ("launchPartitionBackfill", ("repositorySelector",)),
            ("reexecutePartitionBackfill", ("parentRunId",)),
        ),
    ),
    GraphQLOperationSpec(
        "mutation",
        (
            "cancelPartitionBackfill",
            "deletePipelineRun",
            "deleteRun",
            "freeConcurrencySlots",
            "freeConcurrencySlotsForRun",
            "terminatePipelineExecution",
            "terminateRun",
            "terminateRuns",
        ),
        ACTION_RUN_MANAGE,
        "run",
        (
            "runId",
            "runIds",
            "backfillId",
            "stepKey",
        ),
        require_resource=True,
        field_resource_keys=(
            ("cancelPartitionBackfill", ("backfillId",)),
            ("terminateRuns", ("runIds",)),
            ("terminateRun", ("runId",)),
            ("deleteRun", ("runId",)),
            ("deletePipelineRun", ("runId",)),
            ("freeConcurrencySlots", ("runId", "stepKey")),
            ("freeConcurrencySlotsForRun", ("runId",)),
            ("terminatePipelineExecution", ("runId",)),
        ),
    ),
    GraphQLOperationSpec(
        "mutation",
        (
            "addDynamicPartition",
            "deleteDynamicPartitions",
            "wipeAssets",
        ),
        ACTION_ASSET_MANAGE,
        "asset",
        ("assetKey", "partitionsDefName", "repositoryName", "repositoryLocationName"),
        require_resource=True,
        field_resource_keys=(
            ("wipeAssets", ("assetKey",)),
            ("addDynamicPartition", ("partitionsDefName", "repositorySelector")),
            ("deleteDynamicPartitions", ("partitionsDefName", "repositorySelector")),
        ),
    ),
    GraphQLOperationSpec(
        "mutation",
        ("reportAssetCheckEvaluations", "reportRunlessAssetEvents"),
        ACTION_ASSET_EXECUTE,
        "asset",
        ("assetKey",),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        ("deleteConcurrencyLimit", "setConcurrencyLimit"),
        ACTION_ADMIN_MANAGE,
        "admin",
        ("concurrencyKey",),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        ("setAutoMaterializePaused",),
        ACTION_ADMIN_MANAGE,
        "admin",
    ),
    GraphQLOperationSpec(
        "mutation",
        ("deleteAppManagedComponent", "setAppManagedComponent"),
        ACTION_ADMIN_MANAGE,
        "admin",
    ),
    GraphQLOperationSpec(
        "mutation",
        ("reloadRepositoryLocation",),
        ACTION_CATALOG_MANAGE,
        "catalog",
        ("repositoryLocationName",),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        ("reloadWorkspace",),
        ACTION_CATALOG_MANAGE,
        "catalog",
    ),
    GraphQLOperationSpec(
        "mutation",
        ("resetSchedule", "scheduleDryRun", "startSchedule"),
        ACTION_RUN_MANAGE,
        "service",
        ("repositoryName", "repositoryLocationName", "scheduleName"),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        ("resetSensor", "sensorDryRun", "startSensor"),
        ACTION_RUN_MANAGE,
        "service",
        ("repositoryName", "repositoryLocationName", "sensorName"),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        ("resumePartitionBackfill",),
        ACTION_RUN_MANAGE,
        "run",
        ("backfillId",),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        ("shutdownRepositoryLocation",),
        ACTION_RUN_MANAGE,
        "service",
        ("repositoryLocationName",),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        ("stopRunningSchedule",),
        ACTION_RUN_MANAGE,
        "service",
        ("id", "scheduleOriginId", "scheduleSelectorId"),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        (
            "logTelemetry",
            "refreshComponentState",
            "setNuxSeen",
        ),
        ACTION_ADMIN_MANAGE,
        "admin",
        (),
    ),
    GraphQLOperationSpec(
        "mutation",
        ("setSensorCursor",),
        ACTION_RUN_MANAGE,
        "service",
        ("repositoryName", "repositoryLocationName", "sensorName"),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "mutation",
        ("stopSensor",),
        ACTION_RUN_MANAGE,
        "service",
        ("id", "jobOriginId", "jobSelectorId"),
        require_resource=True,
    ),
    GraphQLOperationSpec(
        "subscription",
        ("capturedLogs",),
        ACTION_RUN_READ,
        "run",
        ("logKey",),
    ),
    GraphQLOperationSpec(
        "subscription",
        ("locationStateChangeEvents",),
        ACTION_SERVICE_READ,
        "service",
    ),
    GraphQLOperationSpec(
        "subscription",
        ("pipelineRunLogs",),
        ACTION_RUN_READ,
        "run",
        (),
        field_resource_keys=(("pipelineRunLogs", ("runId",)),),
    ),
    GraphQLOperationSpec("query", ("__schema", "__type"), ACTION_ADMIN_READ, "admin"),
)


def _operation_index() -> dict[tuple[str, str], GraphQLOperationSpec]:
    index: dict[tuple[str, str], GraphQLOperationSpec] = {}
    for spec in _GRAPHQL_OPERATION_SPECS:
        for field in spec.fields:
            key = (spec.operation, field)
            if key in index:
                raise RuntimeError(f"Duplicate Dagster GraphQL operation classification: {key}")
            index[key] = spec
    return index


_GRAPHQL_OPERATION_INDEX = _operation_index()

# Root fields that exist only in some Dagster versions; excluded from the
# extra-classification check so a version skew does not fail validation.
_OPTIONAL_DAGSTER_FIELDS: dict[str, frozenset[str]] = {
    "query": frozenset(
        {
            "appManagedComponentsForLocationOrError",
            "componentsForLocationOrError",
            "componentTypesForLocationOrError",
        }
    ),
    "mutation": frozenset(
        {"deleteAppManagedComponent", "refreshComponentState", "setAppManagedComponent"}
    ),
}


def resolve_graphql_operation(operation: str, field: str) -> GraphQLOperationSpec:
    """Return the exact registry entry for a GraphQL root field."""
    try:
        return _GRAPHQL_OPERATION_INDEX[(operation, field)]
    except KeyError as exc:
        raise RuntimeError(f"Unclassified Dagster GraphQL operation: {operation}.{field}") from exc


def validate_graphql_schema(schema: Any | None = None) -> None:
    """Prove every reachable Dagster root field has one exact classification."""
    if schema is None:
        from dagster_graphql.schema import create_schema

        schema = create_schema().graphql_schema

    for operation in ("query", "mutation", "subscription"):
        root = schema.get_type(operation.capitalize())
        if not isinstance(root, GraphQLObjectType):
            continue
        actual_fields = set(root.fields)
        classified = {field for kind, field in _GRAPHQL_OPERATION_INDEX if kind == operation}
        missing = sorted(actual_fields - classified)
        extra = sorted(
            classified
            - actual_fields
            - ({"__schema", "__type"} if operation == "query" else set())
            - _OPTIONAL_DAGSTER_FIELDS.get(operation, frozenset())
        )
        if missing or extra:
            raise RuntimeError(
                f"Dagster GraphQL registry mismatch for {operation}: "
                f"missing={missing}, extra={extra}"
            )

    validate_graphql_resource_bindings(schema)


def validate_graphql_resource_bindings(schema: Any) -> None:
    """Ensure every registry resource key exists in its live input schema."""
    roots = {
        "query": schema.query_type,
        "mutation": schema.mutation_type,
        "subscription": schema.subscription_type,
    }
    for spec in _GRAPHQL_OPERATION_SPECS:
        root = roots.get(spec.operation)
        if root is None:
            continue
        for field_name in spec.fields:
            field = root.fields.get(field_name)
            if field is None:
                continue
            reachable: set[str] = set()
            visited: set[int] = set()

            def visit(graphql_type: Any) -> None:
                """Collect field names reachable from a GraphQL type."""
                while hasattr(graphql_type, "of_type"):
                    graphql_type = graphql_type.of_type
                fields = getattr(graphql_type, "fields", None)
                if not isinstance(fields, dict) or id(graphql_type) in visited:
                    return
                visited.add(id(graphql_type))
                for nested_name, nested_field in fields.items():
                    reachable.add(nested_name)
                    visit(nested_field.type)

            for argument_name, argument in field.args.items():
                reachable.add(argument_name)
                visit(argument.type)
            missing = sorted(set(spec.keys_for_field(field_name)) - reachable)
            if missing:
                raise RuntimeError(
                    f"Dagster resource registry uses arguments absent from "
                    f"{spec.operation}.{field_name}: {missing}"
                )


class DagsterRegulatedSurfaceAdapter:
    """Regulated surface adapter for Dagster webserver GraphQL API.

    Declares all regulated operations and registers with the capability registry.
    Operations are mapped from GraphQL operation names to canonical actions.
    """

    surface_name: str = SURFACE_NAME
    framework_type: str = SURFACE_FRAMEWORK
    _installed_runtime: Any | None = None

    _OPERATION_MAPPINGS: list[tuple[tuple[str, ...], str, str]] = [
        (spec.fields, spec.action, spec.resource_type) for spec in _GRAPHQL_OPERATION_SPECS
    ]

    def list_operations(self) -> list[SurfaceOperation]:
        """Return all regulated operations exposed by Dagster GraphQL API."""
        seen: set[tuple[str, str]] = set()
        operations: list[SurfaceOperation] = []

        for spec in _GRAPHQL_OPERATION_SPECS:
            op_names, action, resource_type = spec.fields, spec.action, spec.resource_type
            key = (action, resource_type)
            if key in seen:
                continue
            seen.add(key)
            operations.append(
                SurfaceOperation(
                    action=action,
                    resource_type=resource_type,
                    operation_name=f"dagster.{op_names[0]}",
                    resource_id_strategy="graphql_variables",
                    framework_metadata={
                        "graphql_operations": op_names,
                        "resource_keys": sorted(
                            {key for field in op_names for key in spec.keys_for_field(field)}
                        ),
                        "surface": SURFACE_NAME,
                    },
                )
            )

        operations.extend(
            [
                SurfaceOperation(
                    action=ACTION_ASSET_READ,
                    resource_type="asset",
                    operation_name="dagster.asset.query",
                ),
                SurfaceOperation(
                    action=ACTION_ASSET_EXECUTE,
                    resource_type="asset",
                    operation_name="dagster.asset.mutate",
                ),
                SurfaceOperation(
                    action=ACTION_RUN_READ,
                    resource_type="run",
                    operation_name="dagster.run.query",
                ),
                SurfaceOperation(
                    action=ACTION_RUN_EXECUTE,
                    resource_type="run",
                    operation_name="dagster.run.launch",
                ),
                SurfaceOperation(
                    action=ACTION_RUN_MANAGE,
                    resource_type="run",
                    operation_name="dagster.run.manage",
                ),
                SurfaceOperation(
                    action=ACTION_CATALOG_READ,
                    resource_type="catalog",
                    operation_name="dagster.catalog.query",
                ),
                SurfaceOperation(
                    action=ACTION_CATALOG_MANAGE,
                    resource_type="catalog",
                    operation_name="dagster.catalog.manage",
                ),
                SurfaceOperation(
                    action=ACTION_SERVICE_READ,
                    resource_type="service",
                    operation_name="dagster.service.query",
                ),
            ]
        )

        return operations

    def is_active(self, runtime: Any) -> bool:
        """Return True if the adapter is installed on the given runtime."""
        if self._installed_runtime is None:
            return False
        return runtime is self._installed_runtime

    def install(self, runtime: Any) -> None:
        """Register Dagster as a regulated surface and track the runtime."""
        if runtime is None:
            raise ValueError(
                "Dagster adapter requires a non-None Dagster webserver instance as runtime"
            )
        self._installed_runtime = runtime
        schema = getattr(runtime, "_graphene_schema", None)
        if schema is not None:
            validate_graphql_schema(schema.graphql_schema)
        spec = RegulatedSurfaceSpec(
            name=SURFACE_NAME,
            provider=self,
            metadata={
                "framework_runtime": SURFACE_FRAMEWORK,
                "entrypoint": "graphql",
            },
        )
        register_capability("regulated_surface", spec)


_adapter: DagsterRegulatedSurfaceAdapter | None = None


def get_adapter() -> DagsterRegulatedSurfaceAdapter:
    """Return the singleton adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = DagsterRegulatedSurfaceAdapter()
    return _adapter
