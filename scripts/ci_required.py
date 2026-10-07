"""Opt-in pytest contract for required suites, without changing local skips."""

from __future__ import annotations

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """A successful required suite must execute tests without skips or xfails."""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if exitstatus == 0 and (
        session.testscollected == 0
        or reporter.stats.get("skipped")
        or reporter.stats.get("xfailed")
        or reporter.stats.get("xpassed")
        or not reporter.stats.get("passed")
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter.write_sep("=", "Required suite must run tests without skips or xfails", red=True)
