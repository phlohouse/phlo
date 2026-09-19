"""Scenario 01 — correct connection.

Proof: the packaged app is wired to the real configured backend — mission
context reports this project's identity, the staging environment, live data
mode, and ready dependencies; the same-origin proxy enforces authentication;
the browser lands on a truthful overview.
"""

from __future__ import annotations


def test_connection(acceptance_stack, page, report, screenshot):
    stack = acceptance_stack

    # --- API-level identity -------------------------------------------------
    context = stack.api_get("/api/observatory/mission/context").json()["data"]
    report.ids["project_id"] = context["project_id"]
    report.ids["environment_id"] = context["environment_id"]

    assert context["project_valid"] is True, context
    assert context["project_id"] == stack.project_name
    assert context["environment_id"] == "staging"
    assert context["data_mode"] == "live"
    assert context["read_ready"] is True
    assert context["control_ready"] is True
    report.passed(
        "mission_context_identity",
        f"project={context['project_id']} env={context['environment_id']} mode={context['data_mode']}",
    )

    dep_status = {d["name"]: d["status"] for d in context["dependencies"]}
    for required in (
        "orchestrator",
        "catalog",
        "durable_state",
        "run_evidence",
        "authentication",
        "authorization",
    ):
        assert dep_status.get(required) == "ready", (required, dep_status)
    report.passed("dependencies_ready", str(dep_status))

    # Every reported dependency must carry a truthful status — no fabricated
    # "ready". A durable audit sink is not part of this deployment's
    # configuration; the report must say so plainly rather than claim ready.
    truthful = {"ready", "unavailable", "unconfigured", "unsupported"}
    assert set(dep_status.values()) <= truthful, dep_status
    if dep_status.get("audit") != "ready":
        report.mark_unsupported(
            "durable_audit_sink",
            f"audit dependency reports {dep_status.get('audit')} — no durable sink configured",
        )

    # --- Proxy authentication ----------------------------------------------
    assert stack.proxied_get("/api/observatory/mission/context").status_code == 200
    anon = stack.proxied_get("/api/observatory/mission/context", token=None)
    assert anon.status_code == 401, anon.status_code
    report.passed("proxy_auth_enforced", "operator 200, anonymous 401")

    # Invalid API paths fail honestly through the proxy (no SPA fallback).
    bogus = stack.proxied_get("/api/observatory/no-such-surface")
    assert bogus.status_code == 404, bogus.status_code
    report.passed("invalid_path_fails", f"bogus API path -> {bogus.status_code}")

    # --- Browser entry through the packaged app -----------------------------
    page.goto(stack.observatory_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("h1", timeout=30000)
    # The overview renders real project identity in the shell footer.
    page.wait_for_function(
        """(name) => document.body.innerText.includes(name)""",
        arg=stack.project_name,
        timeout=30000,
    )
    screenshot(page, "overview")
    report.passed("browser_overview", "packaged app renders live project identity")
