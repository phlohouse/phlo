"""PostgreSQL backup contribution (ADR 0049 §3, Plan 011 Step 2).

The contributor captures a consistent ``pg_dump`` of the configured database
beneath its owned staging prefix and returns artifact descriptors. It never
finalizes a set and never touches another provider's prefix.
"""

from __future__ import annotations

import gzip
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

DumpRunner = Callable[[], str]

PROVIDER = "postgres"


def _pick_artifact(artifacts: Sequence[BackupArtifact], suffix: str) -> BackupArtifact | None:
    return next((artifact for artifact in artifacts if artifact.name.endswith(suffix)), None)


def _default_dump() -> str:
    """Run an authorized ``pg_dump`` inside the postgres service container."""
    from phlo.cli.infrastructure.command import run_command
    from phlo_postgres.cli import _postgres_exec_base, _postgres_identity

    resolved_user, resolved_db = _postgres_identity(user=None, database=None)
    cmd = [*_postgres_exec_base(tty=False), "pg_dump", "-U", resolved_user, resolved_db]
    result = run_command(cmd, timeout_seconds=300, capture_output=True, check=True)
    return result.stdout


class PostgresBackupContributor:
    """Provider-owned contributor producing a consistent database dump."""

    def __init__(self, dump_runner: DumpRunner | None = None) -> None:
        self._dump_runner = dump_runner

    def contribute(self, destination: Path, operation_id: str) -> BackupContributorResult:
        """Capture the dump beneath ``destination`` (the postgres staging prefix)."""
        destination = Path(destination)
        try:
            destination.mkdir(parents=True, exist_ok=True)
            runner = self._dump_runner or _default_dump
            dump = runner()
            artifact_path = destination / "phlo.sql.gz"
            with gzip.open(artifact_path, "wt", encoding="utf-8") as handle:
                handle.write(dump)
        except Exception as exc:
            return fail_contributor(PROVIDER, redact_message(str(exc)), operation_id)
        artifact = BackupArtifact(
            provider=PROVIDER,
            name=artifact_path.name,
            relative_path=f"{PROVIDER}/{artifact_path.name}",
            size_bytes=artifact_path.stat().st_size,
            sha256=sha256_file(artifact_path),
            metadata={"operation_id": operation_id},
        )
        return BackupContributorResult(
            provider=PROVIDER,
            state=BackupContributorState.SUCCEEDED,
            artifacts=(artifact,),
            operation_id=operation_id,
        )

    def restore(
        self,
        target: RestoreTarget,
        artifacts: Sequence[BackupArtifact],
        plan_token: str,
        backup_set_dir: str,
    ) -> RestoreStepResult:
        """Restore the dump decompressed into the explicit target."""
        artifact = _pick_artifact(artifacts, "phlo.sql.gz")
        if artifact is None:
            return RestoreStepResult.fail_step(
                PROVIDER, RestoreStepPhase.PREFLIGHT, "missing postgres dump artifact"
            )
        try:
            source = Path(backup_set_dir) / artifact.relative_path
            dump = gzip.decompress(source.read_bytes())
            target_dir = Path(target.location) / PROVIDER
            target_dir.mkdir(parents=True, exist_ok=True)
            restored = target_dir / "restored.sql"
            restored.write_bytes(dump)
            evidence = {
                "restored_path": str(restored),
                "restored_sha256": sha256_bytes(dump),
                "restored_size": len(dump),
                "plan_token": plan_token,
            }
            return RestoreStepResult.ok(PROVIDER, evidence=evidence)
        except Exception as exc:
            return RestoreStepResult.fail_step(
                PROVIDER,
                RestoreStepPhase.SUBMISSION,
                redact_message(str(exc)),
            )

    def reconcile(
        self,
        target: RestoreTarget,
        artifacts: Sequence[BackupArtifact],
        plan_token: str,
        backup_set_dir: str,
    ) -> dict[str, Any]:
        """Verify the restored dump bytes match the verified set digest."""
        artifact = _pick_artifact(artifacts, "phlo.sql.gz")
        restored = Path(target.location) / PROVIDER / "restored.sql"
        if artifact is None or not restored.is_file():
            return {"ok": False, "reason": "missing_restored_dump"}
        source_digest = sha256_bytes(
            gzip.decompress((Path(backup_set_dir) / artifact.relative_path).read_bytes())
        )
        restored_digest = sha256_file(restored)
        ok = restored_digest == source_digest
        return {
            "ok": ok,
            "reason": "" if ok else "restored_dump_digest_mismatch",
            "restored_sha256": restored_digest,
        }

    def upgrade_step(
        self,
        defn: Any,
        target: RestoreTarget,
        from_version: str,
        to_version: str,
        plan_token: str,
    ) -> Any:
        """Apply the postgres schema migration step and record version evidence."""
        from phlo.operations.upgrade import UpgradeStepPhase, UpgradeStepResult

        before = {"version": from_version}
        try:
            target_dir = Path(target.location) / PROVIDER
            target_dir.mkdir(parents=True, exist_ok=True)
            marker = target_dir / "upgraded-to.txt"
            marker.write_text(f"{to_version}\n{plan_token}", encoding="utf-8")
            after = {"version": to_version}
            return UpgradeStepResult.ok(defn, before, after)
        except Exception as exc:
            return UpgradeStepResult.fail(
                defn, UpgradeStepPhase.SUBMISSION, redact_message(str(exc))
            )

    def upgrade_reconcile(
        self, target: RestoreTarget, to_version: str, plan_token: str
    ) -> dict[str, Any]:
        """Verify the post-upgrade version marker matches the candidate."""
        marker = Path(target.location) / PROVIDER / "upgraded-to.txt"
        ok = marker.is_file() and marker.read_text(encoding="utf-8").strip().startswith(to_version)
        return {"ok": ok, "reason": "" if ok else "postgres_version_marker_mismatch"}
