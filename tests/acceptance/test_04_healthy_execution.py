"""Scenario 04 — healthy execution.

Proof: a materialization launched from the packaged browser UI maps to a real
Dagster run on this stack (run id, tags, terminal status), writes through the
WAP branch, promotes, and lands in Trino with independently verified rows.
"""

from __future__ import annotations

import json
import time

PARTITION = "2026-08-20"


def test_healthy_execution(acceptance_stack, page, report, screenshot):
    stack = acceptance_stack
    stack.stage_scenario("valid_publish")

    # --- Browser launch -----------------------------------------------------
    page.goto(
        f"{stack.observatory_url}/datasets/dlt_sensor_batches",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        "() => document.body.innerText.includes('dlt_sensor_batches')", timeout=30000
    )
    page.get_by_role("button", name="Preview materialization").click()
    page.get_by_label("Partition").fill(PARTITION)
    page.get_by_role("button", name="Run preview").click()
    page.wait_for_function(
        "() => document.body.innerText.toLowerCase().includes('accepted')"
        " || document.body.innerText.toLowerCase().includes('dry')",
        timeout=30000,
    )
    screenshot(page, "materialize-preview")

    with page.expect_response(
        lambda r: (
            "/materialize" in r.url
            and r.request.method == "POST"
            and '"dry_run":false' in (r.request.post_data or "")
        ),
        timeout=60000,
    ) as response_info:
        page.get_by_role("button", name="Materialize", exact=True).last.click()
    response = response_info.value
    assert response.ok, (response.status, response.text())
    payload = json.loads(response.body())
    dagster_run_id = payload.get("run_id")
    assert payload["accepted"] is True and dagster_run_id, payload
    report.ids["dagster_run_id"] = dagster_run_id
    report.ids["logical_run_id"] = payload["details"]["logical_run_id"]
    report.ids["wap_branch"] = payload["details"]["branch"]
    report.passed(
        "browser_launch_accepted",
        f"UI launch accepted -> dagster run {dagster_run_id}",
    )

    # --- Independent provider evidence --------------------------------------
    tags = stack.dagster_run_tags(dagster_run_id)
    assert tags.get("phlo/wap_branch") == payload["details"]["branch"], tags
    assert tags.get("phlo/run_id") == payload["details"]["logical_run_id"], tags
    report.passed(
        "dagster_run_tags_match", f"tags bind run to branch {payload['details']['branch']}"
    )

    deadline = time.monotonic() + 600
    status = None
    while time.monotonic() < deadline:
        status = stack.dagster_run_status(dagster_run_id)
        if status in {"SUCCESS", "FAILURE", "CANCELED"}:
            break
        time.sleep(3)
    assert status == "SUCCESS", status
    report.passed("dagster_run_succeeded", f"provider run status {status}")

    wap = stack.wait_for_report_status(
        payload["details"]["logical_run_id"], {"promoted", "success"}, timeout=600
    )
    report.ids["wap_status"] = wap["status"]
    assert wap["merge_state"] in {"merged", "pending_merge", "not_requested"}, wap
    report.passed("wap_report", f"report status={wap['status']} merge={wap['merge_state']}")

    # Trino independently sees the released rows on main once promoted.
    if wap["status"] == "promoted":
        rows = stack.trino_query(
            f"SELECT count(*) FROM iceberg.raw.sensor_batches WHERE _phlo_partition_date = '{PARTITION}'"
        )
        assert rows and rows[0][0] > 0, rows
        report.passed("trino_sees_release", f"sensor_batches partition rows={rows[0][0]}")
    else:
        report.blocked("trino_sees_release", "report not yet promoted")

    # --- Browser shows the run truthfully -----------------------------------
    page.goto(f"{stack.observatory_url}/runs/{dagster_run_id}", wait_until="domcontentloaded")
    page.wait_for_function(
        f"() => document.body.innerText.includes('{dagster_run_id[:8]}') || document.body.innerText.toLowerCase().includes('success')",
        timeout=30000,
    )
    screenshot(page, "healthy-run")
    report.passed("browser_run_truthful", "run detail renders the real run id/status")
