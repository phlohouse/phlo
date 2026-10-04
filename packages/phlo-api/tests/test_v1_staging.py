"""Discriminating HTTP tests for the opt-in staging promotion surface."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from phlo.capabilities import AuthPrincipal
from phlo.compliance.signatures.types import SignatureMeaning, SignatureRecord, SignatureRequest
from phlo.identity.authority import IdentityAuthority
from phlo.plugins.observatory_settings import InMemorySettingsService
from phlo_api.api import v1_admin_identity, v1_staging
from phlo_api.api.v1_branch_workflows import BranchCheck
from security_test_support import authenticated_client


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(path), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def _commit(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(
        path,
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def staging_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[TestClient, dict[str, Any]]:
    """Use real linked worktrees and SQLite, replacing only remote providers."""
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "staging")
    (repository / "code.py").write_text("VERSION = 1\n")
    (repository / "prod-only.py").write_text("PROD = True\n")
    base = _commit(repository, "production")
    _git(repository, "branch", "prod", base)
    (repository / "code.py").write_text("VERSION = 2\n")
    (repository / "staging-only.py").write_text("STAGING = True\n")
    staging_head = _commit(repository, "staging")
    prod = tmp_path / "prod"
    _git(repository, "worktree", "add", str(prod), "prod")

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(project))
    monkeypatch.setenv("PHLO_STAGING_SINGLE_REPLICA", "true")
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setenv("PHLO_PROMOTION_PROD_WORKTREE", str(prod))
    monkeypatch.setenv("PHLO_PROMOTION_STAGING_WORKTREE", str(repository))
    monkeypatch.setenv("PHLO_PROMOTION_PROD_REF", "main")
    monkeypatch.setenv("PHLO_PROMOTION_STAGING_REF", "candidate")
    monkeypatch.setenv("PHLO_PROMOTION_DAGSTER_LOCATION", "prod_loc")
    monkeypatch.setenv("PHLO_PROMOTION_DAGSTER_CHECK_JOBS", "tests,contracts,audits")
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod_loc", "nessie_ref": "main"},
                "staging": {
                    "dagster_location": "stage_loc",
                    "nessie_ref": "candidate",
                },
            }
        ),
    )
    actor = AuthPrincipal(subject="release-manager", principal_type="user")
    monkeypatch.setattr(
        v1_staging,
        "require_scope",
        lambda _request, scope: {"subject": actor.subject, "scopes": [scope]},
    )
    monkeypatch.setattr(v1_staging, "get_request_principal", lambda _request: actor)

    refs = {
        "main": {"name": "main", "type": "BRANCH", "hash": "prod-hash"},
        "candidate": {"name": "candidate", "type": "BRANCH", "hash": "stage-hash"},
    }
    remote_calls: list[tuple[str, str, Any, Any]] = []

    async def reference(name: str) -> dict[str, str] | None:
        value = refs.get(name)
        return dict(value) if value else None

    async def nessie(method: str, path: str, *, params=None, body=None, **_kwargs):
        remote_calls.append((method, path, params, body))
        if method == "GET" and path.endswith("/entries"):
            table = "prod.table" if "main@" in path else "stage.table"
            return {
                "entries": [{"type": "ICEBERG_TABLE", "name": {"elements": table.split(".")}}],
                "hasMore": False,
            }
        if method == "PUT":
            assert params == {"expectedHash": refs["candidate"]["hash"]}
            refs["candidate"]["hash"] = body["hash"]
            return dict(refs["candidate"])
        raise AssertionError(f"unexpected Nessie call: {method} {path}")

    jobs = {
        "data": {
            "repositoriesOrError": {
                "__typename": "RepositoryConnection",
                "nodes": [
                    {
                        "name": "repo",
                        "location": {"name": location},
                        "pipelines": [
                            {
                                "name": f"{location}_job" if location == "prod_loc" else name,
                                "description": None,
                            }
                            for name in (
                                ("tests", "contracts", "audits")
                                if location == "stage_loc"
                                else ("unused",)
                            )
                        ],
                    }
                    for location in ("prod_loc", "stage_loc")
                ],
            }
        }
    }
    monkeypatch.setattr(v1_staging, "_reference", reference)
    monkeypatch.setattr(v1_staging, "_nessie", nessie)
    monkeypatch.setattr(v1_staging.v1_jobs, "_graphql", lambda *_args, **_kwargs: _async(jobs))
    monkeypatch.setattr(v1_staging, "resolve_dagster_url", lambda: "http://dagster.invalid")
    monkeypatch.setattr(
        v1_staging, "graphql_request", lambda *_args, **_kwargs: _async(_runs_response([]))
    )

    context: dict[str, Any] = {
        "repository": repository,
        "prod": prod,
        "base": base,
        "staging_head": staging_head,
        "refs": refs,
        "remote_calls": remote_calls,
        "actor": actor,
    }
    return authenticated_client("operator"), context


async def _async(value: Any) -> Any:
    return value


def _candidate(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/v1/staging/promotions/candidate?env=staging")
    assert response.status_code == 200, response.text
    return response.json()


def _check_row(
    state: dict[str, Any], name: str, status: str = "SUCCESS", **tags: str
) -> dict[str, Any]:
    evidence = {
        "environment": "staging",
        "phlo/ref": state["staging_ref"],
        "phlo/code_version": state["staging_git_revision"],
        "phlo/nessie_hash": state["staging_hash"],
        "phlo/promotion_candidate": state["candidate_id"],
        **tags,
    }
    return {
        "runId": f"run-{name}-{status}",
        "status": status,
        "pipelineName": name,
        "tags": [{"key": key, "value": value} for key, value in evidence.items()],
        "repositoryOrigin": {"repositoryLocationName": state["staging_location"]},
    }


def _runs_response(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"runsOrError": {"__typename": "Runs", "results": rows}}}


def _signature(authority: IdentityAuthority, state: dict[str, Any], justification: str):
    request = SignatureRequest(
        signer_subject="release-manager",
        meaning=SignatureMeaning.APPROVED,
        action="staging.promote",
        record_type="promotion",
        record_id="main<-candidate",
        record_version=state["candidate_id"],
        justification=justification,
    )
    record = SignatureRecord.from_request(request, authentication_assurance="mfa")
    authority.save_signature(record)
    return record


@pytest.mark.parametrize(
    "query", ["env=prod", "", "env=staging&env=staging", "env=staging&ref=main"]
)
def test_every_staging_route_requires_exact_staging_selector(staging_api, query: str) -> None:
    client, _ = staging_api
    suffix = f"?{query}" if query else ""
    response = client.get(f"/api/v1/staging/promotions/candidate{suffix}")
    assert response.status_code in {400, 422, 503}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PHLO_STAGING_SINGLE_REPLICA", "false"),
        ("WEB_CONCURRENCY", "2"),
        ("PHLO_PROMOTION_PROD_REF", "override-main"),
        ("PHLO_PROMOTION_DAGSTER_LOCATION", "override-location"),
    ],
)
def test_candidate_requires_opt_in_and_denies_target_overrides(
    staging_api, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    client, context = staging_api
    monkeypatch.setenv(name, value)
    response = client.get("/api/v1/staging/promotions/candidate?env=staging")
    assert response.status_code == 503
    assert context["remote_calls"] == []


def test_candidate_is_opt_in_and_reports_mapped_asymmetric_inventory(staging_api) -> None:
    client, context = staging_api
    state = _candidate(client)

    assert state["prod_ref"] == "main"
    assert state["staging_ref"] == "candidate"
    assert state["dagster_location"] == "prod_loc"
    assert state["staging_location"] == "stage_loc"
    assert state["code_changes"] == ["M\tcode.py", "A\tstaging-only.py"]
    assert state["jobs"] == {
        "prod": ["prod_loc_job"],
        "staging": ["audits", "contracts", "tests"],
    }
    assert state["copy_inventory"] == {
        "prod": ["prod.table"],
        "staging": ["stage.table"],
    }
    assert state["check_configuration_ready"] is True
    assert [item["status"] for item in state["check_readiness"]] == [
        "missing_evidence",
        "missing_evidence",
        "missing_evidence",
    ]
    assert state["prod_git_revision"] == context["base"]
    assert state["staging_git_revision"] == context["staging_head"]

    context["remote_calls"].clear()
    context["repository"].joinpath("dirty.py").write_text("dirty\n")
    denied = client.get("/api/v1/staging/promotions/candidate?env=staging")
    assert denied.status_code == 409
    assert context["remote_calls"] == []


@pytest.mark.parametrize("path", [".env", ".env.production", "secrets/key", ".phlo/state"])
def test_candidate_rejects_secret_and_internal_paths(staging_api, path: str) -> None:
    client, context = staging_api
    target = context["repository"] / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("sensitive\n")
    _commit(context["repository"], f"add {path}")
    response = client.get("/api/v1/staging/promotions/candidate?env=staging")
    assert response.status_code == 422


@pytest.mark.parametrize("kind", ["symlink", "submodule"])
def test_candidate_rejects_gitlinks_symlinks_and_resolved_escape(staging_api, kind: str) -> None:
    client, context = staging_api
    repository = context["repository"]
    if kind == "symlink":
        (repository / "escape").symlink_to("../../outside")
        _commit(repository, "add escaping symlink")
    else:
        child = repository.parent / "child"
        child.mkdir()
        _git(child, "init")
        (child / "x").write_text("x")
        _commit(child, "child")
        _git(
            repository, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "vendor"
        )
        _commit(repository, "add submodule")
    response = client.get("/api/v1/staging/promotions/candidate?env=staging")
    assert response.status_code == 422


def test_disjoint_and_diverged_worktrees_are_denied(staging_api) -> None:
    client, context = staging_api
    (context["prod"] / "prod-diverged.py").write_text("x\n")
    _commit(context["prod"], "diverge production")
    response = client.get("/api/v1/staging/promotions/candidate?env=staging")
    assert response.status_code == 409


def test_disjoint_worktrees_are_denied_before_remote_reads(
    staging_api, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, context = staging_api
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _git(unrelated, "init")
    (unrelated / "code.py").write_text("unrelated\n")
    _commit(unrelated, "unrelated")
    monkeypatch.setenv("PHLO_PROMOTION_STAGING_WORKTREE", str(unrelated))
    response = client.get("/api/v1/staging/promotions/candidate?env=staging")
    assert response.status_code == 503
    assert context["remote_calls"] == []


def test_check_launch_pins_all_evidence_and_replays_without_running_twice(
    staging_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = staging_api
    state = _candidate(client)
    calls: list[dict[str, Any]] = []

    async def old_failed_evidence(*_args, **_kwargs):
        return _runs_response([_check_row(state, "tests", "FAILURE")])

    monkeypatch.setattr(v1_staging, "graphql_request", old_failed_evidence)

    async def launch(**kwargs):
        calls.append(kwargs)
        return BranchCheck(name=kwargs["check_name"], status="passed", run_id="run", message=None)

    monkeypatch.setattr(v1_staging, "_run_check_job", launch)
    headers = {"Idempotency-Key": "checks-once"}
    body = {"candidate_id": state["candidate_id"]}
    first = client.post(
        "/api/v1/staging/promotions/candidate/checks?env=staging", json=body, headers=headers
    )
    replay = client.post(
        "/api/v1/staging/promotions/candidate/checks?env=staging", json=body, headers=headers
    )
    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert [(call["check_name"], call["job_name"]) for call in calls] == [
        ("tests", "tests"),
        ("contracts", "contracts"),
        ("audits", "audits"),
    ]
    assert all(call["timeout_seconds"] == 90 for call in calls)
    assert all(
        call["additional_tags"]
        == {
            "phlo/code_version": state["staging_git_revision"],
            "phlo/nessie_hash": state["staging_hash"],
            "phlo/promotion_candidate": state["candidate_id"],
        }
        for call in calls
    )

    changed = client.post(
        "/api/v1/staging/promotions/candidate/checks?env=staging",
        json={"candidate_id": "0" * 64},
        headers=headers,
    )
    assert changed.status_code == 409
    assert len(calls) == 3


def test_check_evidence_requires_latest_exact_location_ref_code_hash_and_candidate(
    staging_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = staging_api
    state = _candidate(client)

    async def evidence(rows):
        return _runs_response(rows)

    variants = [
        [_check_row(state, name) for name in ("tests", "contracts", "audits")],
        [_check_row(state, "tests", **{"phlo/ref": "wrong"})],
        [_check_row(state, "tests", **{"phlo/code_version": "wrong"})],
        [_check_row(state, "tests", **{"phlo/nessie_hash": "wrong"})],
        [_check_row(state, "tests", **{"phlo/promotion_candidate": "0" * 64})],
    ]
    for rows in variants[1:]:
        monkeypatch.setattr(v1_staging, "graphql_request", lambda *_a, r=rows, **_k: evidence(r))
        with pytest.raises(Exception) as error:
            asyncio.run(v1_staging._checks(state))
        assert getattr(error.value, "status_code", None) == 409

    rows = [
        _check_row(state, "tests", "FAILURE"),
        _check_row(state, "tests", "SUCCESS"),
        _check_row(state, "contracts"),
        _check_row(state, "audits"),
    ]
    monkeypatch.setattr(v1_staging, "graphql_request", lambda *_a, **_k: evidence(rows))
    with pytest.raises(Exception) as error:
        asyncio.run(v1_staging._checks(state))
    assert getattr(error.value, "status_code", None) == 409


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "missing_evidence"),
        ([], "stale_evidence"),
    ],
)
def test_candidate_readiness_explains_missing_and_stale_evidence(
    staging_api, monkeypatch: pytest.MonkeyPatch, rows, expected: str
) -> None:
    client, _ = staging_api
    state = _candidate(client)
    if expected == "stale_evidence":
        rows = [_check_row(state, "tests", **{"phlo/code_version": "old-revision"})]

    async def evidence(_url, _query, _variables):
        return _runs_response(rows)

    monkeypatch.setattr(v1_staging, "graphql_request", evidence)
    readiness = asyncio.run(v1_staging._check_readiness(state))
    assert readiness[0]["status"] == expected
    if expected == "stale_evidence":
        assert "phlo/code_version" in readiness[0]["message"]


def test_candidate_readiness_distinguishes_failed_and_current_successful_runs(
    staging_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = staging_api
    state = _candidate(client)

    async def evidence(rows):
        return _runs_response(rows)

    monkeypatch.setattr(
        v1_staging,
        "graphql_request",
        lambda *_args, **_kwargs: evidence([_check_row(state, "tests", "FAILURE")]),
    )
    failed = asyncio.run(v1_staging._check_readiness(state))
    assert failed[0]["status"] == "failed"
    assert failed[0]["run_id"] == "run-tests-FAILURE"

    monkeypatch.setattr(
        v1_staging,
        "graphql_request",
        lambda *_args, **_kwargs: evidence([_check_row(state, "tests")]),
    )
    passed = asyncio.run(v1_staging._check_readiness(state))
    assert passed[0]["status"] == "ready"
    assert passed[0]["run_id"] == "run-tests-SUCCESS"

    unidentifiable = _check_row(state, "tests")
    del unidentifiable["runId"]
    monkeypatch.setattr(
        v1_staging,
        "graphql_request",
        lambda *_args, **_kwargs: evidence([unidentifiable]),
    )
    unavailable = asyncio.run(v1_staging._check_readiness(state))
    assert unavailable[0]["status"] == "unavailable"
    with pytest.raises(Exception) as error:
        asyncio.run(v1_staging._checks(state))
    assert getattr(error.value, "status_code", None) == 409


def test_candidate_readiness_identifies_unconfigured_and_missing_jobs(
    staging_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = staging_api
    state = _candidate(client)
    monkeypatch.delenv("PHLO_PROMOTION_DAGSTER_CHECK_JOBS")
    unconfigured = asyncio.run(v1_staging._check_readiness(state))
    assert [item["status"] for item in unconfigured] == ["unconfigured"] * 3

    monkeypatch.setenv("PHLO_PROMOTION_DAGSTER_CHECK_JOBS", "tests,contracts,audits")
    state["jobs"]["staging"] = ["tests", "contracts"]
    missing = asyncio.run(v1_staging._check_readiness(state))
    assert missing[2]["status"] == "missing_job"
    assert "audits" in missing[2]["message"]


def test_resync_uses_compare_and_assign_then_replays_without_duplicate(staging_api) -> None:
    client, context = staging_api
    headers = {"Idempotency-Key": "resync-once"}
    body = {
        "expected_prod_hash": "prod-hash",
        "expected_staging_hash": "stage-hash",
        "confirm": True,
    }
    first = client.post("/api/v1/staging/resync?env=staging", json=body, headers=headers)
    replay = client.post("/api/v1/staging/resync?env=staging", json=body, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    puts = [call for call in context["remote_calls"] if call[0] == "PUT"]
    assert puts == [
        (
            "PUT",
            "/api/v1/trees/branch/candidate",
            {"expectedHash": "stage-hash"},
            {"type": "BRANCH", "name": "candidate", "hash": "prod-hash"},
        )
    ]

    stale = client.post(
        "/api/v1/staging/resync?env=staging",
        json={**body, "expected_staging_hash": "stale"},
        headers={"Idempotency-Key": "resync-stale"},
    )
    assert stale.status_code == 409
    assert len([call for call in context["remote_calls"] if call[0] == "PUT"]) == 1
    assert _git(context["prod"], "rev-parse", "HEAD") == context["base"]


def test_promotion_consumes_exact_signature_advances_only_prod_and_replays(
    staging_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, context = staging_api
    state = _candidate(client)
    authority = IdentityAuthority(InMemorySettingsService())
    signature = _signature(authority, state, "Reviewed release")
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)
    monkeypatch.setattr(v1_staging, "_checks", lambda _state: _async([{"name": "tests"}]))
    reloads: list[dict[str, Any]] = []

    async def graphql(_url, query, variables):
        reloads.append({"query": query, "variables": variables})
        return {
            "data": {
                "reloadRepositoryLocation": {
                    "__typename": "WorkspaceLocationEntry",
                    "loadStatus": "LOADED",
                    "locationOrLoadError": {"__typename": "RepositoryLocation", "name": "prod_loc"},
                }
            }
        }

    monkeypatch.setattr(v1_staging, "graphql_request", graphql)
    body = {
        "candidate_id": state["candidate_id"],
        "signature_id": signature.signature_id,
        "justification": "Reviewed release",
        "confirm": True,
    }
    headers = {"Idempotency-Key": "promote-once"}
    first = client.post("/api/v1/staging/promotions?env=staging", json=body, headers=headers)
    replay = client.post("/api/v1/staging/promotions?env=staging", json=body, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert _git(context["prod"], "rev-parse", "HEAD") == context["staging_head"]
    assert _git(context["repository"], "rev-parse", "HEAD") == context["staging_head"]
    assert reloads[0]["variables"] == {"name": "prod_loc"}
    assert len(reloads) == 1
    assert authority.signatures("release-manager")[0].consumed_at is not None


def test_failed_reload_is_unknown_and_never_retried(staging_api, monkeypatch) -> None:
    client, context = staging_api
    state = _candidate(client)
    authority = IdentityAuthority(InMemorySettingsService())
    signature = _signature(authority, state, "Reviewed release")
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)
    monkeypatch.setattr(v1_staging, "_checks", lambda _state: _async([]))
    reloads = 0

    async def failed_reload(*_args, **_kwargs):
        nonlocal reloads
        reloads += 1
        return {"data": {"reloadRepositoryLocation": {"__typename": "PythonError"}}}

    monkeypatch.setattr(v1_staging, "graphql_request", failed_reload)
    body = {
        "candidate_id": state["candidate_id"],
        "signature_id": signature.signature_id,
        "justification": "Reviewed release",
        "confirm": True,
    }
    headers = {"Idempotency-Key": "unknown-promotion"}
    first = client.post("/api/v1/staging/promotions?env=staging", json=body, headers=headers)
    replay = client.post("/api/v1/staging/promotions?env=staging", json=body, headers=headers)
    assert first.status_code == 503
    assert replay.status_code == 409
    assert reloads == 1
    assert _git(context["prod"], "rev-parse", "HEAD") == context["staging_head"]


def test_promotion_rejects_wrong_signature_and_stale_candidate_without_side_effects(
    staging_api, monkeypatch
) -> None:
    client, context = staging_api
    state = _candidate(client)
    authority = IdentityAuthority(InMemorySettingsService())
    wrong = SignatureRecord.from_request(
        SignatureRequest(
            signer_subject="another-actor",
            meaning=SignatureMeaning.APPROVED,
            action="staging.promote",
            record_type="promotion",
            record_id="main<-candidate",
            record_version=state["candidate_id"],
            justification="different",
        ),
        authentication_assurance="mfa",
    )
    authority.save_signature(wrong)
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)
    monkeypatch.setattr(v1_admin_identity, "_audit", lambda _event: None)
    monkeypatch.setattr(v1_staging, "_checks", lambda _state: _async([]))
    body = {
        "candidate_id": state["candidate_id"],
        "signature_id": wrong.signature_id,
        "justification": "Reviewed release",
        "confirm": True,
    }
    denied = client.post(
        "/api/v1/staging/promotions?env=staging",
        json=body,
        headers={"Idempotency-Key": "wrong-signature"},
    )
    assert denied.status_code == 409
    assert _git(context["prod"], "rev-parse", "HEAD") == context["base"]
    assert authority.signatures("another-actor")[0].consumed_at is None

    stale = client.post(
        "/api/v1/staging/promotions?env=staging",
        json={**body, "candidate_id": "0" * 64},
        headers={"Idempotency-Key": "stale-promotion"},
    )
    assert stale.status_code == 409
    assert _git(context["prod"], "rev-parse", "HEAD") == context["base"]
