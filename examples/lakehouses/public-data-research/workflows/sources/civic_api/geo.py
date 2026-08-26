"""DLT ingestion of GeoJSON place metadata.

The civic portal publishes polygon geometry plus free-form properties. The
asset computes one centroid per feature (mean of the ring's distinct
vertices) and flattens every property into a ``prop_`` column, so downstream
models join geometry-derived location onto the registry without touching the
raw document.
"""

from __future__ import annotations

import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.contracts import PlacesGeoSchema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
GEOJSON_FILE = PROJECT_ROOT / "generated-data" / "civic" / "places.geojson"


def parse_places_geojson(geojson_file: Path = GEOJSON_FILE) -> pd.DataFrame:
    """Flatten the FeatureCollection into one row per place."""
    payload = json.loads(geojson_file.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for feature in payload["features"]:
        properties = feature["properties"]
        ring = feature["geometry"]["coordinates"][0]
        vertices = ring[:-1] if ring[0] == ring[-1] else ring
        row: dict[str, object] = {
            "place_id": properties["place_id"],
            "centroid_lat": round(sum(vertex[1] for vertex in vertices) / len(vertices), 6),
            "centroid_lon": round(sum(vertex[0] for vertex in vertices) / len(vertices), 6),
        }
        for key, value in properties.items():
            if key != "place_id":
                row[f"prop_{key}"] = value
        rows.append(row)
    return pd.DataFrame(rows)


@phlo.ingest.dlt(
    table_name="places_geo",
    unique_key="place_id",
    validation_schema=PlacesGeoSchema,
    group="civic_api",
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="civic-platform",
    consumers=[Consumer(name="research", usage="centroid geography for places")],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def places_geo(partition_date: str) -> object:
    """Merge flattened GeoJSON metadata; a small reference keyed by place."""
    del partition_date
    return dlt.resource(parse_places_geojson().to_dict("records"), name="places_geo")
