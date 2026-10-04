"""Opt-in staging inspection and promotion backed by Git, Nessie, and Dagster."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from anyio import to_thread
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import Field

from phlo.compliance.signatures.types import SignatureMeaning, SignatureRequest
from phlo_api.api import v1, v1_jobs
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import (
    audit_operation,
    idempotency_key_target,
    load_operational_settings,
    read_operation_audit,
    replay_or_execute_async,
    require_scope,
)
from phlo_api.api.v1_branch_workflows import BranchReference, _nessie, _reference, _run_check_job
from phlo_api.errors import BackendUnavailableError, BadGatewayError
from phlo_api.observatory_api.dagster import graphql_request, resolve_dagster_url
from phlo_api.v1_contract import WireModel

router = APIRouter(tags=["v1 staging"])

_DENIED_PARTS = {
    ".git",
    ".phlo",
    "secrets",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
}
_PROMOTION_LOCK = asyncio.Lock()
StagingEnvironment = Annotated[Literal["staging"], Query()]


class PromotionCheckReadiness(WireModel):
    name: Literal["tests", "contracts", "audits"]
    job_name: str | None
    status: Literal[
        "ready",
        "unconfigured",
        "missing_job",
        "missing_evidence",
        "stale_evidence",
        "failed",
        "running",
        "unavailable",
    ]
    run_id: str | None = None
    message: str


class PromotionCandidate(WireModel):
    env: Literal["staging"]
    candidate_id: str
    prod_git_revision: str
    staging_git_revision: str
    prod_ref: str
    prod_hash: str
    staging_ref: str
    staging_hash: str
    dagster_location: str
    staging_location: str
    code_paths: list[str]
    code_changes: list[str]
    jobs: dict[str, list[str]]
    check_readiness: list[PromotionCheckReadiness]
    check_configuration_ready: bool
    copy_inventory: dict[str, list[str]]
    observed_at: str


class PromotionCheck(WireModel):
    name: str
    status: Literal["passed", "failed", "unavailable"]
    run_id: str | None
    message: str | None = None


class PromotionChecks(WireModel):
    env: Literal["staging"]
    candidate_id: str
    items: list[PromotionCheck]
    passed: bool


class ChecksRequest(WireModel):
    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class PromotionRequest(WireModel):
    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_id: str = Field(min_length=1, max_length=100)
    justification: str = Field(min_length=1, max_length=1000)
    confirm: Literal[True]


class ResyncRequest(WireModel):
    expected_prod_hash: str = Field(min_length=1, max_length=256)
    expected_staging_hash: str = Field(min_length=1, max_length=256)
    confirm: Literal[True]


def _configured() -> tuple[Path, Path, str, str, str]:
    if (
        os.environ.get("PHLO_STAGING_SINGLE_REPLICA") != "true"
        or os.environ.get("WEB_CONCURRENCY", "1") != "1"
    ):
        raise HTTPException(
            503, "Staging promotion requires an asserted single API replica/process."
        )
    values = [
        os.environ.get(name, "")
        for name in (
            "PHLO_PROMOTION_PROD_WORKTREE",
            "PHLO_PROMOTION_STAGING_WORKTREE",
            "PHLO_PROMOTION_PROD_REF",
            "PHLO_PROMOTION_STAGING_REF",
            "PHLO_PROMOTION_DAGSTER_LOCATION",
        )
    ]
    if not all(values):
        raise HTTPException(503, "Staging promotion is not configured.")
    targets = v1._targets()
    if (
        values[2] != targets["prod"].nessie_ref
        or values[3] != targets["staging"].nessie_ref
        or values[4] != targets["prod"].dagster_location
    ):
        raise HTTPException(503, "Promotion targets must match the environment allowlist.")
    prod, staging = Path(values[0]).resolve(), Path(values[1]).resolve()
    if prod == staging or not prod.is_dir() or not staging.is_dir():
        raise HTTPException(503, "Configured promotion worktrees are invalid.")
    return prod, staging, values[2], values[3], values[4]


async def _git(path: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise BackendUnavailableError(
            "Git promotion did not finish within its time limit."
        ) from exc
    if process.returncode:
        raise HTTPException(409, "Git promotion precondition failed.")
    if len(stdout) > 1024 * 1024:
        raise BackendUnavailableError("Git promotion inventory exceeds its supported bound.")
    return stdout.decode()


async def _copy_inventory(ref: str, head: str) -> list[str]:
    payload = await _nessie(
        "GET",
        f"/api/v2/trees/{quote(ref, safe='')}@{head}/entries",
        params={"maxRecords": 1001},
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise BackendUnavailableError("Nessie copy inventory is unavailable.")
    if payload.get("hasMore") or len(payload["entries"]) > 1000:
        raise BackendUnavailableError("Nessie copy inventory exceeds its supported bound.")
    tables = []
    for item in payload["entries"]:
        if not isinstance(item, dict):
            raise BadGatewayError("Nessie returned an invalid inventory entry.")
        if item.get("type") == "ICEBERG_TABLE":
            name = item.get("name")
            elements = name.get("elements") if isinstance(name, dict) else None
            if (
                not isinstance(elements, list)
                or not elements
                or any(not isinstance(part, str) for part in elements)
            ):
                raise BadGatewayError("Nessie returned an invalid table name.")
            tables.append(".".join(elements))
    return sorted(tables)


async def _release_signing_config(prod: Path) -> None:
    try:
        key = await _git(prod, "config", "--get", "user.signingkey")
    except HTTPException as exc:
        raise HTTPException(
            503,
            "Signed release evidence requires Git user.signingkey and an available signing key.",
        ) from exc
    if not key.strip():
        raise HTTPException(503, "Configure Git user.signingkey before promoting a signed release.")


async def _signed_release_tag(prod: Path, state: dict[str, Any], signature_id: str) -> str:
    tag = f"phlo-approved-release-{state['candidate_id']}-{signature_id}"
    manifest = json.dumps(
        {
            "candidate_id": state["candidate_id"],
            "code_version": state["staging_git_revision"],
            "prod_hash": state["prod_hash"],
            "staging_hash": state["staging_hash"],
            "signature_id": signature_id,
        },
        sort_keys=True,
    )
    try:
        await _git(prod, "tag", "-s", "-m", manifest, tag, state["staging_git_revision"])
        await _git(prod, "verify-tag", tag)
        target = await _git(prod, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    except HTTPException as exc:
        raise HTTPException(
            503,
            "Signed release evidence could not be created and verified. Check Git signing identity, key availability and verification trust, then obtain a new MFA approval before retrying; no code was advanced.",
        ) from exc
    if target.strip() != state["staging_git_revision"]:
        raise HTTPException(409, "Signed release tag does not identify the approved code revision.")
    return tag


async def _state() -> dict[str, Any]:
    prod, staging, prod_ref, staging_ref, location = _configured()
    (
        prod_common,
        staging_common,
        prod_head,
        staging_head,
        prod_status,
        staging_status,
    ) = await asyncio.gather(
        _git(prod, "rev-parse", "--path-format=absolute", "--git-common-dir"),
        _git(staging, "rev-parse", "--path-format=absolute", "--git-common-dir"),
        _git(prod, "rev-parse", "HEAD"),
        _git(staging, "rev-parse", "HEAD"),
        _git(prod, "status", "--porcelain"),
        _git(staging, "status", "--porcelain"),
    )
    prod_head, staging_head = prod_head.strip(), staging_head.strip()
    if Path(prod_common.strip()).resolve() != Path(staging_common.strip()).resolve():
        raise HTTPException(503, "Promotion worktrees do not share a Git object store.")
    if prod_status or staging_status:
        raise HTTPException(409, "Promotion worktrees must be clean.")
    await _git(prod, "merge-base", "--is-ancestor", prod_head, staging_head)
    changed = (
        await _git(prod, "diff", "--name-status", "--no-renames", "-z", prod_head, staging_head)
    ).split("\0")[:-1]
    if len(changed) > 2000 or len(changed) % 2:
        raise BackendUnavailableError("Promotion path inventory exceeds its supported bound.")
    paths = changed[1::2]
    for value in paths:
        if any(character in value for character in "\r\n\t") or any(
            part in _DENIED_PARTS or part.startswith(".env") or part in {"..", "."}
            for part in Path(value).parts
        ):
            raise HTTPException(422, f"Promotion contains a prohibited path: {value}")
        for root in (prod, staging):
            path = root / value
            if not path.resolve(strict=False).is_relative_to(root) or any(
                parent.is_symlink() for parent in [path, *path.parents] if parent != root
            ):
                raise HTTPException(422, "Promotion contains a symlink or path escape.")
        modes = await _git(staging, "ls-tree", staging_head, "--", value)
        if modes.startswith("120000 ") or modes.startswith("160000 "):
            raise HTTPException(422, f"Promotion contains a symlink: {value}")
    prod_nessie, staging_nessie = await asyncio.gather(
        _reference(prod_ref), _reference(staging_ref)
    )
    if not prod_nessie or not staging_nessie:
        raise HTTPException(503, "Configured Nessie references are unavailable.")
    targets = v1._targets()
    repositories = v1_jobs._repositories(
        await v1_jobs._graphql(v1_jobs.JOBS_QUERY), targets["staging"].dagster_location
    )
    prod_repositories = v1_jobs._repositories(
        await v1_jobs._graphql(v1_jobs.JOBS_QUERY), targets["prod"].dagster_location
    )
    prod_tables, staging_tables = await asyncio.gather(
        _copy_inventory(prod_ref, prod_nessie["hash"]),
        _copy_inventory(staging_ref, staging_nessie["hash"]),
    )
    state = {
        "env": "staging",
        "prod_git_revision": prod_head,
        "staging_git_revision": staging_head,
        "prod_ref": prod_ref,
        "prod_hash": prod_nessie["hash"],
        "staging_ref": staging_ref,
        "staging_hash": staging_nessie["hash"],
        "dagster_location": location,
        "staging_location": targets["staging"].dagster_location,
        "code_paths": paths,
        "code_changes": [
            f"{status}\t{path}" for status, path in zip(changed[::2], paths, strict=True)
        ],
        "jobs": {
            "prod": sorted(item.id for item in v1_jobs._jobs(prod_repositories, "prod", {})),
            "staging": sorted(item.id for item in v1_jobs._jobs(repositories, "staging", {})),
        },
        "copy_inventory": {"prod": prod_tables, "staging": staging_tables},
    }
    state["candidate_id"] = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    state["observed_at"] = datetime.now(UTC).isoformat()
    return state


@router.get("/staging/promotions", name="v1_staging_promotions")
async def promotions(request: Request, env: StagingEnvironment) -> dict[str, Any]:
    v1._target(request, env)
    require_scope(request, "lakehouse:read")
    return {
        "env": env,
        "items": read_operation_audit("staging.promote")[-100:],
    }


@router.get(
    "/staging/promotions/candidate", name="v1_staging_candidate", response_model=PromotionCandidate
)
async def candidate(request: Request, env: StagingEnvironment) -> dict[str, Any]:
    v1._target(request, env)
    require_scope(request, "lakehouse:read")
    state = await _state()
    state["check_readiness"] = await _check_readiness(state)
    state["check_configuration_ready"] = all(
        item["status"] not in {"unconfigured", "missing_job"} for item in state["check_readiness"]
    )
    return state


_CHECK_KINDS: tuple[Literal["tests", "contracts", "audits"], ...] = (
    "tests",
    "contracts",
    "audits",
)


def _configured_check_names() -> list[str | None]:
    names = [
        item.strip() for item in os.environ.get("PHLO_PROMOTION_DAGSTER_CHECK_JOBS", "").split(",")
    ]
    valid = (
        len(names) == len(_CHECK_KINDS)
        and len(set(names)) == len(names)
        and all(re.fullmatch(r"[A-Za-z0-9_]+", name) for name in names)
    )
    if not valid:
        return [None, None, None]
    configured: list[str | None] = []
    configured.extend(names)
    return configured


def _check_names() -> list[str]:
    names = [
        item.strip()
        for item in os.environ.get("PHLO_PROMOTION_DAGSTER_CHECK_JOBS", "").split(",")
        if item.strip()
    ]
    if (
        len(names) != 3
        or len(set(names)) != 3
        or any(not re.fullmatch(r"[A-Za-z0-9_]+", name) for name in names)
    ):
        raise HTTPException(
            503,
            "Set PHLO_PROMOTION_DAGSTER_CHECK_JOBS to three distinct Dagster job names in "
            "tests,contracts,audits order; each job must exist in the staging Dagster location.",
        )
    return names


async def _check_readiness(state: dict[str, Any]) -> list[dict[str, Any]]:
    names = _configured_check_names()
    staging_jobs = set(state["jobs"]["staging"])
    readiness = _initial_check_readiness(names, staging_jobs)
    if not any(name is not None and name in staging_jobs for name in names):
        return readiness

    rows = await _promotion_check_runs()
    if rows is None:
        return [
            {
                **item,
                "status": "unavailable",
                "message": "Dagster run evidence could not be read. Retry after Dagster is available.",
            }
            if item["status"] == "missing_evidence"
            else item
            for item in readiness
        ]

    for index, (kind, name) in enumerate(zip(_CHECK_KINDS, names, strict=True)):
        if name is None or name not in staging_jobs:
            continue
        readiness[index] = _check_run_readiness(kind, name, state, rows)
    return readiness


def _initial_check_readiness(
    names: list[str | None], staging_jobs: set[str]
) -> list[dict[str, Any]]:
    readiness = []
    for kind, name in zip(_CHECK_KINDS, names, strict=True):
        if name is None:
            status, message = (
                "unconfigured",
                "Configure PHLO_PROMOTION_DAGSTER_CHECK_JOBS as tests,contracts,audits.",
            )
        elif name not in staging_jobs:
            status, message = (
                "missing_job",
                f"Dagster job {name} is not present in the staging location.",
            )
        else:
            status, message = (
                "missing_evidence",
                f"No candidate-bound {kind} run has been recorded.",
            )
        readiness.append({"name": kind, "job_name": name, "status": status, "message": message})
    return readiness


async def _promotion_check_runs() -> list[dict[str, Any]] | None:
    query = "query Runs($limit:Int!){runsOrError(limit:$limit){__typename ... on Runs{results{runId status pipelineName tags{key value} repositoryOrigin{repositoryLocationName}}}}}"
    try:
        response = await graphql_request(resolve_dagster_url(), query, {"limit": 100})
        rows = v1._source_data(response, "runsOrError", "Runs").get("results")
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) > 100:
        return None
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("pipelineName"), str)
        or not isinstance(row.get("status"), str)
        or (row.get("runId") is not None and not isinstance(row.get("runId"), str))
        or not isinstance(row.get("tags"), list)
        or any(
            not isinstance(tag, dict)
            or not isinstance(tag.get("key"), str)
            or not isinstance(tag.get("value"), str)
            for tag in row.get("tags", [])
        )
        for row in rows
    ):
        return None
    return rows


def _check_run_readiness(
    kind: Literal["tests", "contracts", "audits"],
    name: str,
    state: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    matching = [row for row in rows if row["pipelineName"] == name]
    if not matching:
        return {
            "name": kind,
            "job_name": name,
            "status": "missing_evidence",
            "message": f"No candidate-bound {kind} run has been recorded.",
        }
    staging_location = [
        row
        for row in matching
        if isinstance(row.get("repositoryOrigin"), dict)
        and row["repositoryOrigin"].get("repositoryLocationName") == state["staging_location"]
    ]
    row = (staging_location or matching)[0]
    tags = {tag["key"]: tag["value"] for tag in row["tags"]}
    expected = {
        "environment": "staging",
        "phlo/ref": state["staging_ref"],
        "phlo/code_version": state["staging_git_revision"],
        "phlo/nessie_hash": state["staging_hash"],
        "phlo/promotion_candidate": state["candidate_id"],
    }
    missing = [key for key, value in expected.items() if tags.get(key) != value]
    location = row.get("repositoryOrigin")
    if (
        not isinstance(location, dict)
        or location.get("repositoryLocationName") != state["staging_location"]
    ):
        missing.append("staging Dagster location")
    run_id = row.get("runId")
    if missing:
        return {
            "name": kind,
            "job_name": name,
            "status": "stale_evidence",
            "run_id": run_id,
            "message": f"Latest {kind} run is not bound to this candidate: {', '.join(missing)}.",
        }
    if not isinstance(run_id, str) or not run_id:
        return {
            "name": kind,
            "job_name": name,
            "status": "unavailable",
            "message": f"Dagster did not return a run identifier for the {kind} evidence.",
        }
    status = row["status"]
    if status == "SUCCESS":
        return {
            "name": kind,
            "job_name": name,
            "status": "ready",
            "run_id": run_id,
            "message": "Candidate-bound check passed.",
        }
    if status in {"STARTED", "QUEUED", "NOT_STARTED", "MANAGED"}:
        return {
            "name": kind,
            "job_name": name,
            "status": "running",
            "run_id": run_id,
            "message": f"Candidate-bound {kind} check is still running.",
        }
    return {
        "name": kind,
        "job_name": name,
        "status": "failed",
        "run_id": run_id,
        "message": f"Candidate-bound {kind} check ended with status {status}.",
    }


async def _checks(state: dict[str, Any]) -> list[dict[str, Any]]:
    _check_names()
    readiness = await _check_readiness(state)
    blocked = [item for item in readiness if item["status"] != "ready"]
    if blocked:
        detail = "; ".join(f"{item['name']}: {item['message']}" for item in blocked)
        raise HTTPException(409, f"Promotion checks are not ready: {detail}")
    return [
        {"name": kind, "status": "passed", "run_id": item["run_id"]}
        for kind, item in zip(_CHECK_KINDS, readiness, strict=True)
    ]


@router.post(
    "/staging/promotions/candidate/checks", name="v1_staging_checks", response_model=PromotionChecks
)
async def checks(
    payload: ChecksRequest,
    request: Request,
    env: StagingEnvironment,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    environment_target = v1._target(request, env)
    auth = require_scope(request, "lakehouse:operate")
    state = await _state()
    if payload.candidate_id != state["candidate_id"]:
        raise HTTPException(409, "Promotion candidate is stale.")
    target = _operation_target(
        auth["subject"], "staging.checks", payload.model_dump(), idempotency_key
    )
    names = _check_names()
    missing_jobs = [name for name in names if name not in state["jobs"]["staging"]]
    if missing_jobs:
        raise HTTPException(
            503,
            "Configured candidate check jobs are missing from the staging Dagster location: "
            + ", ".join(missing_jobs),
        )

    async def execute() -> dict[str, Any]:
        branch = BranchReference(
            env=env,
            name=state["staging_ref"],
            type="BRANCH",
            hash=state["staging_hash"],
            protected=True,
        )
        check_rows = await asyncio.gather(
            *[
                _run_check_job(
                    env=env,
                    target=environment_target,
                    branch=branch,
                    check_name=kind,
                    job_name=name,
                    additional_tags={
                        "phlo/code_version": state["staging_git_revision"],
                        "phlo/nessie_hash": state["staging_hash"],
                        "phlo/promotion_candidate": state["candidate_id"],
                    },
                    timeout_seconds=90,
                )
                for kind, name in zip(("tests", "contracts", "audits"), names, strict=True)
            ]
        )
        if (await _state())["candidate_id"] != payload.candidate_id:
            raise HTTPException(409, "Promotion candidate changed while checks ran.")
        return {
            "env": env,
            "candidate_id": payload.candidate_id,
            "items": [
                {**row.model_dump(), "name": name}
                for row, name in zip(check_rows, names, strict=True)
            ],
            "passed": all(row.status == "passed" for row in check_rows),
        }

    return await replay_or_execute_async(
        idempotency_key=idempotency_key,
        operation="staging.checks",
        target=target,
        execute=execute,
        audit=lambda result: audit_operation(
            operation="staging.checks",
            target=payload.candidate_id,
            dry_run=False,
            auth=auth,
            payload=payload.model_dump(),
            result=result,
        ),
    )


@router.post("/staging/resync", name="v1_staging_resync")
async def resync(
    payload: ResyncRequest,
    request: Request,
    env: StagingEnvironment,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    auth = require_scope(request, "lakehouse:operate")
    v1._target(request, env)
    _, _, prod_ref, staging_ref, _ = _configured()
    target = _operation_target(
        auth["subject"], "staging.resync", payload.model_dump(), idempotency_key
    )

    async def execute() -> dict[str, Any]:
        async with _PROMOTION_LOCK:
            prod, staging = await asyncio.gather(_reference(prod_ref), _reference(staging_ref))
            if (
                not prod
                or not staging
                or prod["hash"] != payload.expected_prod_hash
                or staging["hash"] != payload.expected_staging_hash
            ):
                raise HTTPException(409, "Nessie reference changed; reload and retry.")
            await _nessie(
                "PUT",
                f"/api/v1/trees/branch/{quote(staging_ref, safe='')}",
                params={"expectedHash": staging["hash"]},
                body={"type": "BRANCH", "name": staging_ref, "hash": prod["hash"]},
            )
            observed = await _reference(staging_ref)
            if not observed or observed["hash"] != prod["hash"]:
                raise BackendUnavailableError("Nessie resync outcome is unknown.")
        return {
            "env": env,
            "operation": "staging.resync",
            "status": "succeeded",
            "staging_ref": staging_ref,
            "resulting_hash": prod["hash"],
        }

    return await replay_or_execute_async(
        idempotency_key=idempotency_key,
        operation="staging.resync",
        target=target,
        execute=execute,
        audit=lambda result: audit_operation(
            operation="staging.resync",
            target=staging_ref,
            dry_run=False,
            auth=auth,
            payload=payload.model_dump(),
            result=result,
        ),
    )


@router.post("/staging/promotions", name="v1_staging_promote")
async def promote(
    payload: PromotionRequest,
    request: Request,
    env: StagingEnvironment,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    v1._target(request, env)
    auth = require_scope(request, "project:write")
    principal = get_request_principal(request)
    target = _operation_target(
        auth["subject"], "staging.promote", payload.model_dump(), idempotency_key
    )

    async def execute() -> dict[str, Any]:
        settings = await to_thread.run_sync(load_operational_settings)
        state = await _state()
        if state["candidate_id"] != payload.candidate_id:
            raise HTTPException(409, "Promotion candidate is stale.")
        if not state["code_paths"]:
            raise HTTPException(409, "Production already has this code version.")
        check_rows = await _checks(state)
        expected = SignatureRequest(
            signer_subject=principal.subject if principal else auth["subject"],
            meaning=SignatureMeaning.APPROVED,
            action="staging.promote",
            record_type="promotion",
            record_id=f"{state['prod_ref']}<-{state['staging_ref']}",
            record_version=payload.candidate_id,
            justification=payload.justification,
        )
        from phlo_api.api import v1_admin_identity

        prod, _, _, _, location = _configured()
        async with _PROMOTION_LOCK:
            if (await _state())["candidate_id"] != payload.candidate_id:
                raise HTTPException(409, "Promotion candidate is stale.")
            if (
                await to_thread.run_sync(load_operational_settings)
            ).settings_revision != settings.settings_revision:
                raise HTTPException(409, "Governance settings changed; reload and retry.")
            release_tag = None
            if settings.sign_release_tags:
                await _release_signing_config(prod)
            v1_admin_identity._consume_signature(
                v1_admin_identity._authority(), payload.signature_id, expected
            )
            if settings.sign_release_tags:
                release_tag = await _signed_release_tag(prod, state, payload.signature_id)
            await _git(
                prod,
                "update-ref",
                "HEAD",
                state["staging_git_revision"],
                state["prod_git_revision"],
            )
            try:
                await _git(prod, "read-tree", "--reset", "-u", state["staging_git_revision"])
                response = await graphql_request(
                    resolve_dagster_url(),
                    "mutation Reload($name:String!){reloadRepositoryLocation(repositoryLocationName:$name){__typename ... on WorkspaceLocationEntry{loadStatus locationOrLoadError{__typename ... on RepositoryLocation{name}}}}}",
                    {"name": location},
                )
                outcome = (
                    response.get("data", {}).get("reloadRepositoryLocation", {}).get("__typename")
                )
                entry = response.get("data", {}).get("reloadRepositoryLocation", {})
                if (
                    outcome != "WorkspaceLocationEntry"
                    or entry.get("loadStatus") != "LOADED"
                    or entry.get("locationOrLoadError", {}).get("name") != location
                ):
                    raise RuntimeError("Dagster reload was not acknowledged")
            except Exception as exc:
                raise HTTPException(
                    503, "Code advanced but Dagster reload outcome is unknown."
                ) from exc
        return {
            "env": env,
            "operation": "staging.promote",
            "status": "succeeded",
            "candidate_id": payload.candidate_id,
            "resulting_code_version": state["staging_git_revision"],
            "resulting_ref": state["prod_ref"],
            "resulting_hash": state["prod_hash"],
            "checks": check_rows,
            "release_tag": release_tag,
            "settings_revision": settings.settings_revision,
        }

    return await replay_or_execute_async(
        idempotency_key=idempotency_key,
        operation="staging.promote",
        target=target,
        execute=execute,
        audit=lambda result: audit_operation(
            operation="staging.promote",
            target=payload.candidate_id,
            dry_run=False,
            auth=auth,
            payload={"candidate_id": payload.candidate_id, "signature_id": payload.signature_id},
            result=result,
        ),
    )


def _operation_target(actor: str, operation: str, payload: dict[str, Any], key: str | None) -> str:
    if not key or not key.strip() or len(key) > 200:
        raise HTTPException(422, "A non-blank Idempotency-Key is required.")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    target = f"{actor}:staging:{digest}"
    if idempotency_key_target(key, operation) not in {None, target}:
        raise HTTPException(409, "Idempotency key was already used for another intent.")
    return target
