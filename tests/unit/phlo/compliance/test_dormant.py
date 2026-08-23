"""Tests for dormant account governance.

Verifies DormancyDetector severity assignment at the warning and maximum
dormancy thresholds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
