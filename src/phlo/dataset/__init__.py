"""Provider-neutral Dataset authority for Phlo core.

Provides one canonical Dataset identity (``candidate:<table_id>`` /
``<table_id>``), the workflow and
publication state machines, a pure versioned project-configured policy
evaluator, neutral evidence and durable state-store capability interfaces,
and the core transition service over injected capabilities. Providers feed
evidence and storage through the interfaces; nothing here imports a provider.
Imported by later surfaces (API, Observatory read models, CLI workflow
commands) once they dispatch through core read models and transitions.
"""

from phlo.dataset.evidence import (
    EVIDENCE_STATUS_MISSING,
    EVIDENCE_STATUS_PRESENT,
    DatasetEvidenceSource,
    EvidenceRecord,
)
from phlo.dataset.models import (
    CANDIDATE_ID_PREFIX,
    DATASET_STATE_SCHEMA_VERSION,
    PUBLICATION_ACTIONS,
    PUBLICATION_TRANSITIONS,
    TERMINAL_PUBLICATION_STATES,
    TERMINAL_WORKFLOW_STATES,
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
from phlo.dataset.policy import (
    DatasetPolicy,
    EvidenceCondition,
    MissingEvidence,
    PolicyFinding,
    PolicyRule,
    PolicyVerdict,
    TransitionPolicy,
    evaluate_policy,
)
from phlo.dataset.service import (
    DatasetPolicySource,
    DatasetService,
    StaticPolicySource,
)
from phlo.dataset.store import (
    CommittedAction,
    DatasetStateStore,
    StoreWrite,
    StoreWriteResult,
    StoreWriteStatus,
    state_store_namespace,
)

__all__ = [
    "CANDIDATE_ID_PREFIX",
    "DATASET_STATE_SCHEMA_VERSION",
    "EVIDENCE_STATUS_MISSING",
    "EVIDENCE_STATUS_PRESENT",
    "PUBLICATION_ACTIONS",
    "PUBLICATION_TRANSITIONS",
    "TERMINAL_PUBLICATION_STATES",
    "TERMINAL_WORKFLOW_STATES",
    "WORKFLOW_ACTIONS",
    "WORKFLOW_STATE_OPEN",
    "WORKFLOW_TRANSITIONS",
    "CandidateRecord",
    "CommittedAction",
    "DatasetEvidenceSource",
    "DatasetPolicy",
    "DatasetPolicySource",
    "DatasetRecord",
    "DatasetService",
    "DatasetStateRecord",
    "DatasetStateStore",
    "EvidenceCondition",
    "EvidenceRecord",
    "MissingEvidence",
    "PolicyFinding",
    "PolicyRule",
    "PolicyVerdict",
    "PublicationState",
    "StaticPolicySource",
    "StoreWrite",
    "StoreWriteResult",
    "StoreWriteStatus",
    "TransitionAuditEvent",
    "TransitionOutcome",
    "TransitionPolicy",
    "TransitionRequest",
    "TransitionStatus",
    "WorkflowState",
    "candidate_dataset_id",
    "dataset_table_id",
    "evaluate_policy",
    "is_candidate_dataset_id",
    "state_store_namespace",
]
