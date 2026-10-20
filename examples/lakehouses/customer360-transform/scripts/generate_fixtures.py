"""Deterministic Customer 360 fixtures.

Generates every input the example consumes:

- ``commerce/``: PostgreSQL source state for Sling replication (customers and
  orders as CSV) plus an ``update`` scenario whose rows are strictly newer
  than the base watermark.
- ``support/``: ticket payloads served by the replay HTTP API
  (``scripts/support_api.py``).
- ``marketing/``: contacts CSV and consent event JSON, including deliberate
  case and plus-suffix email variants that must collapse to one canonical
  identity.
- ``failures/``: labeled files where each breaks exactly ONE named invariant,
  proven by the test suite.

All output is byte-stable: no clocks, no randomness. The generator fails
loudly if its own invariants are violated (watermark ordering, unique consent
event keys).
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

BASE_START = date(2026, 6, 1)
WATERMARK = datetime(2026, 7, 10, 15, 20, tzinfo=UTC)
UPDATE_TS = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)

SEGMENTS = ("consumer", "business", "enterprise")
REGIONS = ("north", "south", "east", "west")
ORDER_STATUSES = ("pending", "shipped", "delivered", "cancelled")
CONSENT_SOURCES = ("web_form", "checkout", "import", "support_reply")

# One row per seeded person; emails below are written in their canonical form.
# Case variants and plus suffixes are applied on top of these addresses by the
# emitters so that identity resolution has real work to do.
PEOPLE = (
    ("alice.anderson@example.com", "Alice Anderson"),
    ("bob.belsky@example.com", "Bob Belsky"),
    ("carla.cruz@example.com", "Carla Cruz"),
    ("dana.dov@example.com", "Dana Dov"),
    ("evan.ellis@example.com", "Evan Ellis"),
    ("fiona.finn@example.com", "Fiona Finn"),
    ("greg.gustaf@example.com", "Greg Gustaf"),
    ("hana.holt@example.com", "Hana Holt"),
    ("zoe.zephyr@example.com", "Zoe Zephyr"),
)


def _iso(day: date, hour: int, minute: int) -> str:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC).isoformat()


def _customer_rows() -> list[dict[str, str]]:
    """Ten commerce customers over nine canonical people.

    Alice appears twice under distinct addresses (a plain address and a
    plus-suffixed legacy account), so identity resolution must collapse two
    customer records onto one canonical email before dimensioning. Names are
    derived from the local part of each address.
    """
    rows: list[tuple[str, str, str, str, str, str]] = [
        # customer_id, email, segment, region, signup_date, updated_at
        (
            "C0001",
            "alice.anderson@example.com",
            "consumer",
            "north",
            "2026-03-02",
            _iso(date(2026, 6, 1), 9, 15),
        ),
        (
            "C0002",
            "Alice.Anderson+legacy@example.com",
            "business",
            "east",
            "2025-11-18",
            _iso(date(2026, 6, 3), 14, 40),
        ),
        (
            "C0003",
            "bob.belsky@example.com",
            "enterprise",
            "south",
            "2026-01-27",
            _iso(date(2026, 6, 5), 11, 5),
        ),
        (
            "C0004",
            "carla.cruz@example.com",
            "consumer",
            "west",
            "2026-04-09",
            _iso(date(2026, 6, 7), 16, 25),
        ),
        (
            "C0005",
            "DANA.DOV@example.com",
            "business",
            "north",
            "2025-12-30",
            _iso(date(2026, 6, 9), 8, 50),
        ),
        (
            "C0006",
            "evan.ellis+shop@example.com",
            "consumer",
            "south",
            "2026-05-12",
            _iso(date(2026, 6, 11), 13, 35),
        ),
        (
            "C0007",
            "fiona.finn@example.com",
            "enterprise",
            "east",
            "2026-02-21",
            _iso(date(2026, 6, 13), 10, 10),
        ),
        (
            "C0008",
            "greg.gustaf@example.com",
            "consumer",
            "north",
            "2026-04-28",
            _iso(date(2026, 6, 15), 17, 55),
        ),
        (
            "C0009",
            "hana.holt@example.com",
            "business",
            "west",
            "2026-01-08",
            _iso(date(2026, 6, 17), 9, 45),
        ),
        (
            "C0010",
            "zoe.zephyr@example.com",
            "consumer",
            "east",
            "2026-05-30",
            _iso(date(2026, 7, 10), 15, 20),
        ),
    ]
    return [
        {
            "customer_id": r[0],
            "email": r[1],
            "full_name": r[1].split("@")[0].split("+")[0].replace(".", " ").title(),
            "segment": r[2],
            "region": r[3],
            "signup_date": r[4],
            "updated_at": r[5],
        }
        for r in rows
    ]


def _order_rows() -> list[dict[str, str]]:
    """Thirty orders across six days; order emails include case/plus variants."""
    # Order n buys from person index (n * 7) % len(people); greg (6) and zoe (8)
    # receive fewer orders, which keeps per-person counts uneven on purpose.
    order_emails = [PEOPLE[(n * 7) % len(PEOPLE)][0] for n in range(30)]
    # Two deliberately variant addresses so orders join through resolution too.
    order_emails[3] = "ALICE.ANDERSON+orders@example.com"
    order_emails[11] = "Bob.Belsky+vip@example.com"

    rows: list[dict[str, str]] = []
    sequence = 0
    for day_offset in range(6):
        day = date(2026, 7, 1) + timedelta(days=day_offset)
        for slot in range(5):
            sequence += 1
            email = order_emails[sequence - 1]
            status = ORDER_STATUSES[(day_offset + slot) % 4]
            total = round(18.0 + ((sequence * 37) % 220) + slot * 2.5, 2)
            placed_at = _iso(day, 8 + slot, (sequence * 13) % 60)
            rows.append(
                {
                    "order_id": f"O-{day.strftime('%Y%m%d')}-{slot + 1:03d}",
                    "email": email,
                    "status": status,
                    "currency": "USD",
                    "total_amount": f"{total:.2f}",
                    "ordered_at": placed_at,
                    "updated_at": placed_at,
                }
            )
    return rows


def _ticket_rows() -> list[dict[str, str | None]]:
    """Fourteen support tickets; open tickets carry a null resolved_at."""
    specs: list[tuple[str, str]] = [
        ("ALICE.ANDERSON+orders@example.com", "Cannot apply loyalty code"),
        ("BOB.BELSKY+vip@example.com", "Invoice VAT details wrong"),
        ("carla.cruz@example.com", "Where is my refund"),
        ("carla.cruz@example.com", "Duplicate charge on card"),
        ("DANA.DOV@example.com", "Change shipping address"),
        ("dana.dov+mobile@example.com", "App crashes on checkout"),
        ("evan.ellis@example.com", "Size guide missing"),
        ("fiona.finn@example.com", "Enterprise quote request"),
        ("Fiona.Finn+billing@example.com", "Download link expired"),
        ("bob.belsky@example.com", "Update payment method"),
        ("hana.holt@example.com", "Bulk export format"),
        ("alice.anderson@example.com", "Password reset loop"),
        ("CARLA.CRUZ@example.com", "Newsletter preferences broken"),
        ("bob.belsky@example.com", "API key rotation question"),
    ]
    tickets: list[dict[str, str | None]] = []
    for index, (email, subject) in enumerate(specs):
        created_day = date(2026, 7, 1) + timedelta(days=index // 2)
        created_at = _iso(created_day, 9 + (index % 8), (index * 17) % 60)
        open_ticket = index in (6, 9, 12)
        resolved_at = (
            None
            if open_ticket
            else _iso(created_day + timedelta(days=1), 10 + (index % 5), (index * 23) % 60)
        )
        tickets.append(
            {
                "ticket_id": f"TCK-{1001 + index}",
                "email": email,
                "subject": subject,
                "created_at": created_at,
                "resolved_at": resolved_at,
            }
        )
    return tickets


def _contact_rows() -> list[dict[str, str]]:
    rows: list[tuple[str, str, str, str]] = [
        # email, contact_name, list_segment, captured_at
        (
            "alice.anderson@example.com",
            "Alice Anderson",
            "lifecycle",
            _iso(date(2026, 5, 20), 9, 0),
        ),
        ("bob.belsky+news@example.com", "Bob Belsky", "newsletter", _iso(date(2026, 5, 21), 9, 30)),
        ("carla.cruz@example.com", "Carla Cruz", "lifecycle", _iso(date(2026, 5, 22), 10, 0)),
        ("DANA.DOV@example.com", "Dana Dov", "vip", _iso(date(2026, 5, 23), 10, 30)),
        ("evan.ellis@example.com", "Evan Ellis", "newsletter", _iso(date(2026, 5, 24), 11, 0)),
        (
            "fiona.finn+newsletter@example.com",
            "Fiona Finn",
            "newsletter",
            _iso(date(2026, 5, 25), 11, 30),
        ),
        ("hana.holt@example.com", "Hana Holt", "vip", _iso(date(2026, 5, 26), 12, 0)),
    ]
    return [
        {
            "email": r[0],
            "contact_name": r[1],
            "list_segment": r[2],
            "captured_at": r[3],
        }
        for r in rows
    ]


def _consent_events() -> list[dict[str, str]]:
    """Consent history per email; latest occurred_at decides the current state.

    Dana flips twice, Bob recovers after a revocation, Hana is never granted,
    and Zoe has no record at all - so consent_safe_product exercises granted,
    revoked, and never-consented outcomes.
    """
    events: list[tuple[str, str, str, str]] = [
        ("alice.anderson@example.com", "granted", "web_form", _iso(date(2026, 6, 1), 8, 0)),
        ("alice.anderson@example.com", "revoked", "support_reply", _iso(date(2026, 7, 5), 9, 15)),
        ("bob.belsky+news@example.com", "revoked", "web_form", _iso(date(2026, 6, 2), 8, 30)),
        ("bob.belsky+news@example.com", "granted", "checkout", _iso(date(2026, 7, 8), 10, 45)),
        ("carla.cruz@example.com", "granted", "web_form", _iso(date(2026, 6, 3), 9, 0)),
        ("DANA.DOV@example.com", "granted", "import", _iso(date(2026, 6, 4), 9, 30)),
        ("DANA.DOV@example.com", "revoked", "web_form", _iso(date(2026, 6, 20), 12, 0)),
        ("DANA.DOV@example.com", "granted", "checkout", _iso(date(2026, 7, 11), 14, 20)),
        ("evan.ellis@example.com", "granted", "checkout", _iso(date(2026, 6, 5), 10, 0)),
        (
            "fiona.finn+newsletter@example.com",
            "granted",
            "web_form",
            _iso(date(2026, 6, 6), 10, 30),
        ),
        (
            "fiona.finn+newsletter@example.com",
            "revoked",
            "support_reply",
            _iso(date(2026, 7, 1), 16, 40),
        ),
        ("greg.gustaf@example.com", "granted", "web_form", _iso(date(2026, 6, 7), 11, 0)),
        ("hana.holt@example.com", "revoked", "import", _iso(date(2026, 6, 8), 11, 30)),
    ]
    return [
        {
            "event_key": f"{email}|{occurred_at}|{source}",
            "email": email,
            "consent_status": status,
            "source": source,
            "occurred_at": occurred_at,
        }
        for email, status, source, occurred_at in events
    ]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_update_set(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Derive strictly-newer commerce delta rows from the base state.

    Two customers change segment, one new customer signs up, and four new
    orders arrive - all stamped after the base watermark. The function raises
    if any emitted row violates that watermark invariant, because incremental
    replication correctness depends on it.
    """
    base_dir = data_dir / "commerce" / "base"
    update_dir = data_dir / "commerce" / "update"
    update_dir.mkdir(parents=True, exist_ok=True)

    def read(name: str) -> list[dict[str, str]]:
        with (base_dir / f"{name}.csv").open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    customers = read("customers")
    orders = read("orders")

    base_watermark = max(row["updated_at"] for row in customers + orders)
    assert base_watermark == WATERMARK.isoformat(), (
        f"base watermark drifted: {base_watermark} != {WATERMARK.isoformat()}"
    )
    assert UPDATE_TS.isoformat() > base_watermark

    bumped = UPDATE_TS.isoformat()

    customer_updates: list[dict[str, str]] = []
    for row in customers:
        if row["customer_id"] in {"C0001", "C0007"}:
            revised = dict(row)
            revised["segment"] = SEGMENTS[(SEGMENTS.index(row["segment"]) + 1) % len(SEGMENTS)]
            revised["updated_at"] = bumped
            customer_updates.append(revised)
    customer_updates.append(
        {
            "customer_id": "C0011",
            "email": "ivy.ibex@example.com",
            "full_name": "Ivy Ibex",
            "segment": "consumer",
            "region": "south",
            "signup_date": "2026-07-19",
            "updated_at": bumped,
        }
    )

    order_updates: list[dict[str, str]] = []
    for slot in range(4):
        person_index = (slot * 3 + 1) % len(PEOPLE)
        total = round(25.0 + (slot * 41) % 180, 2)
        order_updates.append(
            {
                "order_id": f"O-U2026-{slot + 1:03d}",
                "email": PEOPLE[person_index][0],
                "status": ORDER_STATUSES[slot % 2],
                "currency": "USD",
                "total_amount": f"{total:.2f}",
                "ordered_at": bumped,
                "updated_at": bumped,
            }
        )

    _write_csv(update_dir / "customers.csv", customer_updates)
    _write_csv(update_dir / "orders.csv", order_updates)
    return {"customers": len(customer_updates), "orders": len(order_updates)}


