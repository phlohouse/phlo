"""Core Dataset transition service over injected capabilities.

One provider-neutral authority for Dataset identity, readiness, and
transitions. The service takes its durable state store, evidence
source, and versioned project policy through injected capabilities -- it has
no implicit production or memory store, no built-in rules, and no provider
knowledge. Every transition is a compare-and-set keyed by the client
``action_id``: replays return the committed outcome, conflicts and blocked
attempts write nothing, and every attempt appends an audit event.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from phlo.dataset.evidence import DatasetEvidenceSource, EvidenceRecord
from phlo.dataset.models import (
    DATASET_STATE_SCHEMA_VERSION,
    PUBLICATION_ACTIONS,
    PUBLICATION_TRANSITIONS,
    WORKFLOW_ACTIONS,
    WORKFLOW_STATE_OPEN,
    WORKFLOW_TRANSITIONS,
    CandidateRecord,
    DatasetRecord,
    DatasetStateRecord,
    PublicationState,
    TransitionAuditEvent,
    TransitionOutcome,
    TransitionRequest,
    TransitionStatus,
    WorkflowState,
    candidate_dataset_id,
    dataset_table_id,
    is_candidate_dataset_id,
)
from phlo.dataset.policy import DatasetPolicy, PolicyVerdict, evaluate_policy
from phlo.dataset.store import (
    CommittedAction,
    DatasetStateStore,
    StoreWrite,
    StoreWriteStatus,
)

MAX_CAS_ATTEMPTS = 3
"""Bounded retries when a concurrent writer wins the compare-and-set race."""


@runtime_checkable
class DatasetPolicySource(Protocol):
    """Capability interface supplying the project's versioned policy."""

    def policy_for(self, dataset_id: str) -> DatasetPolicy:
        """Return the project policy governing one Dataset."""
        ...


@dataclass(frozen=True, slots=True)
class StaticPolicySource:
    """Policy source returning one fixed policy for every Dataset."""

    policy: DatasetPolicy

    def policy_for(self, dataset_id: str) -> DatasetPolicy:
        return self.policy


