"""Durable, provider-neutral pipeline run evidence.

Public surface for the evidence subsystem: versioned run models, hook
emission with safe lifecycle handling, run reconciliation against
required-evidence profiles, report building, and SQLite/Postgres stores
with idempotency-conflict semantics.
"""

from phlo.run_evidence.emit import emit_lifecycle_safely, emit_observation
from phlo.run_evidence.hooks import CoreRunEvidenceHookProvider
from phlo.run_evidence.models import (
    RUN_EVIDENCE_SCHEMA_VERSION,
    EvidenceCompleteness,
    PipelineRun,
    RunArtifact,
    RunCatalogChange,
    RunEvent,
    RunLineageEdge,
    RunQualityResult,
    RunResource,
    RunStage,
    StagedObject,
)
from phlo.run_evidence.reconciliation import (
    DEFAULT_CLOCK_SKEW,
    ReconciliationDecision,
    RequiredEvidenceProfile,
    RequiredEvidenceRecord,
    RequiredEvidenceStage,
    RunEvidenceNotFound,
    RunEvidenceSource,
    RunEvidenceUnavailable,
    RunLookupOutcome,
    RunObservation,
    RunReconciler,
    normalize_status,
)
from phlo.run_evidence.report import (
    RunReport,
    RunReportNotFound,
    RunReportStore,
    build_run_report,
)
from phlo.run_evidence.store import (
    IdempotencyConflict,
    PostgresRunEvidenceStore,
    SQLiteRunEvidenceStore,
    default_run_evidence_store,
)

__all__ = [
    "EvidenceCompleteness",
    "RUN_EVIDENCE_SCHEMA_VERSION",
    "CoreRunEvidenceHookProvider",
    "DEFAULT_CLOCK_SKEW",
    "ReconciliationDecision",
    "RequiredEvidenceProfile",
    "RequiredEvidenceRecord",
    "RequiredEvidenceStage",
    "RunLookupOutcome",
    "RunEvidenceUnavailable",
    "RunEvidenceNotFound",
    "RunEvidenceSource",
    "RunObservation",
    "RunReconciler",
    "normalize_status",
    "RunReportNotFound",
    "RunReportStore",
    "RunReport",
    "build_run_report",
    "IdempotencyConflict",
    "PipelineRun",
    "PostgresRunEvidenceStore",
    "RunArtifact",
    "RunCatalogChange",
    "RunEvent",
    "RunLineageEdge",
    "RunQualityResult",
    "RunResource",
    "RunStage",
    "StagedObject",
    "emit_observation",
    "emit_lifecycle_safely",
    "SQLiteRunEvidenceStore",
    "default_run_evidence_store",
]
