"""Generate the deterministic fixture set for the federated-domains example.

The generator writes every byte the example consumes:

- ``sales/deals.csv``: one CRM deal extract snapshot; the reference-style
  source the sales domain merges by ``deal_id``.
- ``finance/invoices.json``: invoice records; a fixed subset references sales
  ``deal_id`` values so cross-domain contracts are exercisable.
- ``operations/incidents.csv``: incident records with resolved and open rows.
- ``failures/``: labeled invalid fixtures; each breaks exactly one named
  invariant (deal stage vocabulary, known-deal attribution, resolution
  consistency).

Every value derives from fixed constants, so regenerating produces identical
files.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

AGING_HORIZON = date(2026, 8, 31)

DEAL_COLUMNS = [
    "deal_id",
    "account_name",
    "owner",
    "amount_usd",
    "stage",
    "opened_on",
    "stage_updated_at",
]
DEAL_STAGES = [
    "prospecting",
    "qualification",
    "proposal",
    "negotiation",
    "won",
    "won",
    "lost",
    "proposal",
    "qualification",
    "negotiation",
    "won",
    "lost",
]
VALID_STAGES = ("prospecting", "qualification", "proposal", "negotiation", "won", "lost")
ACCOUNTS = ["Northwind Traders", "Contoso Logistics", "Fabrikam Energy", "Litware Systems"]
OWNERS = ["jordan", "priya", "sam"]

INVOICE_PAID_INDICES = (1, 3, 6)
INVOICE_ISSUED_ON = [
    "2026-08-25",
    "2026-08-15",
    "2026-07-18",
    "2026-07-05",
    "2026-06-20",
    "2026-06-02",
    "2026-05-10",
    "2026-05-01",
]

INCIDENT_SERVICES = ["checkout", "payments", "inventory"]
INCIDENT_SEVERITIES = ["sev2", "sev1", "sev3", "sev4"]
INCIDENT_COUNT = 10


def _deals() -> list[dict[str, object]]:
    """Twelve CRM deals with fixed amounts, stages, and stage-change times."""
    rows: list[dict[str, object]] = []
    for index in range(12):
        opened = date(2026, 3 + index // 6, (index % 27) + 1)
        updated = opened + timedelta(days=(index % 9) + 1)
        updated_hour = 9 + (index % 4) * 3
        rows.append(
            {
                "deal_id": f"DL-{1001 + index}",
                "account_name": ACCOUNTS[index % len(ACCOUNTS)],
                "owner": OWNERS[index % len(OWNERS)],
                "amount_usd": f"{5000 + 1250 * index:.2f}",
                "stage": DEAL_STAGES[index],
                "opened_on": opened.isoformat(),
                "stage_updated_at": f"{updated.isoformat()}T{updated_hour:02d}:00:00",
            }
        )
    return rows


def _invoices() -> list[dict[str, object]]:
    """Eight invoices; indices in INVOICE_PAID_INDICES settled before due."""
    records: list[dict[str, object]] = []
    for index, issued_label in enumerate(INVOICE_ISSUED_ON):
        issued = date.fromisoformat(issued_label)
        due = issued + timedelta(days=30)
        paid: str | None = None
        if index in INVOICE_PAID_INDICES:
            paid = (issued + timedelta(days=7 + index * 2)).isoformat()
        records.append(
            {
                "invoice_id": f"INV-{2001 + index}",
                "customer": ACCOUNTS[index % len(ACCOUNTS)],
                "deal_id": f"DL-{1001 + index}",
                "amount_usd": float(f"{1200 + 350 * index}.0"),
                "issued_on": issued.isoformat(),
                "due_on": due.isoformat(),
                "paid_on": paid,
            }
        )
    return records


def _incidents() -> list[dict[str, object]]:
    """Ten incidents; even indices are resolved with positive durations."""
    rows: list[dict[str, object]] = []
    for index in range(INCIDENT_COUNT):
        opened = datetime(2026, 8, 18 + index, 8 + (index % 6), 15, 0)
        if index % 2 == 0:
            minutes = 15 + 13 * index
            resolved = opened + timedelta(minutes=minutes)
            resolved_at = resolved.strftime("%Y-%m-%dT%H:%M:%S")
            resolution_minutes = f"{minutes}"
        else:
            resolved_at = ""
            resolution_minutes = ""
        rows.append(
            {
                "incident_id": f"INC-{3001 + index}",
                "service": INCIDENT_SERVICES[index % len(INCIDENT_SERVICES)],
                "severity": INCIDENT_SEVERITIES[index % len(INCIDENT_SEVERITIES)],
                "opened_at": opened.strftime("%Y-%m-%dT%H:%M:%S"),
                "resolved_at": resolved_at,
                "resolution_minutes": resolution_minutes,
            }
        )
    return rows


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, object]]) -> None:
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row[column]) for column in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_json(path: Path, records: list[dict[str, object]]) -> None:
    payload = json.dumps(records, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8", newline="\n")


def generate(data_dir: Path) -> None:
    """Write every fixture under ``data_dir``, replacing any previous set."""
    if data_dir.exists():
        import shutil

        shutil.rmtree(data_dir)
    sales_dir = data_dir / "sales"
    finance_dir = data_dir / "finance"
    operations_dir = data_dir / "operations"
    failures_dir = data_dir / "failures"
    for directory in (sales_dir, finance_dir, operations_dir, failures_dir):
        directory.mkdir(parents=True)

    _write_csv(sales_dir / "deals.csv", DEAL_COLUMNS, _deals())
    _write_json(finance_dir / "invoices.json", _invoices())
    _write_csv(
        operations_dir / "incidents.csv",
        ["incident_id", "service", "severity", "opened_at", "resolved_at", "resolution_minutes"],
        _incidents(),
    )

    # Each failure fixture breaks exactly ONE named invariant and stays valid
    # against every other contract and validator.
    _write_csv(
        failures_dir / "deals_invalid_stage.csv",
        DEAL_COLUMNS,
        [
            {
                "deal_id": "DL-1099",
                "account_name": "Ghost Retail",
                "owner": "avery",
                "amount_usd": "4200.00",
                "stage": "archived",
                "opened_on": "2026-04-10",
                "stage_updated_at": "2026-04-11T10:00:00",
            }
        ],
    )
    _write_json(
        failures_dir / "invoices_unknown_deal.json",
        [
            {
                "invoice_id": "INV-2999",
                "customer": "Ghost Co",
                "deal_id": "DL-9999",
                "amount_usd": 900.0,
                "issued_on": "2026-08-31",
                "due_on": "2026-09-30",
                "paid_on": None,
            }
        ],
    )
    _write_csv(
        failures_dir / "incidents_negative_duration.csv",
        ["incident_id", "service", "severity", "opened_at", "resolved_at", "resolution_minutes"],
        [
            {
                "incident_id": "INC-3999",
                "service": "checkout",
                "severity": "sev2",
                "opened_at": "2026-08-28T10:00:00",
                "resolved_at": "2026-08-28T09:55:00",
                "resolution_minutes": "-5",
            }
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    generate(args.data_dir)
    print(f"fixtures written to {args.data_dir}")


if __name__ == "__main__":
    main()
