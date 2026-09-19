"""Fixtures for the Observatory acceptance suite.

The suite runs the ordered scenario chain against one disposable stack —
``tests/acceptance/stack.py`` boots it once per session. Ordering is
load-bearing: ``test_02_empty_project`` must observe the catalog before any
materialization, and later scenarios build on earlier runs' WAP evidence.
Scenario files are named ``test_NN_<scenario>.py`` so pytest's collection
order matches the intended sequence.

Gate: ``PHLO_RUN_OBSERVATORY_ACCEPTANCE=1`` (Docker + browser required).
Preserve the stack after a failure with ``PHLO_KEEP_BUNDLED_STACK=1``.
Artifacts default to ``.tmp/observatory-acceptance``; override with
``PHLO_ACCEPTANCE_ARTIFACT_ROOT``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The scenario tests import the local helpers (stack.py, evidence.py) directly;
# import-mode=importlib means this directory is not automatically a package.

from evidence import AcceptanceReport, repo_commit, sanitized_config_digest
from stack import (
    OPERATOR_TOKEN,
    VIEWER_TOKEN,
    ObservatoryAcceptanceStack,
    acceptance_artifact_root,
    acceptance_enabled,
    bootstrap_observatory_stack,
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip the whole suite unless the acceptance gate is set."""
    if acceptance_enabled():
        return
    skip = pytest.mark.skip(
        reason="Set PHLO_RUN_OBSERVATORY_ACCEPTANCE=1 to run the acceptance suite"
    )
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def acceptance_stack(
    request: pytest.FixtureRequest,
) -> Iterator[ObservatoryAcceptanceStack]:
    """Boot the disposable wap-failure-lab stack once per acceptance session."""
    if not acceptance_enabled():
        pytest.skip("Set PHLO_RUN_OBSERVATORY_ACCEPTANCE=1 to run the acceptance suite")

    stack = bootstrap_observatory_stack(stream_output=True)
    try:
        yield stack
    finally:
        stack.cleanup(force=request.session.testsfailed == 0)


@pytest.fixture(scope="session")
def playwright_browser() -> Iterator:
    """One headless Chromium instance for the session."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def _new_context(browser, token: str | None):
    """A browser context whose every request carries (or omits) the bearer token."""
    return browser.new_context(
        extra_http_headers=({"Authorization": f"Bearer {token}"} if token else {}),
        viewport={"width": 1440, "height": 900},
    )


@pytest.fixture()
def operator_context(playwright_browser) -> Iterator:
    context = _new_context(playwright_browser, OPERATOR_TOKEN)
    try:
        yield context
    finally:
        context.close()


@pytest.fixture()
def viewer_context(playwright_browser) -> Iterator:
    context = _new_context(playwright_browser, VIEWER_TOKEN)
    try:
        yield context
    finally:
        context.close()


@pytest.fixture()
def anonymous_context(playwright_browser) -> Iterator:
    context = _new_context(playwright_browser, None)
    try:
        yield context
    finally:
        context.close()


@pytest.fixture()
def page(operator_context) -> Iterator:
    """An operator-authenticated page on the packaged app."""
    page = operator_context.new_page()
    try:
        yield page
    finally:
        page.close()


def _scenario_name(request: pytest.FixtureRequest) -> str:
    """Derive the scenario id from the test module name (test_NN_<name>.py)."""
    module = Path(str(request.node.fspath)).stem
    return module.removeprefix("test_")


@pytest.fixture()
def report(
    request: pytest.FixtureRequest,
    acceptance_stack: ObservatoryAcceptanceStack,
) -> Iterator[AcceptanceReport]:
    """One acceptance record per scenario, written on teardown.

    The record is written even when the test fails so the run preserves what
    was proven and what was not.
    """
    stack = acceptance_stack
    phlo_yaml = (stack.project_dir / "phlo.yaml").read_text()
    auth_files: dict[str, str] = {}
    auth_dir = stack.project_dir / ".phlo" / "authorization"
    if auth_dir.is_dir():
        for auth_file in sorted(auth_dir.glob("*.yaml")):
            auth_files[auth_file.name] = auth_file.read_text()
    record = AcceptanceReport(
        scenario=_scenario_name(request),
        artifact_root=acceptance_artifact_root(),
        commit=repo_commit(),
        project_id=stack.project_dir.name,
        fixture=f"wap-failure-lab@{stack.fixture_sha}",
        config_digest=sanitized_config_digest(phlo_yaml, stack.env_vars, auth_files),
    )
    yield record
    # A failed pytest marks every unrecorded assertion; finalize() derives
    # the result from the recorded checks either way.
    record.finalize()
    target = record.write()
    print(f"\n[acceptance] wrote {target}")


@pytest.fixture()
def screenshot(acceptance_stack: ObservatoryAcceptanceStack, request: pytest.FixtureRequest):
    """Capture a screenshot artifact into <root>/screenshots/."""
    scenario = _scenario_name(request)

    def capture(page, name: str) -> Path:
        target_dir = acceptance_artifact_root() / "screenshots"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{scenario}-{name}.png"
        page.screenshot(path=str(target), full_page=False)
        return target

    return capture
