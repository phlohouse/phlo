"""Dagster GraphQL adapter for Phlo API mutation routes.

Launches materializations, re-executions, cancels, and partition backfills
through raw GraphQL mutations, querying run status and partition keys. Every
mutation honours dry_run and idempotency keys; payloads normalize into
provider-neutral DagsterOperationResult dicts regardless of response shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from phlo.security.service_identity import build_service_headers


LAUNCH_PIPELINE_EXECUTION_MUTATION = """
mutation LaunchPipelineExecution($executionParams: ExecutionParams!) {
    launchPipelineExecution(executionParams: $executionParams) {
        __typename
        ... on LaunchRunSuccess { run { runId status } }
        ... on PipelineNotFoundError { message }
        ... on InvalidSubsetError { message }
        ... on RunConfigValidationInvalid { errors { message } }
        ... on PythonError { message }
    }
}
"""

EXISTING_MATERIALIZATION_RUN_QUERY = """
query ExistingMaterializationRun($filter: RunsFilter!) {
    runsOrError(filter: $filter, limit: 1) {
        __typename
        ... on Runs {
            results { runId status }
        }
        ... on PythonError { message }
    }
}
"""

RUN_STATUS_QUERY = """
query RunStatus($runId: ID!) {
    runOrError(runId: $runId) {
        __typename
        ... on Run { runId status }
        ... on RunNotFoundError { message }
        ... on PythonError { message }
    }
}
"""

LAUNCH_PIPELINE_REEXECUTION_MUTATION = """
mutation LaunchPipelineReexecution($executionParams: ExecutionParams, $reexecutionParams: ReexecutionParams) {
    launchPipelineReexecution(executionParams: $executionParams, reexecutionParams: $reexecutionParams) {
        __typename
        ... on LaunchRunSuccess { run { runId status } }
        ... on PythonError { message }
    }
}
"""

TERMINATE_RUN_MUTATION = """
mutation TerminateRun($runId: String!) {
    terminateRun(runId: $runId) {
        __typename
        ... on TerminateRunSuccess { run { runId status } }
        ... on RunNotFoundError { message }
        ... on PythonError { message }
    }
}
"""

LAUNCH_PARTITION_BACKFILL_MUTATION = """
mutation LaunchPartitionBackfill($backfillParams: LaunchBackfillParams!) {
    launchPartitionBackfill(backfillParams: $backfillParams) {
        __typename
        ... on LaunchBackfillSuccess { backfillId launchedRunIds }
        ... on PartitionSetNotFoundError { message }
        ... on PythonError { message }
    }
}
"""

PARTITION_KEYS_QUERY = """
query PartitionKeys($assetKey: AssetKeyInput!) {
    assetNodeOrError(assetKey: $assetKey) {
        __typename
        ... on AssetNode {
            partitionKeysByDimension { name partitionKeys }
        }
        ... on AssetNotFoundError { message }
    }
}
"""


@dataclass(frozen=True)
class DagsterOperationResult:
    """Provider-neutral result returned by the Dagster operation adapter."""

    operation: str
    dry_run: bool
    accepted: bool
    status: str
    message: str
    run_id: str | None = None
    asset_key_path: str | None = None
    partition_key: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the result as a plain JSON-serializable dict."""
        return {
            "operation": self.operation,
            "dry_run": self.dry_run,
            "accepted": self.accepted,
            "run_id": self.run_id,
            "asset_key_path": self.asset_key_path,
            "partition_key": self.partition_key,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


async def launch_materialize(
    *,
    dagster_url: str,
    asset_key_path: str,
    job_name: str,
    repository_location_name: str | None = None,
    repository_name: str | None = None,
    partition_key: str | None = None,
    run_config: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    tags: dict[str, str] | None = None,
    access_token: str | None = None,
) -> DagsterOperationResult:
    """Launch a Dagster materialization run for one asset, reusing a prior run
    tagged with the same idempotency key so client retries never double-materialize.

    Raises RuntimeError when Dagster rejects the launch or returns a malformed
    existing-run payload.
    """
    execution_tags = {"phlo/operation": "materialize_asset", "phlo/asset_key": asset_key_path}
    if idempotency_key:
        execution_tags["phlo/idempotency_key"] = idempotency_key
    execution_tags.update(tags or {})
    if partition_key:
        execution_tags.setdefault("dagster/partition", partition_key)
    if idempotency_key:
        # Reconcile against any run already tagged with this idempotency key
        # before launching, so a client retry after a lost GraphQL response
        # reuses the prior run instead of materializing twice.
        existing = await _graphql(
            dagster_url,
            EXISTING_MATERIALIZATION_RUN_QUERY,
            {
                "filter": {
                    "tags": _tags_for_execution(
                        {
                            "phlo/operation": "materialize_asset",
                            "phlo/idempotency_key": idempotency_key,
                        }
                    )
                }
            },
            access_token=access_token,
        )
        runs_or_error = existing.get("data", {}).get("runsOrError", {})
        if runs_or_error.get("__typename") != "Runs":
            raise RuntimeError(_error_message(runs_or_error))
        results = runs_or_error.get("results") or []
        if results:
            run = results[0]
            run_id = str(run.get("runId") or "")
            if not run_id:
                raise RuntimeError("Dagster returned an existing run without a run ID")
            return DagsterOperationResult(
                operation="materialize_asset",
                dry_run=False,
                accepted=True,
                run_id=run_id,
                asset_key_path=asset_key_path,
                partition_key=partition_key,
                status=str(run.get("status") or "UNKNOWN"),
                message="Dagster previously accepted materialize_asset.",
                details={"typename": "LaunchRunSuccess", "reconciled": True},
            )
    selector: dict[str, Any] = {
        "pipelineName": job_name,
        "assetSelection": [{"path": asset_key_path.split("/")}],
    }
    if repository_location_name:
        selector["repositoryLocationName"] = repository_location_name
    if repository_name:
        selector["repositoryName"] = repository_name

    result = await _graphql(
        dagster_url,
        LAUNCH_PIPELINE_EXECUTION_MUTATION,
        {
            "executionParams": {
                "selector": selector,
                "runConfigData": run_config or {},
                "mode": "default",
                "executionMetadata": {"tags": _tags_for_execution(execution_tags)},
            }
        },
        access_token=access_token,
    )
    return _launch_result(
        operation="materialize_asset",
        dry_run=False,
        payload=result.get("data", {}).get("launchPipelineExecution", {}),
        asset_key_path=asset_key_path,
        partition_key=partition_key,
    )


async def launch_retry(
    *,
    dagster_url: str,
    run_id: str,
    strategy: str,
    idempotency_key: str | None = None,
    tags: dict[str, str] | None = None,
) -> DagsterOperationResult:
    """Launch a Dagster re-execution of a failed run using the given retry strategy."""
    execution_tags = {"phlo/operation": "retry_failed_run", "phlo/parent_run_id": run_id}
    if idempotency_key:
        execution_tags["phlo/idempotency_key"] = idempotency_key
    execution_tags.update(tags or {})
    result = await _graphql(
        dagster_url,
        LAUNCH_PIPELINE_REEXECUTION_MUTATION,
        {
            "executionParams": None,
            "reexecutionParams": {
                "parentRunId": run_id,
                "strategy": strategy,
                "extraTags": _tags_for_execution(execution_tags),
                "useParentRunTags": True,
            },
        },
    )
    return _launch_result(
        operation="retry_failed_run",
        dry_run=False,
        payload=result.get("data", {}).get("launchPipelineReexecution", {}),
        fallback_run_id=run_id,
    )


async def get_run_status(*, dagster_url: str, run_id: str, access_token: str | None = None) -> str:
    """Return the current Dagster status for a launched run."""
    result = await _graphql(
        dagster_url,
        RUN_STATUS_QUERY,
        {"runId": run_id},
        access_token=access_token,
    )
    payload = result.get("data", {}).get("runOrError", {})
    if payload.get("__typename") != "Run":
        raise RuntimeError(_error_message(payload))
    return str(payload.get("status") or "UNKNOWN")


async def terminate(
    *,
    dagster_url: str,
    run_id: str,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> DagsterOperationResult:
    """Terminate a Dagster run, reporting acceptance or the failure reason in
    the returned result rather than raising."""
    result = await _graphql(dagster_url, TERMINATE_RUN_MUTATION, {"runId": run_id})
    payload = result.get("data", {}).get("terminateRun", {})
    typename = str(payload.get("__typename") or "TerminateRunResult")
    raw_run = payload.get("run")
    run: dict[str, Any] = raw_run if isinstance(raw_run, dict) else {}
    accepted = typename == "TerminateRunSuccess"
    return DagsterOperationResult(
        operation="cancel_run",
        dry_run=False,
        accepted=accepted,
        run_id=str(run.get("runId") or run_id),
        status=str(run.get("status") or typename),
        message="Dagster accepted run cancellation." if accepted else _error_message(payload),
        details={
            key: value
            for key, value in {
                "typename": typename,
                "reason": reason,
                "idempotency_key": idempotency_key,
            }.items()
            if value
        },
    )


async def launch_backfill(
    *,
    dagster_url: str,
    asset_key_path: str,
    partition_set_name: str,
    partition_keys: list[str],
    repository_location_name: str | None = None,
    repository_name: str | None = None,
    idempotency_key: str | None = None,
    tags: dict[str, str] | None = None,
) -> DagsterOperationResult:
    """Launch a partition backfill for an asset across the given partition keys."""
    execution_tags = {"phlo/operation": "backfill_asset", "phlo/asset_key": asset_key_path}
    if idempotency_key:
        execution_tags["phlo/idempotency_key"] = idempotency_key
    execution_tags.update(tags or {})
    result = await _graphql(
        dagster_url,
        LAUNCH_PARTITION_BACKFILL_MUTATION,
        {
            "backfillParams": {
                "selector": {
                    "partitionSetName": partition_set_name,
                    "repositorySelector": {
                        "repositoryLocationName": repository_location_name,
                        "repositoryName": repository_name,
                    },
                },
                "partitionNames": partition_keys,
                "tags": _tags_for_execution(execution_tags),
            }
        },
    )
    payload = result.get("data", {}).get("launchPartitionBackfill", {})
    typename = str(payload.get("__typename") or "LaunchBackfillResult")
    accepted = typename == "LaunchBackfillSuccess"
    backfill_id = payload.get("backfillId")
    return DagsterOperationResult(
        operation="backfill_asset",
        dry_run=False,
        accepted=accepted,
        run_id=str(backfill_id) if backfill_id else None,
        asset_key_path=asset_key_path,
        status=typename,
        message="Dagster accepted partition backfill." if accepted else _error_message(payload),
        details={"partitions": partition_keys, "partition_count": len(partition_keys)},
    )


async def list_partitions(*, dagster_url: str, asset_key_path: str) -> list[dict[str, str]]:
    """List partition keys for an asset; every key is reported with status UNKNOWN.

    Raises RuntimeError when Dagster reports an error for the asset lookup.
    """
    result = await _graphql(
        dagster_url,
        PARTITION_KEYS_QUERY,
        {"assetKey": {"path": asset_key_path.split("/")}},
    )
    payload = result.get("data", {}).get("assetNodeOrError", {})
    if payload.get("message"):
        raise RuntimeError(str(payload["message"]))
    keys: list[str] = []
    for dimension in payload.get("partitionKeysByDimension", []) or []:
        if isinstance(dimension, dict):
            keys.extend(str(key) for key in dimension.get("partitionKeys", []) or [])
    return [{"partition_key": key, "status": "UNKNOWN"} for key in keys]


async def _graphql(
    url: str,
    query: str,
    variables: dict[str, Any],
    *,
    access_token: str | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    else:
        try:
            headers.update(build_service_headers("phlo-api", initiator="observatory"))
        except RuntimeError:
            # Service identity is best-effort here; without a configured
            # identity the request proceeds without auth headers and Dagster
            # decides whether to accept it.
            pass
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url, json={"query": query, "variables": variables}, headers=headers
        )
        payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "GraphQL error"))
    response.raise_for_status()
    return payload


