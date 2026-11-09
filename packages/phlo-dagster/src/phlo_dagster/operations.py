"""Dagster GraphQL adapter for Phlo API mutation routes.

Launches materializations, re-executions, cancels, and partition backfills
through raw GraphQL mutations, querying run status and partition keys. Every
mutation honours dry_run and idempotency keys; payloads normalize into
provider-neutral DagsterOperationResult dicts regardless of response shape.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from phlo._attempt import attempt_from_tags
from phlo.capabilities.continuity import canonical_json_bytes, sha256_bytes
from phlo.config.process import get_process_settings
from phlo.operations.journal import (
    OperationJournal,
    OperationJournalError,
    OperationJournalState,
    OperationJournalStore,
)
from phlo.operations.journal_store import FileOperationJournalStore
from phlo.security.mode import requires_http_authorization
from phlo.security.service_identity import (
    build_scoped_service_headers,
    build_service_headers,
    load_service_identity_credentials,
)

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
    runsOrError(filter: $filter, limit: 2) {
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
        ... on Run { runId status rootRunId tags { key value } }
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


async def find_materialization_run(
    *,
    dagster_url: str,
    idempotency_key: str,
    access_token: str | None = None,
    request_token: str | None = None,
) -> dict[str, Any] | None:
    """Read an accepted launch without ever submitting another one."""
    tags = {"phlo/operation": "materialize_asset", "phlo/idempotency_key": idempotency_key}
    if request_token is not None:
        tags["phlo/launch_request"] = request_token
    existing = await _graphql(
        dagster_url,
        EXISTING_MATERIALIZATION_RUN_QUERY,
        {"filter": {"tags": _tags_for_execution(tags)}},
        access_token=access_token,
    )
    payload = existing.get("data", {}).get("runsOrError", {})
    if payload.get("__typename") != "Runs":
        raise RuntimeError(_error_message(payload))
    runs = payload.get("results") or []
    if len(runs) > 1:
        raise RuntimeError(
            "Multiple Dagster runs share this operation key; reconciliation required"
        )
    if runs and not runs[0].get("runId"):
        raise RuntimeError("Dagster returned an existing run without a run ID")
    return runs[0] if runs else None


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
    asset_selection: list[str] | None = None,
    journal_store: OperationJournalStore | None = None,
) -> DagsterOperationResult:
    """Claim a keyed request before submission; replay its durable receipt.

    Replicas must share journal_store or PHLO_OPERATIONS_JOURNAL_DIR. An unknown
    response permits read-only reconciliation, never another submission. Without
    an idempotency key the launch is unjournaled and has no replay guarantee.
    """
    selection = asset_selection if asset_selection is not None else [asset_key_path]
    if not selection or asset_key_path not in selection or len(set(selection)) != len(selection):
        raise ValueError("Asset selection must be unique and include the requested asset")
    execution_tags = {
        **(tags or {}),
        "phlo/operation": "materialize_asset",
        "phlo/asset_key": asset_key_path,
    }
    execution_tags.pop("phlo/launch_request", None)
    if idempotency_key:
        execution_tags["phlo/idempotency_key"] = idempotency_key
    if partition_key:
        execution_tags.setdefault("dagster/partition", partition_key)
    selector: dict[str, Any] = {
        "pipelineName": job_name,
        "assetSelection": [{"path": key.split("/")} for key in selection],
    }
    if repository_location_name:
        selector["repositoryLocationName"] = repository_location_name
    if repository_name:
        selector["repositoryName"] = repository_name

    params = {
        "selector": selector,
        "runConfigData": run_config or {},
        "mode": "default",
        "executionMetadata": {"tags": _tags_for_execution(dict(sorted(execution_tags.items())))},
    }
    if idempotency_key:
        return await _journaled_materialization(
            dagster_url=dagster_url,
            idempotency_key=idempotency_key,
            params=params,
            asset_key_path=asset_key_path,
            partition_key=partition_key,
            access_token=access_token,
            store=journal_store,
        )
    response = await _graphql(
        dagster_url,
        LAUNCH_PIPELINE_EXECUTION_MUTATION,
        {"executionParams": params},
        access_token=access_token,
    )
    return _launch_result(
        operation="materialize_asset",
        payload=response.get("data", {}).get("launchPipelineExecution", {}),
        asset_key_path=asset_key_path,
        partition_key=partition_key,
    )


async def _journaled_materialization(
    *,
    dagster_url: str,
    idempotency_key: str,
    params: dict[str, Any],
    asset_key_path: str,
    partition_key: str | None,
    access_token: str | None,
    store: OperationJournalStore | None,
) -> DagsterOperationResult:
    if store is None:
        directory = get_process_settings().phlo_operations_journal_dir
        if not directory:
            raise RuntimeError(
                "Keyed materialization requires PHLO_OPERATIONS_JOURNAL_DIR or a shared journal store"
            )
        store = FileOperationJournalStore(directory)
    journal = OperationJournal(store)
    endpoint = sha256_bytes(dagster_url.encode())
    operation_id = "materialize:" + sha256_bytes(canonical_json_bytes([endpoint, idempotency_key]))
    binding = dict(
        subject=endpoint,
        action="materialize_asset",
        target=operation_id,
        plan_token=sha256_bytes(canonical_json_bytes(params)),
    )
    try:
        journal.claim(operation_id=operation_id, **binding)
    except OperationJournalError as exc:
        if exc.code != "conflicting_claim":
            raise
        return await _replay_materialization(
            journal=journal,
            operation_id=operation_id,
            binding=binding,
            dagster_url=dagster_url,
            idempotency_key=idempotency_key,
            asset_key_path=asset_key_path,
            partition_key=partition_key,
            access_token=access_token,
        )
    params["executionMetadata"]["tags"].append(
        {"key": "phlo/launch_request", "value": binding["plan_token"]}
    )
    journal.submit(operation_id)
    try:
        response = await _graphql(
            dagster_url,
            LAUNCH_PIPELINE_EXECUTION_MUTATION,
            {"executionParams": params},
            access_token=access_token,
        )
        payload = response.get("data", {}).get("launchPipelineExecution", {})
        result = _launch_result(
            operation="materialize_asset",
            payload=payload,
            asset_key_path=asset_key_path,
            partition_key=partition_key,
        )
        if not result.run_id and payload.get("__typename") not in {
            "PipelineNotFoundError",
            "InvalidSubsetError",
            "RunConfigValidationInvalid",
        }:
            raise RuntimeError("Dagster launch outcome is unknown: " + result.message)
    except BaseException:
        # A concurrent recovery may already have stored the definitive receipt.
        try:
            journal.unknown(operation_id)
        except OperationJournalError as exc:
            if exc.code != "invalid_transition":
                raise
        raise
    state = OperationJournalState.SUCCEEDED if result.accepted else OperationJournalState.FAILED
    try:
        journal.complete(operation_id, state=state, result=result.to_dict())
    except OperationJournalError as exc:
        if exc.code != "invalid_transition":
            raise
        receipt = journal.replay(operation_id, **binding)
        if receipt is None or receipt.get("run_id") != result.run_id:
            raise
        return DagsterOperationResult(**receipt)
    return result


async def _replay_materialization(
    *,
    journal: OperationJournal,
    operation_id: str,
    binding: dict[str, str],
    dagster_url: str,
    idempotency_key: str,
    asset_key_path: str,
    partition_key: str | None,
    access_token: str | None,
) -> DagsterOperationResult:
    try:
        receipt = journal.replay(operation_id, **binding)
    except OperationJournalError as exc:
        if exc.code not in {"operation_in_progress", "unknown_outcome_blocks_replay"}:
            raise
        unknown = exc.code == "unknown_outcome_blocks_replay"
        run = await find_materialization_run(
            dagster_url=dagster_url,
            idempotency_key=idempotency_key,
            request_token=binding["plan_token"],
            access_token=access_token,
        )
        if run is None:
            raise RuntimeError(
                "Materialization is in progress or its outcome is unknown; submission is fenced"
            ) from exc
        result = DagsterOperationResult(
            operation="materialize_asset",
            dry_run=False,
            accepted=True,
            run_id=str(run["runId"]),
            asset_key_path=asset_key_path,
            partition_key=partition_key,
            status=str(run.get("status") or "UNKNOWN"),
            message="Dagster previously accepted materialize_asset.",
            details={"typename": "LaunchRunSuccess", "reconciled": True},
        )
        try:
            command = journal.reconcile if unknown else journal.complete
            command(operation_id, state=OperationJournalState.SUCCEEDED, result=result.to_dict())
        except OperationJournalError as conflict:
            if conflict.code != "invalid_transition":
                raise
        # Always return the persisted winner, never a competing observation.
        receipt = journal.replay(operation_id, **binding)
    if receipt is None:
        raise RuntimeError("Materialization has no durable launch receipt")
    return DagsterOperationResult(**receipt)


async def launch_retry(
    *,
    dagster_url: str,
    run_id: str,
    strategy: str,
    idempotency_key: str | None = None,
    tags: dict[str, str] | None = None,
) -> DagsterOperationResult:
    """Launch a Dagster re-execution of a failed run using the given retry strategy."""
    observed = await _graphql(dagster_url, RUN_STATUS_QUERY, {"runId": run_id})
    parent = observed.get("data", {}).get("runOrError", {})
    if parent.get("__typename") != "Run":
        raise RuntimeError(_error_message(parent))
    parent_tags = {tag["key"]: tag["value"] for tag in parent.get("tags", [])}
    attempt, error = attempt_from_tags(parent_tags)
    if error or attempt is None:
        raise ValueError("Cannot retry a run with invalid_attempt")
    if parent_tags.get("phlo/wap_branch"):
        raise ValueError("WAP retry requires a newly prepared launch and execution binding")
    execution_tags = {"phlo/operation": "retry_failed_run", "phlo/parent_run_id": run_id}
    if idempotency_key:
        execution_tags["phlo/idempotency_key"] = idempotency_key
    execution_tags.update(tags or {})
    execution_tags["phlo/attempt"] = str(attempt + 1)
    execution_tags["phlo/run_id"] = (
        parent_tags.get("phlo/run_id") or parent.get("rootRunId") or run_id
    )
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
        payload=result.get("data", {}).get("launchPipelineReexecution", {}),
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
    all_partitions: bool = False,
    job_name: str | None = None,
    repository_location_name: str | None = None,
    repository_name: str | None = None,
    idempotency_key: str | None = None,
    tags: dict[str, str] | None = None,
) -> DagsterOperationResult:
    """Launch an explicit-key or Dagster-native all-partitions backfill."""
    if all_partitions == bool(partition_keys):
        raise ValueError("Specify either partition keys or all_partitions, but not both.")
    execution_tags = {"phlo/operation": "backfill_asset", "phlo/asset_key": asset_key_path}
    if job_name:
        execution_tags["phlo/job"] = job_name
    if idempotency_key:
        execution_tags["phlo/idempotency_key"] = idempotency_key
    execution_tags.update(tags or {})
    backfill_params: dict[str, Any] = {
        "selector": {
            "partitionSetName": partition_set_name,
            "repositorySelector": {
                "repositoryLocationName": repository_location_name,
                "repositoryName": repository_name,
            },
        },
        "assetSelection": [{"path": asset_key_path.split("/")}],
        "allPartitions": all_partitions,
        "tags": _tags_for_execution(execution_tags),
    }
    if not all_partitions:
        backfill_params["partitionNames"] = partition_keys
    result = await _graphql(
        dagster_url,
        LAUNCH_PARTITION_BACKFILL_MUTATION,
        {"backfillParams": backfill_params},
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
        details={
            "partitions": partition_keys,
            "partition_count": None if all_partitions else len(partition_keys),
            "all_partitions": all_partitions,
        },
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


READINESS_QUERY = "query PhloReadiness { __typename }"


async def wait_for_dagster_http(
    url: str, *, timeout_seconds: float = 180.0, poll_interval_seconds: float = 2.0
) -> None:
    """Poll the Dagster GraphQL endpoint until it answers HTTP.

    Any HTTP response (even an error payload) proves the webserver is serving;
    only transport-level failures keep polling. WAP launches otherwise hit a
    warming webserver with a single attempt and fail as ambiguous. Raises
    RuntimeError after ``timeout_seconds`` with an actionable hint.
    """
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                await client.post(url, json={"query": READINESS_QUERY})
                return
            except httpx.HTTPError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Dagster is not accepting GraphQL requests at {url} after "
                        f"{timeout_seconds:.0f}s. Inspect startup logs with: "
                        "phlo services logs --tail 120 dagster"
                    ) from None
                await asyncio.sleep(poll_interval_seconds)


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
    elif requires_http_authorization():
        # Production orchestration→API calls must carry the declared workload
        # identity; missing credentials fail before any HTTP request is made.
        headers.update(
            build_scoped_service_headers(
                "phlo-orchestration",
                audience="phlo-api",
                scp=("api:orchestrate",),
                credentials=load_service_identity_credentials(),
                initiator="orchestration",
                correlation_id=None,
            )
        )
    else:
        try:
            headers.update(build_service_headers("phlo-orchestration", initiator="observatory"))
        except RuntimeError:
            # Service identity is best-effort in development; without a
            # configured identity the request proceeds and Dagster decides.
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
    payload: dict[str, Any],
    asset_key_path: str | None = None,
    partition_key: str | None = None,
    fallback_run_id: str | None = None,
) -> DagsterOperationResult:
    typename = str(payload.get("__typename") or "DagsterLaunchResult")
    raw_run = payload.get("run")
    run: dict[str, Any] = raw_run if isinstance(raw_run, dict) else {}
    accepted = typename in {"LaunchRunSuccess", "LaunchPipelineRunSuccess"} and bool(run)
    run_id = run.get("runId") or fallback_run_id
    return DagsterOperationResult(
        operation=operation,
        dry_run=False,
        accepted=accepted,
        run_id=str(run_id) if run_id else None,
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
