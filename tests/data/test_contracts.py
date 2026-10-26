"""Regression tests for contract metadata helpers.

Pins normalize/serialize behavior for consumer and SLA metadata: string
consumers are wrapped into Consumer records, and serialization emits plain
JSON-ready dicts or None when metadata is absent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from phlo.contracts import SLA, Consumer, normalize_consumers, serialize_consumers, serialize_sla


def test_normalize_consumers_handles_none_and_existing_records() -> None:
    """normalize_consumers should preserve Consumer records and wrap strings."""
    existing = Consumer(name="analytics", contact="#analytics", usage="dashboards")

    normalized = normalize_consumers([existing, "ml-pipeline"])

    assert normalized == [
        Consumer(name="analytics", contact="#analytics", usage="dashboards"),
        Consumer(name="ml-pipeline"),
    ]
    assert normalize_consumers(None) == []


def test_serialize_consumers_emits_plain_dicts() -> None:
    """serialize_consumers should produce JSON-ready payloads."""
    consumers = [Consumer(name="analytics", contact="#analytics", usage="dashboards")]

    assert serialize_consumers(consumers) == [
        {"name": "analytics", "contact": "#analytics", "usage": "dashboards"}
    ]


def test_serialize_sla_emits_plain_dict_or_none() -> None:
    """serialize_sla should preserve field values and handle missing SLA."""
    sla = SLA(freshness_hours=24, quality_threshold=0.95, max_failures=2, notify=["#alerts"])

    assert serialize_sla(None) is None
    assert serialize_sla(sla) == {
        "freshness_hours": 24,
        "quality_threshold": 0.95,
        "max_failures": 2,
        "notify": ["#alerts"],
    }


def test_explicit_sla_reference_is_json_safe_and_preserves_instant() -> None:
    reference = datetime(2025, 1, 15, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    sla = SLA(2, freshness_column="event_time", freshness_reference_time=reference)
    payload = json.loads(json.dumps(serialize_sla(sla)))
    assert payload["freshness_column"] == "event_time"
    assert payload["freshness_reference_time"] == "2025-01-15T17:30:00+05:30"
    payload["freshness_reference_time"] = datetime.fromisoformat(
        payload["freshness_reference_time"]
    )
    assert SLA(**payload) == sla
    assert payload["freshness_reference_time"].astimezone(UTC).hour == 12


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"freshness_column": "event_time"}, "freshness_hours"),
        ({"freshness_hours": -1, "freshness_column": "event_time"}, "freshness_hours"),
        ({"freshness_hours": 2, "freshness_column": " "}, "freshness_column"),
        ({"freshness_reference_time": datetime.now(UTC)}, "freshness_column"),
        (
            {"freshness_hours": 2, "freshness_reference_time": datetime.now(UTC)},
            "freshness_column",
        ),
        (
            {
                "freshness_hours": 2,
                "freshness_column": "event_time",
                "freshness_reference_time": datetime(2025, 1, 15, tzinfo=UTC).replace(tzinfo=None),
            },
            "timezone-aware",
        ),
    ],
)
def test_explicit_sla_rejects_incomplete_or_ambiguous_freshness(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SLA(**kwargs)
