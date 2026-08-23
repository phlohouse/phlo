"""Verify severity mapping for quality results: warn-threshold boundaries,
contract failures as errors, and dbt tag overrides of default severities."""

from __future__ import annotations

from phlo_pandera.severity import (
    severity_for_dbt_test,
    severity_for_pandera_contract,
    severity_for_quality_check,
)


def test_quality_check_warn_threshold_emits_warn_severity() -> None:
    """Verify warn severity when failure fraction meets warn threshold."""

    severity = severity_for_quality_check(passed=False, failure_fraction=0.5, warn_threshold=0.5)
    assert severity == "warn"


def test_quality_check_below_warn_threshold_emits_error_severity() -> None:
    """Verify error severity when failure fraction exceeds warn policy."""

    severity = severity_for_quality_check(passed=False, failure_fraction=0.5, warn_threshold=0.49)
    assert severity == "error"


def test_pandera_contract_failure_emits_error_severity() -> None:
    """Verify Pandera contract failures map to error severity."""

    assert severity_for_pandera_contract(passed=True) is None
    assert severity_for_pandera_contract(passed=False) == "error"


def test_dbt_severity_tag_overrides() -> None:
    """Verify dbt tags override default severity behavior."""

    assert severity_for_dbt_test(test_type="not_null", tags=[]) == "error"
    assert severity_for_dbt_test(test_type="accepted_values", tags=[]) == "warn"
    assert severity_for_dbt_test(test_type="not_null", tags=["warn"]) == "warn"
    assert severity_for_dbt_test(test_type="accepted_values", tags=["blocking"]) == "error"
