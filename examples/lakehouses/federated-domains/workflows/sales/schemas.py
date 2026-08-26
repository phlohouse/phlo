"""Contracts for the sales-domain CRM extract.

The stage vocabulary is deliberately NOT enforced here: it is a promotion gate
owned by the ``check_stage_vocabulary`` quality check, so an out-of-pipeline
stage fails exactly one invariant instead of tripping the schema contract.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series


class DealSchema(pa.DataFrameModel):
    """One CRM deal snapshot row merged by ``deal_id``.

    DLT normalizes ISO-8601 source strings to timestamps during staging, so
    temporal columns are typed natively.
    """

    deal_id: Series[str] = pa.Field(str_matches=r"^DL-\d{4}$")
    account_name: Series[str]
    owner: Series[str]
    amount_usd: Series[float] = pa.Field(ge=0)
    stage: Series[str]
    opened_on: Series[datetime]
    stage_updated_at: Series[datetime]

    class Config:
        strict = False
        coerce = True
