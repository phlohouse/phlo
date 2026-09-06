"""Tests for Kafka schema policy classification."""

from __future__ import annotations

from phlo_kafka.schema_policy import classify_field_change, evaluate_field_types


def test_type_widening_is_safe() -> None:
    assert (
        classify_field_change(field_name="a", existing_type="int", incoming_type="long") == "safe"
    )
    assert (
        classify_field_change(field_name="a", existing_type="int", incoming_type="double") == "safe"
    )


def test_narrowing_is_breaking() -> None:
    assert (
        classify_field_change(field_name="a", existing_type="long", incoming_type="int")
        == "breaking"
    )


def test_evaluate_field_types_reports_incompatible_batch() -> None:
    decision = evaluate_field_types(
        existing_schema={"event_id": "string", "count": "int"},
        records=[{"event_id": "e1", "count": "not-a-number"}],
    )
    assert decision.decision == "incompatible"
    assert "schema migration is required" in decision.reason


def test_evaluate_field_types_passes_compatible_batch() -> None:
    decision = evaluate_field_types(
        existing_schema={"event_id": "string", "count": "int"},
        records=[{"event_id": "e1", "count": 5, "new_field": "x"}],
    )
    assert decision.decision == "compatible"