def write_failure_fixtures(data_dir: Path = DEFAULT_DATA_DIR) -> None:
    """Labeled failure files; each breaks exactly one named invariant."""
    failures_dir = data_dir / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)

    # consent_tied_timestamps.json: two events for one email share an exact
    # occurred_at, so latest-wins precedence cannot be resolved. Breaks only
    # assert_consent_precedence_resolvable.
    tied = [
        {
            "event_key": "tied-a",
            "email": "priya.patel@example.com",
            "consent_status": "granted",
            "source": "web_form",
            "occurred_at": _iso(date(2026, 7, 15), 12, 0),
        },
        {
            "event_key": "tied-b",
            "email": "PRIYA.PATEL@example.com",
            "consent_status": "revoked",
            "source": "import",
            "occurred_at": _iso(date(2026, 7, 15), 12, 0),
        },
    ]

    # orders_unknown_email.json: one order references an email no customer,
    # contact, or ticket ever used. Breaks only
    # assert_orders_reference_known_customers.
    orphan = [
        {
            "order_id": "O-BAD-001",
            "email": "stranger.nowhere@example.com",
            "status": "delivered",
            "currency": "USD",
            "total_amount": "42.00",
            "ordered_at": UPDATE_TS.isoformat(),
            "updated_at": UPDATE_TS.isoformat(),
        }
    ]

    # ticket_backdated_resolution.json: resolved_at precedes created_at.
    # Breaks only assert_resolved_after_created.
    backdated = [
        {
            "ticket_id": "TCK-9001",
            "email": "lena.lone@example.com",
            "subject": "Resolution predates creation",
            "created_at": _iso(date(2026, 7, 14), 9, 0),
            "resolved_at": _iso(date(2026, 7, 13), 9, 0),
        }
    ]

    _write_json(failures_dir / "consent_tied_timestamps.json", {"events": tied})
    _write_json(failures_dir / "orders_unknown_email.json", {"orders": orphan})
    _write_json(failures_dir / "ticket_backdated_resolution.json", {"tickets": backdated})


