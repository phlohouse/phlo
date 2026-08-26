"""DLT ingestion of carrier events from the replay API plus reference tables.

Two carriers publish event scans through the same replay server
(``scripts/carrier_api.py``) with different polling cadences registered in the
schedules module: ATLAS is polled hourly, CORSAIR every four hours. Each feed
merges into its own staging table keyed by ``event_id`` (the platform derives
asset keys from table names, so one feed per table keeps the two cadences
independently schedulable); the carriers transform folder unifies them into a
single ``raw.carrier_events`` stream, still merged on ``event_id``.

The ingestion gate rejects any batch referencing a carrier that is not in the
carrier directory: unknown-carrier references would silently drop shipments
from coverage marts downstream.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import dlt
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.logistics import (
    CarrierDirectorySchema,
    CarrierEventSchema,
    SlaTermSchema,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DIR = PROJECT_ROOT / "generated-data" / "reference"
API_URL = os.environ.get("CARRIER_API_URL", "http://127.0.0.1:8090/v1")

# Registered carrier codes; kept in lockstep with reference/carrier_directory.csv.
KNOWN_CARRIERS = frozenset({"ATLAS", "CORSAIR"})


def assert_known_carrier_reference(events: object) -> str | None:
    """Gate: every event's carrier code must be a registered carrier."""
    import pandas as pd

    frame = events if isinstance(events, pd.DataFrame) else pd.DataFrame(events)
    if frame.empty or "carrier" not in frame.columns:
        return None
    unknown = sorted(set(frame["carrier"]).difference(KNOWN_CARRIERS))
    if unknown:
        offenders = frame[frame["carrier"].isin(unknown)]["event_id"].head(5).tolist()
        return f"carrier events reference unregistered carriers {unknown}: {offenders}"
    return None


def fetch_carrier_events(carrier: str, event_date: str, url: str = API_URL) -> list[dict]:
    """Fetch one carrier's event page for one calendar day."""
    request_url = f"{url}/events?{urlencode({'carrier': carrier, 'date': event_date})}"
    with urlopen(request_url, timeout=10) as response:  # noqa: S310 - replay endpoint
        payload = json.load(response)
    return payload["events"]


def _register_event_asset(carrier: str, table_name: str) -> None:
    @phlo.ingest.dlt(
        table_name=table_name,
        unique_key="event_id",
        validation_schema=CarrierEventSchema,
        group=f"carriers_{carrier.lower()}",
        quality_checks=[assert_known_carrier_reference],
        freshness_hours=(3, 6),
        merge_strategy="merge",
        strict_validation=True,
        max_runtime_seconds=300,
        max_retries=3,
        retry_delay_seconds=60,
        add_metadata_columns=True,
        owner="logistics-carrier-ops",
        consumers=[
            Consumer(name="control-tower", usage="canonical shipment state"),
            Consumer(name="carrier-ops", usage="exception follow-up"),
        ],
        sla=SLA(freshness_hours=6, quality_threshold=1.0),
    )
    def carrier_feed(partition_date: str) -> object:
        """Merge one day of carrier events; replays are idempotent on event_id."""
        return dlt.resource(
            fetch_carrier_events(carrier, partition_date),
            name=f"{table_name}_resource",
        )

    carrier_feed.__doc__ = f"Merge ATLAS-published events into {table_name} on event_id."
    del carrier_feed


_register_event_asset("ATLAS", "carrier_events_atlas")
_register_event_asset("CORSAIR", "carrier_events_corsair")


@phlo.ingest.dlt(
    table_name="carrier_directory",
    unique_key="carrier_code",
    validation_schema=CarrierDirectorySchema,
    group="carriers_reference",
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="logistics-carrier-ops",
    consumers=[Consumer(name="control-tower", usage="carrier coverage attribution")],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def carrier_directory(partition_date: str) -> object:
    """Reference merge of registered carriers; small lookup, never partitioned."""
    del partition_date
    import pandas as pd

    frame = pd.read_csv(Path(REFERENCE_DIR) / "carrier_directory.csv", dtype=str)
    return dlt.resource(frame.to_dict("records"), name="carrier_directory")


@phlo.ingest.dlt(
    table_name="sla_terms",
    unique_key="sla_term_key",
    validation_schema=SlaTermSchema,
    group="carriers_reference",
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="logistics-contract-admin",
    consumers=[Consumer(name="control-tower", usage="SLA breach detection")],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def sla_terms(partition_date: str) -> object:
    """Reference merge of contractual transit allowances per carrier."""
    del partition_date
    import pandas as pd

    frame = pd.read_csv(Path(REFERENCE_DIR) / "sla_terms.csv", dtype=str)
    frame["sla_term_key"] = frame["carrier_code"] + ":" + frame["service_level"]
    return dlt.resource(frame.to_dict("records"), name="sla_terms")
