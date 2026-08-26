"""Write deterministic paginated SaaS API and account-plan fixtures."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def event(
    event_id: str,
    occurred_at: str,
    account_id: str,
    actor_id: str,
    event_type: str,
    **properties: object,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "occurred_at": occurred_at,
        "account": {"id": account_id, "name": f"Account {account_id}"},
        "actor": {"id": actor_id, "email": f"{actor_id}@example.test"},
        "event": {"type": event_type, "properties": properties},
        "context": {"session_id": f"session-{actor_id}-1", "release": "2025.01"},
    }


def generate(data: Path) -> dict[str, int]:
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    pages = [
        [
            event("evt-001", "2025-01-01T09:00:00Z", "acc-1", "user-1", "signup"),
            event("evt-002", "2025-01-01T09:05:00Z", "acc-1", "user-1", "project_created"),
            event(
                "evt-003",
                "2025-01-01T09:07:00Z",
                "acc-1",
                "user-1",
                "feature_used",
                feature="boards",
            ),
        ],
        [
            event("evt-004", "2025-01-01T10:00:00Z", "acc-2", "user-2", "signup"),
            event("evt-005", "2025-01-01T10:03:00Z", "acc-2", "user-2", "release_viewed"),
            event(
                "evt-006",
                "2025-01-02T08:00:00Z",
                "acc-1",
                "user-1",
                "feature_used",
                feature="automations",
            ),
        ],
        [
            event(
                "evt-007",
                "2025-01-02T08:05:00Z",
                "acc-1",
                "user-1",
                "feature_used",
                feature="boards",
                experiment_variant="treatment",
            ),
            event("evt-008", "2025-01-02T11:00:00Z", "acc-3", "user-3", "signup"),
        ],
    ]
    baseline_pages = copy.deepcopy(pages)
    for page in baseline_pages:
        for source_event in page:
            event_data = source_event["event"]
            properties = event_data.get("properties", {})
            properties.pop("experiment_variant", None)
    (data / "events-v1.json").write_text(json.dumps(baseline_pages, indent=2), encoding="utf-8")
    (data / "events-v2.json").write_text(json.dumps(pages, indent=2), encoding="utf-8")
    (data / "events.json").write_text(json.dumps(pages, indent=2), encoding="utf-8")
    with (data / "account_plans.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["account_id", "plan", "seats", "effective_at"])
        writer.writeheader()
        writer.writerows(
            [
                {"account_id": "acc-1", "plan": "pro", "seats": 20, "effective_at": "2025-01-01"},
                {"account_id": "acc-2", "plan": "free", "seats": 3, "effective_at": "2025-01-01"},
                {
                    "account_id": "acc-3",
                    "plan": "enterprise",
                    "seats": 100,
                    "effective_at": "2025-01-02",
                },
            ]
        )
    failures = data / "failures"
    failures.mkdir()
    invalid = dict(pages[0][0])
    invalid["event"] = {"type": "unknown"}
    (failures / "invalid_event.json").write_text(json.dumps([[invalid]]), encoding="utf-8")
    return {"pages": len(pages), "events": sum(map(len, pages)), "plans": 3}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "generated-data")
    args = parser.parse_args()
    print(generate(args.data_dir))
