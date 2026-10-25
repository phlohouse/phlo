"""Ingestion assets for the YouTube pack.

One asset: the channel export, validated before it lands. It is an ordinary
Phlo dlt asset, run by the orchestrator with ``phlo materialize youtube_channel``
and by the local runner through the same function.
"""

from __future__ import annotations

import dlt
import phlo
from phlo.contracts import Consumer

from source_to_report.ingest import contract_columns
from source_to_report.paths import data_dir
from source_to_report.youtube.sources import read_videos
from workflows.schemas.youtube import VideoSchema

CONSUMERS = [Consumer(name="channel", usage="channel brief and engagement analysis")]


@phlo.ingest.dlt(
    table_name="youtube_videos",
    unique_key="video_id",
    group="youtube",
    validation_schema=VideoSchema,
    partitioned=False,
    freshness_hours=(24 * 7, 24 * 14),
    owner="channel",
    consumers=CONSUMERS,
)
def youtube_channel(partition_date: str) -> object:
    """Every video in the channel export."""
    rows = read_videos(data_dir())
    return dlt.resource(rows, name="youtube_videos", columns=contract_columns(VideoSchema))


def asset_specs() -> tuple[tuple[object, type, str], ...]:
    """Return the pack's (asset function, contract, raw table) in build order."""
    return ((youtube_channel, VideoSchema, "youtube_videos"),)
