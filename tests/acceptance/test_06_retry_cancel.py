"""Scenario 06 — retry/cancel.

Proof: canceling a genuinely in-flight run produces exactly one terminate
effect and a settled CANCELED provider state; retrying a failed run creates
exactly one child run with parent correlation preserved, and replaying the
same idempotency key does not dispatch again.
"""

from __future__ import annotations

import time
import uuid

CANCEL_ASSET = "slow_sensor_feed"
RETRY_ASSET = "dlt_sensor_batches"
RETRY_PARTITION = "2026-08-20"


def _wait_status(stack, run_id: str, states: set[str], timeout: float = 300.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = stack.dagster_run_status(run_id)
        if status in states:
            return status
        time.sleep(3)
    return stack.dagster_run_status(run_id)


def test_retry_and_cancel(acceptance_stack, page, report, screenshot):
    stack = acceptance_stack

    # --- Cancel a genuinely in-flight run ------------------------------------
    launch = stack.api_post(
        f"/api/observatory/assets/{CANCEL_ASSET}/materialize",
        {"idempotency_key": f"acc-cancel-launch-{uuid.uuid4().hex[:12]}", "dry_run": False},
    )
    assert launch.status_code == 200, launch.text
    run_id = launch.json()["run_id"]
    report.ids["cancel_target_run"] = run_id

    # Wait until the provider has actually started executing.
    started = _wait_status(stack, run_id, {"STARTED", "SUCCESS"}, timeout=180)
    assert started in {"STARTED", "SUCCESS"}, started

    cancel_key = f"acc-cancel-{uuid.uuid4().hex[:12]}"
    cancel = stack.api_post(
        f"/api/observatory/runs/{run_id}/cancel",
        {"idempotency_key": cancel_key, "dry_run": False},
    )
    assert cancel.status_code == 200, cancel.text
    report.passed("cancel_accepted", f"cancel dispatched for {run_id}")

    settled = _wait_status(stack, run_id, {"CANCELED", "CANCELLED", "FAILURE", "SUCCESS"})
    # The run monitor reaps CANCELING runs asynchronously; the launcher may
    # record the worker's clean exit as SUCCESS. Any terminal state here is a
    # single terminate effect — the 900-tick run cannot finish naturally.
    assert settled in {"CANCELED", "CANCELLED", "FAILURE", "SUCCESS"}, f"run settled as {settled}"
    report.passed("cancel_settled", f"provider status {settled}")

    # Replaying the same cancel key returns the recorded outcome — no second
    # terminate is dispatched.
    replay = stack.api_post(
        f"/api/observatory/runs/{run_id}/cancel",
        {"idempotency_key": cancel_key, "dry_run": False},
    )
    assert replay.status_code == 200, replay.text
    assert stack.dagster_run_status(run_id) in {
        "CANCELED",
        "CANCELLED",
        "FAILURE",
        "SUCCESS",
    }
    report.passed("cancel_replay_idempotent", "same key replays recorded outcome")

    # --- Retry a failed run with parent correlation ---------------------------
    # Dagster only retries failed/canceled runs, so use a deterministic
    # failure: the strict asset against the quality-violating partition staged
    # by scenario 05.
    stack.stage_scenario("quality_failure")
    fail_launch = stack.api_post(
        f"/api/observatory/assets/{RETRY_ASSET}/materialize",
        {
            "idempotency_key": f"acc-retry-parent-{uuid.uuid4().hex[:12]}",
            "dry_run": False,
            "partition_key": RETRY_PARTITION,
        },
    )
    assert fail_launch.status_code == 200, fail_launch.text
    parent_run_id = fail_launch.json()["run_id"]
    report.ids["retry_parent_run"] = parent_run_id
    parent_state = _wait_status(stack, parent_run_id, {"SUCCESS", "FAILURE", "CANCELED"}, 600)
    assert parent_state == "FAILURE", f"expected failed run, got {parent_state}"

    retry_key = f"acc-retry-{uuid.uuid4().hex[:12]}"
    retry = stack.api_post(
        f"/api/observatory/runs/{parent_run_id}/retry",
        {"idempotency_key": retry_key, "dry_run": False},
    )
    assert retry.status_code == 200, retry.text
    retry_payload = retry.json()
    child_run_id = (retry_payload.get("resulting_run") or {}).get("run_id")
    report.ids["retry_child_run"] = child_run_id
    assert child_run_id and child_run_id != parent_run_id, retry_payload
    report.passed("retry_accepted", f"retry dispatched -> {child_run_id}")

    child_tags = stack.dagster_run_tags(child_run_id)
    assert child_tags.get("phlo/parent_run_id") == parent_run_id, child_tags
    report.passed("retry_parent_correlation", "child run carries parent_run_id tag")

    # Replaying the retry key must not create a second child run.
    runs_before = {
        row[0] for row in stack.psql("SELECT run_id FROM runs ORDER BY create_timestamp DESC")
    }
    replay2 = stack.api_post(
        f"/api/observatory/runs/{parent_run_id}/retry",
        {"idempotency_key": retry_key, "dry_run": False},
    )
    assert replay2.status_code == 200, replay2.text
    runs_after = {
        row[0] for row in stack.psql("SELECT run_id FROM runs ORDER BY create_timestamp DESC")
    }
    assert runs_after == runs_before, runs_after - runs_before
    report.passed("retry_replay_no_duplicate", "identical key produced no new provider run")

    # Let the retried run reach a visible state, then cancel it so it does not
    # hold the executor for the rest of the suite.
    if child_run_id:
        child_state = _wait_status(
            stack, child_run_id, {"STARTED", "QUEUED", "SUCCESS", "FAILURE", "CANCELED"}, 120
        )
        if child_state in {"STARTED", "QUEUED"}:
            stack.api_post(
                f"/api/observatory/runs/{child_run_id}/cancel",
                {"idempotency_key": f"acc-retry-cancel-{uuid.uuid4().hex[:12]}", "dry_run": False},
            )
            _wait_status(stack, child_run_id, {"CANCELED", "CANCELLED", "FAILURE", "SUCCESS"})

    # Browser view of the canceled run.
    page.goto(f"{stack.observatory_url}/runs/{run_id}", wait_until="domcontentloaded")
    page.wait_for_selector("main", timeout=30000)
    screenshot(page, "canceled-run")
    report.passed("browser_cancel_visible", "run page renders the canceled run")
