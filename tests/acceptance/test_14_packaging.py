"""Scenario 14 — packaging.

Proof: the built Observatory image serves the SPA shell, deep links, static
assets, and the same-origin API proxy — the same workflows run through the
installed service path rather than a dev server.
"""

from __future__ import annotations


def test_packaged_app(acceptance_stack, page, report, screenshot):
    stack = acceptance_stack

    # The packaged app serves its own shell and deep links resolve.
    for path in ("/", "/datasets", "/releases", "/runs", "/platform"):
        response = stack.proxied_get(path, token=None)
        assert response.status_code == 200, (path, response.status_code)
        assert (
            '<div id="root"' in response.text
            or "__root" in response.text
            or "<html" in response.text
        )
    report.passed("packaged_spa_served", "shell + deep links served by the built image")

    # The same-origin proxy round-trips real upstream answers.
    via_proxy = stack.proxied_get("/api/observatory/datasets")
    direct = stack.api_get("/api/observatory/datasets")
    assert via_proxy.status_code == 200 and direct.status_code == 200
    assert via_proxy.json() == direct.json()
    report.passed("proxy_round_trip", "proxied datasets payload equals the API's own")

    # No internal service URL leaks into the served HTML/JS entrypoint.
    html = stack.proxied_get("/", token=None).text
    assert "host.docker.internal" not in html and "phlo-api:" not in html
    report.passed("no_internal_urls", "served shell references same-origin only")

    # A browser session through the packaged app loads the real app chrome.
    # `main` renders before the queries resolve, so wait for live content —
    # reading innerText right after the shell would race the datasets list.
    page.goto(stack.observatory_url + "/datasets", wait_until="domcontentloaded")
    page.wait_for_selector("main", timeout=30000)
    page.wait_for_function(
        "() => document.body.innerText.includes('dlt_sensor_batches')",
        timeout=60000,
    )
    screenshot(page, "packaged-datasets")
    report.passed("packaged_browser_surface", "built app renders live datasets")
