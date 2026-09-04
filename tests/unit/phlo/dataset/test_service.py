"""Service tests for the neutral Dataset core.

One canonical Dataset fixture evaluated through the core service must yield
deterministic controls, blockers, warnings, missing evidence, readiness, and
allowed/blocked transitions -- unchanged by contributor discovery order, with
missing evidence kept missing and every lifecycle mutation idempotent by
client ``action_id``.
"""

from __future__ import annotations

import random

from phlo.dataset import (
    DatasetRecord,
    DatasetService,
    EvidenceRecord,
    TransitionRequest,
    TransitionStatus,
    WorkflowState,
)
from tests.unit.phlo.dataset.conftest import (
    CANONICAL_CANDIDATE_ID,
    CANONICAL_DATASET_ID,
    FIXTURE_POLICY_VERSION,
    FixtureEvidenceSource,
    InMemoryStateStore,
    StaticPolicySource,
    complete_evidence,
    project_policy,
)


def _request(resource_id: str, action: str, action_id: str, **kwargs) -> TransitionRequest:
    return TransitionRequest(
        resource_id=resource_id,
        action=action,
        action_id=action_id,
        actor="operator-alice",
        scope="lakehouse:operate",
        **kwargs,
    )


def _promote(service) -> None:
    for action_id, action in (("a1", "claim"), ("a2", "review"), ("a3", "promote")):
        outcome = service.transition(_request(CANONICAL_CANDIDATE_ID, action, action_id))
        assert outcome.status is TransitionStatus.COMMITTED


