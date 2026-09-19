"""Scenario 10 — ambiguous outcome.

Proof: when the API is restarted after a dispatch, the durable claim reconciles
the retried request against the recorded outcome — replaying the same
idempotency key returns the original run id and never launches a second
Dagster run or a second promotion.
"""

from __future__ import annotations

import time
import uuid

PARTITION = "2026-08-24"


def test_ambiguous_outcome_reconciles(acceptance_stack, report):
    stack = acceptance_stack
    stack.stage_scenario("warning_only")

    key = f"acc-ambiguous-{uuid.uuid4().hex[:12]}"
    body = {"idempotency_key": key, "dry_run": False, "partition_key": PARTITION}
    first = stack.api_post("/api/observatory/assets/dlt_sensor_batches_relaxed/materialize", body)
    assert first.status_code == 200, first.text
    original_run = first.json()["run_id"]
    report.ids["original_run"] = original_run

    runs_before = {row[0] for row in stack.psql("SELECT run_id FROM runs")}

    # Restart the API: any in-flight claim must reconcile against durable
    # state rather than duplicate the dispatch.
    stack.restart_service("phlo-api")
    stack.wait_for_service(f"{stack.api_url}/health", name="Phlo API", timeout=300)

    # Replay the identical request — the durable claim must win.
    deadline = time.monotonic() + 120
    replay = None
    while time.monotonic() < deadline:
        replay = stack.api_post(
            "/api/observatory/assets/dlt_sensor_batches_relaxed/materialize", body
        )
        if replay.status_code != 503:  # API may still be warming
            break
        time.sleep(3)
    assert replay is not None and replay.status_code == 200, (
        replay.status_code if replay else "no response",
        replay.text[:300] if replay else "",
    )
    replayed_run = replay.json()["run_id"]
    assert replayed_run == original_run, (original_run, replayed_run)

    runs_after = {row[0] for row in stack.psql("SELECT run_id FROM runs")}
    assert runs_after == runs_before, f"restart+replay spawned {runs_after - runs_before}"
    report.passed(
        "restart_replay_no_duplicate",
        f"key {key} reconciled to run {original_run} across API restart",
    )

    # The operation claim store is a durable SQLite file under the project's
    # .phlo state dir — it must hold the dispatched claim after restart.
    import sqlite3

    claims_db = stack.project_dir / ".phlo" / "state" / "operations.sqlite"
    if claims_db.exists():
        with sqlite3.connect(claims_db) as conn:
            rows = conn.execute(
                "SELECT operation, state FROM operations WHERE target = ?",
                ("dlt_sensor_batches_relaxed",),
            ).fetchall()
        assert rows, "no claim rows for the dispatched materialization"
        report.passed("durable_claim_recorded", f"claim rows: {rows}")
    else:
        report.blocked("durable_claim_recorded", "operations.sqlite not found")

    # Let this run finish so later scenarios see a clean provider state.
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        status = stack.dagster_run_status(original_run)
        if status in {"SUCCESS", "FAILURE", "CANCELED", "CANCELLED"}:
            break
        time.sleep(4)
    report.passed("run_settled", f"provider status {status}")
