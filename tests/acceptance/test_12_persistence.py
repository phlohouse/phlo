"""Scenario 12 — persistence.

Proof: restarting the API and the packaged app preserves pending operation
claims, release receipts, and the audit log; previously recorded runs remain
visible and unknown outcomes stay represented rather than disappearing.
"""

from __future__ import annotations


def test_persistence_across_restart(acceptance_stack, report):
    stack = acceptance_stack

    # Snapshot durable state before the restart.
    claims_db = stack.project_dir / ".phlo" / "state" / "operations.sqlite"
    audit_log = stack.project_dir / ".phlo" / "audit" / "operations.jsonl"
    import sqlite3

    with sqlite3.connect(claims_db) as conn:
        claims_before = sorted(
            conn.execute("SELECT key_hash, operation, target, state FROM operations").fetchall()
        )
    audit_before = audit_log.read_text() if audit_log.exists() else ""
    assert claims_before, "no operation claims recorded by earlier scenarios"
    assert "acceptance-operator" in audit_before
    runs_before = stack.api_get("/api/observatory/runs").json()["items"]
    completed_before = stack.api_get("/api/observatory/mission/releases/completed").json()["data"]

    # Restart the native API and the packaged app.
    stack.restart_service("phlo-api")
    stack.wait_for_service(f"{stack.api_url}/health", name="Phlo API", timeout=300)
    stack.restart_service("observatory")
    stack.wait_for_service(stack.observatory_url + "/", name="Observatory", timeout=300)

    # Claims survive: identical set, still queryable.
    with sqlite3.connect(claims_db) as conn:
        claims_after = sorted(
            conn.execute("SELECT key_hash, operation, target, state FROM operations").fetchall()
        )
    assert claims_after == claims_before
    report.passed("claims_survive_restart", f"{len(claims_after)} claims preserved across restart")

    # Audit log is preserved across restarts (small log, no rotation expected).
    audit_after = audit_log.read_text()
    assert audit_before in audit_after
    report.passed("audit_preserved", f"audit log intact ({len(audit_after)} bytes)")

    # Read surfaces still reflect the same provider truth after restart.
    runs_after = stack.api_get("/api/observatory/runs").json()["items"]
    assert {r.get("run_id") or r.get("id") for r in runs_after} >= {
        r.get("run_id") or r.get("id") for r in runs_before
    }
    completed_after = stack.api_get("/api/observatory/mission/releases/completed").json()["data"]
    assert {c["id"] for c in completed_after} >= {c["id"] for c in completed_before}
    report.passed(
        "runs_and_receipts_survive",
        f"{len(runs_after)} runs, {len(completed_after)} completed releases post-restart",
    )

    # The packaged app still proxies authenticated reads after restart.
    proxied = stack.proxied_get("/api/observatory/mission/context")
    assert proxied.status_code == 200, proxied.status_code
    assert proxied.json()["data"]["project_id"] == stack.project_name
    report.passed("packaged_app_resumes", "proxy serves the same project after restart")
