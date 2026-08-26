from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import dagster as dg
import pandas as pd
import pytest
from phlo_dlt import get_ingestion_assets

from scripts.generate_fixtures import generate
from scripts.replay_server import ReplayHandler, serve
from workflows.product_analytics import schedules
from workflows.product_analytics.ingestion.saas import normalize_event, read_paginated_events
from workflows.product_analytics.quality.events import validate_events, validate_freshness
from workflows.product_analytics.schemas.events import EventsSchema


def fixture(tmp_path: Path) -> Path:
    data = tmp_path / "generated-data"
    generate(data)
    return data


def test_replay_is_paginated_complete_and_retries_rate_limit(tmp_path: Path) -> None:
    data = fixture(tmp_path)
    port = 18091
    thread = threading.Thread(target=serve, args=(data, port), daemon=True)
    thread.start()
    ready = False
    for _ in range(20):
        try:
            urlopen(f"http://127.0.0.1:{port}/v1/events?cursor=0", timeout=1).close()  # noqa: S310
            ready = True
            break
        except OSError:
            time.sleep(0.05)
    assert ready
    events = read_paginated_events(f"http://127.0.0.1:{port}/v1/events")
    assert [event["event_id"] for event in events] == [f"evt-00{i}" for i in range(1, 9)]
    assert ReplayHandler.rate_limited


def test_normalization_retains_required_nested_fields_and_optional_evolution(
    tmp_path: Path,
) -> None:
    data = fixture(tmp_path)
    baseline_pages = json.loads((data / "events-v1.json").read_text(encoding="utf-8"))
    evolved_pages = json.loads((data / "events-v2.json").read_text(encoding="utf-8"))
    baseline = [normalize_event(event) for page in baseline_pages for event in page]
    evolved = [normalize_event(event) for page in evolved_pages for event in page]
    EventsSchema.validate(pd.DataFrame(baseline))
    EventsSchema.validate(pd.DataFrame(evolved))
    assert baseline[0] == {
        "event_id": "evt-001",
        "occurred_at": "2025-01-01T09:00:00Z",
        "account_id": "acc-1",
        "account_name": "Account acc-1",
        "actor_id": "user-1",
        "actor_email": "user-1@example.test",
        "event_type": "signup",
        "feature": None,
        "experiment_variant": None,
        "session_id": "session-user-1-1",
        "release": "2025.01",
    }
    assert all(event["experiment_variant"] is None for event in baseline)
    assert evolved[6]["experiment_variant"] == "treatment"
    assert {event["event_id"] for event in baseline} == {event["event_id"] for event in evolved}


def test_quality_rejects_unknown_events_and_detects_stale_replay(tmp_path: Path) -> None:
    data = fixture(tmp_path)
    pages = json.loads((data / "events.json").read_text(encoding="utf-8"))
    events = pd.DataFrame([normalize_event(event) for page in pages for event in page])
    validate_events(events)
    validate_freshness(events, "2025-01-02T11:00:00Z")
    invalid_pages = json.loads((data / "failures" / "invalid_event.json").read_text())
    invalid = pd.DataFrame([normalize_event(event) for page in invalid_pages for event in page])
    with pytest.raises(ValueError, match="Unsupported"):
        validate_events(invalid)
    with pytest.raises(ValueError, match="stale"):
        validate_freshness(events, "2025-01-03T00:00:00Z")


def test_assets_and_schedules_have_differentiated_contracts() -> None:
    assets = {
        asset.key: asset for asset in get_ingestion_assets() if asset.group == "product_analytics"
    }
    assert set(assets) == {"dlt_saas_events", "dlt_saas_account_plans"}
    assert assets["dlt_saas_events"].metadata["write_mode"] == "merge"
    assert assets["dlt_saas_events"].run.max_retries == 3
    assert assets["dlt_saas_events"].run.freshness_hours == (1, 2)
    assert assets["dlt_saas_account_plans"].metadata["owner"] == "revenue-operations"
    assert all(asset.checks[0].blocking for asset in assets.values())
    registered = (
        schedules.hourly_events_schedule,
        schedules.daily_cohorts_schedule,
        schedules.weekly_publication_schedule,
    )
    assert {schedule.cron_schedule for schedule in registered} == {
        "10 * * * *",
        "30 2 * * *",
        "0 4 * * 1",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in registered
    )


def test_models_preserve_order_and_optional_schema_field() -> None:
    root = Path("workflows/product_analytics/transforms/dbt/models")
    flattened = (root / "flattened_events.sql").read_text(encoding="utf-8")
    sessions = (root / "sessions.sql").read_text(encoding="utf-8")
    assert "experiment_variant" in flattened
    assert "order by occurred_at, event_id" in sessions
    activation = (root / "activation.sql").read_text(encoding="utf-8")
    assert "max(case when event_type = 'project_created' then 1 else 0 end)" in activation


def test_fixture_has_deterministic_product_outcomes(tmp_path: Path) -> None:
    data = fixture(tmp_path)
    pages = json.loads((data / "events-v2.json").read_text(encoding="utf-8"))
    events = [normalize_event(event) for page in pages for event in page]

    assert len(events) == len({event["event_id"] for event in events}) == 8
    assert {event["account_id"] for event in events} == {"acc-1", "acc-2", "acc-3"}
    assert [event["event_type"] for event in events].count("project_created") == 1
    assert [event["feature"] for event in events].count("boards") == 2
    assert [event["feature"] for event in events].count("automations") == 1
    assert sum(event["feature"] is None for event in events) == 5
    assert sum(event["experiment_variant"] == "treatment" for event in events) == 1
