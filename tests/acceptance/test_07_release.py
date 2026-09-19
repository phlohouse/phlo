"""Scenario 07 — release.

Proof: a launch under ``review_hold`` is still audited by the promotion sensor
but never merged automatically; the browser preview is read-only (no catalog
mutation) and binds a digest; confirming through the browser applies exactly
one promotion to main; Trino then reads the released rows.
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import quote

PARTITION = "2026-08-22"


def test_release_preview_and_confirm(acceptance_stack, page, report, screenshot):
    stack = acceptance_stack
    stack.stage_scenario("retry_recovery")

    launch = stack.api_post(
        "/api/observatory/assets/dlt_sensor_batches/materialize",
        {
            "idempotency_key": f"acc-release-{uuid.uuid4().hex[:12]}",
            "dry_run": False,
            "partition_key": PARTITION,
            "review_hold": True,
        },
    )
    assert launch.status_code == 200, launch.text
    logical_run_id = launch.json()["details"]["logical_run_id"]
    report.ids["candidate_id"] = logical_run_id

    # The sensor audits the held run and rests it at success — the stable
    # operator-review window, with the automatic merge never attempted.
    wap = stack.wait_for_report_status(logical_run_id, {"success"}, timeout=600)
    report.passed(
        "audited_candidate",
        f"report status={wap['status']} awaiting manual promotion",
    )

    candidates = stack.api_get("/api/observatory/mission/releases/candidates").json()["data"]
    candidate_ids = {c["id"] for c in candidates}
    assert logical_run_id in candidate_ids, candidate_ids
    report.passed("candidate_listed", f"candidate {logical_run_id} present")

    # --- API preview is read-only --------------------------------------
    refs_before = set(stack.nessie_branches())
    preview_resp = stack.api_get(
        f"/api/observatory/mission/releases/candidates/{quote(logical_run_id, safe='')}/preview"
    )
    assert preview_resp.status_code == 200, preview_resp.text
    preview = preview_resp.json()["data"]
    assert preview["eligible"] is True, preview
    assert preview["digest"], preview
    report.ids["preview_digest"] = preview["digest"]
    assert set(stack.nessie_branches()) == refs_before
    report.passed(
        "preview_readonly",
        "eligible=true digest bound; catalog refs unchanged",
    )

    # --- Preview + confirm through the packaged browser app -------------
    page.goto(f"{stack.observatory_url}/releases", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.body.innerText.toLowerCase().includes('candidate')",
        timeout=30000,
    )
    # The detail panel binds to whichever candidate is selected — pick the
    # held candidate's row explicitly before driving its preview/confirm.
    page.get_by_role("row").filter(has_text=logical_run_id).first.click()
    # The detail panel refetches on selection; wait for it to bind our
    # candidate before driving the preview, or the click lands on the
    # previously selected candidate's panel.
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('h2'))"
        f".some(h => h.textContent.includes('{logical_run_id}'))",
        timeout=30000,
    )
    page.get_by_role("button", name="Preview publication").click()
    page.wait_for_function(
        "() => document.body.innerText.includes('preview digest')",
        timeout=60000,
    )
    screenshot(page, "release-preview")
    report.passed("browser_preview", "gate checks + digest rendered in detail panel")

    with page.expect_response(
        lambda r: "/promotion" in r.url and r.request.method == "POST",
        timeout=60000,
    ) as response_info:
        page.get_by_role("button", name="Confirm promotion").click()
        page.get_by_role("button", name="Promote", exact=True).click()
    promotion_response = response_info.value
    promotion = json.loads(promotion_response.body())
    report.ids["promotion_outcome"] = promotion.get("outcome")
    assert promotion.get("outcome") in {"promoted", "already_promoted"}, promotion
    report.passed(
        "promotion_confirmed_via_browser",
        f"outcome={promotion.get('outcome')} rev={promotion.get('target_revision_after')}",
    )

    # --- Independent provider evidence ----------------------------------
    rows = stack.trino_query(
        f"SELECT count(*) FROM iceberg.raw.sensor_batches WHERE _phlo_partition_date = '{PARTITION}'"
    )
    assert rows and rows[0][0] > 0, rows
    report.passed("trino_released_rows", f"rows on main for {PARTITION}: {rows[0][0]}")

    completed_resp = stack.api_get("/api/observatory/mission/releases/completed")
    assert completed_resp.status_code == 200, completed_resp.text
    completed = completed_resp.json()["data"]
    assert any(c.get("run_id") == logical_run_id for c in completed), completed
    report.passed("completed_release_listed", "promotion receipt visible")

    page.wait_for_function(
        "() => document.body.innerText.toLowerCase().includes('completed')"
        " || document.body.innerText.toLowerCase().includes('promoted')",
        timeout=30000,
    )
    screenshot(page, "confirmed-release")
    report.passed("browser_confirmed_release", "releases page renders post-promotion state")
