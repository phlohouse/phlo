"""Tests for dormant account governance.

Verifies DormancyDetector severity assignment at the warning and maximum
dormancy thresholds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from phlo.compliance.governance.dormant import DormancyDetector, DormancyThreshold


def test_check_dormancy_returns_medium_at_warning_threshold() -> None:
    detector = DormancyDetector(
        DormancyThreshold(
            warning_days=60,
            max_inactive_days=90,
        )
    )
    reference_time = datetime(2026, 4, 16, tzinfo=UTC)
    last_activity = reference_time - timedelta(days=75)

    dormant = detector.check_dormancy(
        principal_subject="user@example.com",
        principal_type="user",
        last_activity=last_activity,
        reference_time=reference_time,
    )

    assert dormant is not None
    assert dormant.days_inactive == 75
    assert dormant.severity == "medium"
    assert dormant.should_review is True


def test_check_dormancy_returns_high_at_max_threshold() -> None:
    detector = DormancyDetector(
        DormancyThreshold(
            warning_days=60,
            max_inactive_days=90,
        )
    )
    reference_time = datetime(2026, 4, 16, tzinfo=UTC)
    last_activity = reference_time - timedelta(days=95)

    dormant = detector.check_dormancy(
        principal_subject="svc:example",
        principal_type="service",
        last_activity=last_activity,
        reference_time=reference_time,
    )

    assert dormant is not None
    assert dormant.severity == "high"


@pytest.mark.parametrize(
    ("inactive_days", "expected_severity"),
    [
        pytest.param(30, None, id="well_within_warning_30d"),
        pytest.param(59, None, id="just_below_warning_59d"),
        pytest.param(60, "medium", id="exactly_at_warning_60d"),
        pytest.param(61, "medium", id="just_above_warning_61d"),
        pytest.param(89, "medium", id="just_below_max_89d"),
        pytest.param(90, "high", id="exactly_at_max_90d"),
        pytest.param(91, "high", id="just_above_max_91d"),
    ],
)
def test_check_dormancy_severity_boundaries(
    inactive_days: int, expected_severity: str | None
) -> None:
    detector = DormancyDetector(
        DormancyThreshold(
            warning_days=60,
            max_inactive_days=90,
        )
    )
    reference_time = datetime(2026, 4, 16, tzinfo=UTC)
    last_activity = reference_time - timedelta(days=inactive_days)

    dormant = detector.check_dormancy(
        principal_subject="user@example.com",
        principal_type="user",
        last_activity=last_activity,
        reference_time=reference_time,
    )

    if expected_severity is None:
        assert dormant is None
    else:
        assert dormant is not None
        assert dormant.days_inactive == inactive_days
        assert dormant.severity == expected_severity
        assert dormant.should_review is True
