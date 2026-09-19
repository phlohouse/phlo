"""Failure codes become operator-readable blockers without losing the raw code."""

from __future__ import annotations

from phlo_api.observatory_api.observatory_wap import (
    WapReport,
    candidate_readiness,
    explain_failure,
)


def _report(**overrides) -> WapReport:
    fields = {
        "logical_run_id": "run-1",
        "status": "promotion_failed",
        "branch": "pipeline-run-1",
        "strategy": "branch",
        "dagster_run_id": "dag-1",
        "source_hash": "src",
        "target_branch": "main",
        "target_hash_before": "tgt",
        "target_hash_after": None,
        "launch_source_hash": "src",
        "launch_target_hash_before": "tgt",
        "launch_manifest_checksum": "chk",
        "launch_tags": {},
        "merge_state": "merge_failed",
        "release_id": None,
        "failure_reason": None,
        "failure_detail": None,
        "source_deleted": None,
        "candidates": (),
        "created_at": None,
        "updated_at": None,
        "raw": {},
    }
    fields.update(overrides)
    return WapReport(**fields)


def test_explain_failure_translates_known_code() -> None:
    text = explain_failure("merge_branch_returned_false")

    assert "catalog refused" in text
    assert "merge_branch_returned_false" in text
    assert "retry" in text


def test_explain_failure_passes_unknown_codes_through() -> None:
    assert explain_failure("some_new_provider_code") == "some_new_provider_code"
    assert explain_failure(None) == "unspecified"


def test_candidate_blocker_leads_with_explanation() -> None:
    report = _report(
        failure_reason="merge_branch_returned_false",
        failure_detail="HTTP 409: Merge conflict on keys: [raw.inventory]",
    )

    _readiness, blockers = candidate_readiness(report, manifest_valid=True)

    failure_blocker = next(b for b in blockers if "Promotion failed" in b)
    assert "catalog refused the merge" in failure_blocker
    assert "merge_branch_returned_false" in failure_blocker


def test_candidate_blocker_without_detail_stays_generic() -> None:
    report = _report(failure_reason="merge_branch_returned_false")

    _readiness, blockers = candidate_readiness(report, manifest_valid=True)

    failure_blocker = next(b for b in blockers if "Promotion failed" in b)
    assert "catalog refused the merge" in failure_blocker
