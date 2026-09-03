"""MinIO object-storage backup contribution (ADR 0049 §3, Plan 011 Step 2).

The contributor copies every user object (lake tables + Iceberg metadata
files) into its owned staging prefix, preserving key paths, and records a
listing with per-object SHA-256 checksums. Objects are fetched through the
provider-owned ``mc`` client inside the MinIO service container; the
contributor never finalizes a set and never touches another provider's
prefix.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from phlo.capabilities.continuity import (
    BackupArtifact,
    BackupContributorResult,
    BackupContributorState,
    RestoreStepPhase,
    RestoreStepResult,
    RestoreTarget,
    fail_contributor,
    redact_message,
    sha256_bytes,
    sha256_file,
)
from phlo.logging import get_logger

logger = get_logger(__name__)

PROVIDER = "minio"
LISTING_ARTIFACT_NAME = "objects.json"
_SYSTEM_BUCKETS = frozenset({"minio", "minioadmin"})

McRunner = Callable[[list[str]], str]


def _default_mc(args: list[str]) -> str:
    """Run an ``mc`` command inside the MinIO service container."""
    from phlo.cli.infrastructure.command import run_command
    from phlo_minio.cli import _mc_shell_exec_base, _mc_with_local_alias

    cmd = [*_mc_shell_exec_base(tty=False), *_mc_with_local_alias(args)]
    result = run_command(cmd, timeout_seconds=600, capture_output=True, check=True)
    return result.stdout


def _mc_json_lines(output: str) -> list[dict[str, Any]]:
    """Parse ``mc --json`` line-delimited output, skipping empty lines."""
    entries: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


class MinioBackupContributor:
    """Provider-owned contributor producing the object-store copy + listing."""

    def __init__(
        self,
        mc_runner: McRunner | None = None,
        buckets: list[str] | None = None,
    ) -> None:
        self._mc_runner = mc_runner
        self._buckets = buckets

    def contribute(self, destination: Path, operation_id: str) -> BackupContributorResult:
        """Copy user objects beneath ``destination`` (minio prefix) and list them."""
        destination = Path(destination)
        artifacts: list[BackupArtifact] = []
        listing: list[dict[str, Any]] = []
        try:
            mc = self._mc_runner or _default_mc
            buckets = self._buckets
            if buckets is None:
                buckets = sorted(
                    entry.get("key", "").rstrip("/")
                    for entry in _mc_json_lines(mc(["ls", "--json", "local"]))
                    if entry.get("key")
                )
                buckets = [bucket for bucket in buckets if bucket not in _SYSTEM_BUCKETS]
            destination.mkdir(parents=True, exist_ok=True)
            for bucket in buckets:
                objects = [
                    entry
                    for entry in _mc_json_lines(
                        mc(["ls", "--recursive", "--json", f"local/{bucket}"])
                    )
                    if entry.get("key") and not str(entry["key"]).endswith("/")
                ]
                for obj in sorted(objects, key=lambda item: str(item["key"])):
                    key = str(obj["key"])
                    body = mc(["cat", f"local/{bucket}/{key}"]).encode("utf-8")
                    local = destination / bucket / key
                    local.parent.mkdir(parents=True, exist_ok=True)
                    local.write_bytes(body)
                    relative = f"{PROVIDER}/{bucket}/{key}"
                    listing.append(
                        {
                            "bucket": bucket,
                            "key": key,
                            "relative_path": relative,
                            "size_bytes": len(body),
                            "sha256": sha256_bytes(body),
                        }
                    )
                    artifacts.append(
                        BackupArtifact(
                            provider=PROVIDER,
                            name=key.rsplit("/", 1)[-1] or key,
                            relative_path=relative,
                            size_bytes=len(body),
                            sha256=sha256_bytes(body),
                            metadata={"bucket": bucket},
                        )
                    )
            listing_path = destination / LISTING_ARTIFACT_NAME
            listing_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "operation_id": operation_id,
                        "objects": sorted(listing, key=lambda item: item["relative_path"]),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("minio_backup_contribution_failed", exc_info=True)
            return fail_contributor(PROVIDER, redact_message(str(exc)), operation_id)
        artifacts.append(
            BackupArtifact(
                provider=PROVIDER,
                name=LISTING_ARTIFACT_NAME,
                relative_path=f"{PROVIDER}/{LISTING_ARTIFACT_NAME}",
                size_bytes=listing_path.stat().st_size,
                sha256=sha256_file(listing_path),
                metadata={"operation_id": operation_id, "object_count": str(len(listing))},
            )
        )
        return BackupContributorResult(
            provider=PROVIDER,
            state=BackupContributorState.SUCCEEDED,
            artifacts=tuple(artifacts),
            operation_id=operation_id,
        )

    def restore(
        self,
        target: RestoreTarget,
        artifacts: Sequence[BackupArtifact],
        plan_token: str,
        backup_set_dir: str,
    ) -> RestoreStepResult:
        object_artifacts = [
            artifact for artifact in artifacts if artifact.name != LISTING_ARTIFACT_NAME
        ]
        try:
            target_dir = Path(target.location) / PROVIDER
            restored: list[str] = []
            for artifact in object_artifacts:
                src = Path(backup_set_dir) / artifact.relative_path
                dest = target_dir / artifact.relative_path.removeprefix(f"{PROVIDER}/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())
                restored.append(str(dest))
            return RestoreStepResult.ok(
                PROVIDER,
                evidence={
                    "restored_objects": len(restored),
                    "plan_token": plan_token,
                },
            )
        except Exception as exc:
            return RestoreStepResult.fail_step(
                PROVIDER, RestoreStepPhase.SUBMISSION, redact_message(str(exc))
            )

    def reconcile(
        self,
        target: RestoreTarget,
        artifacts: Sequence[BackupArtifact],
        plan_token: str,
        backup_set_dir: str,
    ) -> dict[str, Any]:
        object_artifacts = [
            artifact for artifact in artifacts if artifact.name != LISTING_ARTIFACT_NAME
        ]
        mismatches: list[str] = []
        for artifact in object_artifacts:
            dest = (
                Path(target.location)
                / PROVIDER
                / artifact.relative_path.removeprefix(f"{PROVIDER}/")
            )
            if not dest.is_file():
                mismatches.append(f"{artifact.name}:missing")
                continue
            if sha256_file(dest) != artifact.sha256:
                mismatches.append(f"{artifact.name}:digest_mismatch")
        ok = not mismatches
        return {
            "ok": ok,
            "reason": "" if ok else ";".join(mismatches),
            "object_count": str(len(object_artifacts)),
        }
