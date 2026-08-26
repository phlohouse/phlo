"""Generate the deterministic ClickHouse operational fixture set.

The generator writes every byte the example consumes:

- ``platform_events/hour=<label>/batch-<n>.ndjson.gz``: quarter-hour
  micro-batches of platform events. Later batches re-deliver one earlier
  event verbatim (replayed delivery), so the raw append stream accumulates
  duplicate ``event_id`` rows that the marts must collapse at read time.
- ``access_logs/hour=<label>/requests.ndjson.gz``: one request-log file per
  operating hour with a known status-code catalog and distinct durations so
  the hourly p95 lands exactly on a fixture value.
- ``accounts/tenants.csv``: tenant directory seeded into the local PostgreSQL
  metadata source by ``scripts/seed_postgres.py``.
- ``failures/``: labeled invalid fixtures; each breaks exactly one named
  invariant (latency bounds, status catalog, tier-1 freshness, count
  reconciliation).

Every value derives from fixed arithmetic, so regenerating produces identical
files. Gzip members are written with ``mtime=0`` to keep archives stable.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

DAY = "2026-08-20"
OPERATING_HOURS = [f"{DAY}T{hour:02d}" for hour in range(4)]
BATCH_MINUTES = (0, 15, 30, 45)
SLOTS_PER_HOUR = len(BATCH_MINUTES)

# (tenant_id, tenant_name, tier, plan) - two tier-1 tenants must appear in
# every operating hour for the freshness validator to pass.
TENANTS = [
    ("t-northwind", "Northwind Traders", "tier-1", "enterprise"),
    ("t-acme", "Acme Manufacturing", "tier-1", "enterprise"),
    ("t-globex", "Globex Industries", "tier-2", "growth"),
]
TIER1_TENANTS = tuple(t[0] for t in TENANTS if t[2] == "tier-1")

EVENT_TYPES = ("api_request", "job_run", "deploy", "alert")
REQUEST_PATHS = ("/api/v1/events", "/api/v1/queries", "/api/v1/exports", "/api/v1/health")
ALLOWED_STATUS_CODES = (200, 201, 204, 400, 404, 500, 502, 503)

LATENCY_MIN_MS = 0
LATENCY_MAX_MS = 60000


def _occurred_at(hour_index: int, slot: int, tenant_index: int) -> str:
    minute = 2 + slot * 13  # minutes 2, 15, 28, 41 inside the hour
    second = tenant_index * 7
    return f"{DAY}T{hour_index:02d}:{minute:02d}:{second:02d}"


def platform_event(hour_index: int, slot: int, tenant_index: int) -> dict:
    """Build one platform event from fixed arithmetic."""
    return {
        "event_id": f"ev-h{hour_index}b{slot}t{tenant_index}",
        "tenant_id": TENANTS[tenant_index][0],
        "event_type": EVENT_TYPES[slot],
        "occurred_at": _occurred_at(hour_index, slot, tenant_index),
        "latency_ms": LATENCY_MIN_MS
        + 100
        + ((hour_index * 53 + tenant_index * 29 + slot * 17) % 850),
    }


def _request_row(hour_index: int, r: int) -> dict:
    duration = 40 + (((hour_index * 97 + r * 61) % 1200) * 5)
    return {
        "request_id": f"req-h{hour_index}-{r:02d}",
        "tenant_id": TENANTS[r % 3][0],
        "path": REQUEST_PATHS[r % len(REQUEST_PATHS)],
        "status_code": _status_code(hour_index, r),
        "duration_ms": duration,
        "occurred_at": f"{DAY}T{hour_index:02d}:{1 + r:02d}:{(r * 13) % 60:02d}",
    }


def _status_code(hour_index: int, r: int) -> int:
    """Deterministic status mix: server errors at fixed positions.

    Positions where ``(r + 2*hour) % 8 == 3`` fail with a 5xx; the rest are
    successes with occasional 204/400 responses. All codes stay inside the
    allowed catalog.
    """
    if (r + hour_index * 2) % 8 == 3:
        return (500, 502, 503)[r % 3]
    if r % 5 == 4:
        return 204
    if r % 7 == 6:
        return 400
    return 200


REQUESTS_PER_HOUR = 21  # p95 rank 0.95*(21-1)=19 is integral -> exact quantile


def _write_ndjson_gz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as handle:
            handle.write(payload.encode("utf-8"))


def generate_platform_events(data_dir: Path) -> None:
    """Write quarter-hour micro-batch files with verbatim replayed deliveries."""
    for hour_index, hour_label in enumerate(OPERATING_HOURS):
        for slot in range(SLOTS_PER_HOUR):
            rows = [platform_event(hour_index, slot, t) for t in range(len(TENANTS))]
            if slot > 0:
                # The delivery layer replays the previous batch's first event
                # verbatim: same event_id, same bytes, appended again.
                rows.append(platform_event(hour_index, slot - 1, 0))
            minute = BATCH_MINUTES[slot]
            hour_dir = data_dir / "platform_events" / f"hour={hour_label}"
            _write_ndjson_gz(hour_dir / f"batch-{minute:02d}.ndjson.gz", rows)


def generate_access_logs(data_dir: Path) -> None:
    """Write one request-log file per operating hour."""
    for hour_index, hour_label in enumerate(OPERATING_HOURS):
        rows = [_request_row(hour_index, r) for r in range(REQUESTS_PER_HOUR)]
        _write_ndjson_gz(
            data_dir / "access_logs" / f"hour={hour_label}" / "requests.ndjson.gz",
            rows,
        )


def generate_accounts(data_dir: Path) -> None:
    """Write the tenant directory consumed by seed_postgres.py."""
    path = data_dir / "accounts" / "tenants.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tenant_id", "tenant_name", "tier", "plan"])
        writer.writerows(TENANTS)


def generate_failures(data_dir: Path) -> None:
    """Write labeled failure fixtures; each breaks exactly one invariant."""
    failures = data_dir / "failures"

    # Breaks ONLY the latency bounds invariant (0..60000 ms).
    out_of_bounds = platform_event(0, 0, 0)
    out_of_bounds["latency_ms"] = 60001
    _write_ndjson_gz(failures / "platform_events_latency_out_of_bounds.ndjson.gz", [out_of_bounds])

    # Breaks ONLY the status-code catalog invariant.
    unknown_status = _request_row(0, 0)
    unknown_status["status_code"] = 599
    _write_ndjson_gz(failures / "access_logs_status_code_unknown.ndjson.gz", [unknown_status])

    # Breaks ONLY the tier-1 freshness invariant: an hour of events that
    # carries traffic for the tier-2 tenant but none for either tier-1 tenant.
    gap_hour_index = OPERATING_HOURS.index(f"{DAY}T01")
    gap_rows = [
        {
            "event_id": f"ev-gap-t{t}",
            "tenant_id": "t-globex",
            "event_type": EVENT_TYPES[t % len(EVENT_TYPES)],
            "occurred_at": _occurred_at(gap_hour_index, t % SLOTS_PER_HOUR, t),
            "latency_ms": 120 + t * 10,
        }
        for t in range(len(TENANTS))
    ]
    _write_ndjson_gz(failures / "platform_events_tier1_gap.ndjson.gz", gap_rows)


def write_reconciliation_shortfall(data_dir: Path, daily_rows: list[dict]) -> None:
    """Write a tampered daily-totals frame used to prove reconciliation fails.

    Derived from the true fixture arithmetic with one tenant's final-hour
    contribution removed, so hourly sums no longer match daily totals. Kept as
    a static CSV because it represents operator-visible state, not raw input.
    """
    path = data_dir / "failures" / "reconciliation_shortfall.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["usage_date", "tenant_id", "event_count", "request_count", "error_count"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(daily_rows)


def generate(data_dir: Path | str = DEFAULT_DATA_DIR) -> Path:
    """Generate the full fixture tree under ``data_dir`` and return its path."""
    data_dir = Path(data_dir)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    generate_platform_events(data_dir)
    generate_access_logs(data_dir)
    generate_accounts(data_dir)
    generate_failures(data_dir)
    write_reconciliation_shortfall(data_dir, _shortfall_daily_rows())
    return data_dir


def _shortfall_daily_rows() -> list[dict]:
    """True daily totals per tenant with one tenant's final hour removed.

    Mirrors the tenant_usage_daily grain (usage_date, tenant_id) so the
    labeled failure differs from the honest daily aggregate by exactly the
    missing hour: hourly sums no longer reconcile.
    """
    usage_date = DAY
    true_daily = {t[0]: {"event_count": 0, "request_count": 0, "error_count": 0} for t in TENANTS}
    for hour_index in range(len(OPERATING_HOURS)):
        for tenant_index in range(len(TENANTS)):
            tenant_id = TENANTS[tenant_index][0]
            for slot in range(SLOTS_PER_HOUR):
                true_daily[tenant_id]["event_count"] += 1
        for r in range(REQUESTS_PER_HOUR):
            row = _request_row(hour_index, r)
            bucket = true_daily[row["tenant_id"]]
            bucket["request_count"] += 1
            if row["status_code"] >= 500:
                bucket["error_count"] += 1
    shortfall_tenant = TENANTS[-1][0]
    rows = []
    for (tenant_id, _, _, _), totals in zip(TENANTS, true_daily.values(), strict=True):
        adjusted = dict(totals)
        if tenant_id == shortfall_tenant:
            # Remove the final operating hour's contribution entirely.
            adjusted["event_count"] -= len(TENANTS)
            adjusted["request_count"] -= sum(
                1 for r in range(REQUESTS_PER_HOUR) if TENANTS[r % 3][0] == shortfall_tenant
            )
            adjusted["error_count"] -= sum(
                1
                for r in range(REQUESTS_PER_HOUR)
                if TENANTS[r % 3][0] == shortfall_tenant
                and (r + (len(OPERATING_HOURS) - 1) * 2) % 8 == 3
            )
        rows.append({"usage_date": usage_date, "tenant_id": tenant_id, **adjusted})
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="target directory (default: generated-data/)",
    )
    args = parser.parse_args()
    generate(args.data_dir)
    print(f"fixtures written to {args.data_dir}")
