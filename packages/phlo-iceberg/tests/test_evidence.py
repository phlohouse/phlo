"""Tests for provider-neutral Iceberg mutation evidence semantics.

A successful mutation keeps its provider success even when readback evidence is
absent, evidence sink failures are contained instead of propagated, and emitted
errors are bounded so provider PII is never retained.
"""

from phlo_iceberg import evidence


def test_successful_mutation_with_absent_readback_keeps_provider_success(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        evidence,
        "emit_observation",
        lambda **kwargs: captured.append(kwargs),
    )

    evidence.emit_mutation(
        context={"project_id": "project", "run_id": "run", "attempt": 2},
        table_name="raw.events",
        ref="main",
        operation="append",
        status="success",
        before={"state": "present", "snapshot_id": "before"},
        after={"state": "absent", "snapshot_id": None},
    )

    assert captured[0]["status"] == "success"
    assert captured[0]["resources"][0]["resource_identity"] == {
        "resource_type": "iceberg_table",
        "resource_id": "raw.events",
        "tenant": "project",
        "attributes": {"catalog_ref": "main"},
    }
    assert captured[0]["resources"][0]["metadata"]["outcome"] == "contradictory"
    assert captured[0]["resources"][0]["metadata"]["evidence_completeness"] == "incomplete"


def test_mutation_evidence_sink_failure_is_contained(monkeypatch) -> None:
    def fail(**_kwargs):
        raise TypeError("evidence sink failed")

    monkeypatch.setattr(evidence, "emit_observation", fail)

    evidence.emit_mutation(
        context={"project_id": "project", "run_id": "run", "attempt": 1},
        table_name="raw.events",
        ref="main",
        operation="append",
        status="success",
        before={"state": "present"},
        after={"state": "unavailable"},
    )


def test_mutation_error_is_bounded_and_does_not_retain_provider_pii(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        evidence,
        "emit_observation",
        lambda **kwargs: captured.append(kwargs),
    )

    evidence.emit_mutation(
        context={"project_id": "project", "run_id": "run", "attempt": 1},
        table_name="raw.events",
        ref="main",
        operation="append",
        status="failed",
        before={"state": "present"},
        after={"state": "unavailable"},
        error="customer@example.com account=acct-123",
    )

    assert "customer@example.com" not in captured[0]["error"]
    assert "acct-123" not in captured[0]["error"]
    assert "fingerprint:" in captured[0]["error"]
