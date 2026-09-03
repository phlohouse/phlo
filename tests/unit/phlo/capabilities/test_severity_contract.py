"""Tests for the neutral severity/blocking contract (single owner)."""

from __future__ import annotations

from phlo.capabilities import CheckResult, CheckSeverity, is_blocking_severity


def test_severity_blocks_only_error_and_critical() -> None:
    assert CheckSeverity.ERROR.blocking is True
    assert CheckSeverity.CRITICAL.blocking is True
    assert CheckSeverity.WARNING.blocking is False
    assert CheckSeverity.INFO.blocking is False


def test_is_blocking_severity_matches_prior_wap_rule() -> None:
    for value in ("error", "critical"):
        assert is_blocking_severity(value) is True
    for value in ("warning", "info", None, "", "unknown"):
        assert is_blocking_severity(value) is False


def test_normalize_is_case_and_whitespace_insensitive() -> None:
    assert CheckSeverity.normalize("  ERROR ") is CheckSeverity.ERROR
    assert CheckSeverity.normalize("Critical") is CheckSeverity.CRITICAL
    assert CheckSeverity.normalize("warning") is CheckSeverity.WARNING
    assert CheckSeverity.normalize("something-else") is CheckSeverity.INFO
    assert CheckSeverity.normalize(None) is None


def test_check_result_accepts_enum_or_string_severity() -> None:
    enum_result = CheckResult(
        check_name="c", asset_key="a", passed=False, severity=CheckSeverity.ERROR
    )
    str_result = CheckResult(check_name="c", asset_key="a", passed=True, severity="warning")
    assert enum_result.severity.blocking is True
    assert is_blocking_severity(str_result.severity) is False
