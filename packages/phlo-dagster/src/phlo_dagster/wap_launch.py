"""Pre-launch Write-Audit-Publish coordination for Dagster runs.

Prepares an isolated WAP branch and tags before Dagster starts work, then keeps
content-addressed launch manifests and lifecycle reports under .phlo/wap-reports
so promotion binds to the exact audited launch.

Part of phlo-dagster's WAP tooling alongside wap_sensors: runs on the launch path before
Dagster work begins. The durable report/manifest store itself lives in
``phlo.wap_reports`` so operator tooling shares the same claim/receipt contract;
the wrappers here preserve this module's historical import surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from phlo._correlation import resolve_project_identity
from phlo.capabilities.interfaces import SnapshotPromotionCatalog, VersionedCatalog
from phlo.capabilities.resolver import resolve_capability
from phlo.config import get_settings
from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger
from phlo.wap_reports import (
    read_wap_launch_manifest as _core_read_wap_launch_manifest,
    read_wap_report as _core_read_wap_report,
    wap_launch_manifest_path,
    wap_report_path,
    wap_report_snapshot_path,
    write_wap_launch_manifest,
    write_wap_report as _core_write_wap_report,
)

WAP_BRANCH_TAG = "phlo/wap_branch"
WAP_REF_TAG = "phlo/ref"
WAP_RUN_ID_TAG = "phlo/run_id"
WAP_PROJECT_ID_TAG = "phlo/project_id"
WAP_ATTEMPT_TAG = "phlo/attempt"
WAP_BRANCH_PREFIX = "pipeline-run-"
WAP_STRATEGY_BRANCH = "branch"
WAP_STRATEGY_SNAPSHOT = "snapshot"
logger = get_logger(__name__)


def _project_root() -> Path:
    return Path(os.getenv("PHLO_PROJECT_PATH", "."))


def _report_path(logical_run_id: str) -> Path:
    return wap_report_path(_project_root(), logical_run_id)


def _report_snapshot_path(logical_run_id: str, checksum: str) -> Path:
    return wap_report_snapshot_path(_project_root(), logical_run_id, checksum)


def _launch_manifest_path(logical_run_id: str, checksum: str) -> Path:
    return wap_launch_manifest_path(_project_root(), logical_run_id, checksum)


def _write_launch_manifest(
    *,
    logical_run_id: str,
    dagster_run_id: str,
    branch: str,
    tags: dict[str, str],
    source_hash: str | None,
    target_hash_before: str | None,
) -> str | None:
    """Write the immutable, content-addressed binding used for promotion."""
    return write_wap_launch_manifest(
        _project_root(),
        logical_run_id=logical_run_id,
        dagster_run_id=dagster_run_id,
        branch=branch,
        tags=tags,
        source_hash=source_hash,
        target_hash_before=target_hash_before,
    )


def read_wap_launch_manifest(logical_run_id: str, checksum: str) -> dict[str, Any] | None:
    """Read a launch binding only when its content matches the recorded digest."""
    return _core_read_wap_launch_manifest(_project_root(), logical_run_id, checksum)


def write_wap_report(logical_run_id: str, **updates: Any) -> bool:
    """Persist the durable WAP launch manifest and lifecycle audit record.

    The logical ID is created before GraphQL submission, so this record exists
    even when Dagster rejects a run or a response is lost in transit.
    """
    return _core_write_wap_report(_project_root(), logical_run_id, **updates)


def read_wap_report(logical_run_id: str) -> dict[str, Any] | None:
    """Read the latest durable WAP lifecycle record for a logical run."""
    return _core_read_wap_report(_project_root(), logical_run_id)


@dataclass(frozen=True)
class WapLaunch:
    """The logical identity and staging ref prepared before a Dagster run starts.

    ``branch`` carries the staging ref for both strategies: a versioned-catalog
    branch under the ``branch`` strategy, or a candidate namespace under the
    ``snapshot`` strategy. ``catalog`` is the provider bound to that strategy.
    """

    logical_run_id: str
    branch: str
    catalog: Any
    created_branch: bool
    source_hash: str | None
    target_hash_before: str | None
    project_id: str
    attempt: int
    strategy: str = WAP_STRATEGY_BRANCH
    review_hold: bool = False

    @property
    def tags(self) -> dict[str, str]:
        """Return the Dagster tags that bind stages to this WAP staging ref."""
        return {
            WAP_RUN_ID_TAG: self.logical_run_id,
            WAP_BRANCH_TAG: self.branch,
            WAP_REF_TAG: self.branch,
            WAP_PROJECT_ID_TAG: self.project_id,
            WAP_ATTEMPT_TAG: str(self.attempt),
        }

    def cleanup_if_created(self) -> None:
        """Remove only the staging ref created by this launch attempt.

        Normal WAP callers intentionally do not invoke this method: retained
        refs and their reports are the audit trail for failed checks.
        """
        if not self.created_branch:
            return
        if self.strategy == WAP_STRATEGY_SNAPSHOT:
            self.catalog.abort_candidates(namespace=self.branch)
        else:
            self.catalog.delete_branch(self.branch)

    def record_launch_result(
        self,
        *,
        status: str,
        dagster_run_id: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Update the pre-created manifest without changing its staging binding."""
        updates: dict[str, Any] = {
            "status": status,
            "strategy": self.strategy,
            "branch": self.branch,
            "launch_tags": self.tags,
            # These are immutable launch facts.  Promotion's source_hash and
            # target_hash_before instead describe the current merge attempt.
            # Under the snapshot strategy they record the release-pointer
            # revision the promotion CAS guard compares against.
            "launch_source_hash": self.source_hash,
            "launch_target_hash_before": self.target_hash_before,
            "source_hash": self.source_hash,
            "target_branch": "main",
            "target_hash_before": self.target_hash_before,
            # Durable launch policy: when set, the promotion sensor audits the
            # run but holds the merge for an operator-confirmed release.
            "review_hold": self.review_hold,
        }
        if dagster_run_id is not None:
            updates["dagster_run_id"] = dagster_run_id
        if error is not None:
            updates["launch_error"] = error
        if status == "launched" and dagster_run_id is not None:
            checksum = _write_launch_manifest(
                logical_run_id=self.logical_run_id,
                dagster_run_id=dagster_run_id,
                branch=self.branch,
                tags=self.tags,
                source_hash=self.source_hash,
                target_hash_before=self.target_hash_before,
            )
            if checksum is None:
                return False
            updates["launch_manifest_checksum"] = checksum
        return write_wap_report(self.logical_run_id, **updates)


