"""Verify severity mapping for quality results: warn-threshold boundaries,
contract failures as errors, and dbt tag overrides of default severities."""

from __future__ import annotations

import pytest

from phlo_pandera.severity import (
    severity_for_dbt_test,
    severity_for_pandera_contract,
    severity_for_quality_check,
)


@pytest.mark.parametrize(
    ("warn_threshold", "expected"),
    [(0.5, "warn"), (0.49, "error")],
    ids=["at-warn-threshold", "above-warn-threshold"],
)
def test_quality_check_severity_respects_warn_threshold(
    warn_threshold: float, expected: str
) -> None:
    severity = severity_for_quality_check(
        passed=False, failure_fraction=0.5, warn_threshold=warn_threshold
    )
    assert severity == expected


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
