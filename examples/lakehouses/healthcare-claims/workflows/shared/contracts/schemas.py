"""Strict contracts for claims, eligibility periods, and providers.

Temporal fields are typed natively (DLT normalizes ISO-8601 strings during
staging). Amounts carry non-negative bounds, and reconciliation rules ride in
the contract as blocking dataframe checks so an unreconciled batch can never
be promoted.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

AMOUNT_MAX = 1_000_000.0
RECONCILIATION_TOLERANCE = 0.01


class ClaimSchema(pa.DataFrameModel):
    """One raw claim version; versions accumulate in the append-only table."""

    claim_version_key: Series[str]
    claim_id: Series[str] = pa.Field(str_matches=r"^(clm-\d{4}|fb-[a-z]+)$")
    version: Series[int] = pa.Field(ge=1)
    member_id: Series[str] = pa.Field(str_matches=r"^mbr-\d{3}$")
    provider_id: Series[str] = pa.Field(str_matches=r"^prv-\d{3}$")
    service_date: Series[datetime]
    procedure_codes: Series[str] = pa.Field(str_contains="|")
    billed_amount: Series[float] = pa.Field(ge=0.0, le=AMOUNT_MAX)
    allowed_amount: Series[float] = pa.Field(ge=0.0, le=AMOUNT_MAX)
    paid_amount: Series[float] = pa.Field(ge=0.0, le=AMOUNT_MAX)

    @pa.dataframe_check
    def allowed_within_billed(self, data: pd.DataFrame) -> pd.Series[bool]:
        """Allowed amounts may not exceed billed amounts beyond tolerance."""
        return data.allowed_amount <= data.billed_amount + RECONCILIATION_TOLERANCE

    @pa.dataframe_check
    def paid_within_allowed(self, data: pd.DataFrame) -> pd.Series[bool]:
        """Paid amounts may not exceed allowed amounts beyond tolerance."""
        return data.paid_amount <= data.allowed_amount + RECONCILIATION_TOLERANCE

    class Config:
        strict = False
        coerce = True


class EligibilityPeriodSchema(pa.DataFrameModel):
    """One coverage period parsed from the pipe-delimited eligibility file."""

    eligibility_key: Series[str] = pa.Field(unique=True)
    member_id: Series[str] = pa.Field(str_matches=r"^mbr-\d{3}$")
    plan: Series[str] = pa.Field(isin=["ppo", "hmo", "medicare"])
    payer: Series[str]
    effective_start: Series[datetime]
    effective_end: Series[datetime]

    class Config:
        strict = False
        coerce = True


class ProviderSchema(pa.DataFrameModel):
    """Provider directory entry merged by provider id."""

    provider_id: Series[str] = pa.Field(unique=True, str_matches=r"^prv-\d{3}$")
    name: Series[str]
    specialty: Series[str]
    npi: Series[str] = pa.Field(str_matches=r"^\d{10}$")
    network_status: Series[str] = pa.Field(isin=["in_network", "out_of_network"])

    class Config:
        strict = False
        coerce = True
