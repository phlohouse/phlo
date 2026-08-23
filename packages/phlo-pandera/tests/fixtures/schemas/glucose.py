"""Fixture glucose schemas for CLI tests.

Provides a raw-entry schema and a fact-readings schema with unique entry
identifiers, mirroring a raw-to-fact table pattern for contract-driven CLI
paths.
"""

from pandera.pandas import Field
from phlo_pandera.schemas import PhloSchema


class RawGlucoseEntries(PhloSchema):
    """Raw glucose entries schema fixture."""

    _id: str = Field(unique=True)
    sgv: int = Field(ge=0)
    date: int = Field(ge=0)


class FactGlucoseReadings(PhloSchema):
    """Fact glucose readings schema fixture."""

    entry_id: str = Field(unique=True)
    glucose_mg_dl: int = Field(ge=0)
    reading_timestamp: str