def generate(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Write the full fixture tree and return row counts per dataset."""
    customers = _customer_rows()
    orders = _order_rows()
    tickets = _ticket_rows()
    contacts = _contact_rows()
    consent = _consent_events()

    keys = [event["event_key"] for event in consent]
    assert len(keys) == len(set(keys)), "consent event keys must be unique"
    max_order_ts = max(row["updated_at"] for row in orders)
    assert max_order_ts <= WATERMARK.isoformat(), "orders must respect the base watermark"

    _write_csv(data_dir / "commerce" / "base" / "customers.csv", customers)
    _write_csv(data_dir / "commerce" / "base" / "orders.csv", orders)
    _write_json(data_dir / "support" / "tickets.json", {"tickets": tickets})
    _write_csv(data_dir / "marketing" / "contacts.csv", contacts)
    _write_json(data_dir / "marketing" / "consent_events.json", {"events": consent})

    counts = {
        "commerce_customers": len(customers),
        "commerce_orders": len(orders),
        "support_tickets": len(tickets),
        "marketing_contacts": len(contacts),
        "consent_events": len(consent),
    }
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    summary = generate(args.data_dir)
    updates = build_update_set(args.data_dir)
    write_failure_fixtures(args.data_dir)
    print("fixtures:", json.dumps({**summary, **updates}, sort_keys=True))
