"""Tests for deterministic run-evidence profile composition (Plan 007)."""

from __future__ import annotations

from phlo.run_evidence.profiles import (
    EvidenceProfileCompositionError,
    EvidenceProfileContribution,
    compose_evidence_profile,
)
from phlo.run_evidence.reconciliation import RequiredEvidenceRecord, RequiredEvidenceStage


def _contribution(
    contribution_id: str,
    *,
    stage: str = "check",
    provider: str = "pandera",
    profile_version: str = "1",
    requires_terminal: bool = True,
    requires_contributions: tuple[str, ...] = (),
) -> EvidenceProfileContribution:
    return EvidenceProfileContribution(
        contribution_id=contribution_id,
        provider=provider,
        profile_id="wap",
        profile_version=profile_version,
        stages=(RequiredEvidenceStage(stage_type=stage, provider=provider),),
        requires_terminal_event=requires_terminal,
        requires_contributions=requires_contributions,
    )


ROOT = (
    "dlt.ingest",
    "dbt.transform",
    "pandera.check",
    "iceberg.snapshot",
    "nessie.catalog",
    "dagster.terminal",
)


def _six() -> list[EvidenceProfileContribution]:
    return [
        _contribution("dlt.ingest", stage="ingest", provider="dlt"),
        _contribution("dbt.transform", stage="transform", provider="dbt"),
        _contribution("pandera.check", stage="check", provider="pandera"),
        _contribution("iceberg.snapshot", stage="publish", provider="iceberg"),
        _contribution("nessie.catalog", stage="publish", provider="nessie"),
        _contribution("dagster.terminal", stage="lineage", provider="dagster"),
    ]


def test_composition_is_deterministic_regardless_of_registration_order() -> None:
    contributions = _six()
    import random

    for _ in range(5):
        random.shuffle(contributions)
        composed = compose_evidence_profile("wap", "1", ROOT, contributions)
        assert composed.available is True
        assert composed.missing_contribution_ids == ()
        assert len(composed.profile.stages) == 6
        assert composed.digest == compose_evidence_profile("wap", "1", ROOT, contributions).digest


def test_missing_required_contribution_is_unavailable_never_healthy() -> None:
    contributions = _six()[:-1]  # drop dagster.terminal
    composed = compose_evidence_profile("wap", "1", ROOT, contributions)
    assert composed.available is False
    assert composed.missing_contribution_ids == ("dagster.terminal",)
    assert composed.profile.stages == ()


def test_duplicate_contribution_id_is_rejected() -> None:
    contributions = _six() + [_contribution("dlt.ingest", stage="ingest", provider="dlt")]
    try:
        compose_evidence_profile("wap", "1", ROOT, contributions)
    except EvidenceProfileCompositionError as exc:
        assert exc.code == "duplicate_contribution"
    else:
        raise AssertionError("expected duplicate_contribution error")


def test_version_mismatch_is_not_merged() -> None:
    contributions = [
        _contribution("dlt.ingest", stage="ingest", provider="dlt", profile_version="2")
    ]
    composed = compose_evidence_profile("wap", "1", ROOT, contributions)
    assert composed.available is False
    assert composed.discovered_contribution_ids == ()


def test_missing_dependency_is_rejected() -> None:
    contributions = _six()
    contributions.append(
        _contribution(
            "extra.stage",
            stage="lineage",
            provider="extra",
            requires_contributions=("missing.dep",),
        )
    )
    try:
        compose_evidence_profile("wap", "1", ROOT + ("extra.stage",), contributions)
    except EvidenceProfileCompositionError as exc:
        assert exc.code == "missing_dependency"
    else:
        raise AssertionError("expected missing_dependency error")


def test_dependency_cycle_is_rejected() -> None:
    a = _contribution(
        "a.contribution", stage="lineage", provider="a", requires_contributions=("b.contribution",)
    )
    b = _contribution(
        "b.contribution", stage="lineage", provider="b", requires_contributions=("a.contribution",)
    )
    try:
        compose_evidence_profile("wap", "1", ("a.contribution", "b.contribution"), [a, b])
    except EvidenceProfileCompositionError as exc:
        assert exc.code == "dependency_cycle"
    else:
        raise AssertionError("expected dependency_cycle error")


def test_digest_changes_when_requirements_change() -> None:
    base = compose_evidence_profile("wap", "1", ROOT, _six())
    altered = _six()
    altered[0] = _contribution("dlt.ingest", stage="ingest", provider="dlt")  # same
    record_version = EvidenceProfileContribution(
        contribution_id="dlt.ingest",
        provider="dlt",
        profile_id="wap",
        profile_version="1",
        stages=(RequiredEvidenceStage(stage_type="ingest", provider="dlt"),),
        required_records=(RequiredEvidenceRecord(family="resource", minimum=2),),
    )
    altered[0] = record_version
    composed2 = compose_evidence_profile("wap", "1", ROOT, altered)
    assert composed2.digest != base.digest


def test_serialization_is_stable() -> None:
    composed = compose_evidence_profile("wap", "1", ROOT, _six())
    payload = composed.to_dict()
    assert payload["profile_id"] == "wap"
    assert payload["profile_version"] == "1"
    assert payload["available"] is True
    assert payload["digest"]
