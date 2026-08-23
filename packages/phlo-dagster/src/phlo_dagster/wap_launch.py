"""Pre-launch Write-Audit-Publish coordination for Dagster runs.

Prepares an isolated WAP branch and tags before Dagster starts work, then keeps
content-addressed launch manifests and lifecycle reports under .phlo/wap-reports
so promotion binds to the exact audited launch.

Part of phlo-dagster's WAP tooling alongside wap_sensors: runs on the launch path before
Dagster work begins.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phlo._correlation import resolve_project_identity
from phlo.capabilities.interfaces import VersionedCatalog
from phlo.capabilities.resolver import resolve_capability
from phlo.config import get_settings
from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger

WAP_BRANCH_TAG = "phlo/wap_branch"
WAP_REF_TAG = "phlo/ref"
WAP_RUN_ID_TAG = "phlo/run_id"
WAP_PROJECT_ID_TAG = "phlo/project_id"
WAP_ATTEMPT_TAG = "phlo/attempt"
WAP_BRANCH_PREFIX = "pipeline-run-"
logger = get_logger(__name__)


def _report_path(logical_run_id: str) -> Path:
    root = Path(os.getenv("PHLO_PROJECT_PATH", "."))
    return root / ".phlo" / "wap-reports" / f"{logical_run_id}.json"


def _report_snapshot_path(logical_run_id: str, checksum: str) -> Path:
    root = Path(os.getenv("PHLO_PROJECT_PATH", ".")) / ".phlo" / "wap-reports" / "evidence"
    run_key = hashlib.sha256(logical_run_id.encode("utf-8")).hexdigest()[:24]
    return root / f"{run_key}.{checksum}.json"


def _launch_manifest_path(logical_run_id: str, checksum: str) -> Path:
    root = Path(os.getenv("PHLO_PROJECT_PATH", ".")) / ".phlo" / "wap-reports" / "launches"
    run_key = hashlib.sha256(logical_run_id.encode("utf-8")).hexdigest()[:24]
    return root / f"{run_key}.{checksum}.json"


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
    payload = {
        "schema_version": "phlo.wap_launch_manifest.v1",
        "logical_run_id": logical_run_id,
        "dagster_run_id": dagster_run_id,
        "branch": branch,
        "tags": tags,
        "source_hash": source_hash,
        "target_branch": "main",
        "target_hash_before": target_hash_before,
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    path = _launch_manifest_path(logical_run_id, checksum)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(serialized, encoding="utf-8")
            # Read-only so a later launch cannot silently rewrite a binding
            # that promotion verifies by digest.
            path.chmod(0o444)
    except OSError:
        logger.warning(
            "wap_launch_manifest_write_failed",
            logical_run_id=logical_run_id,
            path=str(path),
            exc_info=True,
        )
        return None
    return checksum


def read_wap_launch_manifest(logical_run_id: str, checksum: str) -> dict[str, Any] | None:
    """Read a launch binding only when its content matches the recorded digest."""
    try:
        raw = _launch_manifest_path(logical_run_id, checksum).read_bytes()
        if hashlib.sha256(raw).hexdigest() != checksum:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_wap_report(logical_run_id: str, **updates: Any) -> bool:
    """Persist the durable WAP launch manifest and lifecycle audit record.

    The logical ID is created before GraphQL submission, so this record exists
    even when Dagster rejects a run or a response is lost in transit.
    """
    path = _report_path(logical_run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    now = datetime.now(timezone.utc).isoformat()
    payload.update(updates)
    payload.update(
        {
            "created_at": payload.get("created_at", now),
            "schema_version": "phlo.wap_report.v2",
            "run_id": logical_run_id,
            "updated_at": now,
        }
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        raw = serialized.encode("utf-8")
        # A promotion report is also the local retry record.  Replacing it
        # atomically prevents a crash from turning a valid prior record into
        # a partially-written JSON document that the next sensor cannot use.
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary.write(raw)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        snapshot_path = _report_snapshot_path(logical_run_id, hashlib.sha256(raw).hexdigest())
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot_path.exists():
            snapshot_path.write_bytes(raw)
    except OSError:
        logger.warning(
            "wap_report_write_failed", path=str(path), logical_run_id=logical_run_id, exc_info=True
        )
        return False
    return True


def read_wap_report(logical_run_id: str) -> dict[str, Any] | None:
    """Read the latest durable WAP lifecycle record for a logical run."""
    try:
        payload = json.loads(_report_path(logical_run_id).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class WapLaunch:
    """The logical identity and branch prepared before a Dagster run starts."""

    logical_run_id: str
    branch: str
    catalog: VersionedCatalog
    created_branch: bool
    source_hash: str | None
    target_hash_before: str | None
    project_id: str
    attempt: int

    @property
    def tags(self) -> dict[str, str]:
        """Return the Dagster tags that bind stages to this WAP branch."""
        return {
            WAP_RUN_ID_TAG: self.logical_run_id,
            WAP_BRANCH_TAG: self.branch,
            WAP_REF_TAG: self.branch,
            WAP_PROJECT_ID_TAG: self.project_id,
            WAP_ATTEMPT_TAG: str(self.attempt),
        }

    def cleanup_if_created(self) -> None:
        """Remove only the branch created by this launch attempt.

        Normal WAP callers intentionally do not invoke this method: retained
        refs and their reports are the audit trail for failed checks.
        """
        if self.created_branch:
            self.catalog.delete_branch(self.branch)

    def record_launch_result(
        self,
        *,
        status: str,
        dagster_run_id: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Update the pre-created manifest without changing its branch binding."""
        updates: dict[str, Any] = {
            "status": status,
            "branch": self.branch,
            "launch_tags": self.tags,
            # These are immutable launch facts.  Promotion's source_hash and
            # target_hash_before instead describe the current merge attempt.
            "launch_source_hash": self.source_hash,
            "launch_target_hash_before": self.target_hash_before,
            "source_hash": self.source_hash,
            "target_branch": "main",
            "target_hash_before": self.target_hash_before,
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


def prepare_wap_launch(*, logical_run_id: str) -> WapLaunch:
    """Create a WAP branch and tags before asking Dagster to start work."""
    project = resolve_project_identity(configured_project=get_settings().phlo_project)
    if not project.project_id:
        raise PhloConfigError(
            message="WAP materialization requires PHLO_PROJECT for run correlation.",
            suggestions=["Set PHLO_PROJECT before retrying the WAP materialization."],
        )
    attempt = 1
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
    )
    if not launch.record_launch_result(status="branch_created"):
        raise PhloConfigError(
            message=f"Could not persist the WAP launch report for branch {branch!r}.",
            suggestions=[
                "Repair .phlo/wap-reports storage before retrying; the branch was retained."
            ],
        )
    return launch
