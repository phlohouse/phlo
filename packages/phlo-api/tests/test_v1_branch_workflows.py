"""HTTP contracts for environment-scoped Nessie branch reads."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from phlo.capabilities import AuthPrincipal, AuthorizationDecision, Principal
from fastapi.testclient import TestClient

from phlo_api.api import v1_branch_workflows
from phlo_api.api import v1_jobs
from phlo_api.main import app
from phlo_api import security_manifest


@pytest.fixture
def branch_api(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, dict[str, list[object]]]:
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod_loc", "nessie_ref": "main"},
                "staging": {"dagster_location": "stage_loc", "nessie_ref": "candidate"},
            }
        ),
    )
    actor = AuthPrincipal(subject="branch-reviewer", principal_type="user", groups=("operator",))
    monkeypatch.setattr(security_manifest, "get_request_principal", lambda request: actor)
    monkeypatch.setattr(
        security_manifest,
        "resolve_request_principal",
        lambda *args, **kwargs: Principal(
            subject=actor.subject, principal_type="user", roles=("operator",)
        ),
    )
    monkeypatch.setattr(security_manifest, "is_regulated", lambda: False)

    class AllowAll:
        def explain_decision(self, principal, action, resource, context=None):
            return AuthorizationDecision(allowed=True, reason_code="explicit_allow")

    monkeypatch.setattr(security_manifest, "get_authorization_backend", AllowAll)
    monkeypatch.setattr(
        v1_branch_workflows,
        "require_scope",
        lambda request, scope: {"subject": actor.subject, "scopes": [scope]},
    )
    calls: dict[str, list[object]] = {"paths": [], "requests": []}
    refs = {
        "main": {"name": "main", "type": "BRANCH", "hash": "p3"},
        "prod-feature": {"name": "prod-feature", "type": "BRANCH", "hash": "p2"},
        "prod-target": {"name": "prod-target", "type": "BRANCH", "hash": "p1"},
        "prod-release": {"name": "prod-release", "type": "TAG", "hash": "p2"},
        "candidate": {"name": "candidate", "type": "BRANCH", "hash": "s2"},
        "staging-feature": {"name": "staging-feature", "type": "BRANCH", "hash": "s1"},
    }
    histories = {
        "prod-feature": ["p2", "p1", "root"],
        "prod-target": ["p1", "root"],
        "main": ["p3", "p2", "p1", "root"],
        "staging-feature": ["s1", "root"],
        "candidate": ["s2", "s1", "root"],
    }

    async def nessie(method: str, path: str, *, params=None, body=None, allow_not_found=False):
        calls["paths"].append(path)
        calls["requests"].append((method, path, params, body))
        if method == "GET" and path == "/api/v1/trees":
            return {"references": list(refs.values())}
        if method == "GET" and path.startswith("/api/v1/trees/tree/"):
            name = unquote(path.removeprefix("/api/v1/trees/tree/"))
            if name in refs:
                return refs[name]
            if allow_not_found:
                return None
        if method == "POST" and path == "/api/v1/trees/tree":
            refs[body["name"]] = {
                "name": body["name"],
                "type": body["type"],
                "hash": body["hash"],
            }
            return refs[body["name"]]
        if method == "DELETE" and "/api/v1/trees/branch/" in path:
            refs.pop(unquote(path.removeprefix("/api/v1/trees/branch/")), None)
            return {}
        if method == "PUT" and "/api/v1/trees/branch/" in path:
            name = unquote(path.removeprefix("/api/v1/trees/branch/"))
            refs[name] = {"name": name, "type": "BRANCH", "hash": body["hash"]}
            return refs[name]
        if method == "POST" and path.endswith("/history/transplant"):
            dry_run = body["isDryRun"]
            return {
                "wasApplied": not dry_run,
                "wasSuccessful": True,
                "resultantTargetHash": None if dry_run else "rebased-hash",
                "details": [],
            }
        if method == "POST" and path.endswith("/history/merge"):
            dry_run = body["isDryRun"]
            if not dry_run:
                target_name = unquote(path.removeprefix("/api/v2/trees/")).split("@", 1)[0]
                refs[target_name]["hash"] = "merged-hash"
            return {
                "wasApplied": not dry_run,
                "wasSuccessful": True,
                "resultantTargetHash": None if dry_run else "merged-hash",
                "details": [],
            }
        if method == "GET" and path.endswith("/history"):
            branch = unquote(path.removeprefix("/api/v2/trees/").removesuffix("/history")).split(
                "@", 1
            )[0]
            chain = histories[branch]
            offset = int(params.get("pageToken", 0)) if params else 0
            count = int(params["maxRecords"])
            rows = [
                {
                    "commitMeta": {
                        "hash": value,
                        "parentCommitHashes": [chain[index + 1]] if index + 1 < len(chain) else [],
                    }
                }
                for index, value in enumerate(chain[offset : offset + count], start=offset)
            ]
            next_offset = offset + len(rows)
            return {
                "logEntries": rows,
                "hasMore": next_offset < len(chain),
                "token": str(next_offset) if next_offset < len(chain) else None,
            }
        if method == "GET" and "/diff/" in path:
            return {
                "diffs": [
                    {
                        "key": {"elements": ["orders", "daily"]},
                        "from": {"id": "old"},
                        "to": {"id": "new"},
                    }
                ]
            }
        raise AssertionError(f"Unexpected Nessie request: {method} {path}")

    monkeypatch.setattr(v1_branch_workflows, "_nessie", nessie)
    return TestClient(app), calls


def test_reference_list_is_filtered_to_the_selected_environment(branch_api) -> None:
    client, _ = branch_api

    prod = client.get("/api/v1/branches/refs?env=prod")
    staging = client.get("/api/v1/branches/refs?env=staging")

    assert prod.status_code == 200
    assert {item["name"] for item in prod.json()["items"]} == {
        "main",
        "prod-feature",
        "prod-release",
        "prod-target",
    }
    assert staging.status_code == 200
    assert {item["name"] for item in staging.json()["items"]} == {
        "candidate",
        "staging-feature",
    }


def test_branch_detail_rejects_a_reference_from_another_environment(branch_api) -> None:
    client, _ = branch_api

    response = client.get("/api/v1/branches/staging-feature?env=prod")

    assert response.status_code == 404


def test_branch_reads_require_authorization_before_calling_nessie(
    branch_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = branch_api

    def deny(request, scope):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(v1_branch_workflows, "require_scope", deny)
    response = client.get("/api/v1/branches/refs?env=prod")

    assert response.status_code == 403
    assert calls["paths"] == []


def test_branch_diff_and_ahead_behind_use_the_selected_heads(branch_api) -> None:
    client, _ = branch_api

    diff = client.get("/api/v1/branches/prod-feature/diff?env=prod&target=main")
    comparison = client.get("/api/v1/branches/prod-feature/compare?env=prod&target=main")

    assert diff.status_code == 200
    assert diff.json()["source_hash"] == "p2"
    assert diff.json()["target_hash"] == "p3"
    assert diff.json()["items"] == [
        {
            "key": "orders.daily",
            "status": "modified",
            "from_content_id": "old",
            "to_content_id": "new",
        }
    ]
    assert comparison.status_code == 200
    assert comparison.json()["merge_base"] == "p2"
    assert comparison.json()["ahead"] == 0
    assert comparison.json()["behind"] == 1


def test_commit_pagination_and_stale_cursor(branch_api) -> None:
    client, _ = branch_api

    first = client.get("/api/v1/branches/main/commits?env=prod&limit=2")
    second = client.get(
        f"/api/v1/branches/main/commits?env=prod&limit=2&after={first.json()['next_cursor']}"
    )
    stale = client.get("/api/v1/branches/main/commits?env=prod&after=not-a-commit")

    assert first.status_code == 200
    assert [commit["hash"] for commit in first.json()["items"]] == ["p3", "p2"]
    assert first.json()["next_cursor"] is not None
    assert [commit["hash"] for commit in second.json()["items"]] == ["p1", "root"]
    assert second.json()["next_cursor"] is None
    assert stale.status_code == 400


def test_invalid_environment_query_is_rejected(branch_api) -> None:
    client, _ = branch_api

    response = client.get("/api/v1/branches/refs?env=prod&other=staging")

    assert response.status_code == 400


def test_nessie_outage_is_not_reported_as_an_empty_ref_list(
    branch_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = branch_api

    async def unavailable(*args, **kwargs):
        raise v1_branch_workflows.BackendUnavailableError("Nessie is unavailable.")

    monkeypatch.setattr(v1_branch_workflows, "_nessie", unavailable)
    response = client.get("/api/v1/branches/refs?env=prod")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"


def _enable_branch_mutations(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = AuthPrincipal(subject="branch-reviewer", principal_type="user", groups=("operator",))
    monkeypatch.setattr(v1_branch_workflows, "get_request_principal", lambda request: actor)
    monkeypatch.setattr(v1_branch_workflows, "idempotency_key_target", lambda *args: None)
    monkeypatch.setattr(
        v1_branch_workflows, "_audit_callback", lambda *args, **kwargs: lambda result: None
    )

    async def replay(**kwargs):
        result = await kwargs["execute"]()
        if kwargs.get("audit") is not None:
            kwargs["audit"](result)
        return result

    monkeypatch.setattr(v1_branch_workflows, "replay_or_execute_async", replay)
    for name in (
        "PHLO_V1_ACTIONS_SINGLE_REPLICA",
        "PHLO_V1_ACTIONS_SINGLE_PROCESS",
        "PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
    ):
        monkeypatch.setenv(name, "1")


def _passing_checks() -> list[v1_branch_workflows.BranchCheck]:
    return [
        v1_branch_workflows.BranchCheck(
            name=name, status="passed", run_id=f"run-{name}", message=None
        )
        for name in ("tests", "contracts", "audits")
    ]


def test_branch_create_and_delete_are_environment_scoped_and_hash_guarded(
    branch_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)

    created = client.post(
        "/api/v1/branches?env=prod",
        headers={"Idempotency-Key": "create-prod-feature"},
        json={"name": "prod-new", "from_ref": "main"},
    )
    deleted = client.request(
        "DELETE",
        "/api/v1/branches/prod-new?env=prod",
        headers={"Idempotency-Key": "delete-prod-feature"},
        json={"expected_hash": "p3"},
    )

    assert created.status_code == 200
    assert created.json()["resulting_hash"] == "p3"
    assert deleted.status_code == 200
    assert deleted.json()["details"]["deleted"] is True
    assert any(
        method == "DELETE" and params == {"expectedHash": "p3"}
        for method, _, params, _ in calls["requests"]
    )


def test_branch_rebase_transplants_commits_oldest_first_with_cas(
    branch_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)

    response = client.post(
        "/api/v1/branches/prod-feature/rebase?env=prod",
        headers={"Idempotency-Key": "rebase-prod-feature"},
        json={
            "target": "prod-target",
            "expected_source_hash": "p2",
            "expected_target_hash": "p1",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["resulting_hash"] == "rebased-hash"
    transplant_requests = [
        body
        for method, path, _, body in calls["requests"]
        if method == "POST" and path.endswith("/history/transplant")
    ]
    assert len(transplant_requests) == 2
    assert [item["isDryRun"] for item in transplant_requests] == [True, False]
    assert transplant_requests[0]["hashesToTransplant"] == ["p2"]
    assert any(
        method == "PUT" and params == {"expectedHash": "p2"}
        for method, _, params, _ in calls["requests"]
    )


def test_premerge_check_requires_successful_run_bound_to_branch_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def graphql(query, variables=None):
        calls.append((query, variables))
        if "V1Jobs" in query:
            return {
                "data": {
                    "repositoriesOrError": {
                        "__typename": "RepositoryConnection",
                        "nodes": [
                            {
                                "name": "checks",
                                "location": {"name": "prod_loc"},
                                "pipelines": [{"name": "branch-tests"}],
                            }
                        ],
                    }
                }
            }
        if "V1LaunchJob" in query:
            return {
                "data": {
                    "launchPipelineExecution": {
                        "__typename": "LaunchRunSuccess",
                        "run": {"runId": "test-run", "status": "STARTED"},
                    }
                }
            }
        return {
            "data": {
                "runOrError": {
                    "__typename": "Run",
                    "runId": "test-run",
                    "status": "SUCCESS",
                    "tags": [
                        {"key": "environment", "value": "prod"},
                        {"key": "phlo/ref", "value": "prod-feature"},
                        {"key": "phlo/branch_hash", "value": "p2"},
                        {"key": "phlo/branch_check", "value": "tests"},
                    ],
                    "repositoryOrigin": {"repositoryLocationName": "prod_loc"},
                }
            }
        }

    monkeypatch.setattr(v1_jobs, "_graphql", graphql)
    result = asyncio.run(
        v1_branch_workflows._run_check_job(
            env="prod",
            target=v1_branch_workflows.EnvironmentTarget(
                dagster_location="prod_loc", nessie_ref="main"
            ),
            branch=v1_branch_workflows.BranchReference(
                env="prod",
                name="prod-feature",
                type="BRANCH",
                hash="p2",
                protected=False,
            ),
            check_name="tests",
            job_name="branch-tests",
        )
    )

    assert result.status == "passed"
    launch = next((query, variables) for query, variables in calls if "V1LaunchJob" in query)
    tags = launch[1]["executionParams"]["executionMetadata"]["tags"]
    assert {tag["key"]: tag["value"] for tag in tags}["phlo/branch_hash"] == "p2"


def test_merge_fails_closed_when_premerge_checks_are_unconfigured(
    branch_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)
    monkeypatch.delenv("PHLO_V1_BRANCH_CHECK_JOBS", raising=False)

    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": "merge-without-check-jobs"},
        json={
            "target": "prod-target",
            "expected_source_hash": "p2",
            "expected_target_hash": "p1",
            "signature_id": "unused-signature",
            "message": "Merge checked branch",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "conflict"
    assert response.json()["details"]["checks"]
    assert {check["status"] for check in response.json()["details"]["checks"]} == {"unavailable"}
    assert not any("/history/merge" in path for path in calls["paths"])


def test_merge_rejects_a_stale_expected_head_before_running_checks(
    branch_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)

    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": "merge-stale-head"},
        json={
            "target": "prod-target",
            "expected_source_hash": "stale-source-hash",
            "expected_target_hash": "p1",
            "signature_id": "unused-signature",
            "message": "Attempt stale merge",
        },
    )

    assert response.status_code == 409
    assert not any("/history/merge" in path for path in calls["paths"])


def test_signed_merge_rechecks_exact_heads_and_records_the_result(
    branch_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)

    async def passing_checks(**kwargs):
        return _passing_checks()

    monkeypatch.setattr(
        v1_branch_workflows,
        "_premerge_checks",
        passing_checks,
    )
    from phlo_api.api import v1_admin_identity

    consumed = []
    events = []

    class FakeAuthority:
        pass

    monkeypatch.setattr(v1_admin_identity, "_authority", FakeAuthority)
    monkeypatch.setattr(
        v1_admin_identity,
        "_consume_signature",
        lambda authority, signature_id, expected: consumed.append(
            (signature_id, expected.action, expected.record_id, expected.record_version)
        ),
    )
    monkeypatch.setattr(
        v1_admin_identity,
        "_action_audit",
        lambda **kwargs: events.append(kwargs),
    )

    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": "signed-merge-prod-feature"},
        json={
            "target": "main",
            "expected_source_hash": "p2",
            "expected_target_hash": "p3",
            "signature_id": "sig-123",
            "message": "Reviewed schema update",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["resulting_hash"] == "merged-hash"
    assert consumed == [("sig-123", "branch.merge", "prod:prod-feature->main", "p2:p3")]
    assert events[0]["signature_id"] == "sig-123"
    assert len([path for path in calls["paths"] if path.endswith("/history/merge")]) == 2


def test_signed_schema_merge_binds_the_signature_to_durable_decisions(
    branch_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)

    async def passing_checks(**kwargs):
        return _passing_checks()

    async def trial_with_decisions(**kwargs):
        return (
            {"wasSuccessful": True, "wasApplied": False, "details": []},
            SimpleNamespace(
                key_merge_modes=({"key": {"elements": ["sales", "orders"]}},),
                cleanup=cleanup,
            ),
            [
                SimpleNamespace(
                    id="decision-1",
                    table_key="sales.orders",
                    columns={"status": "source"},
                )
            ],
        )

    async def verify(**kwargs):
        return None

    async def cleanup(_nessie):
        return None

    monkeypatch.setattr(v1_branch_workflows, "_premerge_checks", passing_checks)
    monkeypatch.setattr(v1_branch_workflows, "_trial_merge_with_resolutions", trial_with_decisions)
    monkeypatch.setattr(v1_branch_workflows, "verify_schema_resolutions", verify)
    from phlo_api.api import v1_admin_identity

    consumed = []

    class FakeAuthority:
        pass

    monkeypatch.setattr(v1_admin_identity, "_authority", FakeAuthority)
    monkeypatch.setattr(
        v1_admin_identity,
        "_consume_signature",
        lambda authority, signature_id, expected: consumed.append(expected.record_version),
    )
    monkeypatch.setattr(v1_admin_identity, "_action_audit", lambda **kwargs: None)

    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": "signed-schema-merge"},
        json={
            "target": "main",
            "expected_source_hash": "p2",
            "expected_target_hash": "p3",
            "incident_id": "incident-1",
            "signature_id": "sig-schema",
            "message": "Resolve the reviewed schema conflict",
        },
    )

    bound = [{"id": "decision-1", "table_key": "sales.orders", "columns": {"status": "source"}}]
    digest = hashlib.sha256(json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert response.status_code == 200
    assert response.json()["details"]["signature_target_version"] == f"p2:p3:schema:{digest}"
    assert consumed == [f"p2:p3:schema:{digest}"]
    applied_request = [
        call for call in calls["requests"] if call[0] == "POST" and call[1].endswith("/history/merge")
    ][-1]
    assert applied_request[3]["isDryRun"] is False
    assert "dryRun" not in applied_request[3]
    assert applied_request[3]["keyMergeModes"] == [{"key": {"elements": ["sales", "orders"]}}]
