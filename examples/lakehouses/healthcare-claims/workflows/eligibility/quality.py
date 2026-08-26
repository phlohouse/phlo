"""Eligibility-domain quality checks: period integrity per member.

Diagnostics mask identifiers: failures report enough to act on without
leaking more member detail than necessary.
"""

from __future__ import annotations

import pandas as pd


def assert_no_overlapping_periods(eligibility: pd.DataFrame) -> None:
    """A member's coverage periods must never overlap."""
    overlaps: list[str] = []
    for member_id, group in eligibility.groupby("member_id"):
        ordered = group.sort_values("effective_start").to_dict("records")
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later["effective_start"] < earlier["effective_end"]:
                overlaps.append(
                    f"{str(member_id)[:3]}...:{earlier['eligibility_key']}+{later['eligibility_key']}"
                )
    if overlaps:
        raise ValueError(f"Overlapping coverage periods: {overlaps[:3]}")