def _prepare_snapshot_wap_launch(
    *, logical_run_id: str, project_id: str, attempt: int, review_hold: bool = False
) -> WapLaunch:
    """Open a run-scoped candidate namespace on a snapshot-promotion catalog."""
    resolution = resolve_capability("catalog")
    if resolution is None or not (
        resolution.support.supports_promote and resolution.support.supports_snapshots
    ):
        raise PhloConfigError(
            message=("WAP snapshot strategy requires a catalog with snapshot promotion support."),
            suggestions=[
                "Configure a snapshot-promotion catalog such as phlo-polaris, or set "
                "wap.strategy to 'branch' for a versioned catalog such as phlo-nessie."
            ],
        )
    catalog: Any = resolution.provider
    if not isinstance(catalog, SnapshotPromotionCatalog):
        raise PhloConfigError(
            message="Configured catalog does not implement snapshot-based WAP promotion.",
            suggestions=[
                "Configure a SnapshotPromotionCatalog-compatible provider, or set "
                "wap.strategy to 'branch'."
            ],
        )

    namespace = f"{WAP_BRANCH_PREFIX}{logical_run_id}"
    revision = int(catalog.release_revision())
    launch = WapLaunch(
        logical_run_id=logical_run_id,
        branch=namespace,
        catalog=catalog,
        created_branch=True,
        source_hash=str(revision),
        target_hash_before=str(revision),
        project_id=project_id,
        attempt=attempt,
        strategy=WAP_STRATEGY_SNAPSHOT,
        review_hold=review_hold,
    )
    if not launch.record_launch_result(status="branch_created"):
        raise PhloConfigError(
            message=f"Could not persist the WAP launch report for namespace {namespace!r}.",
            suggestions=[
                "Repair .phlo/wap-reports storage before retrying; the candidate namespace "
                "was retained."
            ],
        )
    return launch


def prepare_wap_launch(*, logical_run_id: str, review_hold: bool = False) -> WapLaunch:
    """Create a WAP staging ref and tags before asking Dagster to start work.

    ``review_hold`` records a durable launch policy: the promotion sensor still
    audits the run but stops before the merge, leaving an operator-confirmable
    release candidate instead of auto-publishing.
    """
    from phlo.infrastructure import load_wap_config

    project = resolve_project_identity(configured_project=get_settings().phlo_project)
    if not project.project_id:
        raise PhloConfigError(
            message="WAP materialization requires PHLO_PROJECT for run correlation.",
            suggestions=["Set PHLO_PROJECT before retrying the WAP materialization."],
        )
    attempt = 1
    strategy = load_wap_config().strategy
    if strategy == WAP_STRATEGY_SNAPSHOT:
        return _prepare_snapshot_wap_launch(
            logical_run_id=logical_run_id,
            project_id=project.project_id,
            attempt=attempt,
            review_hold=review_hold,
        )

    resolution = resolve_capability("catalog")
    if resolution is None or not (
        resolution.support.supports_refs and resolution.support.supports_promote
    ):
        raise PhloConfigError(
            message="WAP materialization requires a catalog with refs and promotion support.",
            suggestions=["Configure a versioned catalog such as phlo-nessie before enabling wap."],
        )

    catalog: Any = resolution.provider
    if not isinstance(catalog, VersionedCatalog):
        raise PhloConfigError(
            message="Configured catalog does not implement the WAP branch lifecycle.",
            suggestions=["Configure a VersionedCatalog-compatible provider before using --wap."],
        )

    branch = f"{WAP_BRANCH_PREFIX}{logical_run_id}"
    if catalog.get_branch_hash(branch):
        raise PhloConfigError(
            message=f"WAP branch {branch!r} already exists; refusing to reuse it for a new run.",
            suggestions=["Retry the command to create a new WAP branch."],
        )

    target_hash_before = catalog.get_branch_hash("main")
    if catalog.create_branch(branch, from_ref="main") is None:
        raise PhloConfigError(
            message=f"Could not create WAP branch {branch!r} from main.",
            suggestions=["Confirm the configured catalog can create branches from main."],
        )

    source_hash = catalog.get_branch_hash(branch)
    launch = WapLaunch(
        logical_run_id=logical_run_id,
        branch=branch,
        catalog=catalog,
        created_branch=True,
        source_hash=source_hash,
        target_hash_before=target_hash_before,
        project_id=project.project_id,
        attempt=attempt,
        review_hold=review_hold,
    )
    if not launch.record_launch_result(status="branch_created"):
        raise PhloConfigError(
            message=f"Could not persist the WAP launch report for branch {branch!r}.",
            suggestions=[
                "Repair .phlo/wap-reports storage before retrying; the branch was retained."
            ],
        )
    return launch
