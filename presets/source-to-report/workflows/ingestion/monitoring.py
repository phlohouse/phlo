"""Ingestion assets for the monitoring pack.

Three assets, in the order the lakehouse builds them:

* ``air_readings``: hourly measurements from the generated source pull;
* ``monitoring_notes``: the published monthly notes, kept whole;
* ``note_claims``: the figures extracted from those notes, with their line.

They are ordinary Phlo dlt assets. The orchestrator runs them with
``phlo materialize air_readings``; the local runner calls the same functions
from ``source_to_report.ingest``. The decorator returns the source function
unchanged, so both paths execute identical code.
"""

from __future__ import annotations

import dlt
import phlo
from phlo.contracts import Consumer

from source_to_report.domain.extraction import extract_claims
from source_to_report.domain.sources import read_notes, read_readings
from source_to_report.ingest import contract_columns
from source_to_report.paths import data_dir
from workflows.schemas.monitoring import ClaimSchema, NoteSchema, ReadingSchema

CONSUMERS = [
    Consumer(name="air-quality", usage="monthly station brief and published-figure checks"),
]


@phlo.ingest.dlt(
    table_name="air_readings",
    unique_key="reading_id",
    group="monitoring",
    validation_schema=ReadingSchema,
    partitioned=False,
    freshness_hours=(24, 72),
    owner="air-quality",
    consumers=CONSUMERS,
)
def air_readings(partition_date: str) -> object:
    """Hourly concentration measurements from every station."""
    rows = read_readings(data_dir())
    return dlt.resource(rows, name="air_readings", columns=contract_columns(ReadingSchema))


@phlo.ingest.dlt(
    table_name="monitoring_notes",
    unique_key="path",
    group="monitoring",
    validation_schema=NoteSchema,
    partitioned=False,
    freshness_hours=(24 * 28, 24 * 35),
    owner="air-quality",
    consumers=CONSUMERS,
)
def monitoring_notes(partition_date: str) -> object:
    """The network's published monthly notes, one row per note."""
    rows = read_notes(data_dir())
    return dlt.resource(rows, name="monitoring_notes", columns=contract_columns(NoteSchema))


@phlo.ingest.dlt(
    table_name="note_claims",
    unique_key="claim_id",
    group="monitoring",
    validation_schema=ClaimSchema,
    partitioned=False,
    freshness_hours=(24 * 28, 24 * 35),
    owner="air-quality",
    consumers=CONSUMERS,
)
def note_claims(partition_date: str) -> object:
    """Figures published in the notes, each with its note and line."""
    rows = extract_claims(read_notes(data_dir()))
    return dlt.resource(rows, name="note_claims", columns=contract_columns(ClaimSchema))
