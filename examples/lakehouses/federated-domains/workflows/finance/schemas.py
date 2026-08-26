"""Contracts for the finance-domain invoice stream.

Amount positivity IS enforced here (a data-shape invariant). Deal attribution
is deliberately NOT: it is a cross-domain promotion gate owned by the
``check_known_deals`` quality check, so an unknown deal id fails exactly one
invariant instead of tripping the schema contract.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series


class InvoiceSchema(pa.DataFrameModel):
    """One invoice row; ``deal_id`` attributes revenue to a sales deal."""

    invoice_id: Series[str] = pa.Field(str_matches=r"^INV-\d{4}$")
    customer: Series[str]
    deal_id: Series[str] = pa.Field(str_matches=r"^DL-\d{4}$")
    amount_usd: Series[float] = pa.Field(gt=0)
    issued_on: Series[datetime]
    due_on: Series[datetime]
    paid_on: Series[datetime] = pa.Field(nullable=True)

    class Config:
        strict = False
        coerce = True
