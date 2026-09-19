"""Scenario 02 — genuine empty project.

Proof: before any materialization the catalog is honestly empty — no tables
in Nessie/Trino, no seeded or example datasets, and the datasets surface
lists only the fixture's real declared assets (defined, never materialized).
"""

from __future__ import annotations

EXPECTED_DATASETS = {
    "dlt_sensor_batches",
    "dlt_sensor_batches_relaxed",
    "wap_failure_lab.batch_summary",
    "slow_sensor_feed",
}


def test_empty_project(acceptance_stack, page, report):
    stack = acceptance_stack

    # Catalog-level emptiness: no user schemas/tables exist yet.
    schemas = {row[0] for row in stack.trino_query("SHOW SCHEMAS FROM iceberg")}
    assert "raw" not in schemas, schemas
    report.passed("catalog_empty", f"iceberg schemas: {sorted(schemas)}")

    # The datasets surface must contain only the fixture's declared data
    # assets — no demo/example seed data. ``slow_sensor_feed`` is a bare
    # orchestration asset (no phlo ingestion registration) and legitimately
    # absent from the data-product surface.
    datasets = stack.api_get("/api/observatory/datasets").json()["items"]
    ids = {d["id"] for d in datasets}
    foreign = ids - EXPECTED_DATASETS
    assert not foreign, f"unexpected seeded datasets: {foreign}"
    assert {
        "dlt_sensor_batches",
        "dlt_sensor_batches_relaxed",
        "wap_failure_lab.batch_summary",
    } <= ids, sorted(ids)
    assert not any(d["candidate"] for d in datasets)
    assert all(d["publication_state"] == "draft" for d in datasets)
    report.ids["datasets"] = sorted(ids)
    report.passed("no_seeded_datasets", f"datasets: {sorted(ids)}")

    # Runs list is empty before any launch.
    runs = stack.api_get("/api/observatory/runs").json()["items"]
    assert runs == [], runs
    report.passed("no_runs", "run list empty")

    # Browser sees the same truthful state.
    page.goto(stack.observatory_url + "/datasets", wait_until="domcontentloaded")
    page.wait_for_selector("table, [role='table'], main", timeout=30000)
    body = page.inner_text("body")
    assert "orders" not in body.lower()  # the old demo dataset name
    report.passed("browser_datasets_honest", "no demo content rendered")
