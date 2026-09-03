"""Blessed WAP evidence profile — full composition matrix (Plan 008).

Proves the ADR 0048 six-contributor profile composes deterministically and
that every missing required contributor yields an unavailable profile.
"""

from __future__ import annotations

from phlo.run_evidence.profiles import (
    EvidenceProfileContribution,
    compose_evidence_profile,
)
from phlo.run_evidence.reconciliation import RequiredEvidenceRecord, RequiredEvidenceStage

BLESSED_ROOT = (
    "dlt.ingest",
    "dbt.transform",
    "pandera.check",
    "iceberg.snapshot",
    "nessie.catalog",
    "dagster.terminal",
)


def _blessed_contributions() -> list[EvidenceProfileContribution]:
    specs = [
        ("dlt.ingest", "dlt", "ingest", "resource"),
        ("dbt.transform", "dbt", "transform", "artifact"),
        ("pandera.check", "pandera", "check", "quality_result"),
        ("iceberg.snapshot", "iceberg", "publish", "resource"),
        ("nessie.catalog", "nessie", "publish", "catalog_change"),
        ("dagster.terminal", "dagster", "lineage", "resource"),
    ]
    return [
        EvidenceProfileContribution(
            contribution_id=contribution_id,
            provider=provider,
            profile_id="wap",
            profile_version="1",
            stages=(RequiredEvidenceStage(stage_type=stage, provider=provider),),
            required_records=(RequiredEvidenceRecord(family=family, minimum=1),),
        )
        for contribution_id, provider, stage, family in specs
    ]


def test_blessed_profile_is_complete_and_deterministic() -> None:
    composed = compose_evidence_profile("wap", "1", BLESSED_ROOT, _blessed_contributions())
    assert composed.available is True
    assert composed.missing_contribution_ids == ()
    assert composed.discovered_contribution_ids == tuple(sorted(BLESSED_ROOT))
    assert len(composed.profile.stages) == 6
    assert (
        composed.digest
        == compose_evidence_profile(
            "wap", "1", BLESSED_ROOT, list(reversed(_blessed_contributions()))
        ).digest
    )


def test_each_missing_contributor_yields_unavailable_profile() -> None:
    for missing_id in BLESSED_ROOT:
        contributions = [c for c in _blessed_contributions() if c.contribution_id != missing_id]
        composed = compose_evidence_profile("wap", "1", BLESSED_ROOT, contributions)
        assert composed.available is False
        assert composed.missing_contribution_ids == (missing_id,)


def test_each_stage_and_record_family_is_required() -> None:
    composed = compose_evidence_profile("wap", "1", BLESSED_ROOT, _blessed_contributions())
    stage_types = {stage.stage_type for stage in composed.profile.stages}
    assert stage_types == {"ingest", "transform", "check", "publish", "lineage"}
    families = {record.family for record in composed.profile.required_records}
    assert families == {"resource", "artifact", "quality_result", "catalog_change"}
