"""Environment-scoped Nessie branch workflows for ``/api/v1``."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from anyio import to_thread
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import Field

from phlo.audit.events import AuditEventType, CanonicalAuditEvent
from phlo.capabilities.authentication import AuthPrincipal
from phlo.compliance.signatures.types import SignatureMeaning, SignatureRequest
from phlo.config.env import project_env_value
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import (
    audit_operation,
    enforce_rate_limit,
    idempotency_key_target,
    load_operational_settings,
    replay_or_execute_async,
    require_scope,
    shared_operation_controls,
)
from phlo_api.api.v1 import _target
from phlo_api.errors import BackendUnavailableError, BadGatewayError, NotFoundError
from phlo_api.observatory_api.http_client import backend_client
from phlo_api.branch_schema_resolution import (
    PreparedSchemaResolutions,
    prepare_schema_resolutions,
    verify_schema_resolutions,
)
from phlo_api.settings import get_deployment_settings
from phlo_api.v1_contract import Environment, EnvironmentTarget, WireModel

router = APIRouter(tags=["v1 branches"])

_REFERENCE_LIMIT = 500
_HISTORY_PAGE_SIZE = 100
_HISTORY_LIMIT = 2_000
_CHECK_POLL_SECONDS = 1
_CHECK_TIMEOUT_SECONDS = 300
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MERGE_LOCK = asyncio.Lock()
BranchCheckName = Literal["tests", "contracts", "audits"]


class BranchReference(WireModel):
    env: Environment
    name: str
    type: Literal["BRANCH", "TAG"]
    hash: str
    protected: bool


class BranchReferencePage(WireModel):
    env: Environment
    items: list[BranchReference]


class BranchCommit(WireModel):
    hash: str
    parent_hashes: list[str]
    message: str | None
    author: str | None
    committer: str | None
    committed_at: str | None


class BranchCommitPage(WireModel):
    env: Environment
    branch: str
    items: list[BranchCommit]
    next_cursor: str | None


class BranchChange(WireModel):
    key: str
    status: Literal["added", "modified", "deleted"]
    from_content_id: str | None
    to_content_id: str | None


class BranchDiff(WireModel):
    env: Environment
    source: str
    target: str
    source_hash: str
    target_hash: str
    items: list[BranchChange]
    truncated: bool


class BranchComparison(WireModel):
    env: Environment
    source: str
    target: str
    source_hash: str
    target_hash: str
    merge_base: str | None
    ahead: int | None
    behind: int | None
    status: Literal["compared", "unavailable"]


class BranchCreate(WireModel):
    name: str = Field(min_length=1, max_length=128)
    from_ref: str | None = Field(default=None, min_length=1, max_length=128)


class BranchDelete(WireModel):
    expected_hash: str = Field(min_length=1, max_length=256)


class BranchRebase(WireModel):
    target: str = Field(min_length=1, max_length=128)
    expected_source_hash: str = Field(min_length=1, max_length=256)
    expected_target_hash: str = Field(min_length=1, max_length=256)


class BranchTrialMerge(WireModel):
    target: str = Field(min_length=1, max_length=128)
    expected_source_hash: str = Field(min_length=1, max_length=256)
    expected_target_hash: str = Field(min_length=1, max_length=256)
    incident_id: str | None = Field(default=None, min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)


class BranchMerge(WireModel):
    target: str = Field(min_length=1, max_length=128)
    expected_source_hash: str = Field(min_length=1, max_length=256)
    expected_target_hash: str = Field(min_length=1, max_length=256)
    incident_id: str | None = Field(default=None, min_length=1, max_length=100)
    signature_id: str = Field(min_length=1, max_length=100)
    reviewer_subject: str | None = Field(default=None, min_length=1, max_length=256)
    review_signature_id: str | None = Field(default=None, min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)


class BranchCheck(WireModel):
    name: BranchCheckName
    status: Literal["passed", "failed", "unavailable"]
    run_id: str | None
    message: str | None


class BranchChecksRequest(WireModel):
    expected_hash: str = Field(min_length=1, max_length=256)


class BranchCheckPage(WireModel):
    env: Environment
    branch: str
    branch_hash: str
    items: list[BranchCheck]
    passed: bool
    status: Literal["succeeded", "rejected"]


class BranchActionResult(WireModel):
    env: Environment
    operation: str
    branch: str
    target: str | None
    status: Literal["succeeded", "conflict", "rejected"]
    source_hash: str | None
    target_hash: str | None
    resulting_hash: str | None
    details: dict[str, Any]


def _base_url() -> str:
    configured = project_env_value("NESSIE_URL")
    if not configured:
        raise BackendUnavailableError("Nessie is unavailable.")
    base = configured.rstrip("/")
    for suffix in ("/api/v1", "/api/v2"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base


async def _nessie(
    method: str,
    path: str,
    *,
    params: dict[str, str | int | bool] | None = None,
    body: dict[str, Any] | None = None,
    allow_not_found: bool = False,
) -> dict[str, Any] | list[Any] | None:
    try:
        async with backend_client() as client:
            response = await client.request(
                method,
                f"{_base_url()}{path}",
                params=params,
                json=body,
                timeout=20.0,
            )
    except (httpx.HTTPError, OSError) as exc:
        raise BackendUnavailableError("Nessie is unavailable.") from exc
    if allow_not_found and response.status_code == 404:
        return None
    if response.status_code == 404:
        raise NotFoundError("Nessie reference was not found.")
    if response.status_code in {409, 412}:
        raise HTTPException(status_code=409, detail="Nessie reference changed; reload and retry.")
    if response.status_code >= 500:
        raise BackendUnavailableError("Nessie is unavailable.")
    if response.is_error:
        raise HTTPException(status_code=422, detail="Nessie rejected the branch operation.")
    if response.status_code == 204 or not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise BadGatewayError("Nessie returned invalid JSON.") from exc
    if isinstance(payload, dict) or isinstance(payload, list):
        return payload
    raise BadGatewayError("Nessie returned an invalid response.")


def _object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BadGatewayError(message)
    return value


def _scope_ref(name: str, env: Environment, target: EnvironmentTarget) -> bool:
    return name == target.nessie_ref or name.startswith(f"{env}-")


def _validate_name(name: str, env: Environment) -> str:
    if not _NAME_PATTERN.fullmatch(name) or not name.startswith(f"{env}-"):
        raise HTTPException(
            status_code=422,
            detail=f"Branch and tag names must use the {env}- namespace.",
        )
    return name


def _require_branch_actions() -> None:
    settings = get_deployment_settings()
    if (
        shared_operation_controls() is None
        and (not settings.actions_single_replica or not settings.actions_single_process)
    ) or not settings.actions_ref_tag_contract:
        raise BackendUnavailableError("Environment-pinned branch actions are not enabled.")


async def _reference(name: str) -> dict[str, Any] | None:
    value = await _nessie("GET", f"/api/v1/trees/tree/{quote(name, safe='')}", allow_not_found=True)
    return value if isinstance(value, dict) else None


def _reference_view(
    raw: dict[str, Any], env: Environment, target: EnvironmentTarget
) -> BranchReference:
    name, ref_type, head_hash = raw.get("name"), raw.get("type"), raw.get("hash")
    if (
        not isinstance(name, str)
        or ref_type not in {"BRANCH", "TAG"}
        or not isinstance(head_hash, str)
        or not head_hash
    ):
        raise BadGatewayError("Nessie returned an invalid reference.")
    return BranchReference(
        env=env,
        name=name,
        type=ref_type,
        hash=head_hash,
        protected=name == target.nessie_ref,
    )


async def _scoped_reference(
    name: str, env: Environment, target: EnvironmentTarget, *, branch_only: bool = False
) -> BranchReference:
    if not _scope_ref(name, env, target):
        raise NotFoundError("Reference was not found in the selected environment.")
    raw = await _reference(name)
    if raw is None:
        raise NotFoundError("Reference was not found in the selected environment.")
    result = _reference_view(raw, env, target)
    if result.name != name:
        raise BadGatewayError("Nessie returned a different reference than requested.")
    if branch_only and result.type != "BRANCH":
        raise HTTPException(status_code=422, detail="The selected ref is not a branch.")
    return result


async def _history(name: str, head_hash: str, *, limit: int) -> list[BranchCommit]:
    cursor: str | None = None
    items: list[BranchCommit] = []
    while len(items) < limit:
        params: dict[str, str | int] = {
            "maxRecords": min(_HISTORY_PAGE_SIZE, limit - len(items)),
            "fetch": "ALL",
        }
        if cursor is not None:
            params["pageToken"] = cursor
        value = await _nessie(
            "GET",
            f"/api/v2/trees/{quote(name, safe='')}@{quote(head_hash, safe='')}/history",
            params=params,
        )
        payload = _object(value, "Nessie returned invalid commit history.")
        rows = payload.get("logEntries")
        if not isinstance(rows, list) or len(rows) > _HISTORY_PAGE_SIZE:
            raise BadGatewayError("Nessie returned invalid commit history.")
        for row in rows:
            entry = _object(row, "Nessie returned invalid commit history.")
            meta = _object(entry.get("commitMeta"), "Nessie returned invalid commit metadata.")
            commit_hash = meta.get("hash")
            if not isinstance(commit_hash, str) or not commit_hash:
                raise BadGatewayError("Nessie returned invalid commit metadata.")
            parents = meta.get("parentCommitHashes")
            if parents is None:
                parent = entry.get("parentCommitHash")
                parents = [parent] if isinstance(parent, str) and parent else []
            if not isinstance(parents, list) or not all(
                isinstance(parent, str) for parent in parents
            ):
                raise BadGatewayError("Nessie returned invalid commit ancestry.")
            items.append(
                BranchCommit(
                    hash=commit_hash,
                    parent_hashes=parents,
                    message=meta.get("message") if isinstance(meta.get("message"), str) else None,
                    author=meta.get("author") if isinstance(meta.get("author"), str) else None,
                    committer=meta.get("committer")
                    if isinstance(meta.get("committer"), str)
                    else None,
                    committed_at=meta.get("commitTime")
                    if isinstance(meta.get("commitTime"), str)
                    else None,
                )
            )
            if len(items) >= limit:
                break
        if not payload.get("hasMore"):
            return items
        next_cursor = payload.get("token")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise BadGatewayError("Nessie returned an invalid commit cursor.")
        cursor = next_cursor
    return items


def _commit_page(
    items: list[BranchCommit],
    *,
    after: str | None,
    limit: int,
    env: Environment,
    branch: BranchReference,
) -> tuple[list[BranchCommit], str | None]:
    start = 0
    if after is not None:
        try:
            payload = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
            if (
                not isinstance(payload, dict)
                or payload.get("env") != env
                or payload.get("branch") != branch.name
                or payload.get("head") != branch.hash
                or not isinstance(payload.get("after"), str)
            ):
                raise ValueError
            cursor_hash = payload["after"]
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail="Commit cursor is stale or invalid."
            ) from exc
        positions = [index for index, item in enumerate(items) if item.hash == cursor_hash]
        if len(positions) != 1:
            raise HTTPException(status_code=400, detail="Commit cursor is stale or invalid.")
        start = positions[0] + 1
    page = items[start : start + limit]
    next_cursor = None
    if len(items) > start + limit and page:
        cursor = {
            "env": env,
            "branch": branch.name,
            "head": branch.hash,
            "after": page[-1].hash,
        }
        next_cursor = (
            base64.urlsafe_b64encode(json.dumps(cursor, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
    return page, next_cursor


async def _diff(source: BranchReference, target: BranchReference, env: Environment) -> BranchDiff:
    value = await _nessie(
        "GET",
        f"/api/v2/trees/{quote(source.name, safe='')}@{quote(source.hash, safe='')}/diff/{quote(target.name, safe='')}@{quote(target.hash, safe='')}",
        params={"maxRecords": _REFERENCE_LIMIT + 1},
    )
    payload = _object(value, "Nessie returned invalid branch differences.")
    rows = payload.get("diffs")
    if not isinstance(rows, list):
        raise BadGatewayError("Nessie returned invalid branch differences.")
    changes: list[BranchChange] = []
    for row in rows[:_REFERENCE_LIMIT]:
        item = _object(row, "Nessie returned an invalid branch difference.")
        key = _object(item.get("key"), "Nessie returned an invalid branch key.")
        elements = key.get("elements")
        if (
            not isinstance(elements, list)
            or not elements
            or not all(isinstance(part, str) and part for part in elements)
        ):
            raise BadGatewayError("Nessie returned an invalid branch key.")
        before, after = item.get("from"), item.get("to")
        status: Literal["added", "modified", "deleted"] = (
            "added" if before is None else "deleted" if after is None else "modified"
        )
        changes.append(
            BranchChange(
                key=".".join(elements),
                status=status,
                from_content_id=_content_id(before),
                to_content_id=_content_id(after),
            )
        )
    return BranchDiff(
        env=env,
        source=source.name,
        target=target.name,
        source_hash=source.hash,
        target_hash=target.hash,
        items=changes,
        truncated=len(rows) > _REFERENCE_LIMIT or bool(payload.get("hasMore")),
    )


def _content_id(value: Any) -> str | None:
    if value is None:
        return None
    content = _object(value, "Nessie returned invalid content metadata.")
    identity = content.get("id")
    return identity if isinstance(identity, str) else None


async def _requires_independent_review(
    request: Request, source: BranchReference, target: BranchReference
) -> bool:
    diff = await _diff(source, target, source.env)
    if diff.truncated:
        raise BackendUnavailableError("Independent review requires a complete branch diff.")
    keys = {item.key for item in diff.items}
    if any(key.split(".")[0] == "gold" for key in keys):
        return True
    if not keys:
        return False
    # Read only explicit declarations in the selected code location. Model
    # names such as dim_*, fct_* and staging names are not layer evidence.
    from phlo_api.api import v1_assets

    nodes = await v1_assets._asset_nodes(request, source.env)
    layers: dict[str, str | None] = {}
    for node in nodes:
        entries = node.get("metadataEntries") or []
        asset_key = v1_assets._key_path(node.get("assetKey"))
        matched = keys.intersection({".".join(asset_key), v1_assets._declared_relation(entries)})
        if not matched:
            continue
        declarations = [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("label") == "phlo/layer"
        ]
        if len(declarations) > 1:
            raise BackendUnavailableError("Asset has ambiguous phlo/layer declarations.")
        declared = declarations[0].get("text") if declarations else node.get("groupName")
        layer = (
            declared
            if isinstance(declared, str) and declared in {"bronze", "silver", "gold"}
            else None
        )
        for key in matched:
            if key in layers and layers[key] != layer:
                raise BackendUnavailableError(
                    "Changed content has conflicting selected layer declarations."
                )
            layers[key] = layer
    # Unknown content is not gold evidence, but cannot prove eligibility for
    # bypassing the independent review either.
    return any(layers.get(key, key.split(".")[0]) not in {"bronze", "silver"} for key in keys)


def _consume_independent_review(payload: BranchMerge, expected: SignatureRequest) -> None:
    from dataclasses import replace

    from phlo_api.api import v1_admin_identity

    if (
        not payload.reviewer_subject
        or payload.reviewer_subject == expected.signer_subject
        or not payload.review_signature_id
        or payload.review_signature_id == payload.signature_id
    ):
        raise HTTPException(
            409, "Gold or unclassified changes require a different reviewer's MFA signature."
        )
    authority = v1_admin_identity._authority()
    member = authority.member(payload.reviewer_subject)
    if (
        member is None
        or not member.active
        or member.principal_type != "user"
        or not {"admin", "operator"}.intersection(member.roles)
    ):
        raise HTTPException(409, "The gold reviewer must be an active authorised human operator.")
    reviewed = replace(expected, signer_subject=member.subject, meaning=SignatureMeaning.REVIEWED)
    review_id = payload.review_signature_id
    if not v1_admin_identity._identity_call(
        lambda: authority.consume_signature(
            payload.signature_id, expected, review=(review_id, reviewed)
        )
    ):
        raise HTTPException(
            409, "Approval or independent review is missing, stale, reused or invalid."
        )


async def _comparison(
    source: BranchReference, target: BranchReference, env: Environment
) -> BranchComparison:
    left, right = await asyncio.gather(
        _history(source.name, source.hash, limit=_HISTORY_LIMIT),
        _history(target.name, target.hash, limit=_HISTORY_LIMIT),
    )
    right_positions = {commit.hash: index for index, commit in enumerate(right)}
    common = [
        (left_index + right_index, left_index, right_index, commit_hash)
        for left_index, commit in enumerate(left)
        if (right_index := right_positions.get(commit.hash)) is not None
        for commit_hash in [commit.hash]
    ]
    if not common:
        return BranchComparison(
            env=env,
            source=source.name,
            target=target.name,
            source_hash=source.hash,
            target_hash=target.hash,
            merge_base=None,
            ahead=None,
            behind=None,
            status="unavailable",
        )
    _, left_index, right_index, merge_base = min(common)
    return BranchComparison(
        env=env,
        source=source.name,
        target=target.name,
        source_hash=source.hash,
        target_hash=target.hash,
        merge_base=merge_base,
        ahead=left_index,
        behind=right_index,
        status="compared",
    )


def _check_job_names(env: Environment) -> dict[str, str]:
    raw = os.environ.get("PHLO_V1_BRANCH_CHECK_JOBS")
    try:
        if raw is None or len(raw) > 16_384:
            raise ValueError
        configured = json.loads(raw)
        jobs = configured[env]
        if (
            not isinstance(configured, dict)
            or set(configured) != {"prod", "staging"}
            or not isinstance(jobs, dict)
            or set(jobs) != {"tests", "contracts", "audits"}
            or any(
                not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name)
                for name in jobs.values()
            )
        ):
            raise ValueError
        return jobs
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackendUnavailableError("Branch pre-merge check jobs are not configured.") from exc


async def _run_check_job(
    *,
    env: Environment,
    target: EnvironmentTarget,
    branch: BranchReference,
    check_name: BranchCheckName,
    job_name: str,
    additional_tags: dict[str, str] | None = None,
    timeout_seconds: int = _CHECK_TIMEOUT_SECONDS,
) -> BranchCheck:
    from phlo_api.api import v1_jobs

    repositories = v1_jobs._repositories(
        await v1_jobs._graphql(v1_jobs.JOBS_QUERY), target.dagster_location
    )
    jobs = v1_jobs._jobs(repositories, env, {})
    matches = [job for job in jobs if job.id == job_name]
    if len(matches) != 1:
        return BranchCheck(
            name=check_name,
            status="failed",
            run_id=None,
            message="Configured check job was not found.",
        )
    repository = v1_jobs._repository(repositories, matches[0].repository_name)
    result = await v1_jobs._graphql(
        v1_jobs.LAUNCH_JOB_MUTATION,
        {
            "executionParams": {
                "selector": {
                    "pipelineName": job_name,
                    "repositoryLocationName": target.dagster_location,
                    "repositoryName": repository["name"],
                },
                "runConfigData": {},
                "mode": "default",
                "executionMetadata": {
                    "tags": [
                        {"key": "environment", "value": env},
                        {"key": "phlo/ref", "value": branch.name},
                        {"key": "phlo/branch_hash", "value": branch.hash},
                        {"key": "phlo/branch_check", "value": check_name},
                        {"key": "phlo/operation", "value": "v1_branch_check"},
                        *[
                            {"key": key, "value": value}
                            for key, value in (additional_tags or {}).items()
                        ],
                    ]
                },
            }
        },
    )
    launched = v1_jobs._mutation_field(
        result,
        "launchPipelineExecution",
        "LaunchRunSuccess",
        target_type="Branch check job",
    )
    run = launched.get("run")
    if not isinstance(run, dict) or not isinstance(run.get("runId"), str):
        raise BadGatewayError("Dagster returned an invalid branch check run.")
    run_id = run["runId"]
    query = """query BranchCheckRun($runId: ID!) {
      runOrError(runId: $runId) {
        __typename
        ... on Run { runId status tags { key value } repositoryOrigin { repositoryLocationName } }
        ... on RunNotFoundError { message }
      }
    }"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        checked = await v1_jobs._graphql(query, {"runId": run_id})
        data = checked.get("data")
        run_result = data.get("runOrError") if isinstance(data, dict) else None
        if not isinstance(run_result, dict):
            raise BadGatewayError("Dagster returned an invalid branch check status.")
        if run_result.get("__typename") == "RunNotFoundError":
            raise BackendUnavailableError("Dagster branch check run is unavailable.")
        status = run_result.get("status")
        if not isinstance(status, str):
            raise BadGatewayError("Dagster returned an invalid branch check status.")
        if status in {"SUCCESS", "FAILURE", "CANCELED"}:
            tags = run_result.get("tags")
            tag_map = (
                {
                    tag.get("key"): tag.get("value")
                    for tag in tags
                    if isinstance(tag, dict)
                    and isinstance(tag.get("key"), str)
                    and isinstance(tag.get("value"), str)
                }
                if isinstance(tags, list)
                else {}
            )
            repository_origin = run_result.get("repositoryOrigin")
            if (
                run_result.get("runId") != run_id
                or not isinstance(repository_origin, dict)
                or repository_origin.get("repositoryLocationName") != target.dagster_location
                or tag_map.get("environment") != env
                or tag_map.get("phlo/ref") != branch.name
                or tag_map.get("phlo/branch_hash") != branch.hash
                or tag_map.get("phlo/branch_check") != check_name
                or any(tag_map.get(key) != value for key, value in (additional_tags or {}).items())
            ):
                return BranchCheck(
                    name=check_name,
                    status="failed",
                    run_id=run_id,
                    message="Dagster check evidence did not match the selected branch head.",
                )
            return BranchCheck(
                name=check_name,
                status="passed" if status == "SUCCESS" else "failed",
                run_id=run_id,
                message=None if status == "SUCCESS" else f"Check run ended with {status}.",
            )
        await asyncio.sleep(_CHECK_POLL_SECONDS)
    return BranchCheck(
        name=check_name,
        status="unavailable",
        run_id=run_id,
        message="Check run did not reach a terminal state before the deadline.",
    )


