"""Provider-neutral Dataset identity, records, and state machines.

Defines the Dataset identity and state contract: canonical ID scheme
(``candidate:<table_id>`` for candidates and
``<table_id>`` for promoted Datasets, with promotion preserving the table key),
the workflow state machine (``claimed -> review -> promoted | rejected``, with
no record meaning open and ``rejected`` terminal), and the publication state
machine (``draft -> published -> retired``, with ``retired`` terminal).
Records are immutable domain values; every write produces a new record.
Pure data-model core of phlo.dataset, imported by its policy, store, and
service modules; never imports a provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

DATASET_STATE_SCHEMA_VERSION = 2
"""Payload schema version of the durable Dataset workflow collection."""

CANDIDATE_ID_PREFIX = "candidate:"
WORKFLOW_STATE_OPEN = "open"


class WorkflowState(StrEnum):
    """Workflow states of a candidate Dataset."""

    CLAIMED = "claimed"
    REVIEW = "review"
    PROMOTED = "promoted"
    REJECTED = "rejected"

    @classmethod
    def coerce(cls, value: str) -> WorkflowState:
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"Unknown workflow state: {value}") from exc


class PublicationState(StrEnum):
    """Publication states of a promoted Dataset."""

    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"

    @classmethod
    def coerce(cls, value: str) -> PublicationState:
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"Unknown publication state: {value}") from exc


class TransitionStatus(StrEnum):
    """Outcome status of a transition attempt."""

    COMMITTED = "committed"
    """The transition was applied and the new state persisted."""

    REPLAYED = "replayed"
    """A committed ``action_id`` was replayed with an identical request."""

    IDEMPOTENT = "idempotent"
    """The record already sits in the target state; nothing was written."""

    BLOCKED = "blocked"
    """Policy blocked the transition; nothing was written."""

    CONFLICT = "conflict"
    """The current state or action identity conflicts with the request."""

    @property
    def committed(self) -> bool:
        return self is TransitionStatus.COMMITTED


TERMINAL_WORKFLOW_STATES = frozenset({WorkflowState.REJECTED})
TERMINAL_PUBLICATION_STATES = frozenset({PublicationState.RETIRED})

WORKFLOW_ACTIONS = frozenset({"claim", "review", "promote", "reject"})
PUBLICATION_ACTIONS = frozenset({"publish", "retire"})

WORKFLOW_TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "claim": ((WORKFLOW_STATE_OPEN,), WorkflowState.CLAIMED.value),
    "review": ((WorkflowState.CLAIMED.value,), WorkflowState.REVIEW.value),
    "promote": ((WorkflowState.REVIEW.value,), WorkflowState.PROMOTED.value),
    "reject": ((WorkflowState.REVIEW.value,), WorkflowState.REJECTED.value),
}

PUBLICATION_TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "publish": ((PublicationState.DRAFT.value,), PublicationState.PUBLISHED.value),
    "retire": ((PublicationState.PUBLISHED.value,), PublicationState.RETIRED.value),
}

TARGET_ACTIONS = frozenset({"promote", "publish"})


def candidate_dataset_id(table_id: str) -> str:
    """Return the canonical candidate Dataset ID for a table key."""
    table_id = _validated_component(table_id, "table_id")
    return f"{CANDIDATE_ID_PREFIX}{table_id}"


def is_candidate_dataset_id(dataset_id: str) -> bool:
    """Return whether ``dataset_id`` uses the canonical candidate form."""
    return dataset_id.startswith(CANDIDATE_ID_PREFIX) and len(dataset_id) > len(CANDIDATE_ID_PREFIX)


def dataset_table_id(dataset_id: str) -> str:
    """Return the table key preserved by a canonical Dataset ID."""
    if is_candidate_dataset_id(dataset_id):
        return _validated_component(dataset_id.removeprefix(CANDIDATE_ID_PREFIX), "dataset_id")
    return _validated_component(dataset_id, "dataset_id")


def _validated_component(value: str, name: str) -> str:
    if not value or value != value.strip() or ":" in value:
        raise ValueError(f"{name} must be a non-empty table key without ':' separators")
    return value


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Durable workflow record for one candidate Dataset.

    Keyed ``candidate:<table_id>``. Promoted candidates keep this record (with
    ``state: promoted`` and ``promoted_dataset_id``) so promoted identity and
    publication state survive promotion.
    """

    dataset_id: str
    table_id: str
    state: str
    owner: str | None = None
    approval_state: str | None = None
    promoted_dataset_id: str | None = None
    publication_state: str | None = None
    policy_version: str | None = None
    schema_version: int = DATASET_STATE_SCHEMA_VERSION
    last_action_id: str | None = None

    def __post_init__(self) -> None:
        if self.dataset_id != candidate_dataset_id(self.table_id):
            raise ValueError(
                f"Candidate record dataset_id must be {candidate_dataset_id(self.table_id)!r}"
            )
        WorkflowState.coerce(self.state)
        if self.promoted_dataset_id is not None and self.state != WorkflowState.PROMOTED.value:
            raise ValueError("promoted_dataset_id may only be set on a promoted candidate")

    @property
    def current_state(self) -> str:
        """State string compared by compare-and-set."""
        return self.state

    def to_read_model(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "table_id": self.table_id,
            "state": self.state,
            "owner": self.owner,
            "approval_state": self.approval_state,
            "promoted_dataset_id": self.promoted_dataset_id,
            "publication_state": self.publication_state,
            "policy_version": self.policy_version,
            "schema_version": self.schema_version,
            "last_action_id": self.last_action_id,
        }


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    """Durable publication record for one promoted Dataset, keyed ``<table_id>``."""

    dataset_id: str
    table_id: str
    publication_state: str
    owner: str | None = None
    approval_state: str | None = None
    policy_version: str | None = None
    schema_version: int = DATASET_STATE_SCHEMA_VERSION
    last_action_id: str | None = None

    def __post_init__(self) -> None:
        if is_candidate_dataset_id(self.dataset_id):
            raise ValueError("DatasetRecord dataset_id must be the promoted <table_id> form")
        if self.dataset_id != self.table_id:
            raise ValueError("DatasetRecord dataset_id must equal its table key")
        PublicationState.coerce(self.publication_state)

    @property
    def current_state(self) -> str:
        """State string compared by compare-and-set."""
        return self.publication_state

    def to_read_model(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "table_id": self.table_id,
            "publication_state": self.publication_state,
            "owner": self.owner,
            "approval_state": self.approval_state,
            "policy_version": self.policy_version,
            "schema_version": self.schema_version,
            "last_action_id": self.last_action_id,
        }


