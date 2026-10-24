"""Generate synthetic source files locally; never starts a service or writes a lakehouse."""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

COLUMNS = [
    "batch_id",
    "timepoint",
    "temp_c",
    "pull_date",
    "test",
    "result",
    "unit",
    "method",
    "analyst",
    "sample_id",
    "site",
]


def build_rows() -> list[dict[str, str]]:
    first = [
        ("061", "5", "Potency", "98.2", "%"),
        ("061", "5", "Purity (SEC)", "99.1", "%"),
        ("061", "5", "Endotoxin", "<LOQ", "EU/mL"),
        ("061", "25", "Potency", "91.7", "%"),
        ("061", "25", "Purity (SEC)", "97.4", "%"),
        ("064", "5", "Potency", "101.4", "%"),
        ("064", "5", "Mycoplasma", "ND", "—"),
        ("064", "25", "Potency", "93.0", "%"),
        ("064", "25", "Purity (SEC)", "97.9", "%"),
        ("066", "5", "Potency", "99.6", "%"),
    ]
    rows = []
    for index in range(1284):
        if index < len(first):
            batch, temperature, test, result, unit = first[index]
        else:
            batch = f"{61 + index // 24:03d}"
            temperature = "5" if index % 2 == 0 else "25"
            test, unit = ("Potency", "%") if index % 3 else ("Purity (SEC)", "%")
            result = f"{94 + (index * 7 % 72) / 10:.1f}"
            if 10 <= index < 54:
                test, result, unit = (
                    ("Endotoxin", "<LOQ", "EU/mL") if index % 2 == 0 else ("Mycoplasma", "ND", "—")
                )
        rows.append(
            dict(
                zip(
                    COLUMNS,
                    [
                        f"BR-2026-{batch}",
                        "T6M",
                        temperature,
                        "24/09/2026" if index < 1201 else "2026-09-24",
                        test,
                        result,
                        unit,
                        "SYNTHETIC-COMPARISON",
                        "Assay Dev fixture",
                        f"STAB-{index + 1:04d}",
                        "Cambridge",
                    ],
                    strict=True,
                )
            )
        )
    assert len(rows) == 1284 and len(COLUMNS) == 11
    assert sum(row["pull_date"] == "24/09/2026" for row in rows) == 1201
    assert sum(row["pull_date"] == "2026-09-24" for row in rows) == 83
    assert sum(row["result"] in {"ND", "<LOQ"} for row in rows) == 46
    return rows


ROOT = Path(__file__).resolve().parents[1]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def generate(root: Path, *, as_of: datetime) -> dict[str, int]:
    """Reproduce grains and deliberate QC failures; timestamps follow an explicit anchor."""
    if as_of.tzinfo is None:
        raise ValueError("Use an offset-aware timestamp.")
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    rng = random.Random(214)
    anchor = (as_of.astimezone(UTC) - timedelta(days=1)).replace(second=59, microsecond=0)
    telemetry, samples, plates, batches = [], [], [], []
    for batch in range(24):
        run = f"CGT-04-{batch + 1:03d}"
        batches.append(
            {
                "batch_id": run,
                "product": "CGT-04",
                "site": "Cambridge",
                "release_status": "on_hold" if batch < 10 else "released",
            }
        )
        for reading in range(120):
            telemetry.append(
                {
                    "ts": (
                        anchor + timedelta(minutes=30 * batch, seconds=10 * reading)
                    ).isoformat(),
                    "vessel_id": f"BR-{batch % 4 + 1:02d}",
                    "run_id": run,
                    "ph": round(rng.uniform(6.9, 7.2), 3),
                    "do_pct": round(rng.uniform(35, 60), 2),
                    "temp_c": round(rng.uniform(36.7, 37.3), 2),
                    "agitation_rpm": round(rng.uniform(110, 130), 1),
                }
            )
        for replicate in range(4):
            index = batch * 4 + replicate
            samples.append(
                {
                    "sample_id": f"S-{index + 1:04d}",
                    "run_id": run,
                    "batch_id": run,
                    "assay": "potency",
                    "result_value": 85.0 if index < 37 else round(rng.uniform(97, 105), 2),
                    "spec_lower": 90.0,
                    "spec_upper": 110.0,
                    "analyst": "QC fixture",
                    "sampled_at": anchor.isoformat(),
                }
            )
    for index in range(384):
        plates.append(
            {
                "plate_id": f"P-{4473 + index // 96}",
                "well": f"{chr(65 + index % 96 // 12)}{index % 12 + 1:02d}",
                "sample_id": f"S-{index // 4 + 1:04d}",
                "od": round(rng.uniform(0.1, 1.5), 4),
                "read_at": anchor.isoformat(),
            }
        )
    sources = {
        "bioreactor_telemetry": telemetry,
        "lims_samples": samples,
        "elisa_plate_reads": plates,
        "erp_batches": batches,
    }
    for name, rows in sources.items():
        write_csv(data / f"{name}.csv", rows)
    stability = build_rows()
    write_csv(data / "stability_CGT-04_T6M.csv", stability)
    (data / "scenario.json").write_text(
        json.dumps({"bioreactor_schema_drift": False, "plate_schema_drift": False}, indent=2) + "\n"
    )
    return {**{name: len(rows) for name, rows in sources.items()}, "stability": len(stability)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument(
        "--as-of",
        default=datetime.now(UTC).isoformat(),
        help="Offset-aware ISO timestamp; fixed input makes output deterministic.",
    )
    args = parser.parse_args()
    print(json.dumps(generate(args.output, as_of=datetime.fromisoformat(args.as_of)), indent=2))


if __name__ == "__main__":
    main()
