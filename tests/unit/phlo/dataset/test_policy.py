"""Pure policy evaluator: versioned, project-configured, deterministic."""

from __future__ import annotations

import random
from typing import Any

import pytest

from phlo.dataset import (
    DatasetPolicy,
    EvidenceCondition,
    EvidenceRecord,
    MissingEvidence,
    PolicyRule,
    TransitionPolicy,
    evaluate_policy,
)


def _policy(**overrides: Any) -> DatasetPolicy:
    defaults: dict[str, Any] = {
        "policy_version": "v7",
        "rules": (
            PolicyRule(
                control="quality_checks_passed",
                evidence_kind="quality_checks",
                condition=EvidenceCondition(field="passed", operator="true"),
                message="quality failed",
                applies_to=frozenset({"claim"}),
            ),
        ),
        "transitions": (TransitionPolicy(action="publish", required_evidence=("run_evidence",)),),
    }
    defaults.update(overrides)
    return DatasetPolicy(**defaults)


def _evidence(**payload) -> tuple[EvidenceRecord, ...]:
    return (
        EvidenceRecord(kind="quality_checks", subject="t", payload={"passed": True, **payload}),
    )


class TestEvaluator:
    def test_ready_when_all_controls_pass(self) -> None:
        verdict = evaluate_policy(_policy(), "t", "claim", _evidence())
        assert verdict.ready is True
        assert verdict.controls[0]["status"] == "passed"
        assert verdict.missing_evidence == ()

    def test_missing_required_evidence_stays_missing(self) -> None:
        verdict = evaluate_policy(_policy(), "t", "publish", _evidence())
        assert verdict.ready is False
        assert verdict.blockers == ()
        assert verdict.missing_evidence == (
            MissingEvidence(
                kind="run_evidence",
                control=None,
                message="Required evidence of kind 'run_evidence' is missing for t.",
            ),
        )

    def test_missing_rule_evidence_names_the_control(self) -> None:
        verdict = evaluate_policy(_policy(), "t", "claim", ())
        assert verdict.ready is False
        assert verdict.missing_evidence[0].control == "quality_checks_passed"
        assert verdict.controls[0]["status"] == "missing"

    def test_failed_condition_becomes_a_blocker(self) -> None:
        verdict = evaluate_policy(_policy(), "t", "claim", _evidence(passed=False))
        assert verdict.ready is False
        assert verdict.missing_evidence == ()
        assert verdict.blockers[0].control == "quality_checks_passed"

    def test_warning_severity_never_blocks(self) -> None:
        policy = _policy(
            rules=(
                PolicyRule(
                    control="owner_recorded",
                    evidence_kind="ownership",
                    condition=EvidenceCondition(field="owner", operator="ne", value=None),
                    severity="warning",
                ),
            ),
        )
        verdict = evaluate_policy(policy, "t", "claim", ())
        assert verdict.ready is True
        assert verdict.warnings == ()
        assert [missing.kind for missing in verdict.missing_evidence] == ["ownership"]
        assert all(not missing.blocks for missing in verdict.missing_evidence)

    def test_missing_warning_evidence_does_not_block_but_present_failure_warns(self) -> None:
        policy = _policy(
            rules=(
                PolicyRule(
                    control="owner_recorded",
                    evidence_kind="ownership",
                    condition=EvidenceCondition(field="owner", operator="ne", value=None),
                    severity="warning",
                ),
            ),
        )
        verdict = evaluate_policy(
            policy,
            "t",
            "claim",
            (EvidenceRecord(kind="ownership", subject="t", payload={"owner": None}),),
        )
        assert verdict.ready is True
        assert verdict.missing_evidence == ()
        assert [warning.control for warning in verdict.warnings] == ["owner_recorded"]

    def test_verdict_is_unchanged_by_evidence_order(self) -> None:
        policy = _policy(
            rules=(
                PolicyRule(control="c1", evidence_kind="k1"),
                PolicyRule(control="c2", evidence_kind="k2"),
                PolicyRule(
                    control="c3",
                    evidence_kind="k3",
                    condition=EvidenceCondition(field="x", operator="eq", value=1),
                ),
            ),
            transitions=(),
        )
        records = [
            EvidenceRecord(kind="k1", subject="t", payload={}),
            EvidenceRecord(kind="k2", subject="t", payload={}),
            EvidenceRecord(kind="k3", subject="t", payload={"x": 1}),
        ]
        baseline = evaluate_policy(policy, "t", "claim", tuple(records))
        for seed in range(5):
            shuffled = list(records)
            random.Random(seed).shuffle(shuffled)
            verdict = evaluate_policy(policy, "t", "claim", tuple(shuffled))
            assert verdict.to_read_model() == baseline.to_read_model()

    def test_condition_operators(self) -> None:
        payload = {"n": 2, "tag": "gold", "ok": True}
        assert EvidenceCondition(field="n", operator="eq", value=2).matches(payload)
        assert EvidenceCondition(field="n", operator="ne", value=3).matches(payload)
        assert EvidenceCondition(field="tag", operator="in", value=["gold", "silver"]).matches(
            payload
        )
        assert EvidenceCondition(field="tag", operator="not_in", value=["bronze"]).matches(payload)
        assert EvidenceCondition(field="ok", operator="true").matches(payload)
        assert EvidenceCondition(field="ok", operator="false").matches({"ok": False})
        assert not EvidenceCondition(field="absent", operator="eq", value=1).matches(payload)

    def test_policy_requires_a_version(self) -> None:
        with pytest.raises(ValueError):
            DatasetPolicy(policy_version="")

    def test_verdict_carries_the_policy_version(self) -> None:
        verdict = evaluate_policy(_policy(policy_version="v9"), "t", "claim", _evidence())
        assert verdict.policy_version == "v9"

    def test_unconfigured_action_has_no_requirements(self) -> None:
        verdict = evaluate_policy(_policy(), "t", "review", ())
        assert verdict.ready is True
        assert verdict.controls == ()
