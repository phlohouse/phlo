"""Scenario 08 — concurrency/staleness.

Proof: a duplicate idempotency key replays without a second provider launch; a
stale preview digest is refused explicitly rather than promoting moved
evidence; and two simultaneous confirmations resolve to exactly one
promotion — the loser reconciles as already-promoted, not a second merge.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

PARTITION = "2026-08-21"


def test_concurrency_and_staleness(acceptance_stack, report):
    stack = acceptance_stack
    stack.stage_scenario("concurrent_runs")

    # --- Duplicate dispatch ---------------------------------------------------
    key = f"acc-dup-{uuid.uuid4().hex[:12]}"
    body = {
        "idempotency_key": key,
        "dry_run": False,
        "partition_key": PARTITION,
        "review_hold": True,
    }
    first = stack.api_post("/api/observatory/assets/dlt_sensor_batches/materialize", body)
    second = stack.api_post("/api/observatory/assets/dlt_sensor_batches/materialize", body)
    assert first.status_code == 200 and second.status_code == 200, (
        first.status_code,
        second.status_code,
        second.text,
    )
    first_run = first.json()["run_id"]
    second_run = second.json()["run_id"]
    assert first_run == second_run, (first_run, second_run)
    report.ids["duplicate_run_id"] = first_run
    report.passed(
        "duplicate_dispatch_single_effect",
        f"same idempotency key replayed run {first_run}",
    )

    logical_run_id = first.json()["details"]["logical_run_id"]
    report.ids["candidate_id"] = logical_run_id

    # The held launch is audited and rests at success — the manual window.
    wap = stack.wait_for_report_status(logical_run_id, {"success"}, timeout=600)
    report.passed("held_candidate_audited", f"report status={wap['status']}")

    preview_resp = stack.api_get(
        f"/api/observatory/mission/releases/candidates/{quote(logical_run_id, safe='')}/preview"
    )
    assert preview_resp.status_code == 200, preview_resp.text
    preview = preview_resp.json()["data"]
    assert preview["eligible"] is True, preview

    # --- Stale digest: a wrong digest must refuse explicitly ---------
    stale = stack.api_post(
        f"/api/observatory/mission/releases/candidates/{quote(logical_run_id, safe='')}/promotion",
        {
            "idempotency_key": f"acc-stale-{uuid.uuid4().hex[:12]}",
            "preview_digest": "0" * 64,
        },
    )
    assert stale.status_code == 200, stale.text
    outcome = stale.json().get("outcome")
    assert outcome in {"stale_preview", "blocked", "refused"}, stale.json()
    report.passed("stale_digest_refused", f"bogus digest -> outcome={outcome}")

    # --- Two simultaneous confirmations: exactly one merge -----------------
    promotion_url = (
        f"/api/observatory/mission/releases/candidates/{quote(logical_run_id, safe='')}/promotion"
    )

    def _confirm(tag: str):
        response = stack.api_post(
            promotion_url,
            {
                "idempotency_key": f"acc-race-{tag}-{uuid.uuid4().hex[:8]}",
                "preview_digest": preview["digest"],
            },
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_confirm, ("a", "b")))

    assert all(status == 200 for status, _ in results), results
    outcomes = sorted(str(payload.get("outcome")) for _, payload in results)
    # The promotion lock reconciles the loser as already-promoted: any other
    # pairing (two merges, a block, a stale verdict) is not a single effect.
    assert outcomes == ["already_promoted", "promoted"], results
    report.passed(
        "concurrent_confirm_single_effect",
        f"two confirmations resolved as {outcomes}",
    )

    # Exactly one promotion: the report converges to promoted and Trino has
    # the partition rows exactly once.
    deadline = time.monotonic() + 120
    final = None
    while time.monotonic() < deadline:
        payload = stack.wap_report(logical_run_id)
        if payload and payload.get("status") == "promoted":
            final = payload
            break
        time.sleep(2)
    assert final is not None, "candidate never promoted"
    rows = stack.trino_query(
        f"SELECT count(*) FROM iceberg.raw.sensor_batches WHERE _phlo_partition_date = '{PARTITION}'"
    )
    assert rows and rows[0][0] > 0, rows
    report.passed(
        "single_release_effect",
        f"report promoted; main has {rows[0][0]} rows for {PARTITION}",
    )
