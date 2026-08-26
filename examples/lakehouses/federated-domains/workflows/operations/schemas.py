"""Contracts for the operations-domain incident stream.

Resolution consistency (resolved implies non-negative duration; open implies
null duration) is deliberately NOT enforced here: it is a promotion gate owned
by ``check_resolution_consistency``, so a negative duration fails exactly one
invariant instead of tripping the schema contract.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series


class IncidentSchema(pa.DataFrameModel):
    """One incident row; open incidents carry null resolution fields."""

    incident_id: Series[str] = pa.Field(str_matches=r"^INC-\d{4}$")
    service: Series[str]
    severity: Series[str]
    opened_at: Series[datetime]
    resolved_at: Series[datetime] = pa.Field(nullable=True)
    resolution_minutes: Series[float] = pa.Field(nullable=True)

    class Config:
        strict = False
        coerce = True
