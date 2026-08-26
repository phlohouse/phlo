"""Contracts shared by the commerce, support, and marketing domains.

Temporal columns are typed natively because DLT normalizes ISO-8601 source
strings to timestamps during staging. Raw email addresses keep their original
casing and plus-suffixes; canonicalization happens in SQL, not at ingest.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series


class SupportTicketSchema(pa.DataFrameModel):
    """Ticket payloads replayed from the support HTTP API."""

    ticket_id: Series[str] = pa.Field(str_matches=r"^TCK-\d+$")
    email: Series[str] = pa.Field(str_matches=r".+@.+\..+")
    subject: Series[str]
    created_at: Series[datetime]
    resolved_at: Series[datetime] | None = pa.Field(nullable=True)

    class Config:
        strict = False
        coerce = True


class MarketingContactSchema(pa.DataFrameModel):
    """Marketing contact captured from forms or imports."""

    email: Series[str] = pa.Field(str_matches=r".+@.+\..+")
    contact_name: Series[str]
    list_segment: Series[str]
    captured_at: Series[datetime]

    class Config:
        strict = False
        coerce = True


class ConsentEventSchema(pa.DataFrameModel):
    """Consent grant or revocation event; latest occurred_at wins per email."""

    event_key: Series[str]
    email: Series[str] = pa.Field(str_matches=r".+@.+\..+")
    consent_status: Series[str] = pa.Field(isin=["granted", "revoked"])
    source: Series[str]
    occurred_at: Series[datetime]

    class Config:
        strict = False
        coerce = True
