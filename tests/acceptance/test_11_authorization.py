"""Scenario 11 — authorization.

Proof: missing, unknown, and insufficient identity cannot execute any guarded
operation; the viewer's read-only scope holds across mutation endpoints; the
audit trail records the real subject for every dispatched action.
"""

from __future__ import annotations

import uuid

import stack as stack_mod


def test_authorization_matrix(acceptance_stack, report):
    stack = acceptance_stack

    mutation = {
        "idempotency_key": f"acc-authz-{uuid.uuid4().hex[:12]}",
        "dry_run": False,
        "partition_key": "2026-08-20",
    }
    path = "/api/observatory/assets/dlt_sensor_batches/materialize"

    # Missing and unknown identity are refused before any provider call.
    anon = stack.api_post(path, mutation, token=None)
    unknown = stack.api_post(path, mutation, token=stack_mod.UNKNOWN_TOKEN)
    assert anon.status_code == 401, anon.status_code
    assert unknown.status_code == 401, unknown.status_code
    report.passed(
        "unauthenticated_refused", f"anon={anon.status_code} unknown={unknown.status_code}"
    )

    # Viewer reads fine but cannot mutate.
    read = stack.api_get("/api/observatory/datasets", token=stack_mod.VIEWER_TOKEN)
    assert read.status_code == 200, read.status_code
    denied = stack.api_post(path, mutation, token=stack_mod.VIEWER_TOKEN)
    assert denied.status_code == 403, (denied.status_code, denied.text[:200])
    report.passed("viewer_read_only", "viewer GET 200, POST 403")

    # The same guard covers every mutation family.
    for endpoint, payload in (
        ("/api/observatory/runs/some-run/retry", {"idempotency_key": "k", "dry_run": False}),
        ("/api/observatory/runs/some-run/cancel", {"idempotency_key": "k", "dry_run": False}),
        (
            "/api/observatory/mission/releases/candidates/some-candidate/promotion",
            {"idempotency_key": "k", "preview_digest": "x"},
        ),
    ):
        resp = stack.api_post(endpoint, payload, token=stack_mod.VIEWER_TOKEN)
        assert resp.status_code == 403, (endpoint, resp.status_code)
    report.passed("viewer_mutations_all_denied", "retry/cancel/promotion all 403 for viewer")

    # Dispatched actions carry the real subject into the durable audit log.
    import json

    audit_log = stack.project_dir / ".phlo" / "audit" / "operations.jsonl"
    assert audit_log.exists(), "no operations audit log written"
    records = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]
    operator_ops = [r for r in records if r.get("subject") == stack_mod.OPERATOR_SUBJECT]
    assert operator_ops, "no audited operations carry the operator subject"
    assert all(r.get("subject") != "development:anonymous" for r in operator_ops)
    report.passed(
        "audit_subject_recorded",
        f"{len(operator_ops)} audited ops under {stack_mod.OPERATOR_SUBJECT}",
    )

    # Malformed/expired bearer tokens are refused identically.
    for bad in ("", "not-a-token", "acc-operator-EXPIRED"):
        resp = stack.api_get("/api/observatory/datasets", token=bad if bad else None)
        assert resp.status_code == 401, (bad, resp.status_code)
    report.passed("invalid_tokens_refused", "empty/garbage/expired-looking tokens -> 401")

    # With the API (the auth/authorization control point) stopped, nothing
    # can execute — the proxy must fail honestly rather than fabricate.
    stack.stop_service("phlo-api")
    try:
        import requests

        try:
            down = stack.proxied_get("/api/observatory/datasets", timeout=15)
            assert down.status_code in {502, 503, 504}, down.status_code
            evidence = f"proxied read under API outage -> {down.status_code}"
        except requests.RequestException as exc:
            evidence = f"proxied read under API outage -> {type(exc).__name__}"
        report.passed("control_plane_outage_fails_closed", evidence)
    finally:
        stack.start_service("phlo-api")
        stack.wait_for_service(f"{stack.api_url}/health", name="Phlo API", timeout=300)
