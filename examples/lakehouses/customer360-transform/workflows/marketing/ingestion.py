"""DLT ingestion of marketing contacts and consent events.

Contacts are a small reference file merged by email; consent events merge by a
composite ``event_key`` so replays never double-count. The consent asset
carries the blocking precedence check because every downstream
consent-gated product depends on latest-event-wins being decidable.
"""

from __future__ import annotations

import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.marketing.quality import (
    assert_consent_precedence_resolvable,
    assert_consent_status_domain,
)
from workflows.schemas.customer360 import ConsentEventSchema, MarketingContactSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKETING_DIR = PROJECT_ROOT / "generated-data" / "marketing"


def read_contacts(marketing_dir: Path = MARKETING_DIR) -> pd.DataFrame:
    """Load the contacts reference CSV."""
    return pd.read_csv(marketing_dir / "contacts.csv", dtype=str)


def read_consent_events(marketing_dir: Path = MARKETING_DIR) -> list[dict[str, object]]:
    """Load consent events from the generated JSON payload."""
    payload = json.loads((marketing_dir / "consent_events.json").read_text(encoding="utf-8"))
    return payload["events"]


@phlo.ingest.dlt(
    table_name="marketing_contacts",
    unique_key="email",
    validation_schema=MarketingContactSchema,
    group="marketing_audience",
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="growth-marketing",
    consumers=[
        Consumer(name="identity-resolution", usage="address variants per identity"),
        Consumer(name="lifecycle-team", usage="audience segmentation"),
    ],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def marketing_contacts(partition_date: str) -> object:
    """Merge the contacts reference; one row per captured address."""
    del partition_date
    return dlt.resource(
        read_contacts().to_dict("records"),
        name="marketing_contacts",
    )


@phlo.ingest.dlt(
    table_name="consent_events",
    unique_key="event_key",
    validation_schema=ConsentEventSchema,
    group="marketing_consent",
    quality_checks=[assert_consent_precedence_resolvable, assert_consent_status_domain],
    partitioned=False,
    freshness_hours=(24, 48),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=2,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="privacy-office",
    consumers=[
        Consumer(name="consent-safe-products", usage="current consent state per identity"),
        Consumer(name="privacy-office", usage="grant and revocation audit trail"),
    ],
    sla=SLA(freshness_hours=48, quality_threshold=1.0, notify=["privacy-office"]),
)
def consent_events(partition_date: str) -> object:
    """Merge consent history; latest occurred_at wins downstream."""
    del partition_date
    return dlt.resource(read_consent_events(), name="consent_events")