class DatasetService:
    """Dataset readiness and transition authority over injected capabilities."""

    def __init__(
        self,
        *,
        store: DatasetStateStore,
        evidence_source: DatasetEvidenceSource,
        policy_source: DatasetPolicySource,
    ) -> None:
        self._store = store
        self._evidence_source = evidence_source
        self._policy_source = policy_source

    # -- Reads -----------------------------------------------------------

    def record(self, dataset_id: str) -> DatasetStateRecord | None:
        """Return the durable record for one canonical Dataset ID."""
        return self._store.load(_validated_resource_id(dataset_id))

    def readiness(self, dataset_id: str, action: str | None = None) -> PolicyVerdict:
        """Evaluate the project policy for one Dataset's default or given action.

        The default action is ``promote`` for candidates (including open
        candidates with no record yet) and ``publish`` for promoted Datasets.
        Readiness is policy plus evidence only; it never consults or changes
        workflow or publication state.
        """
        dataset_id = _validated_resource_id(dataset_id)
        if action is None:
            action = "promote" if is_candidate_dataset_id(dataset_id) else "publish"
        policy = self._policy_source.policy_for(dataset_id)
        evidence = self._evidence(dataset_id, _policy_evidence_kinds(policy, action))
        return evaluate_policy(policy, dataset_id, action, evidence)

    def allowed_transitions(self, dataset_id: str) -> tuple[str, ...]:
        """Return the state-machine actions available from the current state."""
        dataset_id = _validated_resource_id(dataset_id)
        record = self._store.load(dataset_id)
        if is_candidate_dataset_id(dataset_id):
            current = record.current_state if record else WORKFLOW_STATE_OPEN
            return tuple(
                sorted(
                    action
                    for action, (from_states, _to) in WORKFLOW_TRANSITIONS.items()
                    if current in from_states
                )
            )
        if record is None:
            return ()
        current = record.current_state
        return tuple(
            sorted(
                action
                for action, (from_states, _to) in PUBLICATION_TRANSITIONS.items()
                if current in from_states
            )
        )

    # -- Transitions -----------------------------------------------------

    def transition(self, request: TransitionRequest) -> TransitionOutcome:
        """Apply one transition with compare-and-set semantics.

        Replay: an ``action_id`` already committed with an identical request
        returns the original committed outcome without re-applying. Conflict:
        a mismatched expected state or an action the state machine forbids
        (including anything from the terminal ``retired`` state) fails while
        naming the conflict. Blocked: the project policy refuses. Nothing is
        written on replay-identity conflicts, state conflicts, or blocked
        attempts; every attempt appends an audit event.
        """
        resource_id = _validated_resource_id(request.resource_id)
        is_candidate = is_candidate_dataset_id(resource_id)
        valid_actions = WORKFLOW_ACTIONS if is_candidate else PUBLICATION_ACTIONS
        if request.action not in valid_actions:
            return self._failed_outcome(
                request,
                TransitionStatus.CONFLICT,
                message=f"Action {request.action!r} is not valid for {resource_id}.",
            )

        for _attempt in range(MAX_CAS_ATTEMPTS):
            record = self._store.load(resource_id)
            before_state = record.current_state if record else WORKFLOW_STATE_OPEN
            if request.expected_state is not None and request.expected_state != before_state:
                return self._failed_outcome(
                    request,
                    TransitionStatus.CONFLICT,
                    before_state=before_state,
                    message=(
                        f"Expected state {request.expected_state!r} for {resource_id}, "
                        f"found {before_state!r}."
                    ),
                )

            committed = self._store.committed_action(resource_id, request.action_id)
            if committed is not None:
                return self._replay_outcome(request, committed, before_state)

            allowed, message = _state_machine_check(resource_id, request.action, before_state)
            if not allowed:
                if _is_idempotent_publish(resource_id, request.action, before_state):
                    return self._failed_outcome(
                        request,
                        TransitionStatus.IDEMPOTENT,
                        before_state=before_state,
                        after_state=before_state,
                        message=(
                            f"{resource_id} is already published; reporting the existing state."
                        ),
                    )
                return self._failed_outcome(
                    request,
                    TransitionStatus.CONFLICT,
                    before_state=before_state,
                    message=message,
                )

            verdict = self.readiness(resource_id, request.action)
            if not verdict.ready:
                return self._failed_outcome(
                    request,
                    TransitionStatus.BLOCKED,
                    before_state=before_state,
                    message=f"Policy blocked {request.action!r} on {resource_id}.",
                    verdict=verdict,
                )

            writes = _plan_writes(resource_id, request, record, verdict.policy_version)
            result = self._store.compare_and_set(
                writes=writes,
                action_id=request.action_id,
                action=request.action,
                fingerprint=request.fingerprint(),
            )
            if result.status is StoreWriteStatus.PRECONDITION_FAILED:
                continue
            if result.status is StoreWriteStatus.REPLAYED:
                committed = self._store.committed_action(resource_id, request.action_id)
                after = result.records[-1] if result.records else None
                after_state = after.current_state if after else before_state
                return self._outcome(
                    request,
                    TransitionStatus.REPLAYED,
                    before_state=before_state,
                    after_state=after_state,
                    record=after,
                    message=result.detail or "Transition already committed; replaying outcome.",
                    audit_outcome="replayed",
                )
            if result.status is StoreWriteStatus.ACTION_CONFLICT:
                return self._failed_outcome(
                    request,
                    TransitionStatus.CONFLICT,
                    before_state=before_state,
                    message=result.detail or "action_id already committed for this record.",
                )

            after = result.records[-1] if result.records else None
            after_state = after.current_state if after else before_state
            return self._outcome(
                request,
                TransitionStatus.COMMITTED,
                before_state=before_state,
                after_state=after_state,
                record=after,
                message=result.detail or f"{request.action!r} committed for {resource_id}.",
                audit_outcome="committed",
                verdict=verdict,
            )

        return self._failed_outcome(
            request,
            TransitionStatus.CONFLICT,
            message=(
                f"Concurrent modification of {resource_id}; "
                f"retry after re-reading its current state."
            ),
        )

    # -- Internals --------------------------------------------------------

    def _evidence(self, dataset_id: str, kinds: Collection[str]) -> tuple[EvidenceRecord, ...]:
        subject = dataset_table_id(dataset_id)
        return self._evidence_source.evidence(subject, kinds)

    def _replay_outcome(
        self,
        request: TransitionRequest,
        committed: CommittedAction,
        before_state: str | None,
    ) -> TransitionOutcome:
        if committed.fingerprint == request.fingerprint():
            return self._outcome(
                request,
                TransitionStatus.REPLAYED,
                before_state=before_state,
                after_state=committed.after_state,
                record=self._store.load(request.resource_id),
                message=f"Action {request.action!r} already committed; replaying outcome.",
                audit_outcome="replayed",
            )
        return self._failed_outcome(
            request,
            TransitionStatus.CONFLICT,
            before_state=before_state,
            message=(
                f"action_id {request.action_id!r} was already committed with a different request."
            ),
        )

    def _outcome(
        self,
        request: TransitionRequest,
        status: TransitionStatus,
        *,
        before_state: str | None,
        after_state: str | None,
        record: DatasetStateRecord | None = None,
        message: str = "",
        audit_outcome: str = "",
        verdict: PolicyVerdict | None = None,
    ) -> TransitionOutcome:
        audit = TransitionAuditEvent(
            actor=request.actor,
            scope=request.scope,
            action_id=request.action_id,
            resource_id=request.resource_id,
            action=request.action,
            before_state=before_state,
            after_state=after_state,
            outcome=audit_outcome or status.value,
            detail=message,
        )
        self._store.append_audit(audit)
        return TransitionOutcome(
            request=request,
            status=status,
            before_state=before_state,
            after_state=after_state,
            record=record,
            verdict=verdict,
            message=message,
            audit=audit,
        )

    def _failed_outcome(
        self,
        request: TransitionRequest,
        status: TransitionStatus,
        *,
        message: str,
        before_state: str | None = None,
        after_state: str | None = None,
        verdict: PolicyVerdict | None = None,
    ) -> TransitionOutcome:
        return self._outcome(
            request,
            status,
            before_state=before_state,
            after_state=after_state,
            message=message,
            verdict=verdict,
        )


