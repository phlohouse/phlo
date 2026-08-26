"""DLT ingestion of the JSON provider directory."""

from __future__ import annotations

import json
from pathlib import Path

import dlt
import phlo
from phlo.contracts import SLA, Consumer

from workflows.shared.contracts.schemas import ProviderSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_FILE = PROJECT_ROOT / "generated-data" / "inbound" / "providers" / "providers.json"


def read_providers(providers_file: Path = PROVIDERS_FILE) -> list[dict[str, str]]:
    return json.loads(providers_file.read_text(encoding="utf-8"))


@phlo.ingest.dlt(
    table_name="providers",
    unique_key="provider_id",
    validation_schema=ProviderSchema,
    group="providers",
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=600,
    max_retries=1,
    retry_delay_seconds=120,
    add_metadata_columns=True,
    owner="provider-network",
    consumers=[
        Consumer(name="compliance-officer", usage="network status attestation"),
        Consumer(name="claims-operations", usage="claim routing"),
    ],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def providers(partition_date: str) -> object:
    """Merge the provider directory."""
    del partition_date
    return dlt.resource(read_providers(), name="providers")
