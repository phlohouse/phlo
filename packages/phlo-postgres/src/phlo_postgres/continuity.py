"""PostgreSQL backup contribution (ADR 0049 §3, Plan 011 Step 2).

The contributor captures a consistent ``pg_dump`` of the configured database
beneath its owned staging prefix and returns artifact descriptors. It never
finalizes a set and never touches another provider's prefix.
"""

from __future__ import annotations

import gzip
from collections.abc import Callable
from pathlib import Path

from phlo.capabilities.continuity import (
    BackupArtifact,
    BackupContributorResult,
    BackupContributorState,
    fail_contributor,
    redact_message,
    sha256_file,
)

DumpRunner = Callable[[], str]

PROVIDER = "postgres"


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
