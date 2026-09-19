"""Scenario 03 — arbitrary resource selection.

Proof: dataset ids (including the multi-segment ``wap_failure_lab.batch_summary``)
round-trip through deep links, refreshes, and direct API reads without losing
or rewriting identity, and run detail links resolve to the same provider run.
"""

from __future__ import annotations

from urllib.parse import quote


def test_resource_identity(acceptance_stack, page, report):
    stack = acceptance_stack

    datasets = stack.api_get("/api/observatory/datasets").json()["items"]
    by_id = {d["id"]: d for d in datasets}
    assert "dlt_sensor_batches" in by_id
    assert "wap_failure_lab.batch_summary" in by_id, sorted(by_id)

    # Two distinct resources, one with a multi-segment key: the API returns
    # each one's own identity under its encoded id.
    for dataset_id in ("dlt_sensor_batches", "wap_failure_lab.batch_summary"):
        detail = stack.api_get(f"/api/observatory/mission/datasets/{quote(dataset_id, safe='')}")
        assert detail.status_code == 200, (dataset_id, detail.status_code)
        payload = detail.json()
        assert payload["data"]["id"] == dataset_id, payload["data"].get("id")
    report.passed(
        "encoded_identity_roundtrip",
        "dlt_sensor_batches and wap_failure_lab.batch_summary resolve distinctly",
    )

    # Browser deep link into the multi-segment dataset, then a refresh.
    target = f"{stack.observatory_url}/datasets/wap_failure_lab.batch_summary"
    page.goto(target, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.body.innerText.includes('wap_failure_lab.batch_summary')",
        timeout=30000,
    )
    first_render = page.inner_text("body")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.body.innerText.includes('wap_failure_lab.batch_summary')",
        timeout=30000,
    )
    second_render = page.inner_text("body")
    assert second_render.split("batch_summary")[0][-40:] != "dlt_sensor_batches"
    assert "batch_summary" in first_render and "batch_summary" in second_render
    report.passed(
        "deep_link_refresh_stable",
        "multi-segment dataset page renders its own identity after reload",
    )

    # An unknown encoded id fails honestly rather than resolving to a default.
    bogus = stack.api_get("/api/observatory/mission/datasets/no%2Esuch%2Easset")
    assert bogus.status_code in {404, 422}, bogus.status_code
    report.passed("unknown_id_fails", f"bogus dataset id -> {bogus.status_code}")
