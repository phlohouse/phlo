"""Regression tests for contract metadata helpers.

Pins normalize/serialize behavior for consumer and SLA metadata: string
consumers are wrapped into Consumer records, and serialization emits plain
JSON-ready dicts or None when metadata is absent.
"""

from __future__ import annotations

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
