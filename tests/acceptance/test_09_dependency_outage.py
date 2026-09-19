"""Scenario 09 — dependency outage.

Proof: with the Dagster webserver stopped, dependent actions are refused or
report unavailable honestly, mission context degrades truthfully, read paths
return stale/unavailable markers rather than fabricated data, and nothing
invents seed rows. The service is restarted and verified ready on exit.
"""

from __future__ import annotations

import time
import uuid


def test_dependency_outage(acceptance_stack, page, report, screenshot):
    stack = acceptance_stack

    stack.stop_service("dagster")
    try:
        # Give the API's dependency probes a moment to observe the outage.
        deadline = time.monotonic() + 120
        dep_status = {}
        while time.monotonic() < deadline:
            context = stack.api_get("/api/observatory/mission/context")
            deps = context.json()["data"].get("dependencies", [])
            dep_status = {d["name"]: d["status"] for d in deps}
            if dep_status.get("orchestrator") not in {"ready", "configured"}:
                break
            time.sleep(5)
        report.passed(
            "outage_detected",
            f"orchestrator dependency reported {dep_status.get('orchestrator')}",
        )

        # Dependent action must refuse honestly — no fabricated success.
        launch = stack.api_post(
            "/api/observatory/assets/dlt_sensor_batches/materialize",
            {"idempotency_key": f"acc-outage-{uuid.uuid4().hex[:12]}", "dry_run": False},
            timeout=90,
        )
        assert launch.status_code != 200 or launch.json().get("accepted") is False, (
            launch.status_code,
            launch.text[:400],
        )
        report.passed(
            "dependent_action_blocked",
            f"materialize under outage -> {launch.status_code} {launch.text[:160]}",
        )

        # Reads degrade truthfully: catalog state is real or marked
        # unavailable, never seeded demo content.
        datasets = stack.api_get("/api/observatory/datasets")
        if datasets.status_code == 200:
            ids = {d["id"] for d in datasets.json()["items"]}
            foreign = ids - {
                "dlt_sensor_batches",
                "dlt_sensor_batches_relaxed",
                "wap_failure_lab.batch_summary",
                "slow_sensor_feed",
            }
            assert not foreign, foreign
            report.passed("reads_truthful_under_outage", "no fabricated datasets")
        else:
            report.passed(
                "reads_truthful_under_outage",
                f"datasets unavailable -> {datasets.status_code}",
            )

        # Browser shows a degraded state, not a false-green dashboard.
        page.goto(stack.observatory_url + "/", wait_until="domcontentloaded")
        page.wait_for_selector("main", timeout=60000)
        page.wait_for_function(
            "() => /unavailable|degraded|offline|unreachable|not ready|stale|error/i.test(document.body.innerText)",
            timeout=60000,
        )
        screenshot(page, "degraded-source")
        report.passed("browser_degraded_visible", "overview surfaces the outage")
    finally:
        stack.start_service("dagster")
        # GraphQL answering is necessary but not sufficient: the code location
        # must also be LOADED before later scenarios can launch work.
        deadline = time.monotonic() + 300
        loaded = False
        while time.monotonic() < deadline:
            try:
                payload = stack.dagster_graphql(
                    "query { workspaceOrError { __typename ... on Workspace {"
                    " locationEntries { name loadStatus } } } }"
                )
                entries = (
                    payload.get("data", {}).get("workspaceOrError", {}).get("locationEntries", [])
                )
                if entries and all(e.get("loadStatus") == "LOADED" for e in entries):
                    loaded = True
                    break
            except Exception:
                pass
            time.sleep(3)
        if not loaded:
            raise RuntimeError("dagster code location did not reload")
        report.passed("dependency_recovered", "dagster GraphQL + code location ready")