DatasetStateRecord = CandidateRecord | DatasetRecord


@dataclass(frozen=True, slots=True)
class TransitionRequest:
    """Compare-and-set transition request with idempotency semantics.

    ``resource_id`` uses the canonical ID scheme, ``action`` is a workflow
    action for candidates or a publication action for promoted Datasets, and
    ``action_id`` is the client-supplied idempotency key. ``expected_state`` is
    the caller's observed current state (``open`` when no record exists); when
    omitted the service compares against the state it reads.
    """

    resource_id: str
    action: str
    action_id: str
    actor: str | None = None
    scope: str | None = None
    expected_state: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id:
            raise ValueError("action_id is required for idempotent transitions")

    def fingerprint(self) -> str:
        """Stable identity of this request used for replay detection."""
        payload = {
            "action": self.action,
            "action_id": self.action_id,
            "expected_state": self.expected_state,
            "resource_id": self.resource_id,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class TransitionAuditEvent:
    """Append-only audit event for one transition attempt."""

    actor: str | None
    scope: str | None
    action_id: str
    resource_id: str
    action: str
    before_state: str | None
    after_state: str | None
    outcome: str
    detail: str = ""

    def to_read_model(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "scope": self.scope,
            "action_id": self.action_id,
            "resource_id": self.resource_id,
            "action": self.action,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "outcome": self.outcome,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    """Result of one transition attempt against the core service."""

    request: TransitionRequest
    status: TransitionStatus
    before_state: str | None
    after_state: str | None
    record: DatasetStateRecord | None = None
    verdict: Any = None
    message: str = ""
    audit: TransitionAuditEvent | None = field(default=None, repr=False)

    @property
    def committed(self) -> bool:
        return self.status.committed
