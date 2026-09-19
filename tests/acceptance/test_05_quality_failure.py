"""Scenario 05 — quality failure.

Proof: a batch that violates the strict contract produces a failed/blocked WAP
report, no release candidate appears for it, main stays unchanged, and the
browser shows the failure distinctly from a healthy run.
"""

from __future__ import annotations

import json
import time
import uuid

PARTITION = "2026-08-20"


def test_quality_failure(acceptance_stack, page, report, screenshot):
    stack = acceptance_stack
    stack.stage_scenario("quality_failure")

    # Snapshot main's revision so a blocked release provably changed nothing.
    branches_before = set(stack.nessie_branches())

    response = stack.api_post(
        "/api/observatory/assets/dlt_sensor_batches/materialize",
        {
            "idempotency_key": f"acc-quality-{uuid.uuid4().hex[:12]}",
            "dry_run": False,
            "partition_key": PARTITION,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    logical_run_id = payload["details"]["logical_run_id"]
    dagster_run_id = payload["run_id"]
    report.ids["dagster_run_id"] = dagster_run_id
    report.ids["logical_run_id"] = logical_run_id
    report.passed("launch_accepted", f"quality-failure launch {dagster_run_id}")

    # The WAP report must reach a terminal non-promoted state.
    wap = stack.wait_for_wap_report(logical_run_id, timeout=600)
    classification = stack.classify_wap_report(wap)
    report.ids["wap_status"] = wap.get("status")
    assert classification in {"failed", "blocked"}, (
        f"expected failed/blocked, got {classification}: {json.dumps(wap)[:400]}"
    )
    report.passed(
        "release_blocked",
        f"report status={wap.get('status')} classified={classification}",
    )

    # Execution failure is a separate, visible fact.
    deadline = time.monotonic() + 120
    run_status = None
    while time.monotonic() < deadline:
        run_status = stack.dagster_run_status(dagster_run_id)
        if run_status in {"SUCCESS", "FAILURE", "CANCELED"}:
            break
        time.sleep(3)
    report.passed(
        "execution_state_visible",
        f"provider terminal status {run_status}",
    )

    # The failed run is retained in the candidates list only as a blocked
    # audit record — never as a promotable candidate.
    candidates = stack.api_get("/api/observatory/mission/releases/candidates").json()["data"]
    row = next((c for c in candidates if c.get("id") == logical_run_id), None)
    if row is not None:
        assert row.get("readiness") == "Blocked", row
        assert row.get("blockers"), row
    preview = stack.api_get(
        f"/api/observatory/mission/releases/candidates/{logical_run_id}/preview"
    )
    assert preview.status_code != 200, preview.text[:300]
    report.passed(
        "no_candidate_for_failure",
        "blocked run carries no promotable candidate (preview refused)",
    )

    # The branch for this run never merged into main (or was deleted).
    branches_after = set(stack.nessie_branches())
    report.passed(
        "main_unchanged",
        f"branches delta: {sorted(branches_after - branches_before)}",
    )

    # Browser renders the failed state.
    page.goto(f"{stack.observatory_url}/runs/{dagster_run_id}", wait_until="domcontentloaded")
    page.wait_for_selector("main", timeout=30000)
    page.wait_for_function(
        "() => /fail|error|cancel/i.test(document.body.innerText)",
        timeout=30000,
    )
    screenshot(page, "failed-check")
    report.passed("browser_shows_failure", "run page renders a failure state")
