"""Durable WAP lifecycle report store.

Canonical home for the content-addressed launch manifests and per-run
lifecycle reports under ``.phlo/wap-reports``. These records are project
state, not orchestrator state: phlo-dagster's launch path and promotion
sensors write them, and operator-facing tooling (phlo-api) reads and
advances them through the same store so both writers share one durable
claim/receipt contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phlo.logging import get_logger

WAP_TARGET_BRANCH = "main"
PROMOTED_OUTCOME_FIELDS = frozenset({"failure_reason", "failure_detail"})

logger = get_logger(__name__)

_PROCESS_LOCKS: dict[Path, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def wap_reports_dir(project_root: Path) -> Path:
    return project_root / ".phlo" / "wap-reports"


def wap_report_path(project_root: Path, logical_run_id: str) -> Path:
    return wap_reports_dir(project_root) / f"{logical_run_id}.json"


def wap_report_snapshot_path(project_root: Path, logical_run_id: str, checksum: str) -> Path:
    run_key = hashlib.sha256(logical_run_id.encode("utf-8")).hexdigest()[:24]
    return wap_reports_dir(project_root) / "evidence" / f"{run_key}.{checksum}.json"


def wap_launch_manifest_path(project_root: Path, logical_run_id: str, checksum: str) -> Path:
    run_key = hashlib.sha256(logical_run_id.encode("utf-8")).hexdigest()[:24]
    return wap_reports_dir(project_root) / "launches" / f"{run_key}.{checksum}.json"


def write_wap_report(project_root: Path, logical_run_id: str, /, **updates: Any) -> bool:
    """Persist the durable WAP launch manifest and lifecycle audit record.

    The logical ID is created before orchestrator submission, so this record
    exists even when submission is rejected or a response is lost in transit.
    """
    path = wap_report_path(project_root, logical_run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    if updates.get("status") == "promoted":
        # Terminal success replaces failure-only outcome fields from prior
        # attempts. Identity and evidence binding fields remain append/update
        # compatible, including the immutable launch-manifest checksum.
        for field in PROMOTED_OUTCOME_FIELDS:
            payload.pop(field, None)
            updates.pop(field, None)

    now = datetime.now(UTC).isoformat()
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
        # a partially-written JSON document that the next reader cannot use.
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary.write(raw)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
        snapshot_path = wap_report_snapshot_path(
            project_root, logical_run_id, hashlib.sha256(raw).hexdigest()
        )
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot_path.exists():
            snapshot_path.write_bytes(raw)
    except OSError:
        logger.warning(
            "wap_report_write_failed", path=str(path), logical_run_id=logical_run_id, exc_info=True
        )
        return False
    return True


def read_wap_report(project_root: Path, logical_run_id: str) -> dict[str, Any] | None:
    """Read the latest durable WAP lifecycle record for a logical run."""
    try:
        payload = json.loads(
            wap_report_path(project_root, logical_run_id).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    # Older writers may have serialized an explicit null; callers should see
    # it exactly like an absent failure reason.
    for field in ("failure_reason", "failure_detail"):
        if payload.get(field) is None:
            payload.pop(field, None)
    return payload


def write_wap_launch_manifest(
    project_root: Path,
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
        "target_branch": WAP_TARGET_BRANCH,
        "target_hash_before": target_hash_before,
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    path = wap_launch_manifest_path(project_root, logical_run_id, checksum)
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


def read_wap_launch_manifest(
    project_root: Path, logical_run_id: str, checksum: str
) -> dict[str, Any] | None:
    """Read a launch binding only when its content matches the recorded digest."""
    try:
        raw = wap_launch_manifest_path(project_root, logical_run_id, checksum).read_bytes()
        if hashlib.sha256(raw).hexdigest() != checksum:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@contextmanager
def promotion_lock(project_root: Path, logical_run_id: str) -> Iterator[None]:
    """Mutual exclusion for one candidate's promotion sequence.

    The durable report is the claim/receipt, but claim acquisition and the
    catalog mutation are not one atomic step: an advisory lockfile makes the
    read-guard → outbox → merge → receipt sequence single-writer across the
    auto-promotion sensor and any operator-triggered command. Falls back to
    a process-local lock on platforms without ``fcntl``.
    """
    locks_dir = wap_reports_dir(project_root) / ".locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    run_key = hashlib.sha256(logical_run_id.encode("utf-8")).hexdigest()[:24]
    lock_path = locks_dir / f"{run_key}.lock"
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX fallback
        with _PROCESS_LOCKS_GUARD:
            lock = _PROCESS_LOCKS.setdefault(lock_path, threading.Lock())
        with lock:
            yield
        return
    with lock_path.open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class WapReportIdentity:
    """The identity fields a launch manifest binds — shared by verification."""

    logical_run_id: str
    dagster_run_id: str | None
    branch: str | None
    tags: dict[str, str]
    source_hash: str | None
    target_hash_before: str | None
