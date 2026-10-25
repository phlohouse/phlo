"""Write the deterministic YouTube channel export.

The pull is synthetic: two uploads a month through 2025, with views, likes and
comments derived from the format and the month. Nothing here calls the network,
so the preset and its tests run without an API key.

Replace this script with a YouTube Data API pull to use a real channel; the
importer reads the same shape.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date
from pathlib import Path

FORMATS = ("tutorial", "interview", "demo")
BASE_VIEWS = {"tutorial": 18_000, "interview": 9_000, "demo": 13_000}
SEASON = (0.82, 0.88, 0.96, 1.02, 1.05, 1.00, 0.94, 1.04, 1.12, 1.18, 1.10, 0.95)
TOPICS = (
    "incremental models",
    "partitioned assets",
    "data contracts",
    "lineage",
    "backfills",
    "quality rules",
    "warehouse costs",
    "schema evolution",
    "branching",
    "observability",
    "fixtures",
    "dbt tests",
)
TITLE = {
    "tutorial": "Build {topic} in Phlo",
    "interview": "Inside the team behind {topic}",
    "demo": "{topic} in five minutes",
}
DURATION = {"tutorial": (540, 1_180), "interview": (1_500, 3_000), "demo": (280, 640)}


def generate(data_dir: Path) -> list[dict[str, object]]:
    """Write the channel export and return its videos."""
    rng = random.Random(2025)
    videos: list[dict[str, object]] = []
    for month in range(1, 13):
        for slot, day in enumerate((5, 20)):
            index = (month - 1) * 2 + slot
            video_format = FORMATS[index % len(FORMATS)]
            topic = TOPICS[index % len(TOPICS)]
            views = int(BASE_VIEWS[video_format] * SEASON[month - 1] * rng.uniform(0.78, 1.28))
            likes = int(views * rng.uniform(0.035, 0.058))
            comments = int(views * rng.uniform(0.003, 0.0065))
            low, high = DURATION[video_format]
            videos.append(
                {
                    "video_id": f"vid-{index + 1:03d}",
                    "published_at": date(2025, month, day).isoformat(),
                    "title": TITLE[video_format].format(topic=topic),
                    "format": video_format,
                    "duration_seconds": rng.randrange(low, high),
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                }
            )

    export = {
        "channel": {
            "id": "UC-fixture-fieldnotes",
            "title": "Field Notes",
            "pulled_at": "2026-01-05",
        },
        "videos": videos,
    }
    target = data_dir / "youtube" / "channel.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8")
    return videos


def main() -> int:
    """Write the export and report what landed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    videos = generate(args.data_dir)
    total = sum(int(video["views"]) for video in videos)
    print(f"  youtube {len(videos)} videos, {total:,} views -> {args.data_dir / 'youtube'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
