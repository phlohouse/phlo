"""Analyse the monitoring marts and write the brief.

Everything here is domain-specific: which tables matter, which comparisons
the reader needs, and what the report says. The spine (warehouse access,
strict facts, charts, rendering, provenance) is generic.

The analysis reads only published marts. It never touches a raw file.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

from source_to_report import paths, provenance
from source_to_report.charts import PALETTE, Series, bar_chart, line_chart
from source_to_report.domain import (
    PERIOD_END,
    PERIOD_START,
    POLLUTANTS,
    STATION_IDS,
    STATIONS,
)
from source_to_report.facts import Facts
from source_to_report.report import render
from source_to_report.warehouse import Warehouse

PM25 = "PM2.5"
NO2 = "NO2"


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
    """Build every table, chart and the report under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    monthly = _query(
        warehouse,
        "monthly_station",
        "cast(month as varchar) as month, station_id, station_name, pollutant, "
        "cast(mean_value as double) as mean_value, cast(max_value as double) as max_value, "
        "cast(readings as bigint) as readings",
        "month, station_id, pollutant",
    )
    annual = _query(
        warehouse,
        "annual_station",
        "station_id, station_name, pollutant, cast(mean_value as double) as mean_value, "
        "cast(max_value as double) as max_value, cast(readings as bigint) as readings",
        "station_id, pollutant",
    )
    claims = _query(
        warehouse,
        "claims_checked",
        "claim_id, cast(month as varchar) as month, station_id, pollutant, "
        "cast(published_value as double) as published_value, "
        "cast(measured_value as double) as measured_value, "
        "cast(difference as double) as difference, verdict, source_path, "
        "cast(line as integer) as line, sentence",
        "month, station_id, pollutant",
    )
    coverage = _query(
        warehouse,
        "station_coverage",
        "station_id, station_name, pollutant, cast(observed_hours as bigint) as observed_hours, "
        "cast(expected_hours as bigint) as expected_hours, cast(coverage as double) as coverage",
        "station_id, pollutant",
    )
    daily = _query(
        warehouse,
        "days_over",
        "cast(month as varchar) as month, station_id, station_name, pollutant, "
        "cast(days_over as bigint) as days_over",
        "month, station_id, pollutant",
    )

    if not monthly:
        raise RuntimeError("monthly_station is empty; the dbt build did not produce a mart")
    months = sorted({str(row["month"]) for row in monthly})
    if {str(row["station_id"]) for row in monthly} != set(STATION_IDS):
        raise RuntimeError("the mart is missing a station; refusing to write a partial brief")
    if len(monthly) != len(months) * len(STATIONS) * len(POLLUTANTS):
        raise RuntimeError(
            f"monthly_station has {len(monthly)} rows; expected "
            f"{len(months) * len(STATIONS) * len(POLLUTANTS)}"
        )

    _write_csv(
        data_dir / "monthly_station.csv",
        monthly,
        ("month", "station_id", "station_name", "pollutant", "mean_value", "max_value", "readings"),
    )
    _write_csv(
        data_dir / "annual_station.csv",
        annual,
        ("station_id", "station_name", "pollutant", "mean_value", "max_value", "readings"),
    )
    _write_csv(
        data_dir / "claims_checked.csv",
        claims,
        (
            "claim_id",
            "month",
            "station_id",
            "pollutant",
            "published_value",
            "measured_value",
            "difference",
            "verdict",
            "source_path",
            "line",
            "sentence",
        ),
    )
    _write_csv(
        data_dir / "station_coverage.csv",
        coverage,
        ("station_id", "station_name", "pollutant", "observed_hours", "expected_hours", "coverage"),
    )
    _write_csv(
        data_dir / "days_over.csv",
        daily,
        ("month", "station_id", "station_name", "pollutant", "days_over"),
    )

    monthly_mean = {
        (str(row["month"]), str(row["station_id"]), str(row["pollutant"])): float(row["mean_value"])
        for row in monthly
    }
    annual_mean = {(str(row["station_id"]), str(row["pollutant"])): row for row in annual}
    days_by = {
        (str(row["month"]), str(row["station_id"]), str(row["pollutant"])): int(row["days_over"])
        for row in daily
    }

    def series_for(pollutant: str) -> list[Series]:
        draw: list[Series] = []
        for station in STATIONS:
            values = []
            for month in months:
                try:
                    values.append(monthly_mean[(month, station.id, pollutant)])
                except KeyError as error:
                    raise RuntimeError(
                        f"no {pollutant} mean for {station.id} in {month}"
                    ) from error
            draw.append(Series(station.name, values))
        return draw

    pm25_by_month = [
        sum(days_by[(month, station.id, PM25)] for station in STATIONS) for month in months
    ]
    no2_by_month = [
        sum(days_by[(month, station.id, NO2)] for station in STATIONS) for month in months
    ]
    charts = {
        "pm25_monthly": line_chart(
            series_for(PM25),
            [_month_label(month) for month in months],
            y_label="µg/m³ (monthly mean)",
            title="PM2.5 monthly mean by station",
        ),
        "pm25_annual": bar_chart(
            [station.name for station in STATIONS],
            [float(annual_mean[(station.id, PM25)]["mean_value"]) for station in STATIONS],
            y_label="µg/m³ (annual mean)",
            title="PM2.5 annual mean against the 10 µg/m³ objective",
            threshold=10.0,
            threshold_label="objective 10 µg/m³",
        ),
        "no2_annual": bar_chart(
            [station.name for station in STATIONS],
            [float(annual_mean[(station.id, NO2)]["mean_value"]) for station in STATIONS],
            y_label="µg/m³ (annual mean)",
            title="NO2 annual mean against the 40 µg/m³ objective",
            threshold=40.0,
            threshold_label="objective 40 µg/m³",
            colour=PALETTE[2],
        ),
        "pm25_days": bar_chart(
            [_month_label(month) for month in months],
            [float(value) for value in pm25_by_month],
            y_label="station-days above the limit",
            title="Days above the 24-hour PM2.5 objective",
            colour=PALETTE[1],
            integer_ticks=True,
        ),
    }
    for name, svg in charts.items():
        (charts_dir / f"{name}.svg").write_text(svg, encoding="utf-8")

    pm25_annual = [annual_mean[(station.id, PM25)] for station in STATIONS]
    no2_annual = [annual_mean[(station.id, NO2)] for station in STATIONS]
    worst_pm25 = max(pm25_annual, key=lambda row: float(row["mean_value"]))
    best_pm25 = min(pm25_annual, key=lambda row: float(row["mean_value"]))
    worst_no2 = max(no2_annual, key=lambda row: float(row["mean_value"]))
    differing = [row for row in claims if row["verdict"] == "differs"]
    missing = [row for row in claims if row["verdict"] == "missing"]
    lowest_coverage = min(float(row["coverage"]) for row in coverage) if coverage else 0.0

    checks = [
        {
            "name": "coverage_at_least_95",
            "ok": bool(coverage) and all(float(row["coverage"]) >= 0.95 for row in coverage),
            "detail": f"lowest station/pollutant coverage is {lowest_coverage:.1%}",
        },
        {
            "name": "every_month_is_complete",
            "ok": len(monthly) == len(months) * len(STATIONS) * len(POLLUTANTS),
            "detail": f"{len(monthly)} monthly rows across {len(months)} months",
        },
        {
            "name": "every_published_figure_has_a_measurement",
            "ok": not missing,
            "detail": f"{len(missing)} of {len(claims)} published figures could not be matched",
        },
    ]

    summary: dict[str, Any] = {
        "as_of": as_of,
        "period": {"start": PERIOD_START.isoformat(), "end": PERIOD_END.isoformat()},
        "period_label": f"{PERIOD_START:%B} – {PERIOD_END:%B %Y}",
        "counts": {
            "readings": sum(int(row["readings"]) for row in annual),
            "months": len(months),
            "stations": len(STATIONS),
            "pollutants": len(POLLUTANTS),
            "claims": len(claims),
            "claims_differ": len(differing),
            "claims_missing": len(missing),
            "pm25_station_days_over": sum(pm25_by_month),
            "no2_station_days_over": sum(no2_by_month),
            "tables": 5,
        },
        "headlines": {
            "pm25_highest_station": worst_pm25["station_name"],
            "pm25_highest_mean": round(float(worst_pm25["mean_value"]), 2),
            "pm25_lowest_station": best_pm25["station_name"],
            "pm25_lowest_mean": round(float(best_pm25["mean_value"]), 2),
            "no2_highest_station": worst_no2["station_name"],
            "no2_highest_mean": round(float(worst_no2["mean_value"]), 2),
            "coverage_min": round(lowest_coverage, 4),
            "first_month": months[0],
            "last_month": months[-1],
        },
        "tables": {
            "monthly_station": len(monthly),
            "annual_station": len(annual),
            "claims_checked": len(claims),
            "station_coverage": len(coverage),
            "days_over": len(daily),
        },
        "checks": checks,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    inputs: list[tuple[str, Path]] = []
    data_dir_path = paths.data_dir()
    readings = data_dir_path / "raw" / "readings.json"
    inputs.append(("source pull", readings))
    inputs.extend(
        (f"note {path.stem}", path) for path in sorted((data_dir_path / "notes").glob("*.md"))
    )
    record = provenance.document(
        inputs=inputs,
        tables=summary["tables"],
        as_of=as_of,
        db_path=db_path,
    )
    provenance.write(out_dir, record)

    facts = Facts(data_dir, summary)
    render(out_dir, facts=facts, charts=charts, provenance=record)
    return summary
