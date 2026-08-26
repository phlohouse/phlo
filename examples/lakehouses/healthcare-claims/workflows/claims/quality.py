"""Claims-domain quality checks: versions, reconciliation, temporal validity.

Diagnostics deliberately mask member identifiers (first three characters) so
failure evidence stays useful but non-sensitive.
"""

from __future__ import annotations

import pandas as pd

AMOUNT_TOLERANCE = 0.01


def _mask(member_id: object) -> str:
    return str(member_id)[:3] + "..."


def assert_versions_unique_and_advancing(claims: pd.DataFrame) -> None:
    """A claim id may carry many versions, but never the same version twice."""
    duplicated = claims[claims.duplicated(["claim_id", "version"], keep=False)]
    if not duplicated.empty:
        offenders = sorted(f"{row.claim_id}-v{row.version}" for row in duplicated.itertuples())
        raise ValueError(f"Duplicate claim versions: {offenders[:5]}")


def assert_amount_reconciliation(claims: pd.DataFrame, tolerance: float = AMOUNT_TOLERANCE) -> None:
    """Allowed must not exceed billed; paid must not exceed allowed."""
    over_allowed = claims[claims.allowed_amount > claims.billed_amount + tolerance]
    if not over_allowed.empty:
        offender = over_allowed.iloc[0]
        raise ValueError(
            f"Allowed amount exceeds billed for {_mask(offender.member_id)} "
            f"claim {offender.claim_id}: {offender.allowed_amount} > {offender.billed_amount}"
        )
    over_paid = claims[claims.paid_amount > claims.allowed_amount + tolerance]
    if not over_paid.empty:
        offender = over_paid.iloc[0]
        raise ValueError(
            f"Paid amount exceeds allowed for {_mask(offender.member_id)} "
            f"claim {offender.claim_id}: {offender.paid_amount} > {offender.allowed_amount}"
        )


def assert_service_dates_covered(latest_claims: pd.DataFrame, eligibility: pd.DataFrame) -> None:
    """Every claim's service date must fall inside one of its member's periods.

    Claims for uncovered dates are listed with masked member identifiers so
    operations can route them without exposing protected detail.
    """
    uncovered_rows: list[str] = []
    periods_by_member = {
        str(member): group.to_dict("records") for member, group in eligibility.groupby("member_id")
    }
    for row in latest_claims.to_dict("records"):
        periods = periods_by_member.get(str(row["member_id"]), [])
        covered = any(
            period["effective_start"] <= row["service_date"] <= period["effective_end"]
            for period in periods
        )
        if not covered:
            uncovered_rows.append(
                f"{_mask(row['member_id'])}:{row['claim_id']}@{str(row['service_date'])[:10]}"
            )
    if uncovered_rows:
        raise ValueError(f"Service dates outside any coverage period: {uncovered_rows[:5]}")
