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
from phlo.operations.restore import (
    RestoreError,
    plan_restore,
    restore_apply,
)
from phlo.operations.transformation import (
    AsyncTransformer,
    BaseTransformer,
    TransformationResult,
)
from phlo.operations.upgrade import (
    SUPPORTED_FROM_VERSION,
    SUPPORTED_TO_VERSION,
    UPGRADE_PIPELINE,
    UpgradeError,
    UpgradePlan,
    UpgradeResult,
    UpgradeStepResult,
    migration_digest,
    plan_upgrade,
    upgrade_apply,
    validate_upgrade_pair,
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
    "SUPPORTED_FROM_VERSION",
    "SUPPORTED_TO_VERSION",
    "UPGRADE_PIPELINE",
    "UpgradeError",
    "UpgradePlan",
    "UpgradeResult",
    "UpgradeStepResult",
    "RestoreError",
    "claim_operation",
    "complete_operation",
    "create_backup_set",
    "default_backup_contributors",
    "mark_submitted",
    "mark_unknown",
    "migration_digest",
    "plan_restore",
    "plan_upgrade",
    "read_or_replay",
    "reconcile_unknown",
    "restore_apply",
    "upgrade_apply",
    "validate_upgrade_pair",
    "verify_backup_set",
]
