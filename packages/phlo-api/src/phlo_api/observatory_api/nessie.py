"""Nessie API Router.

Endpoints for interacting with the Nessie REST API.
Enables git-like data versioning features in Observatory.

This module provides a proxy to the Project Nessie REST API, enabling
branching, tagging, merging, and history tracking for Iceberg tables.
It abstracts the Nessie API complexity and provides Observatory-compatible
response formats.

Key Endpoints:
    GET /connection: Check Nessie connectivity.
    GET /branches: List all branches and tags.
    GET /branches/{name}: Get specific branch info.
    GET /branches/{name}/history: Get commit history.
    GET /branches/{name}/entries: List branch contents.
    GET /diff/{from}/{to}: Compare two branches.
    POST /branches: Create a new branch.
    DELETE /branches/{name}: Delete a branch.
    POST /merge: Merge branches.

Environment Variables:
    NESSIE_URL: URL for the Nessie server.

Example:
    Listing branches:

    .. code-block:: bash

        curl http://localhost:4000/api/nessie/branches

"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from phlo.config.env import project_env_value
from phlo.config.network import resolve_url
from phlo.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["nessie"])

DEFAULT_NESSIE_URL = "http://nessie:19120/api/v2"


def resolve_nessie_url(override: str | None = None) -> str:
    """Resolve the Nessie API URL."""
    env_url = project_env_value("NESSIE_URL")
    if override and override.strip():
        if env_url and override.strip() == "http://localhost:19120/api/v2":
            return resolve_url(env_url, port_env_var="NESSIE_PORT")
        return resolve_url(override, port_env_var="NESSIE_PORT")
    return resolve_url(env_url or DEFAULT_NESSIE_URL, port_env_var="NESSIE_PORT")


# --- Pydantic Models ---


class Branch(BaseModel):
    """Nessie reference metadata."""

    type: Literal["BRANCH", "TAG"]
    name: str
    hash: str


class CommitMeta(BaseModel):
    """Commit metadata returned by Nessie history APIs."""

    hash: str
    message: str
    committer: str | None = None
    authors: list[str] = []
    commit_time: str | None = None
    author_time: str | None = None
    parent_commit_hashes: list[str] = []


class LogEntry(BaseModel):
    """Single Nessie log entry."""

    commit_meta: CommitMeta
    parent_commit_hash: str | None = None
    operations: list[dict[str, Any]] | None = None


class NessieConnectionStatus(BaseModel):
    """Connection status for Nessie API checks."""

    connected: bool
    error: str | None = None
    default_branch: str | None = None


class NessieContent(BaseModel):
    """Content item returned by Nessie entry/content APIs."""

    name: dict[str, Any]
    type: str
    content: dict[str, Any] | None = None


class MergeResult(BaseModel):
    """Result of a merge operation."""

    success: bool
    hash: str | None = None


# --- API Endpoints ---


@router.get("/connection", response_model=NessieConnectionStatus)
async def check_connection(
    nessie_url: str | None = None,
) -> NessieConnectionStatus:
    """Check whether Nessie is reachable.

    Exceptions are caught and returned in the response.
    """
    url = resolve_nessie_url(nessie_url)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/config")

            if response.status_code != 200:
                return NessieConnectionStatus(
                    connected=False,
                    error=f"HTTP {response.status_code}: {response.reason_phrase}",
                )

            config = response.json()
            return NessieConnectionStatus(
                connected=True,
                default_branch=config.get("defaultBranch", "main"),
            )
    except Exception as e:
        return NessieConnectionStatus(connected=False, error=str(e))


@router.get("/branches", response_model=list[Branch] | dict)
async def get_branches(nessie_url: str | None = None) -> list[Branch] | dict[str, str]:
    """List all Nessie references (branches and tags).

    Exceptions are caught and returned in the response.
    """
    url = resolve_nessie_url(nessie_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{url}/trees")

            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}: {response.reason_phrase}"}

            payload = response.json()
            references = payload.get("references", [])

            return [
                Branch(
                    type=ref.get("type", "BRANCH"),
                    name=ref["name"],
                    hash=ref["hash"],
                )
                for ref in references
            ]
    except Exception as e:
        return {"error": str(e)}


@router.get("/branches/{branch_name}", response_model=Branch | dict)
async def get_branch(branch_name: str, nessie_url: str | None = None) -> Branch | dict[str, str]:
    """Get a branch by name."""
    url = resolve_nessie_url(nessie_url)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/trees/{quote(branch_name, safe='')}")

            if response.status_code == 404:
                return {"error": f"Branch '{branch_name}' not found"}
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}: {response.reason_phrase}"}

            payload = response.json()
            return Branch(
                type=payload.get("type", "BRANCH"),
                name=payload["name"],
                hash=payload["hash"],
            )
    except Exception as e:
        return {"error": str(e)}


@router.get("/branches/{branch_name}/history", response_model=list[LogEntry] | dict)
async def get_commits(
    branch_name: str,
    limit: int = Query(default=50, le=200),
    nessie_url: str | None = None,
) -> list[LogEntry] | dict[str, str]:
    """Get commit history for a branch."""
    url = resolve_nessie_url(nessie_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{url}/trees/{quote(branch_name, safe='')}/history",
                params={"maxRecords": str(limit)},
            )

            if response.status_code == 404:
                return {"error": f"Branch '{branch_name}' not found"}
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}: {response.reason_phrase}"}

            data = response.json()
            log_entries = data.get("logEntries", [])

            return [
                LogEntry(
                    commit_meta=CommitMeta(
                        hash=entry["commitMeta"]["hash"],
                        message=entry["commitMeta"].get("message", ""),
                        committer=entry["commitMeta"].get("committer"),
                        authors=entry["commitMeta"].get("authors", []),
                        commit_time=entry["commitMeta"].get("commitTime"),
                        author_time=entry["commitMeta"].get("authorTime"),
                        parent_commit_hashes=entry["commitMeta"].get("parentCommitHashes", []),
                    ),
                    parent_commit_hash=entry.get("parentCommitHash"),
                    operations=entry.get("operations"),
                )
                for entry in log_entries
            ]
    except Exception as e:
        return {"error": str(e)}


@router.get("/branches/{branch_name}/entries", response_model=list[dict] | dict)
async def get_contents(
    branch_name: str,
    prefix: str | None = None,
    nessie_url: str | None = None,
) -> list[dict[str, Any]] | dict[str, str]:
    """Get branch contents."""
    url = resolve_nessie_url(nessie_url)

    try:
        params = {}
        if prefix:
            params["filter"] = f"entry.namespace.startsWith('{prefix}')"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{url}/trees/{quote(branch_name, safe='')}/entries",
                params=params,
            )

            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}: {response.reason_phrase}"}

            data = response.json()
            return data.get("entries", [])
    except Exception as e:
        return {"error": str(e)}


@router.get("/diff/{from_branch}/{to_branch}", response_model=dict)
async def compare_branches(
    from_branch: str,
    to_branch: str,
    nessie_url: str | None = None,
) -> dict[str, Any]:
    """Compare two branches."""
    url = resolve_nessie_url(nessie_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{url}/trees/{quote(to_branch, safe='')}/diff/{quote(from_branch, safe='')}"
            )

            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}: {response.reason_phrase}"}

            return response.json()
    except Exception as e:
        return {"error": str(e)}


@router.post("/branches", response_model=Branch | dict)
async def create_branch(
    name: str,
    from_branch: str,
    nessie_url: str | None = None,
) -> Branch | dict[str, str]:
    """Create a new branch."""
    url = resolve_nessie_url(nessie_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get source branch hash
            source_response = await client.get(f"{url}/trees/{quote(from_branch, safe='')}")

            if source_response.status_code != 200:
                return {"error": f"Source branch '{from_branch}' not found"}

            source_branch = source_response.json()

            # Create new branch
            create_response = await client.post(
                f"{url}/trees",
                json={
                    "type": "BRANCH",
                    "name": name,
                    "hash": source_branch["hash"],
                },
            )

            if create_response.status_code not in (200, 201):
                error_text = create_response.text
                return {"error": f"Failed to create branch: {error_text}"}

            new_branch = create_response.json()
            return Branch(
                type="BRANCH",
                name=new_branch["name"],
                hash=new_branch["hash"],
            )
    except Exception as e:
        return {"error": str(e)}


@router.delete("/branches/{branch_name}", response_model=dict)
async def delete_branch(
    branch_name: str,
    expected_hash: str,
    nessie_url: str | None = None,
) -> dict[str, Any]:
    """Delete a branch."""
    url = resolve_nessie_url(nessie_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{url}/trees/{quote(branch_name, safe='')}",
                params={"expectedHash": expected_hash},
            )

            if response.status_code != 200:
                error_text = response.text
                return {"error": f"Failed to delete branch: {error_text}"}

            return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@router.post("/merge", response_model=MergeResult | dict)
async def merge_branch(
    from_branch: str,
    into_branch: str,
    message: str | None = None,
    nessie_url: str | None = None,
) -> MergeResult | dict[str, str]:
    """Merge one branch into another."""
    url = resolve_nessie_url(nessie_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get source branch hash
            source_response = await client.get(f"{url}/trees/{quote(from_branch, safe='')}")
            if source_response.status_code != 200:
                return {"error": f"Source branch '{from_branch}' not found"}
            source_branch = source_response.json()

            # Get target branch hash
            target_response = await client.get(f"{url}/trees/{quote(into_branch, safe='')}")
            if target_response.status_code != 200:
                return {"error": f"Target branch '{into_branch}' not found"}
            target_branch = target_response.json()

            # Perform merge
            merge_response = await client.post(
                f"{url}/trees/{quote(into_branch, safe='')}/history/merge",
                params={"expectedHash": target_branch["hash"]},
                json={
                    "fromRefName": from_branch,
                    "fromHash": source_branch["hash"],
                    "message": message or f"Merge {from_branch} into {into_branch}",
                },
            )

            if merge_response.status_code not in (200, 201):
                error_text = merge_response.text
                return {"error": f"Merge failed: {error_text}"}

            result = merge_response.json()
            return MergeResult(success=True, hash=result.get("resultantTargetHash"))
    except Exception as e:
        return {"error": str(e)}
