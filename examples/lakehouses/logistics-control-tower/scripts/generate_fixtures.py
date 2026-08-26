"""Deterministic logistics fixtures for the control-tower lakehouse.

Everything the pipeline consumes is generated, never recorded: the PostgreSQL
source state (orders), per-carrier event scans served by the replay API,
per-warehouse CSV scans, reference tables, and labeled failure fixtures that
each break exactly one named invariant.

Layout produced under ``generated-data/``::

    base/orders.csv                     order versions present in the source database
    update/orders.csv                   strictly-newer delta applied by --stage update
    carriers/<CARRIER>/<YYYY-MM-DD>.json  per-day event pages served by scripts/carrier_api.py
    warehouses/<WH>/scans.csv           inbound/outbound scan pairs per warehouse
    reference/carrier_directory.csv     registered carriers and polling cadence
    reference/sla_terms.csv             SLA hours per carrier and service level
    failures/                           labeled single-invariant violations

All timestamps are UTC ISO-8601 strings so the generator is byte-stable.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

UTC = timezone.utc
BASE_START = date(2026, 8, 10)
BASE_DAYS = 3
ORDERS_PER_DAY = 8
WATERMARK = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
UPDATE_TS = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)

SHIPMENT_COUNT = 18
CONTRADICTION_SHIPMENT = "SHP-2018"
RECOVERED_SHIPMENT = "SHP-2017"
CONTRADICTION_DELIVERED_AT = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)
CONTRADICTION_EXCEPTION_AT = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)

STATUS_RANK = {
    "pending": 0,
    "allocated": 1,
    "shipped": 2,
    "delivered": 3,
    "cancelled": 4,
}
STATUS_CYCLE = ("pending", "allocated", "shipped", "delivered")
LOCATIONS = (
    "Rotterdam DC",
    "Hamburg Gateway",
    "Antwerp Crossdock",
    "Lyon Hub",
    "Milan Depot",
    "Madrid Gateway",
)
WAREHOUSES = ("WH-NORTH-01", "WH-CENTRAL-01", "WH-SOUTH-01")
CARRIERS = {
    "ATLAS": {
        "carrier_name": "Atlas Freight",
        "dispatch_email": "dispatch@atlasfreight.example",
        "polling_minutes": 60,
    },
    "CORSAIR": {
        "carrier_name": "Corsair Logistics",
        "dispatch_email": "ops@corsairlogistics.example",
        "polling_minutes": 240,
    },
}
SLA_TERMS = [
    ("ATLAS", "standard", 26),
    ("ATLAS", "express", 18),
    ("CORSAIR", "standard", 30),
    ("CORSAIR", "express", 22),
]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _shipment_carrier(index: int) -> str:
    """Even-numbered shipments move on ATLAS, odd ones on CORSAIR."""
    return "ATLAS" if index % 2 == 0 else "CORSAIR"


def _order_rows() -> list[dict[str, str]]:
    """Base order state: one row per version, including in-place advancements."""
    rows: list[dict[str, str]] = []
    for n in range(1, BASE_DAYS * ORDERS_PER_DAY + 1):
        day = BASE_START + timedelta(days=(n - 1) // ORDERS_PER_DAY)
        hour = 8 + ((n - 1) % ORDERS_PER_DAY) // 2
        minute = ((n - 1) % 2) * 30
        ordered_at = datetime.combine(day, time(hour, minute), tzinfo=UTC)
        status = "cancelled" if n % 7 == 0 else STATUS_CYCLE[n % 4]
        rows.append(
            {
                "order_id": f"ORD-{1000 + n}",
                "customer_ref": f"CUST-{101 + (n - 1) % 12:03d}",
                "status": status,
                "ordered_at": _iso(ordered_at),
                "updated_at": _iso(ordered_at + timedelta(minutes=30)),
            }
        )
    # A few orders advance in place before the watermark: the source database
    # keeps one current row per order, so seeding replays versions in order.
    advanced: list[dict[str, str]] = []
    for row in rows:
        if int(row["order_id"].split("-")[1]) % 6 == 0 and row["status"] != "cancelled":
            rank = min(STATUS_RANK[row["status"]] + 1, STATUS_RANK["cancelled"])
            next_status = next(name for name, value in STATUS_RANK.items() if value == rank)
            updated_at = datetime.fromisoformat(row["updated_at"]) + timedelta(hours=5)
            advanced.append({**row, "status": next_status, "updated_at": _iso(updated_at)})
    return sorted(rows + advanced, key=lambda item: (item["order_id"], item["updated_at"]))


def _update_order_rows(data_dir: Path) -> list[dict[str, str]]:
    """Delta rows that are all strictly newer than the replication watermark."""
    base = _order_rows()
    latest: dict[str, dict[str, str]] = {}
    for row in base:
        latest[row["order_id"]] = row

    updates: list[dict[str, str]] = []
    # New orders placed after the watermark.
    for offset in range(6):
        ordered_at = UPDATE_TS + timedelta(minutes=15 * offset)
        updates.append(
            {
                "order_id": f"ORD-{1025 + offset}",
                "customer_ref": f"CUST-{101 + offset:03d}",
                "status": STATUS_CYCLE[offset % 4],
                "ordered_at": _iso(ordered_at),
                "updated_at": _iso(ordered_at + timedelta(minutes=30)),
            }
        )
    # In-place status advancements of existing orders.
    for order_number in (1002, 1010, 1019, 1024):
        current = latest[f"ORD-{order_number}"]
        if current["status"] == "cancelled":
            continue
        rank = min(STATUS_RANK[current["status"]] + 1, STATUS_RANK["cancelled"])
        next_status = next(name for name, value in STATUS_RANK.items() if value == rank)
        updates.append(
            {
                "order_id": current["order_id"],
                "customer_ref": current["customer_ref"],
                "status": next_status,
                "ordered_at": current["ordered_at"],
                "updated_at": _iso(UPDATE_TS + timedelta(minutes=10 * order_number % 600)),
            }
        )
    del data_dir
    return sorted(updates, key=lambda item: (item["order_id"], item["updated_at"]))


def _carrier_events() -> list[dict[str, str]]:
    """Full carrier event stream for shipments SHP-2001 .. SHP-2018."""
    events: list[dict[str, object]] = []
    counter = 0

    def add(shipment: str, carrier: str, event_type: str, moment: datetime, location: str) -> None:
        nonlocal counter
        counter += 1
        events.append(
            {
                "event_id": f"EVT-{counter:04d}",
                "carrier": carrier,
                "shipment_id": shipment,
                "event_type": event_type,
                "event_time": _iso(moment),
                "location": location,
            }
        )

    for index in range(1, SHIPMENT_COUNT + 1):
        shipment = f"SHP-{2000 + index}"
        carrier = _shipment_carrier(index)
        location = LOCATIONS[index % len(LOCATIONS)]
        pickup = datetime.combine(BASE_START, time(8, 0), tzinfo=UTC) + timedelta(
            minutes=41 * index
        )
        add(shipment, carrier, "pickup", pickup, LOCATIONS[(index + 1) % len(LOCATIONS)])
        if shipment == RECOVERED_SHIPMENT:
            # Exception raised, then cleared: a later delivered event wins.
            add(shipment, carrier, "exception", pickup + timedelta(hours=26), location)
            add(shipment, carrier, "delivered", pickup + timedelta(hours=30), location)
            continue
        if shipment == CONTRADICTION_SHIPMENT:
            # Contradiction case: delivered first, exception later. The later
            # timestamp must win, and the mart must flag the contradiction.
            add(shipment, carrier, "delivered", CONTRADICTION_DELIVERED_AT, location)
            add(shipment, carrier, "exception", CONTRADICTION_EXCEPTION_AT, location)
            continue
        add(shipment, carrier, "in_transit", pickup + timedelta(hours=6), location)
        add(
            shipment,
            carrier,
            "delivered",
            pickup + timedelta(hours=20 + (index % 5) * 3),
            location,
        )
    return sorted(events, key=lambda item: str(item["event_id"]))


def _warehouse_scans(events: list[dict[str, object]]) -> list[dict[str, str]]:
    """One inbound/outbound scan pair per shipment; dwell varies by lane."""
    scans: list[dict[str, str]] = []
    counter = 0
    for index in range(1, SHIPMENT_COUNT + 1):
        shipment = f"SHP-{2000 + index}"
        warehouse = WAREHOUSES[index % len(WAREHOUSES)]
        dwell_hours = 4 + (index % 4) * 2
        pickup = datetime.combine(BASE_START, time(8, 0), tzinfo=UTC) + timedelta(
            minutes=41 * index
        )
        inbound = pickup - timedelta(hours=2)
        outbound = inbound + timedelta(hours=dwell_hours)
        for scan_type, moment in (("inbound", inbound), ("outbound", outbound)):
            counter += 1
            scans.append(
                {
                    "scan_id": f"SCAN-{counter:04d}",
                    "warehouse_id": warehouse,
                    "shipment_id": shipment,
                    "scan_type": scan_type,
                    "scanned_at": _iso(moment),
                }
            )
    del events
    return scans


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generate(data_dir: Path = DEFAULT_DATA_DIR, scale_name: str = "default") -> dict[str, int]:
    """Write the base source state, carrier scans, scans, and references."""
    del scale_name  # single deterministic scale; kept for CLI symmetry
    orders = _order_rows()
    events = _carrier_events()
    scans = _warehouse_scans(events)

    _write_csv(data_dir / "base" / "orders.csv", orders)

    event_dates = sorted({str(event["event_time"])[:10] for event in events})
    for carrier in CARRIERS:
        for event_date in event_dates:
            daily = [
                event
                for event in events
                if event["carrier"] == carrier and str(event["event_time"]).startswith(event_date)
            ]
            _write_json(data_dir / "carriers" / carrier / f"{event_date}.json", {"events": daily})

    by_warehouse: dict[str, list[dict[str, str]]] = {name: [] for name in WAREHOUSES}
    for scan in scans:
        by_warehouse[scan["warehouse_id"]].append(scan)
    for warehouse, rows in by_warehouse.items():
        _write_csv(data_dir / "warehouses" / warehouse / "scans.csv", rows)

    _write_csv(
        data_dir / "reference" / "carrier_directory.csv",
        [
            {
                "carrier_code": code,
                "carrier_name": meta["carrier_name"],
                "dispatch_email": meta["dispatch_email"],
                "polling_minutes": str(meta["polling_minutes"]),
            }
            for code, meta in sorted(CARRIERS.items())
        ],
    )
    _write_csv(
        data_dir / "reference" / "sla_terms.csv",
        [
            {
                "carrier_code": code,
                "service_level": level,
                "sla_hours": str(hours),
            }
            for code, level, hours in SLA_TERMS
        ],
    )
    return {
        "order_versions": len(orders),
        "distinct_orders": len({row["order_id"] for row in orders}),
        "shipments": SHIPMENT_COUNT,
        "carrier_events": len(events),
        "warehouse_scans": len(scans),
        "warehouses": len(WAREHOUSES),
        "carriers": len(CARRIERS),
    }


def build_update_set(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Derive strictly-newer delta rows and fail fast on watermark regressions."""
    updates = _update_order_rows(data_dir)
    base_latest = {}
    for row in _order_rows():
        base_latest[row["order_id"]] = row["updated_at"]
    for row in updates:
        previous = base_latest.get(row["order_id"])
        if previous is not None and row["updated_at"] <= previous:
            raise ValueError(f"delta row regresses the watermark: {row}")
        if previous is None and row["updated_at"] <= WATERMARK.isoformat():
            raise ValueError(f"new delta row predates the watermark: {row}")
    _write_csv(data_dir / "update" / "orders.csv", updates)
    return {"order_updates": len(updates)}


