"""Ingestion, transformation, and continuity operations for Phlo.

Exposes sync and async ingester/transformer base classes, the durable
operation journal (ADR 0049 §1), and the backup create/verify coordination
(ADR 0049 §3).
"""

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
    OperationJournal,
    OperationJournalEntry,
    OperationJournalError,
    OperationJournalState,
    OperationJournalStore,
    maintenance_operation_id,
    maintenance_plan_token,
)
from phlo.operations.journal_store import FileOperationJournalStore
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
    "AsyncTransformer",
    "BackupCreateResult",
    "BackupVerifyResult",
    "BaseIngester",
    "BaseTransformer",
    "FileOperationJournalStore",
    "InMemoryOperationJournalStore",
    "IngestionResult",
    "OperationJournal",
    "OperationJournalEntry",
    "OperationJournalError",
    "OperationJournalState",
    "OperationJournalStore",
    "TransformationResult",
    "SUPPORTED_FROM_VERSION",
    "SUPPORTED_TO_VERSION",
    "UPGRADE_PIPELINE",
    "UpgradeError",
    "UpgradePlan",
    "UpgradeResult",
    "UpgradeStepResult",
    "RestoreError",
    "create_backup_set",
    "default_backup_contributors",
    "maintenance_operation_id",
    "maintenance_plan_token",
    "migration_digest",
    "plan_restore",
    "plan_upgrade",
    "restore_apply",
    "upgrade_apply",
    "validate_upgrade_pair",
    "verify_backup_set",
]
