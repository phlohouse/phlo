"""GitHub draft-PR provider tests; all requests use a local mock transport."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from phlo_api.api.v1_audit_proposals import AssetAuditProposal
from phlo_api.api.v1_git_review import (
    GitReviewConfig,
    GitReviewConflict,
    GitReviewUnavailable,
    project_git_review_config,
    publish_project_draft_pr,
)


def _proposal(**changes: object) -> AssetAuditProposal:
    values: dict[str, object] = {
        "proposal_id": "a1" * 16,
        "env": "prod",
        "asset_id": "warehouse/orders",
        "nessie_ref": "main",
        "check_name": "orders_quality",
        "file_path": "workflows/quality/warehouse_orders_orders_quality.py",
        "source": "from phlo_pandera import UniqueCheck\n",
        "patch": "patch",
        "status": "pending_review",
        "created_at": datetime(2026, 9, 26, tzinfo=UTC),
    }
    values.update(changes)
    if "source_digest" not in changes:
        values["source_digest"] = hashlib.sha256(str(values["source"]).encode()).hexdigest()
    return AssetAuditProposal.model_validate(values)


def _config() -> GitReviewConfig:
    return GitReviewConfig(
        repository="acme/project-data",
        base_branch="main",
        token="test-only-token",
    )


@pytest.mark.anyio
async def test_project_draft_pr_uses_fixed_target_and_creates_review_only_change():
    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-only-token"
        if request.method == "GET" and request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if request.method == "GET" and "/git/ref/heads/phlo" in request.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and request.url.path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit"}})
        if request.method == "GET" and request.url.path.endswith("/git/commits/base-commit"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
        if request.method == "GET" and "/contents/workflows/quality/" in request.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST" and request.url.path.endswith("/git/blobs"):
            body = json.loads(request.content)
            assert body["encoding"] == "base64"
            assert base64.b64decode(body["content"]).decode() == _proposal().source
            return httpx.Response(201, json={"sha": "blob-sha"})
        if request.method == "POST" and request.url.path.endswith("/git/trees"):
            body = json.loads(request.content)
            assert body["base_tree"] == "base-tree"
            assert body["tree"][0]["path"].startswith("workflows/quality/")
            return httpx.Response(201, json={"sha": "proposal-tree"})
        if request.method == "POST" and request.url.path.endswith("/git/commits"):
            body = json.loads(request.content)
            assert body["parents"] == ["base-commit"]
            return httpx.Response(201, json={"sha": "proposal-commit"})
        if request.method == "POST" and request.url.path.endswith("/git/refs"):
            body = json.loads(request.content)
            assert body["ref"] == f"refs/heads/phlo/asset-check-{_proposal().proposal_id}"
            return httpx.Response(201, json={"ref": body["ref"]})
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            body = json.loads(request.content)
            assert body["draft"] is True
            assert "not active until" in body["body"]
            assert body["base"] == "main"
            return httpx.Response(
                201,
                json={
                    "number": 42,
                    "html_url": "https://github.com/acme/project-data/pull/42",
                    "base": {"ref": "main"},
                    "head": {
                        "ref": f"phlo/asset-check-{_proposal().proposal_id}",
                        "repo": {"full_name": "acme/project-data"},
                    },
                    "draft": True,
                    "body": f"Source SHA-256: `{_proposal().source_digest}`",
                },
            )
        raise AssertionError(f"Unexpected GitHub request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(respond),
        headers={"Authorization": "Bearer test-only-token"},
    ) as client:
        result = await publish_project_draft_pr(client, _config(), _proposal())

    assert result.status == "pending_review"
    assert result.repository == "acme/project-data"
    assert result.pull_request_number == 42
    assert len(calls) == 10
    assert all("test-only-token" not in str(call.url) for call in calls)


@pytest.mark.anyio
async def test_existing_draft_pull_request_is_replayed_without_writes():
    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/pulls")
        return httpx.Response(
            200,
            json=[
                {
                    "number": 42,
                    "html_url": "https://github.com/acme/project-data/pull/42",
                    "base": {"ref": "main"},
                    "head": {
                        "ref": f"phlo/asset-check-{_proposal().proposal_id}",
                        "repo": {"full_name": "acme/project-data"},
                    },
                    "draft": True,
                    "body": f"Source SHA-256: `{_proposal().source_digest}`",
                }
            ],
        )

    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(respond),
    ) as client:
        result = await publish_project_draft_pr(client, _config(), _proposal())

    assert result.pull_request_number == 42


@pytest.mark.anyio
async def test_existing_branch_without_matching_open_pr_fails_closed():
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if request.method == "GET" and "/git/ref/heads/phlo" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "partial"}})
        raise AssertionError(f"Unexpected GitHub request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(respond),
    ) as client:
        with pytest.raises(GitReviewConflict):
            await publish_project_draft_pr(client, _config(), _proposal())


@pytest.mark.anyio
async def test_existing_quality_file_is_never_overwritten():
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if request.method == "GET" and "/git/ref/heads/phlo" in request.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and request.url.path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit"}})
        if request.method == "GET" and request.url.path.endswith("/git/commits/base-commit"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
        if request.method == "GET" and "/contents/workflows/quality/" in request.url.path:
            return httpx.Response(200, json={"sha": "existing-file"})
        raise AssertionError(f"Unexpected GitHub request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(respond),
    ) as client:
        with pytest.raises(GitReviewConflict):
            await publish_project_draft_pr(client, _config(), _proposal())


def test_project_git_review_configuration_is_required_and_fixed(monkeypatch):
    for name in (
        "PHLO_V1_GIT_REVIEW_REPOSITORY",
        "PHLO_V1_GIT_REVIEW_BASE_BRANCH",
        "PHLO_V1_GIT_REVIEW_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(GitReviewUnavailable, match="not configured"):
        project_git_review_config()

    monkeypatch.setenv("PHLO_V1_GIT_REVIEW_REPOSITORY", "acme/project-data")
    monkeypatch.setenv("PHLO_V1_GIT_REVIEW_BASE_BRANCH", "main")
    monkeypatch.setenv("PHLO_V1_GIT_REVIEW_TOKEN", "test-token")
    config = project_git_review_config()
    assert config.repository == "acme/project-data"
    assert config.base_branch == "main"


@pytest.mark.anyio
async def test_project_git_review_rejects_non_quality_path():
    proposal = _proposal(file_path="../../outside.py")

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as client:
        with pytest.raises(GitReviewUnavailable, match="quality-check directory"):
            await publish_project_draft_pr(client, _config(), proposal)