def write_failure_fixtures(data_dir: Path = DEFAULT_DATA_DIR) -> None:
    """Write labeled invalid fixtures; each breaks exactly ONE named invariant."""
    failures = data_dir / "failures"

    # orders_status_regression: a later version moves the status backwards.
    _write_csv(
        failures / "orders_status_regression.csv",
        [
            {
                "order_id": "ORD-9001",
                "customer_ref": "CUST-101",
                "status": "delivered",
                "ordered_at": _iso(datetime(2026, 8, 10, 8, 0, tzinfo=UTC)),
                "updated_at": _iso(datetime(2026, 8, 10, 12, 0, tzinfo=UTC)),
            },
            {
                "order_id": "ORD-9001",
                "customer_ref": "CUST-101",
                "status": "shipped",
                "ordered_at": _iso(datetime(2026, 8, 10, 8, 0, tzinfo=UTC)),
                "updated_at": _iso(datetime(2026, 8, 10, 18, 0, tzinfo=UTC)),
            },
        ],
    )

    # events_unknown_carrier: references a carrier absent from the directory.
    _write_json(
        failures / "events_unknown_carrier.json",
        {
            "events": [
                {
                    "event_id": "EVT-9001",
                    "carrier": "ZEPHYR",
                    "shipment_id": "SHP-2901",
                    "event_type": "pickup",
                    "event_time": _iso(datetime(2026, 8, 14, 8, 0, tzinfo=UTC)),
                    "location": "Unregistered Lane",
                }
            ]
        },
    )

    # events_ambiguous_state: identical timestamps with contradictory states
    # leave the event-time tiebreak undefined.
    ambiguous_time = _iso(datetime(2026, 8, 14, 12, 0, tzinfo=UTC))
    _write_json(
        failures / "events_ambiguous_state.json",
        {
            "events": [
                {
                    "event_id": "EVT-9002",
                    "carrier": "ATLAS",
                    "shipment_id": "SHP-2999",
                    "event_type": "delivered",
                    "event_time": ambiguous_time,
                    "location": "Ambiguity Yard",
                },
                {
                    "event_id": "EVT-9003",
                    "carrier": "ATLAS",
                    "shipment_id": "SHP-2999",
                    "event_type": "exception",
                    "event_time": ambiguous_time,
                    "location": "Ambiguity Yard",
                },
            ]
        },
    )

    # sla_clock_negative: an SLA term with a negative clock can never be met.
    _write_csv(
        failures / "sla_terms_negative.csv",
        [{"carrier_code": "CORSAIR", "service_level": "standard", "sla_hours": "-6"}],
    )


def _validate_watermarks(base_dir: Path, update_dir: Path) -> None:
    """Fail if any delta row could regress the incremental watermark."""
    with (base_dir / "orders.csv").open(encoding="utf-8") as handle:
        base_latest = {}
        for row in csv.DictReader(handle):
            base_latest[row["order_id"]] = row["updated_at"]
    with (update_dir / "orders.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            previous = base_latest.get(row["order_id"])
            if previous is not None and row["updated_at"] <= previous:
                raise ValueError(f"watermark regression for {row['order_id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--scenario",
        choices=("base", "update"),
        default="base",
        help="'update' also derives the strictly-newer delta set",
    )
    args = parser.parse_args()
    counts = generate(args.data_dir)
    print(f"generated base fixtures in {args.data_dir}: {counts}")
    if args.scenario == "update":
        counts = build_update_set(args.data_dir)
        print(f"built update set: {counts}")
        _validate_watermarks(args.data_dir / "base", args.data_dir / "update")
    write_failure_fixtures(args.data_dir)
