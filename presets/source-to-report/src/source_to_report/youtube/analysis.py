"""Analyse the YouTube marts and build the channel brief.

Same spine as the air-quality pack: read only the marts, write the tables the
page may look up, build the charts, and render. Only the questions are about
videos.
"""

from __future__ import annotations

import csv
import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any

from source_to_report import paths, provenance, youtube
from source_to_report.charts import PALETTE, Series, bar_chart, line_chart
from source_to_report.facts import Facts
from source_to_report.report import render
from source_to_report.warehouse import Warehouse
from source_to_report.youtube import PERIOD_END, PERIOD_START
from source_to_report.youtube.sources import export_path, read_channel

FORMAT_LABEL = {"tutorial": "Tutorial", "interview": "Interview", "demo": "Demo"}


def _month_label(month: str) -> str:
    return date.fromisoformat(month).strftime("%b")


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {column: "" if row.get(column) is None else row[column] for column in columns}
            )


def _query(warehouse: Warehouse, table: str, columns: str, order: str) -> list[dict[str, Any]]:
    return warehouse.query(f"select {columns} from {warehouse.relation(table)} order by {order}")


def build(warehouse: Warehouse, out_dir: Path, *, db_path: Path, as_of: str) -> dict[str, Any]:
    """Build every table, chart and the channel brief under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    videos = _query(
        warehouse,
        "youtube_videos",
        "video_id, title, cast(published_date as varchar) as published_date, format, "
        "cast(views as bigint) as views, cast(likes as bigint) as likes, "
        "cast(comments as bigint) as comments, cast(engagement_rate as double) as engagement_rate",
        "published_date",
    )
    monthly = _query(
        warehouse,
        "youtube_monthly",
        "cast(month as varchar) as month, cast(videos as bigint) as videos, "
        "cast(views as bigint) as views, cast(likes as bigint) as likes, "
        "cast(comments as bigint) as comments, cast(engagement_rate as double) as engagement_rate",
        "month",
    )
    formats = _query(
        warehouse,
        "youtube_formats",
        "format, cast(videos as bigint) as videos, cast(views as bigint) as views, "
        "cast(avg_views as double) as avg_views, cast(avg_engagement as double) as avg_engagement",
        "avg_views desc",
    )

    if not videos:
        raise RuntimeError("youtube_videos is empty; the dbt build did not produce a mart")
    if len(monthly) != 12:
        raise RuntimeError(f"youtube_monthly has {len(monthly)} months; expected 12")
    if {str(row["format"]) for row in formats} != set(FORMAT_LABEL):
        raise RuntimeError("youtube_formats is missing a format; refusing a partial brief")
    if len(videos) != sum(int(row["videos"]) for row in monthly):
        raise RuntimeError("monthly upload counts do not add up to the video count")

    videos = sorted(videos, key=lambda row: int(row["views"]), reverse=True)
    for rank, row in enumerate(videos, start=1):
        row["rank"] = rank
        row["format_label"] = FORMAT_LABEL[str(row["format"])]

    _write_csv(
        data_dir / "videos.csv",
        videos,
        (
            "rank",
            "video_id",
            "title",
            "published_date",
            "format",
            "format_label",
            "views",
            "likes",
            "comments",
            "engagement_rate",
        ),
    )
    _write_csv(
        data_dir / "monthly.csv",
        monthly,
        ("month", "videos", "views", "likes", "comments", "engagement_rate"),
    )
    _write_csv(
        data_dir / "formats.csv",
        formats,
        ("format", "videos", "views", "avg_views", "avg_engagement"),
    )

    total_views = sum(int(row["views"]) for row in videos)
    total_likes = sum(int(row["likes"]) for row in videos)
    total_comments = sum(int(row["comments"]) for row in videos)
    median_engagement = statistics.median(float(row["engagement_rate"]) for row in videos)
    best_month = max(monthly, key=lambda row: int(row["views"]))
    top = videos[0]
    top_format = formats[0]

    channel = read_channel(paths.data_dir())
    summary = {
        "channel": str(channel["title"]),
        "period_label": "2025",
        "period": {"start": PERIOD_START.isoformat(), "end": PERIOD_END.isoformat()},
        "as_of": as_of,
        "counts": {
            "videos": len(videos),
            "views": total_views,
            "likes": total_likes,
            "comments": total_comments,
            "months": len(monthly),
            "formats": len(formats),
        },
        "headlines": {
            "median_engagement": median_engagement,
            "best_month": str(best_month["month"]),
            "best_month_name": date.fromisoformat(str(best_month["month"])).strftime("%B"),
            "best_month_views": int(best_month["views"]),
            "top_video": str(top["title"]),
            "top_video_views": int(top["views"]),
            "top_format": str(top_format["format"]),
            "top_format_avg_views": float(top_format["avg_views"]),
        },
        "checks": [
            {
                "name": "every_month_present",
                "ok": len(monthly) == 12,
                "detail": f"{len(monthly)} months",
            },
            {
                "name": "every_video_counted",
                "ok": len(videos) == sum(int(row["videos"]) for row in monthly),
                "detail": f"{len(videos)} videos",
            },
            {
                "name": "every_format_represented",
                "ok": len(formats) == len(FORMAT_LABEL),
                "detail": f"{len(formats)} formats",
            },
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    charts = {
        "monthly_views": line_chart(
            [Series("Views", [int(row["views"]) for row in monthly])],
            [_month_label(str(row["month"])) for row in monthly],
            y_label="views (monthly)",
            title="Monthly views across the channel",
        ),
        "format_views": bar_chart(
            [FORMAT_LABEL[str(row["format"])] for row in formats],
            [round(float(row["avg_views"])) for row in formats],
            y_label="average views per video",
            title="Average views by video format",
            colour=PALETTE[0],
        ),
    }
    for name, svg in charts.items():
        (charts_dir / f"{name}.svg").write_text(svg, encoding="utf-8")

    record = provenance.document(
        inputs=[("channel export", export_path(paths.data_dir()))],
        tables={
            "youtube_videos": len(videos),
            "youtube_monthly": len(monthly),
            "youtube_formats": len(formats),
        },
        as_of=as_of,
        db_path=db_path,
    )
    provenance.write(out_dir, record)

    facts = Facts(data_dir, summary)
    render(
        out_dir,
        facts=facts,
        charts=charts,
        provenance=record,
        domain=youtube,
        template="channel.html.j2",
        extra_paths=[Path(__file__).parent],
    )
    return summary