async def _premerge_checks(
    *, env: Environment, target: EnvironmentTarget, branch: BranchReference
) -> list[BranchCheck]:
    try:
        job_names = _check_job_names(env)
    except BackendUnavailableError as exc:
        return [
            BranchCheck(name=name, status="unavailable", run_id=None, message=str(exc))
            for name in ("tests", "contracts", "audits")
        ]
    results: list[BranchCheck] = []
    for check_name in ("tests", "contracts", "audits"):
        try:
            check = await _run_check_job(
                env=env,
                target=target,
                branch=branch,
                check_name=check_name,
                job_name=job_names[check_name],
            )
        except BackendUnavailableError as exc:
            check = BranchCheck(
                name=check_name, status="unavailable", run_id=None, message=str(exc)
            )
        except HTTPException as exc:
            check = BranchCheck(
                name=check_name,
                status="failed",
                run_id=None,
                message=f"Check request failed ({exc.status_code}).",
            )
        results.append(check)
    return results


def _event(
    actor: AuthPrincipal,
    *,
    action: str,
    env: Environment,
    source: str,
    target: str | None,
    outcome: str,
    source_hash: str | None = None,
    target_hash: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    from phlo_api.api.v1_admin_identity import _audit

    _audit(
        CanonicalAuditEvent(
            event_type=AuditEventType.MUTATION,
            surface="phlo-api",
            actor_subject=actor.subject,
            actor_type=actor.principal_type,
            actor_roles=actor.groups,
            authentication_source=actor.issuer or "phlo-authentication",
            action=action,
            resource_type="branch",
            resource_id=f"{env}:{source}->{target or ''}",
            decision="allow" if outcome == "success" else "skip",
            reason_code=f"branch_{outcome}",
            outcome=outcome,
            attributes={
                "env": env,
                "source_hash": source_hash,
                "target_hash": target_hash,
                **(attributes or {}),
            },
        )
    )


def _audit_callback(
    actor: AuthPrincipal,
    *,
    operation: str,
    target: str,
    env: Environment,
    source: str,
    target_ref: str | None,
    payload: dict[str, Any],
) -> Callable[[dict[str, Any]], None]:
    def audit(result: dict[str, Any]) -> None:
        audit_operation(
            operation=operation,
            target=target,
            dry_run=False,
            auth={"subject": actor.subject, "scopes": ["lakehouse:operate"]},
            payload={"env": env, "source": source, "target_ref": target_ref, **payload},
            result=result,
        )
        _event(
            actor,
            action=operation,
            env=env,
            source=source,
            target=target_ref,
            outcome="success" if result.get("status") == "succeeded" else "failure",
            source_hash=result.get("source_hash"),
            target_hash=result.get("target_hash"),
            attributes=result,
        )

    return audit


def _action_actor(request: Request, auth: dict[str, Any]) -> AuthPrincipal:
    principal = get_request_principal(request)
    if principal is not None:
        return principal
    return AuthPrincipal(
        subject=auth["subject"], principal_type="user", groups=tuple(auth["scopes"])
    )


def _require_action_key(key: str | None) -> str:
    if key is None or not key.strip() or len(key) > 200:
        raise HTTPException(status_code=422, detail="A valid Idempotency-Key is required.")
    return key


def _check_result(checks: list[BranchCheck]) -> dict[str, Any]:
    passed = len(checks) == 3 and all(item.status == "passed" for item in checks)
    return {
        "items": [item.model_dump(mode="json") for item in checks],
        "passed": passed,
        "status": "succeeded" if passed else "rejected",
    }


async def _merge_payload(
    *,
    source: BranchReference,
    target: BranchReference,
    message: str,
    dry_run: bool,
    key_merge_modes: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "fromRefName": source.name,
        "fromHash": source.hash,
        "message": message,
        "isDryRun": dry_run,
        "isReturnConflictAsResult": True,
        "isFetchAdditionalInfo": True,
    }
    if key_merge_modes:
        body["keyMergeModes"] = list(key_merge_modes)
    response = await _nessie(
        "POST",
        f"/api/v2/trees/{quote(target.name, safe='')}@{quote(target.hash, safe='')}/history/merge",
        body=body,
    )
    return _object(response, "Nessie returned an invalid trial merge result.")


def _merge_conflict_keys(trial: dict[str, Any]) -> list[str]:
    details = trial.get("details")
    if not isinstance(details, list):
        return []
    keys: set[str] = set()
    for item in details:
        if not isinstance(item, dict) or not isinstance(item.get("conflict"), dict):
            continue
        conflict = item["conflict"]
        key = conflict.get("key", item.get("key"))
        key_object = key if isinstance(key, dict) else {}
        elements = key_object.get("elements")
        if (
            not isinstance(elements, list)
            or not elements
            or not all(isinstance(part, str) and part for part in elements)
        ):
            raise BadGatewayError("Nessie returned an invalid merge conflict key.")
        keys.add(".".join(elements))
    return sorted(keys)


def _decision_signature_version(
    source: BranchReference,
    target: BranchReference,
    decisions: list[Any],
) -> str:
    base = f"{source.hash}:{target.hash}"
    if not decisions:
        return base
    bound = [
        {
            "id": decision.id,
            "table_key": decision.table_key,
            "columns": decision.columns,
        }
        for decision in sorted(decisions, key=lambda item: item.table_key)
    ]
    digest = hashlib.sha256(
        json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"{base}:schema:{digest}"


async def _trial_merge_with_resolutions(
    *,
    incident_id: str | None,
    env: Environment,
    source: BranchReference,
    target: BranchReference,
    message: str,
) -> tuple[dict[str, Any], PreparedSchemaResolutions | None, list[Any]]:
    trial = await _merge_payload(source=source, target=target, message=message, dry_run=True)
    conflict_keys = _merge_conflict_keys(trial)
    if not conflict_keys or incident_id is None:
        return trial, None, []
    from phlo_api import incidents

    decisions = await asyncio.to_thread(
        incidents.load_schema_decisions,
        incident_id,
        env,
        source.name,
        target.name,
        source.hash,
        target.hash,
    )
    prepared = await prepare_schema_resolutions(
        env=env,
        source_ref=source.name,
        target_ref=target.name,
        source_hash=source.hash,
        target_hash=target.hash,
        requested_keys=conflict_keys,
        decisions=decisions,
        nessie_request=_nessie,
    )
    try:
        trial = await _merge_payload(
            source=source,
            target=target,
            message=message,
            dry_run=True,
            key_merge_modes=prepared.key_merge_modes,
        )
        return trial, prepared, decisions
    except BaseException:
        await prepared.cleanup(_nessie)
        raise


@router.get("/branches/refs", response_model=BranchReferencePage)
async def v1_branch_refs(request: Request, env: Environment = Query()) -> BranchReferencePage:
    target = _target(request, env)
    require_scope(request, "lakehouse:read")
    value = await _nessie("GET", "/api/v1/trees", params={"fetch": "ALL"})
    payload = _object(value, "Nessie returned an invalid reference list.")
    rows = payload.get("references")
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise BadGatewayError("Nessie returned an invalid reference list.")
    items = [
        _reference_view(_object(row, "Nessie returned an invalid reference."), env, target)
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and _scope_ref(row["name"], env, target)
    ]
    if len(items) > _REFERENCE_LIMIT:
        raise BackendUnavailableError("Environment ref inventory exceeds the supported page limit.")
    return BranchReferencePage(
        env=env, items=sorted(items, key=lambda item: (item.type, item.name))
    )


@router.get("/branches/{branch_name}/commits", response_model=BranchCommitPage)
async def v1_branch_commits(
    request: Request,
    branch_name: str,
    env: Environment = Query(),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> BranchCommitPage:
    target = _target(request, env, allowed_query=frozenset({"env", "limit", "after"}))
    require_scope(request, "lakehouse:read")
    branch = await _scoped_reference(branch_name, env, target, branch_only=True)
    history = await _history(branch.name, branch.hash, limit=_HISTORY_LIMIT)
    items, next_cursor = _commit_page(history, after=after, limit=limit, env=env, branch=branch)
    return BranchCommitPage(env=env, branch=branch.name, items=items, next_cursor=next_cursor)


@router.get("/branches/{branch_name}/diff", response_model=BranchDiff)
async def v1_branch_diff(
    request: Request,
    branch_name: str,
    target_ref: Annotated[str, Query(alias="target", min_length=1, max_length=128)],
    env: Environment = Query(),
) -> BranchDiff:
    target = _target(request, env, allowed_query=frozenset({"env", "target"}))
    require_scope(request, "lakehouse:read")
    source = await _scoped_reference(branch_name, env, target, branch_only=True)
    target_branch = await _scoped_reference(target_ref, env, target, branch_only=True)
    return await _diff(source, target_branch, env)


@router.get("/branches/{branch_name}/compare", response_model=BranchComparison)
async def v1_branch_compare(
    request: Request,
    branch_name: str,
    target_ref: Annotated[str, Query(alias="target", min_length=1, max_length=128)],
    env: Environment = Query(),
) -> BranchComparison:
    target = _target(request, env, allowed_query=frozenset({"env", "target"}))
    require_scope(request, "lakehouse:read")
    source = await _scoped_reference(branch_name, env, target, branch_only=True)
    target_branch = await _scoped_reference(target_ref, env, target, branch_only=True)
    return await _comparison(source, target_branch, env)


@router.get("/branches/{branch_name}", response_model=BranchReference)
async def v1_branch_detail(
    request: Request, branch_name: str, env: Environment = Query()
) -> BranchReference:
    target = _target(request, env)
    require_scope(request, "lakehouse:read")
    return await _scoped_reference(branch_name, env, target, branch_only=True)


@router.get("/branches", response_model=BranchReferencePage)
async def v1_branches(request: Request, env: Environment = Query()) -> BranchReferencePage:
    page = await v1_branch_refs(request, env)
    return page.model_copy(update={"items": [item for item in page.items if item.type == "BRANCH"]})


@router.post("/branches", response_model=BranchActionResult)
async def v1_branch_create(
    request: Request,
    payload: BranchCreate,
    env: Environment = Query(),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BranchActionResult:
    auth = require_scope(request, "lakehouse:operate")
    _require_branch_actions()
    enforce_rate_limit(auth["subject"], "v1_branch_create")
    key = _require_action_key(idempotency_key)
    target_config = _target(request, env)
    name = _validate_name(payload.name, env)
    from_ref = payload.from_ref or target_config.nessie_ref
    if not _scope_ref(from_ref, env, target_config):
        raise NotFoundError("Source reference was not found in the selected environment.")
    operation = "v1_branch_create"
    action_target = f"{auth['subject']}:{env}:{name}<-{from_ref}"
    if idempotency_key_target(key, operation) not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        if await _reference(name) is not None:
            raise HTTPException(status_code=409, detail="Branch already exists.")
        source = await _scoped_reference(from_ref, env, target_config)
        await _nessie(
            "POST",
            "/api/v1/trees/tree",
            body={"name": name, "type": "BRANCH", "hash": source.hash},
        )
        created = await _scoped_reference(name, env, target_config, branch_only=True)
        return {
            "env": env,
            "operation": operation,
            "branch": created.name,
            "target": source.name,
            "status": "succeeded",
            "source_hash": source.hash,
            "target_hash": None,
            "resulting_hash": created.hash,
            "details": {"created_from": source.name},
        }

    outcome = await replay_or_execute_async(
        idempotency_key=key,
        operation=operation,
        target=action_target,
        exclusion_target=f"{env}:{name}",
        execute=execute,
        audit=_audit_callback(
            _action_actor(request, auth),
            operation=operation,
            target=action_target,
            env=env,
            source=name,
            target_ref=from_ref,
            payload={"from_ref": from_ref},
        ),
    )
    return BranchActionResult.model_validate(outcome)


@router.delete("/branches/{branch_name}", response_model=BranchActionResult)
async def v1_branch_delete(
    request: Request,
    branch_name: str,
    payload: BranchDelete,
    env: Environment = Query(),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BranchActionResult:
    auth = require_scope(request, "lakehouse:operate")
    _require_branch_actions()
    enforce_rate_limit(auth["subject"], "v1_branch_delete")
    key = _require_action_key(idempotency_key)
    target_config = _target(request, env)
    name = _validate_name(branch_name, env)
    if name == target_config.nessie_ref:
        raise HTTPException(
            status_code=422, detail="The configured environment ref cannot be deleted."
        )
    operation = "v1_branch_delete"
    action_target = f"{auth['subject']}:{env}:{name}@{payload.expected_hash}"
    if idempotency_key_target(key, operation) not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        branch = await _scoped_reference(name, env, target_config, branch_only=True)
        if branch.hash != payload.expected_hash:
            raise HTTPException(status_code=409, detail="Branch head changed; reload and retry.")
        await _nessie(
            "DELETE",
            f"/api/v1/trees/branch/{quote(branch.name, safe='')}",
            params={"expectedHash": branch.hash},
        )
        if await _reference(branch.name) is not None:
            raise BackendUnavailableError("Nessie did not confirm the branch deletion.")
        return {
            "env": env,
            "operation": operation,
            "branch": branch.name,
            "target": None,
            "status": "succeeded",
            "source_hash": branch.hash,
            "target_hash": None,
            "resulting_hash": None,
            "details": {"deleted": True},
        }

    outcome = await replay_or_execute_async(
        idempotency_key=key,
        operation=operation,
        target=action_target,
        exclusion_target=f"{env}:{name}",
        execute=execute,
        audit=_audit_callback(
            _action_actor(request, auth),
            operation=operation,
            target=action_target,
            env=env,
            source=name,
            target_ref=None,
            payload={"expected_hash": payload.expected_hash},
        ),
    )
    return BranchActionResult.model_validate(outcome)


@router.post("/branches/{branch_name}/rebase", response_model=BranchActionResult)
async def v1_branch_rebase(
    request: Request,
    branch_name: str,
    payload: BranchRebase,
    env: Environment = Query(),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BranchActionResult:
    auth = require_scope(request, "lakehouse:operate")
    _require_branch_actions()
    enforce_rate_limit(auth["subject"], "v1_branch_rebase")
    key = _require_action_key(idempotency_key)
    target_config = _target(request, env)
    source_name = _validate_name(branch_name, env)
    target_name = payload.target
    if not _scope_ref(target_name, env, target_config):
        raise NotFoundError("Target reference was not found in the selected environment.")
    operation = "v1_branch_rebase"
    action_target = (
        f"{auth['subject']}:{env}:{source_name}@{payload.expected_source_hash}"
        f"<-{target_name}@{payload.expected_target_hash}"
    )
    if idempotency_key_target(key, operation) not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        source = await _scoped_reference(source_name, env, target_config, branch_only=True)
        target = await _scoped_reference(target_name, env, target_config)
        if (
            source.hash != payload.expected_source_hash
            or target.hash != payload.expected_target_hash
        ):
            raise HTTPException(status_code=409, detail="Branch head changed; reload and retry.")
        source_history, target_history = await asyncio.gather(
            _history(source.name, source.hash, limit=_HISTORY_LIMIT),
            _history(target.name, target.hash, limit=_HISTORY_LIMIT),
        )
        right_positions = {commit.hash: index for index, commit in enumerate(target_history)}
        common = [
            (source_index + target_index, source_index, target_index)
            for source_index, commit in enumerate(source_history)
            if (target_index := right_positions.get(commit.hash)) is not None
        ]
        if not common:
            raise HTTPException(status_code=409, detail="Branches have no known common ancestor.")
        _, source_ahead, target_behind = min(common)
        if not source_ahead:
            await _nessie(
                "PUT",
                f"/api/v1/trees/branch/{quote(source.name, safe='')}",
                params={"expectedHash": source.hash},
                body={"name": source.name, "type": "BRANCH", "hash": target.hash},
            )
            rebased = await _scoped_reference(source.name, env, target_config, branch_only=True)
            return {
                "env": env,
                "operation": operation,
                "branch": source.name,
                "target": target.name,
                "status": "succeeded",
                "source_hash": source.hash,
                "target_hash": target.hash,
                "resulting_hash": rebased.hash,
                "details": {"mode": "fast_forward", "merge_base": source.hash},
            }
        commits_to_replay = [item.hash for item in reversed(source_history[:source_ahead])]
        temporary_name = f"{env}-rebase-{uuid4().hex}"
        await _nessie(
            "POST",
            "/api/v1/trees/tree",
            body={"name": temporary_name, "type": "BRANCH", "hash": target.hash},
        )
        transplant_body = {
            "fromRefName": source.name,
            "hashesToTransplant": commits_to_replay,
            "isDryRun": False,
            "isReturnConflictAsResult": True,
            "isFetchAdditionalInfo": True,
        }
        transplant_path = (
            f"/api/v2/trees/{quote(temporary_name, safe='')}@{quote(target.hash, safe='')}"
            "/history/transplant"
        )
        try:
            # Nessie 0.108 dry runs misreport create-then-update transplants. Replay on
            # the isolated ref instead; neither user branch changes until source CAS.
            applied = _object(
                await _nessie("POST", transplant_path, body=transplant_body),
                "Nessie returned an invalid rebase result.",
            )
            result_hash = applied.get("resultantTargetHash")
            if applied.get("wasSuccessful") is not True or applied.get("wasApplied") is not True:
                return {
                    "env": env,
                    "operation": operation,
                    "branch": source.name,
                    "target": target.name,
                    "status": "conflict",
                    "source_hash": source.hash,
                    "target_hash": target.hash,
                    "resulting_hash": None,
                    "details": {"mode": "transplant", "result": applied},
                }
            if not isinstance(result_hash, str) or not result_hash:
                raise BadGatewayError("Nessie rebase response omitted its resulting hash.")
            await _nessie(
                "PUT",
                f"/api/v1/trees/branch/{quote(source.name, safe='')}",
                params={"expectedHash": source.hash},
                body={"name": source.name, "type": "BRANCH", "hash": result_hash},
            )
            rebased = await _scoped_reference(source.name, env, target_config, branch_only=True)
            if rebased.hash != result_hash:
                raise BackendUnavailableError("Nessie did not confirm the rebased branch head.")
            return {
                "env": env,
                "operation": operation,
                "branch": source.name,
                "target": target.name,
                "status": "succeeded",
                "source_hash": source.hash,
                "target_hash": target.hash,
                "resulting_hash": rebased.hash,
                "details": {
                    "mode": "transplant",
                    "merge_base": source_history[source_ahead].hash,
                    "transplanted_commits": commits_to_replay,
                    "result": applied,
                    "temporary_ref_removed": True,
                },
            }
        finally:
            temporary = await _reference(temporary_name)
            if temporary is not None:
                await _nessie(
                    "DELETE",
                    f"/api/v1/trees/branch/{quote(temporary_name, safe='')}",
                    params={"expectedHash": temporary["hash"]},
                )

    outcome = await replay_or_execute_async(
        idempotency_key=key,
        operation=operation,
        target=action_target,
        exclusion_target=f"{env}:{source_name}",
        execute=execute,
        audit=_audit_callback(
            _action_actor(request, auth),
            operation=operation,
            target=action_target,
            env=env,
            source=source_name,
            target_ref=target_name,
            payload=payload.model_dump(mode="json"),
        ),
    )
    return BranchActionResult.model_validate(outcome)


@router.post("/branches/{branch_name}/checks", response_model=BranchCheckPage)
async def v1_branch_checks(
    request: Request,
    branch_name: str,
    payload: BranchChecksRequest,
    env: Environment = Query(),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BranchCheckPage:
    auth = require_scope(request, "lakehouse:operate")
    _require_branch_actions()
    enforce_rate_limit(auth["subject"], "v1_branch_checks")
    key = _require_action_key(idempotency_key)
    target = _target(request, env)
    operation = "v1_branch_checks"
    action_target = f"{auth['subject']}:{env}:{branch_name}@{payload.expected_hash}"
    if idempotency_key_target(key, operation) not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        branch = await _scoped_reference(branch_name, env, target, branch_only=True)
        if branch.hash != payload.expected_hash:
            raise HTTPException(status_code=409, detail="Branch head changed; reload and retry.")
        checks = await _premerge_checks(env=env, target=target, branch=branch)
        result = _check_result(checks)
        return {
            "env": env,
            "branch": branch.name,
            "branch_hash": branch.hash,
            **result,
        }

    outcome = await replay_or_execute_async(
        idempotency_key=key,
        operation=operation,
        target=action_target,
        exclusion_target=f"{env}:{branch_name}",
        execute=execute,
        audit=_audit_callback(
            _action_actor(request, auth),
            operation=operation,
            target=action_target,
            env=env,
            source=branch_name,
            target_ref=None,
            payload=payload.model_dump(mode="json"),
        ),
    )
    return BranchCheckPage.model_validate(outcome)


@router.post("/branches/{branch_name}/trial-merge", response_model=BranchActionResult)
async def v1_branch_trial_merge(
    request: Request,
    branch_name: str,
    payload: BranchTrialMerge,
    env: Environment = Query(),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BranchActionResult:
    auth = require_scope(request, "lakehouse:operate")
    _require_branch_actions()
    enforce_rate_limit(auth["subject"], "v1_branch_trial_merge")
    key = _require_action_key(idempotency_key)
    target_config = _target(request, env)
    operation = "v1_branch_trial_merge"
    action_target = (
        f"{auth['subject']}:{env}:{branch_name}@{payload.expected_source_hash}"
        f"->{payload.target}@{payload.expected_target_hash}"
    )
    if idempotency_key_target(key, operation) not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        source = await _scoped_reference(branch_name, env, target_config, branch_only=True)
        target = await _scoped_reference(payload.target, env, target_config, branch_only=True)
        if (
            source.hash != payload.expected_source_hash
            or target.hash != payload.expected_target_hash
        ):
            raise HTTPException(status_code=409, detail="Branch head changed; reload and retry.")
        checks = await _premerge_checks(env=env, target=target_config, branch=source)
        if len(checks) != 3 or any(item.status != "passed" for item in checks):
            return {
                "env": env,
                "operation": operation,
                "branch": source.name,
                "target": target.name,
                "status": "rejected",
                "source_hash": source.hash,
                "target_hash": target.hash,
                "resulting_hash": None,
                "details": {"checks": [item.model_dump(mode="json") for item in checks]},
            }
        trial, prepared, decisions = await _trial_merge_with_resolutions(
            incident_id=payload.incident_id,
            env=env,
            source=source,
            target=target,
            message=payload.message,
        )
        try:
            conflicts = _merge_conflict_keys(trial)
            success = trial.get("wasSuccessful") is True and not conflicts
            return {
                "env": env,
                "operation": operation,
                "branch": source.name,
                "target": target.name,
                "status": "succeeded" if success else "conflict",
                "source_hash": source.hash,
                "target_hash": target.hash,
                "resulting_hash": None,
                "details": {
                    "checks": [item.model_dump(mode="json") for item in checks],
                    "trial_merge": trial,
                    "conflicts": conflicts,
                    "schema_decision_ids": [item.id for item in decisions],
                    "signature_target_version": _decision_signature_version(
                        source, target, decisions
                    ),
                },
            }
        finally:
            if prepared is not None:
                await prepared.cleanup(_nessie)

    outcome = await replay_or_execute_async(
        idempotency_key=key,
        operation=operation,
        target=action_target,
        exclusion_target=f"{env}:{payload.target}",
        execute=execute,
        audit=_audit_callback(
            _action_actor(request, auth),
            operation=operation,
            target=action_target,
            env=env,
            source=branch_name,
            target_ref=payload.target,
            payload=payload.model_dump(mode="json"),
        ),
    )
    if outcome.get("status") == "rejected":
        outcome["status"] = "conflict"
    return BranchActionResult.model_validate(outcome)


@router.post("/branches/{branch_name}/merge", response_model=BranchActionResult)
async def v1_branch_merge(
    request: Request,
    branch_name: str,
    payload: BranchMerge,
    env: Environment = Query(),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BranchActionResult:
    auth = require_scope(request, "lakehouse:operate")
    _require_branch_actions()
    enforce_rate_limit(auth["subject"], "v1_branch_merge")
    key = _require_action_key(idempotency_key)
    target_config = _target(request, env)
    operation = "v1_branch_merge"
    request_hash = hashlib.sha256(
        json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    action_target = (
        f"{auth['subject']}:{env}:{branch_name}@{payload.expected_source_hash}"
        f"->{payload.target}@{payload.expected_target_hash}:{request_hash}"
    )
    if idempotency_key_target(key, operation) not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})
    actor = _action_actor(request, auth)

    async def execute() -> dict[str, Any]:
        settings = await to_thread.run_sync(load_operational_settings)
        source = await _scoped_reference(branch_name, env, target_config, branch_only=True)
        target = await _scoped_reference(payload.target, env, target_config, branch_only=True)
        if settings.require_merge_reason and target.name == "main" and not payload.message.strip():
            raise HTTPException(422, "A non-blank reason is required for merges into main.")
        if (
            source.hash != payload.expected_source_hash
            or target.hash != payload.expected_target_hash
        ):
            raise HTTPException(status_code=409, detail="Branch head changed; reload and retry.")
        checks = await _premerge_checks(env=env, target=target_config, branch=source)
        if len(checks) != 3 or any(item.status != "passed" for item in checks):
            return {
                "env": env,
                "operation": operation,
                "branch": source.name,
                "target": target.name,
                "status": "conflict",
                "source_hash": source.hash,
                "target_hash": target.hash,
                "resulting_hash": None,
                "details": {"checks": [item.model_dump(mode="json") for item in checks]},
            }
        trial, prepared, decisions = await _trial_merge_with_resolutions(
            incident_id=payload.incident_id,
            env=env,
            source=source,
            target=target,
            message=payload.message,
        )
        try:
            conflicts = _merge_conflict_keys(trial)
            if trial.get("wasSuccessful") is not True or conflicts:
                return {
                    "env": env,
                    "operation": operation,
                    "branch": source.name,
                    "target": target.name,
                    "status": "conflict",
                    "source_hash": source.hash,
                    "target_hash": target.hash,
                    "resulting_hash": None,
                    "details": {
                        "checks": [item.model_dump(mode="json") for item in checks],
                        "trial_merge": trial,
                        "conflicts": conflicts,
                    },
                }
            current_source, current_target = await asyncio.gather(
                _scoped_reference(source.name, env, target_config, branch_only=True),
                _scoped_reference(target.name, env, target_config, branch_only=True),
            )
            if current_source.hash != source.hash or current_target.hash != target.hash:
                raise HTTPException(
                    status_code=409, detail="Branch head changed during pre-merge checks."
                )
            expected = SignatureRequest(
                signer_subject=actor.subject,
                meaning=SignatureMeaning.APPROVED,
                action="branch.merge",
                record_type="branch",
                record_id=f"{env}:{source.name}->{target.name}",
                record_version=_decision_signature_version(source, target, decisions),
                justification=payload.message,
            )
            from phlo_api.api import v1_admin_identity

            independent_review = (
                settings.second_gold_reviewer
                and await _requires_independent_review(request, source, target)
            )
            async with _MERGE_LOCK:
                current_settings = await to_thread.run_sync(load_operational_settings)
                if current_settings.settings_revision != settings.settings_revision:
                    raise HTTPException(409, "Governance settings changed; reload and retry.")
                if independent_review:
                    await to_thread.run_sync(_consume_independent_review, payload, expected)
                else:
                    v1_admin_identity._consume_signature(
                        v1_admin_identity._authority(), payload.signature_id, expected
                    )
                applied = await _merge_payload(
                    source=source,
                    target=target,
                    message=payload.message,
                    dry_run=False,
                    key_merge_modes=prepared.key_merge_modes if prepared is not None else (),
                )
            if applied.get("wasApplied") is not True or applied.get("wasSuccessful") is not True:
                raise BackendUnavailableError("Nessie did not confirm the signed branch merge.")
            resulting = await _scoped_reference(target.name, env, target_config, branch_only=True)
            resultant_hash = applied.get("resultantTargetHash")
            if isinstance(resultant_hash, str) and resultant_hash != resulting.hash:
                raise BackendUnavailableError("Nessie returned an unexpected resulting ref hash.")
            if prepared is not None:
                await verify_schema_resolutions(
                    prepared=prepared,
                    target_ref=target.name,
                    resulting_hash=resulting.hash,
                    nessie_request=_nessie,
                )
            v1_admin_identity._action_audit(
                actor=actor,
                action="branch.merge",
                target_type="branch",
                target_id=f"{env}:{source.name}->{target.name}",
                signature_id=payload.signature_id,
            )
            return {
                "env": env,
                "operation": operation,
                "branch": source.name,
                "target": target.name,
                "status": "succeeded",
                "source_hash": source.hash,
                "target_hash": target.hash,
                "resulting_hash": resulting.hash,
                "details": {
                    "settings_revision": settings.settings_revision,
                    "review_signature_id": payload.review_signature_id,
                    "signature_id": payload.signature_id,
                    "checks": [item.model_dump(mode="json") for item in checks],
                    "trial_merge": trial,
                    "merge_result": applied,
                    "schema_decision_ids": [item.id for item in decisions],
                    "signature_target_version": _decision_signature_version(
                        source, target, decisions
                    ),
                },
            }
        finally:
            if prepared is not None:
                await prepared.cleanup(_nessie)

    outcome = await replay_or_execute_async(
        idempotency_key=key,
        operation=operation,
        target=action_target,
        exclusion_target=f"{env}:{payload.target}",
        execute=execute,
        audit=_audit_callback(
            actor,
            operation=operation,
            target=action_target,
            env=env,
            source=branch_name,
            target_ref=payload.target,
            payload={
                "source_hash": payload.expected_source_hash,
                "target_hash": payload.expected_target_hash,
                "signature_id": payload.signature_id,
                "message": payload.message,
            },
        ),
    )
    return BranchActionResult.model_validate(outcome)
