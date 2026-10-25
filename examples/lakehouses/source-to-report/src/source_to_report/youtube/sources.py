"""Read the YouTube pack's source export.

The export is the same shape a YouTube Data API pull would write: channel
metadata and a list of videos with their public statistics. Replacing this
reader with an API call changes nothing downstream.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def export_path(data_dir: Path) -> Path:
    """Return the channel export's path."""
    return data_dir / "youtube" / "channel.json"


def _read_export(data_dir: Path) -> dict[str, Any]:
    path = export_path(data_dir)
    if not path.is_file():
        raise FileNotFoundError(
            f"no channel export at {path}; run "
            "`uv run python scripts/generate_youtube_fixtures.py` first"
        )
    export = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(export, dict):
        raise ValueError(f"{path} is not a channel export")
    return export


def read_channel(data_dir: Path) -> dict[str, Any]:
    """Return the channel metadata from the export."""
    channel = _read_export(data_dir).get("channel")
    if not isinstance(channel, dict) or not channel.get("title"):
        raise ValueError(f"{export_path(data_dir)} has no channel title")
    return channel


def read_videos(data_dir: Path) -> list[dict[str, Any]]:
    """Return every video in the channel export."""
    videos = _read_export(data_dir).get("videos")
    if not isinstance(videos, list) or not videos:
        raise ValueError(f"{export_path(data_dir)} has no videos; the export is incomplete")
    return videos