class TestLifecycle:
    def test_candidate_workflow_follows_the_state_machine(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        assert service.allowed_transitions(candidate) == ("claim",)
        assert (
            service.transition(_request(candidate, "claim", "a1")).status
            is TransitionStatus.COMMITTED
        )
        assert service.allowed_transitions(candidate) == ("review",)
        assert (
            service.transition(_request(candidate, "review", "a2")).status
            is TransitionStatus.COMMITTED
        )
        assert service.allowed_transitions(candidate) == ("promote", "reject")
        outcome = service.transition(_request(candidate, "promote", "a3"))
        assert outcome.status is TransitionStatus.COMMITTED
        record = service.record(candidate)
        assert record.state == WorkflowState.PROMOTED.value
        assert record.promoted_dataset_id == CANONICAL_DATASET_ID

    def test_promotion_preserves_identity_and_creates_draft(self, service) -> None:
        _promote(service)
        candidate = service.record(CANONICAL_CANDIDATE_ID)
        assert candidate.state == WorkflowState.PROMOTED.value
        assert candidate.promoted_dataset_id == CANONICAL_DATASET_ID
        promoted = service.record(CANONICAL_DATASET_ID)
        assert isinstance(promoted, DatasetRecord)
        assert promoted.publication_state == "draft"

    def test_publish_then_retire_with_retired_terminal(self, service) -> None:
        _promote(service)
        publish = service.transition(_request(CANONICAL_DATASET_ID, "publish", "p1"))
        assert publish.status is TransitionStatus.COMMITTED
        assert service.record(CANONICAL_DATASET_ID).publication_state == "published"
        retire = service.transition(_request(CANONICAL_DATASET_ID, "retire", "r1"))
        assert retire.status is TransitionStatus.COMMITTED
        assert service.record(CANONICAL_DATASET_ID).publication_state == "retired"
        assert service.allowed_transitions(CANONICAL_DATASET_ID) == ()

    def test_reject_is_terminal(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        service.transition(_request(candidate, "claim", "a1"))
        service.transition(_request(candidate, "review", "a2"))
        outcome = service.transition(_request(candidate, "reject", "a3"))
        assert outcome.status is TransitionStatus.COMMITTED
        assert service.allowed_transitions(candidate) == ()
        conflict = service.transition(_request(candidate, "promote", "a4"))
        assert conflict.status is TransitionStatus.CONFLICT

    def test_state_machine_conflicts_name_the_conflict_and_write_nothing(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        blocked = service.transition(_request(candidate, "promote", "a1"))
        assert blocked.status is TransitionStatus.CONFLICT
        assert "promote" in blocked.message
        assert "open" in blocked.message
        assert service.record(candidate) is None

        _promote(service)
        conflict = service.transition(_request(CANONICAL_DATASET_ID, "retire", "r1"))
        assert conflict.status is TransitionStatus.CONFLICT
        assert "draft" in conflict.message
        assert service.record(CANONICAL_DATASET_ID).publication_state == "draft"

    def test_publish_on_retired_is_a_conflict(self, service) -> None:
        _promote(service)
        service.transition(_request(CANONICAL_DATASET_ID, "publish", "p1"))
        service.transition(_request(CANONICAL_DATASET_ID, "retire", "r1"))
        outcome = service.transition(_request(CANONICAL_DATASET_ID, "publish", "p2"))
        assert outcome.status is TransitionStatus.CONFLICT
        assert "retired" in outcome.message


class TestIdempotency:
    def test_replayed_action_id_returns_the_committed_outcome(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        first = service.transition(_request(candidate, "claim", "a1"))
        replay = service.transition(_request(candidate, "claim", "a1"))
        assert replay.status is TransitionStatus.REPLAYED
        assert replay.after_state == first.after_state == WorkflowState.CLAIMED.value

    def test_same_action_id_with_different_request_conflicts(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        service.transition(_request(candidate, "claim", "a1"))
        outcome = service.transition(_request(candidate, "review", "a1"))
        assert outcome.status is TransitionStatus.CONFLICT
        assert "action_id" in outcome.message

    def test_publishing_an_already_published_dataset_is_idempotent_success(self, service) -> None:
        _promote(service)
        first = service.transition(_request(CANONICAL_DATASET_ID, "publish", "p1"))
        assert first.status is TransitionStatus.COMMITTED
        replay = service.transition(_request(CANONICAL_DATASET_ID, "publish", "p2"))
        assert replay.status is TransitionStatus.IDEMPOTENT
        assert replay.after_state == "published"


class TestReadiness:
    def test_verdict_is_deterministic_and_order_independent(self, make_service) -> None:
        shuffled = list(complete_evidence())
        random.Random(7).shuffle(shuffled)
        baseline = make_service().readiness(CANONICAL_CANDIDATE_ID)
        reordered = make_service(records=shuffled).readiness(CANONICAL_CANDIDATE_ID)
        assert reordered.to_read_model() == baseline.to_read_model()
        assert baseline.ready is True
        assert baseline.reasons == ()

    def test_missing_evidence_is_reported_and_blocks_without_failing_controls(
        self, make_service
    ) -> None:
        records = [record for record in complete_evidence() if record.kind != "run_evidence"]
        sparse = make_service(records=records)
        verdict = sparse.readiness(CANONICAL_DATASET_ID, "publish")
        assert not verdict.ready
        assert [missing.kind for missing in verdict.missing_evidence] == ["run_evidence"]
        assert verdict.blockers == ()
        statuses = {control["control"]: control["status"] for control in verdict.controls}
        assert statuses["quality_checks_passed"] == "passed"
        assert statuses["governance_declarations_present"] == "passed"

    def test_publish_blocked_until_missing_evidence_arrives(self, make_service) -> None:
        records = [record for record in complete_evidence() if record.kind != "run_evidence"]
        sparse = make_service(records=records)
        _promote(sparse)
        blocked = sparse.transition(_request(CANONICAL_DATASET_ID, "publish", "p1"))
        assert blocked.status is TransitionStatus.BLOCKED
        assert blocked.verdict.missing_evidence[0].kind == "run_evidence"
        assert sparse.record(CANONICAL_DATASET_ID).publication_state == "draft"

    def test_policy_blockers_block_the_transition(self, make_service) -> None:
        records = [
            EvidenceRecord(
                kind="quality_checks",
                subject=CANONICAL_DATASET_ID,
                payload={"passed": False},
            ),
            *[record for record in complete_evidence() if record.kind != "quality_checks"],
        ]
        guarded = make_service(records=records)
        assert guarded.transition(_request(CANONICAL_CANDIDATE_ID, "claim", "a1")).status is (
            TransitionStatus.COMMITTED
        )
        assert guarded.transition(_request(CANONICAL_CANDIDATE_ID, "review", "a2")).status is (
            TransitionStatus.COMMITTED
        )
        blocked = guarded.transition(_request(CANONICAL_CANDIDATE_ID, "promote", "a3"))
        assert blocked.status is TransitionStatus.BLOCKED
        assert blocked.verdict.blockers[0].control == "quality_checks_passed"
        candidate = guarded.record(CANONICAL_CANDIDATE_ID)
        assert candidate.state == WorkflowState.REVIEW.value
        assert guarded.record(CANONICAL_DATASET_ID) is None

    def test_warnings_do_not_block(self, make_service) -> None:

        records = [record for record in complete_evidence() if record.kind != "ownership"] + [
            EvidenceRecord(
                kind="ownership",
                subject=CANONICAL_DATASET_ID,
                payload={"owner": None},
            )
        ]
        ownerless = make_service(records=records)
        _promote(ownerless)
        outcome = ownerless.transition(_request(CANONICAL_DATASET_ID, "publish", "p1"))
        assert outcome.status is TransitionStatus.COMMITTED
        assert outcome.verdict.warnings[0].control == "owner_recorded"


class TestAuditAndAtomicity:
    def test_every_attempt_is_audited(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        service.transition(_request(candidate, "claim", "a1"))
        service.transition(_request(candidate, "claim", "a1"))
        service.transition(_request(candidate, "promote", "a2"))
        outcomes = [event.outcome for event in service._store.audit_events]
        assert outcomes == ["committed", "replayed", "conflict"]
        actors = {event.actor for event in service._store.audit_events}
        assert actors == {"operator-alice"}

    def test_failed_attempts_write_nothing(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        service.transition(_request(candidate, "promote", "a1"))
        assert service.record(candidate) is None
        _promote(service)
        service.transition(_request(CANONICAL_DATASET_ID, "retire", "r1"))
        assert service.record(CANONICAL_DATASET_ID).publication_state == "draft"

    def test_expected_state_mismatch_conflicts(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        service.transition(_request(candidate, "claim", "a1"))
        outcome = service.transition(_request(candidate, "review", "a2", expected_state="open"))
        assert outcome.status is TransitionStatus.CONFLICT

    def test_retry_after_concurrent_write_succeeds(self, make_service) -> None:
        """A store-side precondition failure re-reads and re-applies, bounded."""
        from phlo.dataset import StoreWriteResult, StoreWriteStatus

        class RacyStore(InMemoryStateStore):
            """Wins the first compare-and-set race, then delegates."""

            def __init__(self, inner: InMemoryStateStore) -> None:
                super().__init__()
                self._inner = inner
                self._raced = False

            def load(self, dataset_id: str):
                return self._inner.load(dataset_id)

            def compare_and_set(self, *, writes, action_id, action, fingerprint):
                if not self._raced:
                    self._raced = True
                    return StoreWriteResult(
                        status=StoreWriteStatus.PRECONDITION_FAILED,
                        detail="simulated concurrent writer",
                    )
                return self._inner.compare_and_set(
                    writes=writes,
                    action_id=action_id,
                    action=action,
                    fingerprint=fingerprint,
                )

        store = InMemoryStateStore()
        service = make_service(store=store)
        racy = make_service(store=RacyStore(store))
        first = racy.transition(_request(CANONICAL_CANDIDATE_ID, "claim", "a1"))
        assert first.status is TransitionStatus.COMMITTED
        assert service.record(CANONICAL_CANDIDATE_ID).state == WorkflowState.CLAIMED.value

    def test_policy_version_is_pinned_on_committed_records(self, service) -> None:
        candidate = CANONICAL_CANDIDATE_ID
        service.transition(_request(candidate, "claim", "a1"))
        record = service.record(candidate)
        assert record.policy_version == FIXTURE_POLICY_VERSION


class TestCapabilityWiring:
    def test_service_requires_injected_capabilities(self) -> None:
        import inspect

        signature = inspect.signature(DatasetService.__init__)
        parameters = [name for name in signature.parameters if name != "self"]
        assert set(parameters) == {"store", "evidence_source", "policy_source"}
        for parameter in parameters:
            assert signature.parameters[parameter].default is inspect.Parameter.empty

    def test_policy_source_protocol_is_satisfied_by_test_double(self) -> None:
        assert isinstance(StaticPolicySource(project_policy()), StaticPolicySource)
        assert FixtureEvidenceSource().evidence("x", {"quality_checks"}) == ()
