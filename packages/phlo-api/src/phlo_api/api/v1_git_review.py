"""Optional, project-scoped GitHub draft-PR publisher for check proposals."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from urllib.parse import quote
from typing import Literal, cast

import httpx
from pydantic import Field

from phlo_api.api.v1_audit_proposals import AssetAuditProposal
from phlo_api.v1_contract import WireModel


class AssetAuditPublishRequest(WireModel):
    """Request to publish an existing source-only proposal for human review."""

    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")


class AssetAuditDraftPullRequest(WireModel):
    """A draft pull request; source remains inactive until reviewed and merged."""

    proposal_id: str
    status: Literal["pending_review"] = "pending_review"
    repository: str
    base_branch: str
    head_branch: str
    pull_request_number: int = Field(ge=1)
    pull_request_url: str


class GitReviewUnavailable(RuntimeError):
    """Project repository publishing is not configured or GitHub is unavailable."""


class GitReviewConflict(RuntimeError):
    """A deterministic proposal branch exists without a verifiable matching PR."""


class GitReviewConfig(WireModel):
    """Fixed repository target and credential for one project API deployment."""

    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_branch: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._/-]+$")
    token: str = Field(min_length=1)


def project_git_review_config() -> GitReviewConfig:
    """Resolve only a fixed deployment target; callers cannot select a repository."""
    try:
        repository = os.environ["PHLO_V1_GIT_REVIEW_REPOSITORY"]
        base_branch = os.environ["PHLO_V1_GIT_REVIEW_BASE_BRANCH"]
        token = os.environ["PHLO_V1_GIT_REVIEW_TOKEN"]
        return GitReviewConfig(
            repository=repository,
            base_branch=base_branch,
            token=token,
        )
    except (KeyError, ValueError) as exc:
        raise GitReviewUnavailable("Project Git review is not configured.") from exc


def _source_path(proposal: AssetAuditProposal) -> str:
    path = proposal.file_path
    if (
        not path.startswith("workflows/quality/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise GitReviewUnavailable("The proposal path is outside the quality-check directory.")
    return path


async def _json_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json_body: dict[str, object] | None = None,
    allow_not_found: bool = False,
) -> dict[str, object] | list[object] | None:
    response = await client.request(method, path, json=json_body)
    if allow_not_found and response.status_code == 404:
        return None
    if response.is_error:
        raise GitReviewUnavailable("GitHub draft pull request publishing failed.")
    try:
        value = response.json()
    except ValueError as exc:
        raise GitReviewUnavailable("GitHub returned an invalid response.") from exc
    if isinstance(value, dict):
        mapped = _string_keyed_object(value)
        if mapped is None:
            raise GitReviewUnavailable("GitHub returned an invalid response.")
        return mapped
    if isinstance(value, list):
        return value
    else:
        raise GitReviewUnavailable("GitHub returned an invalid response.")


async def publish_project_draft_pr(
    client: httpx.AsyncClient,
    config: GitReviewConfig,
    proposal: AssetAuditProposal,
) -> AssetAuditDraftPullRequest:
    """Create or replay one deterministic review PR in the configured project repo."""
    path = _source_path(proposal)
    if (
        not re.fullmatch(r"[a-f0-9]{32}", proposal.proposal_id)
        or not re.fullmatch(r"[a-f0-9]{64}", proposal.source_digest)
        or hashlib.sha256(proposal.source.encode("utf-8")).hexdigest() != proposal.source_digest
    ):
        raise GitReviewUnavailable("The stored proposal failed its source integrity check.")
    owner, repo = config.repository.split("/", maxsplit=1)
    branch = f"phlo/asset-check-{proposal.proposal_id}"
    repository_path = f"/repos/{quote(owner)}/{quote(repo)}"
    head_query = quote(f"{owner}:{branch}", safe="")
    existing = await _json_request(
        client,
        "GET",
        f"{repository_path}/pulls?head={head_query}&base={quote(config.base_branch, safe='')}&state=open",
    )
    if isinstance(existing, list) and existing:
        pull = existing[0]
        mapped_pull = _string_keyed_object(pull)
        if mapped_pull is None:
            raise GitReviewUnavailable("GitHub returned an invalid pull request.")
        return _pull_request_result(mapped_pull, proposal, config, branch)
    if existing is not None and not isinstance(existing, list):
        raise GitReviewUnavailable("GitHub returned an invalid pull request listing.")

    branch_ref = await _json_request(
        client,
        "GET",
        f"{repository_path}/git/ref/heads/{quote(branch, safe='')}",
        allow_not_found=True,
    )
    if branch_ref is not None:
        raise GitReviewConflict("The proposal branch already exists without a matching open PR.")

    base_ref = await _json_request(
        client,
        "GET",
        f"{repository_path}/git/ref/heads/{quote(config.base_branch, safe='')}",
    )
    base_sha = _nested_string(base_ref, "object", "sha")
    base_commit = await _json_request(
        client,
        "GET",
        f"{repository_path}/git/commits/{quote(base_sha, safe='')}",
    )
    base_tree = _nested_string(base_commit, "tree", "sha")

    existing_file = await _json_request(
        client,
        "GET",
        f"{repository_path}/contents/{quote(path, safe='/')}?ref={quote(config.base_branch, safe='')}",
        allow_not_found=True,
    )
    if existing_file is not None:
        raise GitReviewConflict(
            "The generated check path already exists in the project repository."
        )

    blob = await _json_request(
        client,
        "POST",
        f"{repository_path}/git/blobs",
        json_body={
            "content": base64.b64encode(proposal.source.encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        },
    )
    blob_sha = _nested_string(blob, "sha")
    tree = await _json_request(
        client,
        "POST",
        f"{repository_path}/git/trees",
        json_body={
            "base_tree": base_tree,
            "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}],
        },
    )
    tree_sha = _nested_string(tree, "sha")
    commit = await _json_request(
        client,
        "POST",
        f"{repository_path}/git/commits",
        json_body={
            "message": f"Add proposed asset check: {proposal.check_name}",
            "tree": tree_sha,
            "parents": [base_sha],
        },
    )
    commit_sha = _nested_string(commit, "sha")
    await _json_request(
        client,
        "POST",
        f"{repository_path}/git/refs",
        json_body={"ref": f"refs/heads/{branch}", "sha": commit_sha},
    )
    pull = await _json_request(
        client,
        "POST",
        f"{repository_path}/pulls",
        json_body={
            "title": f"Review proposed asset check: {proposal.check_name}",
            "head": branch,
            "base": config.base_branch,
            "draft": True,
            "body": (
                "Human review required. This generated check is not active until "
                "the source change is reviewed, merged, and loaded by the Dagster code location.\n\n"
                f"Proposal: `{proposal.proposal_id}`\n"
                f"Asset: `{proposal.asset_id}` ({proposal.env}, Nessie ref `{proposal.nessie_ref}`)\n"
                f"Source SHA-256: `{proposal.source_digest}`"
            ),
        },
    )
    mapped_pull = _string_keyed_object(pull)
    if mapped_pull is None:
        raise GitReviewUnavailable("GitHub returned an invalid pull request.")
    return _pull_request_result(mapped_pull, proposal, config, branch)


def _nested_string(value: object, *keys: str) -> str:
    current = value
    for key in keys:
        mapped = _string_keyed_object(current)
        if mapped is None:
            raise GitReviewUnavailable("GitHub returned an invalid repository object.")
        current = mapped.get(key)
    if not isinstance(current, str) or not current:
        raise GitReviewUnavailable("GitHub returned an invalid repository object.")
    return current


def _pull_request_result(
    pull: dict[str, object],
    proposal: AssetAuditProposal,
    config: GitReviewConfig,
    branch: str,
) -> AssetAuditDraftPullRequest:
    number = pull.get("number")
    url = pull.get("html_url")
    base = pull.get("base")
    head = pull.get("head")
    body = pull.get("body")
    mapped_base = _string_keyed_object(base)
    mapped_head = _string_keyed_object(head)
    if mapped_base is None:
        raise GitReviewUnavailable("GitHub returned an invalid pull request.")
    if mapped_head is None:
        raise GitReviewUnavailable("GitHub returned an invalid pull request.")
    head_repo = _string_keyed_object(mapped_head.get("repo"))
    if head_repo is None:
        raise GitReviewUnavailable("GitHub returned an invalid pull request.")
    if (
        not isinstance(number, int)
        or not isinstance(url, str)
        or not url.startswith("https://github.com/")
        or mapped_base.get("ref") != config.base_branch
        or pull.get("draft") is not True
        or not isinstance(body, str)
        or f"Source SHA-256: `{proposal.source_digest}`" not in body
        or mapped_head.get("ref") != branch
        or head_repo.get("full_name") != config.repository
    ):
        raise GitReviewUnavailable("GitHub returned an invalid pull request.")
    return AssetAuditDraftPullRequest(
        proposal_id=proposal.proposal_id,
        repository=config.repository,
        base_branch=config.base_branch,
        head_branch=branch,
        pull_request_number=number,
        pull_request_url=url,
    )


def _string_keyed_object(value: object) -> dict[str, object] | None:
    """Narrow untrusted decoded JSON objects after checking their key types."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast(dict[str, object], value)


def project_git_review_client(config: GitReviewConfig) -> httpx.AsyncClient:
    """Create a bounded GitHub client without logging or exposing its token."""
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {config.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=httpx.Timeout(15),
        follow_redirects=False,
    )