def _state_machine_check(resource_id: str, action: str, current_state: str) -> tuple[bool, str]:
    transitions = (
        WORKFLOW_TRANSITIONS if is_candidate_dataset_id(resource_id) else (PUBLICATION_TRANSITIONS)
    )
    entry = transitions.get(action)
    if entry is None:  # pragma: no cover - action membership validated by caller
        return False, f"Action {action!r} is not a recognized transition."
    from_states, _to_state = entry
    if current_state not in from_states:
        return False, (
            f"Action {action!r} is not allowed from state {current_state!r} for {resource_id}."
        )
    return True, ""


def _is_idempotent_publish(resource_id: str, action: str, current_state: str) -> bool:
    return (
        not is_candidate_dataset_id(resource_id)
        and action == "publish"
        and current_state == PublicationState.PUBLISHED.value
    )


def _plan_writes(
    resource_id: str,
    request: TransitionRequest,
    record: DatasetStateRecord | None,
    policy_version: str,
) -> tuple[StoreWrite, ...]:
    """Build the atomic write batch for one allowed transition."""
    if is_candidate_dataset_id(resource_id):
        table_id = dataset_table_id(resource_id)
        transitions = WORKFLOW_TRANSITIONS
        to_state = transitions[request.action][1]
        owner = record.owner if record else None
        schema_version = record.schema_version if record else DATASET_STATE_SCHEMA_VERSION
        if to_state == WorkflowState.PROMOTED.value:
            candidate = CandidateRecord(
                dataset_id=resource_id,
                table_id=table_id,
                state=to_state,
                owner=owner,
                approval_state=WorkflowState.REVIEW.value,
                promoted_dataset_id=table_id,
                publication_state=PublicationState.DRAFT.value,
                policy_version=policy_version,
                schema_version=schema_version,
                last_action_id=request.action_id,
            )
            promoted = DatasetRecord(
                dataset_id=table_id,
                table_id=table_id,
                publication_state=PublicationState.DRAFT.value,
                owner=owner,
                approval_state=WorkflowState.REVIEW.value,
                policy_version=policy_version,
                last_action_id=request.action_id,
            )
            return (
                StoreWrite(
                    record_id=resource_id,
                    expected_state=record.current_state if record else WORKFLOW_STATE_OPEN,
                    next_record=candidate,
                ),
                StoreWrite(
                    record_id=table_id,
                    expected_state=WORKFLOW_STATE_OPEN,
                    next_record=promoted,
                ),
            )
        next_record = CandidateRecord(
            dataset_id=resource_id,
            table_id=table_id,
            state=to_state,
            owner=owner,
            approval_state=to_state,
            policy_version=policy_version,
            schema_version=schema_version,
            last_action_id=request.action_id,
        )
        return (
            StoreWrite(
                record_id=resource_id,
                expected_state=record.current_state if record else WORKFLOW_STATE_OPEN,
                next_record=next_record,
            ),
        )

    transitions = PUBLICATION_TRANSITIONS
    to_state = transitions[request.action][1]
    assert record is not None  # caller verified the state machine allows this action
    next_record = DatasetRecord(
        dataset_id=record.dataset_id,
        table_id=record.table_id,
        publication_state=to_state,
        owner=record.owner,
        approval_state=to_state,
        policy_version=policy_version,
        schema_version=record.schema_version,
        last_action_id=request.action_id,
    )
    return (
        StoreWrite(
            record_id=resource_id,
            expected_state=record.current_state,
            next_record=next_record,
        ),
    )


def _validated_resource_id(dataset_id: str) -> str:
    if not dataset_id:
        raise ValueError("dataset_id is required")
    dataset_table_id(dataset_id)
    return dataset_id


def _policy_evidence_kinds(policy: DatasetPolicy, action: str) -> Collection[str]:
    kinds = set(policy.for_action(action).required_evidence)
    for rule in policy.rules:
        if rule.applies_to_action(action):
            kinds.add(rule.evidence_kind)
    return kinds


__all__ = [
    "MAX_CAS_ATTEMPTS",
    "DatasetPolicySource",
    "DatasetService",
    "StaticPolicySource",
    "candidate_dataset_id",
    "is_candidate_dataset_id",
]
