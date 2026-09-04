"""Shared fixtures for the neutral Dataset core tests.

The canonical Dataset fixture is one governed table, ``gold.customer_health``.
Every capability the service needs -- durable state store, evidence source,
project policy -- is an explicit in-memory test double; core itself ships no
memory store.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection
from typing import Any

import pytest

from phlo.dataset import (
    CommittedAction,
    DatasetPolicy,
    DatasetService,
    EvidenceCondition,
    EvidenceRecord,
    PolicyRule,
    StoreWrite,
    StoreWriteResult,
    StoreWriteStatus,
    TransitionAuditEvent,
    TransitionPolicy,
    candidate_dataset_id,
)

CANONICAL_TABLE_ID = "gold.customer_health"
CANONICAL_CANDIDATE_ID = candidate_dataset_id(CANONICAL_TABLE_ID)
CANONICAL_DATASET_ID = CANONICAL_TABLE_ID
FIXTURE_POLICY_VERSION = "2026.09-project-policy"

BLESSED_EVIDENCE_PROFILE = "blessed"
"""Test-fixture name of the complete run-evidence profile."""


class InMemoryStateStore:
    """Minimal test double for the durable compare-and-set store contract."""

    def __init__(self) -> None:
        self._records: dict[str, Any] = {}
        self._actions: dict[tuple[str, str], Any] = {}
        self.audit_events: list[TransitionAuditEvent] = []

    def load(self, dataset_id: str) -> Any:
        return self._records.get(dataset_id)

    def committed_action(self, dataset_id: str, action_id: str) -> Any:
        return self._actions.get((dataset_id, action_id))

    def compare_and_set(
        self,
        *,
        writes: tuple[StoreWrite, ...],
        action_id: str,
        action: str,
        fingerprint: str,
    ) -> StoreWriteResult:
        for write in writes:
            current = self._records.get(write.record_id)
            current_state = current.current_state if current else "open"
            if current_state != write.expected_state:
                return StoreWriteResult(
                    status=StoreWriteStatus.PRECONDITION_FAILED,
                    detail=(
                        f"{write.record_id} moved from {write.expected_state!r} "
                        f"to {current_state!r}"
                    ),
                )
        committed = []
        for write in writes:
            self._records[write.record_id] = write.next_record
            committed.append(write.next_record)
        last = writes[-1]
        self._actions[(last.record_id, action_id)] = CommittedAction(
            action_id=action_id,
            resource_id=last.record_id,
            action=action,
            fingerprint=fingerprint,
            outcome_status=StoreWriteStatus.COMMITTED.value,
            after_state=last.next_record.current_state,
        )
        return StoreWriteResult(
            status=StoreWriteStatus.COMMITTED,
            records=tuple(committed),
            committed_fingerprint=fingerprint,
        )

    def append_audit(self, event: TransitionAuditEvent) -> None:
        self.audit_events.append(event)


class FixtureEvidenceSource:
    """Evidence source test double serving per-kind records for the fixture."""

    def __init__(self, records: Collection[EvidenceRecord] = ()) -> None:
        self._records: dict[str, list[EvidenceRecord]] = defaultdict(list)
        for record in records:
            self._records[record.kind].append(record)

    def evidence(self, subject: str, kinds: Collection[str]) -> tuple[EvidenceRecord, ...]:
        return tuple(
            record
            for kind in kinds
            for record in self._records.get(kind, [])
            if record.subject == subject
        )


class StaticPolicySource:
    """Policy source test double returning one fixed project policy."""

    def __init__(self, policy: DatasetPolicy) -> None:
        self.policy = policy

    def policy_for(self, dataset_id: str) -> DatasetPolicy:
        return self.policy


def project_policy() -> DatasetPolicy:
    """Project-configured promotion and publication policy (no core rules)."""
    return DatasetPolicy(
        policy_version=FIXTURE_POLICY_VERSION,
        rules=(
            PolicyRule(
                control="quality_checks_passed",
                evidence_kind="quality_checks",
                condition=EvidenceCondition(field="passed", operator="true"),
                severity="blocker",
                message="Quality checks must pass before the transition.",
                applies_to=frozenset({"promote", "publish"}),
            ),
            PolicyRule(
                control="governance_declarations_present",
                evidence_kind="governance_surface",
                condition=EvidenceCondition(field="declared", operator="true"),
                severity="blocker",
                message="The table must carry @phlo.contract, @phlo.publish, and @phlo.access.",
                applies_to=frozenset({"promote", "publish"}),
            ),
            PolicyRule(
                control="owner_recorded",
                evidence_kind="ownership",
                condition=EvidenceCondition(field="owner", operator="ne", value=None),
                severity="warning",
                message="Dataset has no recorded owner.",
                applies_to=frozenset({"publish"}),
            ),
        ),
        transitions=(
            TransitionPolicy(
                action="promote",
                required_evidence=("quality_checks", "governance_surface"),
            ),
            TransitionPolicy(
                action="publish",
                required_evidence=("quality_checks", "governance_surface", "run_evidence"),
            ),
        ),
    )


def complete_evidence() -> tuple[EvidenceRecord, ...]:
    """Evidence set that satisfies every fixture policy control."""
    return (
        EvidenceRecord(
            kind="quality_checks",
            subject=CANONICAL_TABLE_ID,
            payload={"passed": True, "profile": BLESSED_EVIDENCE_PROFILE},
            source="quality executor",
        ),
        EvidenceRecord(
            kind="governance_surface",
            subject=CANONICAL_TABLE_ID,
            payload={"declared": True},
            source="governance surface",
        ),
        EvidenceRecord(
            kind="run_evidence",
            subject=CANONICAL_TABLE_ID,
            payload={"profile": BLESSED_EVIDENCE_PROFILE, "complete": True},
            source="run evidence store",
        ),
        EvidenceRecord(
            kind="ownership",
            subject=CANONICAL_TABLE_ID,
            payload={"owner": "data-platform"},
            source="project config",
        ),
    )


@pytest.fixture
def make_service() -> Callable[..., DatasetService]:
    """Build a core service over explicit test doubles."""

    def factory(
        *,
        store: InMemoryStateStore | None = None,
        records: Collection[EvidenceRecord] | None = None,
        policy: DatasetPolicy | None = None,
    ) -> DatasetService:
        return DatasetService(
            store=store or InMemoryStateStore(),
            evidence_source=FixtureEvidenceSource(
                complete_evidence() if records is None else records
            ),
            policy_source=StaticPolicySource(policy or project_policy()),
        )

    return factory


@pytest.fixture
def service(
    make_service: Callable[..., DatasetService],
) -> DatasetService:
    """The canonical fixture service: complete evidence, project policy."""
    return make_service()
