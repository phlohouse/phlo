"""Native editor authorization and pre-plan source restrictions."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from phlo_api.api import v1_transform_editor as editor
from phlo_api.candidate_runtime import release_handoff
from phlo_api.main import app
from security_test_support import authenticated_client


@pytest.fixture
def setup_editor(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        '{"sandbox":{"dagster_location":"sandbox","nessie_ref":"main"},"prod":{"dagster_location":"prod","nessie_ref":"prod"}}',
    )
    calls = []
    draft = dict(
        env="sandbox",
        ref="main",
        model="review.inspection",
        path="transforms/inspection.sql",
        sql="SELECT * FROM gold.batches",
        draft_id=str(uuid4()),
        revision=1,
        source_digest="a" * 64,
        workspace_digest="b" * 64,
        revision_digest="c" * 64,
        sql_digest="d" * 64,
        created_at="2026-10-22T00:00:00Z",
    )

    async def workspace(request, env):
        return SimpleNamespace(
            models=[SimpleNamespace(model="review.inspection", asset_id="review/inspection")]
            if env == "sandbox"
            else []
        )

    async def assets(request, env):
        return [SimpleNamespace(id="gold/batches", relation="gold.batches")]

    async def call(method, *args, **kwargs):
        calls.append((method, kwargs))
        if method == "read_model_source":
            return {
                key: draft[key]
                for key in ("model", "path", "sql", "source_digest", "workspace_digest")
            } | {"model_version": "actual-version"}
        if method == "compile_model_draft":
            return dict(
                draft_id=draft["draft_id"],
                revision=1,
                revision_digest="c" * 64,
                ref=draft["ref"],
                status="ready",
                diagnostics=[],
                plan_id="actual-plan",
                compiled_sql="SELECT * FROM iceberg.gold.batches",
                target="iceberg.review.inspection",
                sql_hash="hash",
                desired_version="version",
                materialization_available=False,
            )
        return draft

    monkeypatch.setattr(editor, "v1_transform", workspace)
    monkeypatch.setattr(editor, "_assets", assets)
    monkeypatch.setattr(editor, "_call", call)
    monkeypatch.setattr(editor, "preview_catalog", lambda env, ref: "iceberg")
    monkeypatch.setattr(editor, "resolve_trino_url", lambda: "https://trino-preview:8443")
    return draft, calls


def paths(draft):
    base = "/api/v1/transform/models/review.inspection"
    return base, f"{base}/drafts/{draft['draft_id']}/1/compile?env=sandbox"


def test_source_and_draft_save_use_actual_identity_and_project_authorization(setup_editor):
    draft, calls = setup_editor
    base, _ = paths(draft)
    client = authenticated_client("operator")
    assert client.get(base + "/source?env=sandbox").status_code == 200
    payload = {key: draft[key] for key in ("sql", "source_digest", "workspace_digest")}
    assert client.post(base + "/drafts?env=sandbox", json=payload).status_code == 201
    assert calls[-1][1]["actor"] == "test:operator"
    assert calls[-1][1]["ref"] == "main"
    before = len(calls)
    assert (
        authenticated_client("viewer").post(base + "/drafts?env=sandbox", json=payload).status_code
        == 403
    )
    assert TestClient(app).post(base + "/drafts?env=sandbox", json=payload).status_code == 401
    assert client.get(base + "/source?env=prod").status_code == 404
    assert len(calls) == before


@pytest.mark.parametrize("ref", ["main", "release_test"])
def test_compile_revalidates_source_visibility_and_pins_catalog_revision(
    setup_editor, monkeypatch, ref
):
    draft, calls = setup_editor
    draft["ref"] = ref
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        '{"sandbox":{"dagster_location":"sandbox","nessie_ref":"'
        + ref
        + '"},"prod":{"dagster_location":"prod","nessie_ref":"prod"}}',
    )
    _, url = paths(draft)
    client = authenticated_client("analyst")
    response = client.post(url)
    assert response.status_code == 200, response.text
    assert response.json()["materialization_available"] is False
    assert calls[-1][0] == "compile_model_draft"
    assert calls[-1][1]["required_ref"] == ref
    assert calls[-1][1]["required_catalog"] == "iceberg"
    assert calls[-1][1]["required_endpoint"] == "https://trino-preview:8443"
    assert calls[-1][1]["required_user"] == "phlo_api_preview_sandbox"
    assert calls[-1][1]["revision"] == 1
    assert authenticated_client("viewer").post(url).status_code == 403
    original = editor.enforce_http_operation

    async def revoked(request, manifest, values):
        if values.get("asset_id") == "gold/batches":
            raise HTTPException(403, "Source revoked")
        return await original(request, manifest, values)

    monkeypatch.setattr(editor, "enforce_http_operation", revoked)
    calls.clear()
    assert client.post(url).status_code == 403
    assert all(method != "compile_model_draft" for method, _ in calls)


@pytest.mark.parametrize(
    "sql,code",
    [
        ("SELECT * FROM TABLE(system.query(query => 'DELETE FROM x'))", 422),
        ("WITH hidden AS (SELECT * FROM other.gold.batches) SELECT * FROM hidden", 422),
        ("WITH hidden AS (SELECT * FROM gold.missing) SELECT * FROM hidden", 403),
        ("DELETE FROM gold.batches", 422),
    ],
)
def test_unsafe_or_invisible_sql_never_reaches_engine_plan(setup_editor, sql, code):
    draft, calls = setup_editor
    draft["sql"] = sql
    _, url = paths(draft)
    response = authenticated_client("analyst").post(url)
    assert response.status_code == code, response.text
    assert all(method != "compile_model_draft" for method, _ in calls)


def test_cte_scope_preserves_real_authorized_source():
    editor._validate_draft_sources(
        "WITH batches AS (SELECT * FROM iceberg.gold.batches) SELECT * FROM batches",
        "iceberg",
        {"gold.batches"},
    )


def candidate_mocks(setup_editor, monkeypatch, *, preview_fails=False):
    draft, calls = setup_editor
    original_call = editor._call
    state = {"provisions": 0, "runs": 0, "preview_fails": preview_fails}

    async def call(method, *args, **kwargs):
        if method == "prepare_model_execution":
            state["record"] = {
                "execution_id": kwargs["execution_id"],
                "model": "review.inspection",
                "env": "sandbox",
                "ref": "main",
                "draft_id": draft["draft_id"],
                "revision": 1,
                "revision_digest": draft["revision_digest"],
                "status": "prepared",
                "created_at": draft["created_at"],
                "candidate_ref": None,
                "candidate_catalog": None,
                "report": None,
                "operation_claim": kwargs.get("operation_claim"),
            }
            return state["record"]
        if method == "execute_model_draft":
            state["runs"] += 1
            state["record"].update(
                status="passed",
                candidate_ref=kwargs["candidate_ref"],
                candidate_catalog=kwargs["candidate_catalog"],
                candidate_binding=kwargs["candidate_binding"],
                report={
                    "run_id": "actual-run",
                    "plan_id": "actual-plan",
                    "models": [
                        {
                            "model": "review.inspection",
                            "target": kwargs["candidate_catalog"] + ".review.inspection",
                        }
                    ],
                },
            )
            return dict(state["record"])
        if method == "record_execution_observation":
            state["record"]["candidate_result_hash"] = kwargs["candidate_result_hash"]
            return dict(state["record"])
        if method == "record_execution_preview":
            state["record"].update(
                preview=kwargs["preview"], candidate_snapshot_id=kwargs["preview"]["snapshot_id"]
            )
            return dict(state["record"])
        if method == "read_model_execution":
            return dict(state["record"])
        return await original_call(method, *args, **kwargs)

    async def provision(**kwargs):
        state["provisions"] += 1
        return {
            "candidate_ref": "sandbox-transform-" + kwargs["operation_id"],
            "catalog": "iceberg_candidate_" + kwargs["operation_id"],
        }

    async def verify(binding, *, expected_hash=None):
        return expected_hash or "f" * 64

    async def preview(binding, target, **kwargs):
        if state["preview_fails"]:
            raise HTTPException(504, "Readback timed out")
        assert kwargs["expected_hash"] == "f" * 64
        return {
            "columns": ["batch_id"],
            "column_types": ["varchar"],
            "rows": [{"batch_id": "B01"}],
            "row_count": 1,
            "has_more": False,
            "snapshot_id": "1234",
            "ref_hash": "f" * 64,
        }

    monkeypatch.setattr(editor, "_call", call)
    runtime = SimpleNamespace(
        provision_candidate=provision,
        verify_candidate=verify,
        candidate_connection=lambda binding: {"endpoint": "http://trino:8080", "user": "phlo"},
        preview_candidate=preview,
        release_handoff=release_handoff,
    )

    async def evidence(binding, *, expected_hash):
        try:
            observed = await runtime.verify_candidate(binding, expected_hash=expected_hash)
        except HTTPException:
            observed = None
        return {
            "stale": observed is None,
            "eligible_for_release": observed is not None,
            "reason": None if observed else "Candidate drifted.",
            "base_hash": "e" * 64,
            "current_base_hash": "e" * 64,
            "candidate_hash": expected_hash,
            "observed_candidate_hash": observed,
        }

    runtime.candidate_evidence = evidence
    monkeypatch.setattr(editor, "_runtime", lambda: runtime)
    return draft, state


def test_candidate_success_and_preview_failure_replay_never_duplicate_native_execution(
    setup_editor, monkeypatch
):
    draft, state = candidate_mocks(setup_editor, monkeypatch, preview_fails=True)
    base, _ = paths(draft)
    url = f"{base}/drafts/{draft['draft_id']}/1/executions?env=sandbox"
    payload = {"revision_digest": draft["revision_digest"], "base_hash": "e" * 64}
    client = authenticated_client("operator")
    first = client.post(url, json=payload, headers={"Idempotency-Key": "candidate-once"})
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "passed" and first.json()["preview"] is None
    second = client.post(url, json=payload, headers={"Idempotency-Key": "candidate-once"})
    assert second.status_code == 201 and second.json() == first.json()
    assert state["runs"] == state["provisions"] == 1
    state["preview_fails"] = False
    execution_id = first.json()["execution_id"]
    recovered = client.get(
        f"{base}/drafts/{draft['draft_id']}/1/executions/{execution_id}/preview?env=sandbox"
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["preview"]["snapshot_id"] == "1234"
    assert state["runs"] == 1


def test_candidate_stale_revision_denied_actor_and_catalog_escape_fail_before_provision(
    setup_editor, monkeypatch
):
    draft, state = candidate_mocks(setup_editor, monkeypatch)
    base, _ = paths(draft)
    url = f"{base}/drafts/{draft['draft_id']}/1/executions?env=sandbox"
    payload = {"revision_digest": "d" * 64, "base_hash": "e" * 64}
    client = authenticated_client("operator")
    assert client.post(url, json=payload, headers={"Idempotency-Key": "stale"}).status_code == 409
    payload["revision_digest"] = draft["revision_digest"]
    assert (
        authenticated_client("viewer")
        .post(url, json=payload, headers={"Idempotency-Key": "denied"})
        .status_code
        == 403
    )
    draft["sql"] = "WITH allowed AS (SELECT * FROM iceberg.gold.batches) SELECT * FROM allowed"
    assert client.post(url, json=payload, headers={"Idempotency-Key": "catalog"}).status_code == 422
    assert state["provisions"] == state["runs"] == 0


@pytest.mark.parametrize(
    "failure", [HTTPException(422, "Invalid plan"), RuntimeError("Missing binary")]
)
def test_read_only_compile_failure_allows_a_confirmed_fresh_key_retry(
    setup_editor, monkeypatch, failure
):
    draft, state = candidate_mocks(setup_editor, monkeypatch)
    original = editor.v1_transform_model_compile

    async def unavailable(*args, **kwargs):
        raise failure

    monkeypatch.setattr(editor, "v1_transform_model_compile", unavailable)
    base, _ = paths(draft)
    url = f"{base}/drafts/{draft['draft_id']}/1/executions?env=sandbox"
    payload = {"revision_digest": draft["revision_digest"], "base_hash": "e" * 64}
    client = authenticated_client("operator")
    rejected = client.post(url, json=payload, headers={"Idempotency-Key": "preflight-rejected"})
    assert rejected.status_code == (422 if isinstance(failure, HTTPException) else 503)
    assert rejected.headers["X-Phlo-Mutation-Outcome"] == "rejected-before-write"
    assert state["provisions"] == state["runs"] == 0
    monkeypatch.setattr(editor, "v1_transform_model_compile", original)
    retried = client.post(url, json=payload, headers={"Idempotency-Key": "preflight-recovered"})
    assert retried.status_code == 201, retried.text
    assert retried.json()["status"] == "passed"
    assert state["provisions"] == state["runs"] == 1


def test_unknown_candidate_write_still_fences_fresh_key_retries(setup_editor, monkeypatch):
    draft, state = candidate_mocks(setup_editor, monkeypatch)
    runtime = editor._runtime()
    original = runtime.provision_candidate

    async def response_lost(**kwargs):
        await original(**kwargs)
        raise HTTPException(504, "Candidate fork response lost")

    runtime.provision_candidate = response_lost
    monkeypatch.setattr(editor, "_runtime", lambda: runtime)
    base, _ = paths(draft)
    url = f"{base}/drafts/{draft['draft_id']}/1/executions?env=sandbox"
    payload = {"revision_digest": draft["revision_digest"], "base_hash": "e" * 64}
    client = authenticated_client("operator")
    first = client.post(url, json=payload, headers={"Idempotency-Key": "write-uncertain"})
    assert first.status_code == 504
    assert "X-Phlo-Mutation-Outcome" not in first.headers
    second = client.post(url, json=payload, headers={"Idempotency-Key": "write-not-retried"})
    assert second.status_code == 409
    assert "operation_unknown" in second.json()["error"]["message"]
    assert state["provisions"] == 1 and state["runs"] == 0


def test_revision_history_compare_contracts_preserve_identity_and_current_asset_access(
    setup_editor, monkeypatch
):
    draft, calls = setup_editor
    original_call = editor._call

    async def call(method, *args, **kwargs):
        calls.append((method, kwargs))
        if method == "list_model_drafts":
            return {
                "items": [
                    {
                        key: draft[key]
                        for key in (
                            "draft_id",
                            "revision",
                            "model",
                            "env",
                            "ref",
                            "path",
                            "created_at",
                            "source_digest",
                            "revision_digest",
                            "sql_digest",
                        )
                    }
                ],
                "total": 1,
                "truncated": False,
            }
        if method == "compare_model_draft":
            return {
                "draft_id": draft["draft_id"],
                "revision": 1,
                "revision_digest": draft["revision_digest"],
                "source_digest": draft["source_digest"],
                "source_path": draft["path"],
                "baseline_matches_saved_source": True,
                "added_lines": 0,
                "removed_lines": 0,
                "unified_diff": [],
            }
        return await original_call(method, *args, **kwargs)

    monkeypatch.setattr(editor, "_call", call)
    base, _ = paths(draft)
    client = authenticated_client("operator")
    history = client.get(base + "/drafts?env=sandbox")
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert "sql" not in history.json()["items"][0] and "actor" not in history.json()["items"][0]
    comparison = client.get(f"{base}/drafts/{draft['draft_id']}/1/compare?env=sandbox")
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["base_kind"] == "workspace_source"
    assert calls[-1][1]["actor"] == "test:operator" and calls[-1][1]["ref"] == "main"
    assert client.get(base + "/drafts?env=prod").status_code == 404
    before = len(calls)

    async def deny_asset(request, manifest, values):
        raise HTTPException(403, "Source asset revoked")

    monkeypatch.setattr(editor, "enforce_http_operation", deny_asset)
    assert client.get(base + "/drafts?env=sandbox").status_code == 403
    assert client.get(f"{base}/drafts/{draft['draft_id']}/1/compare?env=sandbox").status_code == 403
    assert len(calls) == before


def test_execution_history_retains_native_evidence_but_withholds_unverifiable_rows(
    setup_editor, monkeypatch
):
    draft, state = candidate_mocks(setup_editor, monkeypatch)
    base, _ = paths(draft)
    endpoint = f"{base}/drafts/{draft['draft_id']}/1/executions?env=sandbox"
    client = authenticated_client("operator")
    created = client.post(
        endpoint,
        json={"revision_digest": draft["revision_digest"], "base_hash": "e" * 64},
        headers={"Idempotency-Key": "history-retained"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["preview"]["rows"]
    original = editor._call

    async def call(method, *args, **kwargs):
        if method == "list_model_executions":
            return [dict(state["record"])]
        return await original(method, *args, **kwargs)

    runtime = editor._runtime()

    async def changed(binding, *, expected_hash=None):
        raise HTTPException(409, "Candidate head no longer matches")

    original_verify = runtime.verify_candidate
    runtime.verify_candidate = changed
    monkeypatch.setattr(editor, "_call", call)
    monkeypatch.setattr(editor, "_runtime", lambda: runtime)
    history = client.get(endpoint)
    assert history.status_code == 200, history.text
    item = history.json()["items"][0]
    assert item["report"]["run_id"] == "actual-run" and item["status"] == "passed"
    assert item["preview"] is None
    assert state["record"]["preview"]["rows"], "Listing history must not rewrite recorded evidence."
    runtime.verify_candidate = original_verify
    complete = client.get(endpoint + "&include_archived=true")
    assert complete.status_code == 200, complete.text
    assert complete.json()["items"][0]["preview"]["rows"]
    assert complete.json()["items"][0]["evidence"]["eligible_for_release"] is True
    handoff_url = f"{base}/drafts/{draft['draft_id']}/1/handoff?env=sandbox&execution_id={created.json()['execution_id']}"
    handoff = client.get(handoff_url)
    assert handoff.status_code == 200, handoff.text
    assert handoff.json()["candidate_evidence"]["eligible_for_release"] is True

    async def no_sources(request, env):
        return []

    monkeypatch.setattr(editor, "_assets", no_sources)
    revoked = client.get(endpoint)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["items"][0]["preview"] is None
    assert revoked.json()["items"][0]["report"]["run_id"] == "actual-run"
    assert revoked.json()["items"][0]["evidence"]["eligible_for_release"] is False
    denied_handoff = client.get(handoff_url)
    assert denied_handoff.status_code == 200, denied_handoff.text
    assert denied_handoff.json()["candidate_evidence"]["eligible_for_release"] is False
    assert "source" in denied_handoff.json()["candidate_evidence"]["reason"].lower()


def test_reconcile_preserved_success_resolves_unknown_claim_without_native_retry(
    setup_editor, monkeypatch
):
    draft, state = candidate_mocks(setup_editor, monkeypatch)
    original_audit = editor.audit_operation
    monkeypatch.setattr(
        editor, "audit_operation", lambda **kwargs: (_ for _ in ()).throw(OSError("audit offline"))
    )
    base, _ = paths(draft)
    path = f"{base}/drafts/{draft['draft_id']}/1/executions"
    client = authenticated_client("operator")
    payload = {"revision_digest": draft["revision_digest"], "base_hash": "e" * 64}
    headers = {"Idempotency-Key": "lost-success-response"}
    failed = client.post(path + "?env=sandbox", json=payload, headers=headers)
    assert failed.status_code == 500 and state["runs"] == 1
    assert client.post(path + "?env=sandbox", json=payload, headers=headers).status_code == 409
    original_call = editor._call

    async def call(method, *args, **kwargs):
        if method == "reconcile_model_execution":
            assert kwargs["preview"]["ref_hash"] == state["record"]["candidate_result_hash"]
            return {**state["record"], "preview": kwargs["preview"]}
        return await original_call(method, *args, **kwargs)

    monkeypatch.setattr(editor, "_call", call)
    monkeypatch.setattr(editor, "audit_operation", original_audit)
    url = path + f"/{state['record']['execution_id']}/reconcile?env=sandbox"
    assert authenticated_client("viewer").post(url).status_code == 403
    reconciled = client.post(url)
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["reconciliation"]["resolution"] == "verified_success"
    replayed = client.post(path + "?env=sandbox", json=payload, headers=headers)
    assert replayed.status_code == 201, replayed.text
    assert replayed.json()["execution_id"] == state["record"]["execution_id"]
    assert state["runs"] == state["provisions"] == 1


def test_unknown_execution_without_success_evidence_remains_fenced_on_reconcile(
    setup_editor, monkeypatch
):
    draft, state = candidate_mocks(setup_editor, monkeypatch)
    base, _ = paths(draft)
    path = f"{base}/drafts/{draft['draft_id']}/1/executions"
    client = authenticated_client("operator")
    created = client.post(
        path + "?env=sandbox",
        json={"revision_digest": draft["revision_digest"], "base_hash": "e" * 64},
        headers={"Idempotency-Key": "native-uncertain"},
    ).json()
    state["record"].update(status="unknown", report=None, candidate_result_hash=None, preview=None)
    result = client.post(path + f"/{created['execution_id']}/reconcile?env=sandbox")
    assert result.status_code == 200, result.text
    assert (
        result.json()["status"] == "unknown"
        and result.json()["reconciliation"]["resolution"] == "unresolved"
    )
    assert result.json()["preview"] is None and state["runs"] == state["provisions"] == 1


def test_transform_handoff_exports_saved_sql_to_source_review_without_publication(
    setup_editor, monkeypatch
):
    import hashlib

    draft, _ = setup_editor
    draft["project_path"] = "workflows/transforms/native/transforms/inspection.sql"
    base, _ = paths(draft)
    monkeypatch.setattr(
        editor, "_runtime", lambda: SimpleNamespace(release_handoff=release_handoff)
    )
    response = authenticated_client("operator").get(
        f"{base}/drafts/{draft['draft_id']}/1/handoff?env=sandbox"
    )
    assert response.status_code == 200, response.text
    handoff = response.json()
    assert handoff["files"] == [
        {
            "path": draft["project_path"],
            "content": draft["sql"],
            "sha256": hashlib.sha256(draft["sql"].encode()).hexdigest(),
        }
    ]
    assert handoff["candidate_evidence"] is None
    assert handoff["source_review_required"] is True and handoff["automatic_promotion"] is False
    assert (
        handoff["release"]["requires_signature"] is True and handoff["release"]["merge_url"] is None
    )


def test_history_and_handoff_reject_missing_pinned_snapshot_despite_verified_head(
    setup_editor, monkeypatch
):
    draft, state = candidate_mocks(setup_editor, monkeypatch)
    base, _ = paths(draft)
    path = f"{base}/drafts/{draft['draft_id']}/1"
    client = authenticated_client("operator")
    created = client.post(
        path + "/executions?env=sandbox",
        json={"revision_digest": draft["revision_digest"], "base_hash": "e" * 64},
        headers={"Idempotency-Key": "pinned-snapshot-disappeared"},
    )
    assert created.status_code == 201, created.text

    async def missing_snapshot(binding, target, **kwargs):
        assert target.endswith(".review.inspection")
        assert kwargs["expected_hash"] == "f" * 64
        assert kwargs["expected_snapshot_id"] == "1234"
        assert kwargs["require_current_base"] is False
        raise HTTPException(409, "Pinned table metadata no longer exists")

    editor._runtime().preview_candidate = missing_snapshot
    detail = client.get(path + f"/executions/{created.json()['execution_id']}?env=sandbox")
    assert detail.status_code == 200, detail.text
    evidence = detail.json()["evidence"]
    assert evidence["observed_candidate_hash"] == "f" * 64
    assert evidence["eligible_for_release"] is False and evidence["stale"] is True
    assert detail.json()["preview"] is None and detail.json()["report"]["run_id"] == "actual-run"
    handoff = client.get(
        path + f"/handoff?env=sandbox&execution_id={created.json()['execution_id']}"
    )
    assert handoff.status_code == 200, handoff.text
    assert handoff.json()["candidate_evidence"]["eligible_for_release"] is False
    assert "snapshot" in handoff.json()["release"]["blocked_reason"]
    assert state["record"]["preview"]["rows"] and state["runs"] == 1
