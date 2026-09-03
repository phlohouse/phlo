"""Ingestion, transformation, and continuity operations for Phlo.

Exposes sync and async ingester/transformer base classes plus adapters that
bridge sync implementations into async pipelines and vice versa, the durable
operation journal (ADR 0049 §1), and the backup create/verify coordination
(ADR 0049 §3).
"""

from phlo.operations.adapters import (
    AsyncToSyncIngesterAdapter,
    AsyncToSyncTransformerAdapter,
    SyncToAsyncIngesterAdapter,
    SyncToAsyncTransformerAdapter,
)
from phlo.operations.backup import (
    BackupCreateResult,
    BackupVerifyResult,
    create_backup_set,
    default_backup_contributors,
    verify_backup_set,
)
from phlo.operations.ingestion import AsyncIngester, BaseIngester, IngestionResult
from phlo.operations.journal import (
    InMemoryOperationJournalStore,
    OperationJournalEntry,
    OperationJournalError,
    OperationJournalState,
    OperationJournalStore,
    claim_operation,
    complete_operation,
    mark_submitted,
    mark_unknown,
    read_or_replay,
    reconcile_unknown,
)
from phlo.operations.transformation import (
    AsyncTransformer,
    BaseTransformer,
    TransformationResult,
)

__all__ = [
    "AsyncIngester",
    "AsyncToSyncIngesterAdapter",
    "AsyncToSyncTransformerAdapter",
    "AsyncTransformer",
    "BackupCreateResult",
    "BackupVerifyResult",
    "BaseIngester",
    "BaseTransformer",
    "InMemoryOperationJournalStore",
    "IngestionResult",
    "OperationJournalEntry",
    "OperationJournalError",
    "OperationJournalState",
    "OperationJournalStore",
    "SyncToAsyncIngesterAdapter",
    "SyncToAsyncTransformerAdapter",
    "TransformationResult",
    "claim_operation",
    "complete_operation",
    "create_backup_set",
    "default_backup_contributors",
    "mark_submitted",
    "mark_unknown",
    "read_or_replay",
    "reconcile_unknown",
    "verify_backup_set",
]