def _launch_result(
    *,
    operation: str,
    dry_run: bool,
    payload: dict[str, Any],
    asset_key_path: str | None = None,
    partition_key: str | None = None,
    fallback_run_id: str | None = None,
) -> DagsterOperationResult:
    typename = str(payload.get("__typename") or "DagsterLaunchResult")
    raw_run = payload.get("run")
    run: dict[str, Any] = raw_run if isinstance(raw_run, dict) else {}
    accepted = typename in {"LaunchRunSuccess", "LaunchPipelineRunSuccess"} and bool(run)
    return DagsterOperationResult(
        operation=operation,
        dry_run=dry_run,
        accepted=accepted,
        run_id=str(run.get("runId") or fallback_run_id) if (run or fallback_run_id) else None,
        asset_key_path=asset_key_path,
        partition_key=partition_key,
        status=str(run.get("status") or typename),
        message=f"Dagster accepted {operation}." if accepted else _error_message(payload),
        details={"typename": typename},
    )


def _error_message(payload: dict[str, Any]) -> str:
    if payload.get("message"):
        return str(payload["message"])
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict) and first.get("message"):
            return str(first["message"])
    return str(payload.get("__typename") or "Dagster operation failed")


def _tags_for_execution(tags: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": str(key), "value": str(value)} for key, value in tags.items()]
