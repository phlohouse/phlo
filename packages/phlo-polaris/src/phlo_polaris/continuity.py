"""Polaris release-ledger backup contribution (ADR 0049 §3 pattern).

The contributor exports the WAP release ledger (candidate and release rows)
as a JSON artifact beneath its owned staging prefix. It never finalizes a set
and never touches another provider's prefix.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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
    sha256_file,
)

PROVIDER = "polaris"
RELEASES_ARTIFACT_NAME = "releases.json"


def _pick_artifact(artifacts: Sequence[BackupArtifact], suffix: str) -> BackupArtifact | None:
    return next((artifact for artifact in artifacts if artifact.name.endswith(suffix)), None)


class PolarisBackupContributor:
    """Provider-owned contributor producing a release-ledger export."""

    def __init__(self, catalog: Any | None = None) -> None:
        self._catalog = catalog

    def _ledger_rows(self) -> list[dict[str, Any]]:
        catalog = self._catalog
        if catalog is None:
            from phlo_polaris.promotion import PolarisSnapshotPromotionCatalog

            catalog = PolarisSnapshotPromotionCatalog()
        rows = catalog.store.rows()
        rows.sort(key=lambda row: (str(row.get("kind")), str(row.get("table_name"))))
        return rows

    def contribute(self, destination: Path, operation_id: str) -> BackupContributorResult:
        """Capture the release-ledger export beneath ``destination``."""
        destination = Path(destination)
        try:
            rows = self._ledger_rows()
            payload = {
                "schema_version": "1",
                "operation_id": operation_id,
                "releases": rows,
            }
            destination.mkdir(parents=True, exist_ok=True)
            artifact_path = destination / RELEASES_ARTIFACT_NAME
            artifact_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
            )
        except Exception as exc:
            return fail_contributor(PROVIDER, redact_message(str(exc)), operation_id)
        artifact = BackupArtifact(
            provider=PROVIDER,
            name=RELEASES_ARTIFACT_NAME,
            relative_path=f"{PROVIDER}/{RELEASES_ARTIFACT_NAME}",
            size_bytes=artifact_path.stat().st_size,
            sha256=sha256_file(artifact_path),
            metadata={"operation_id": operation_id, "row_count": str(len(rows))},
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
        artifact = _pick_artifact(artifacts, RELEASES_ARTIFACT_NAME)
        if artifact is None:
            return RestoreStepResult.fail_step(
                PROVIDER, RestoreStepPhase.PREFLIGHT, "missing polaris release ledger artifact"
            )
        try:
            content = (Path(backup_set_dir) / artifact.relative_path).read_bytes()
            target_dir = Path(target.location) / PROVIDER
            target_dir.mkdir(parents=True, exist_ok=True)
            restored = target_dir / RELEASES_ARTIFACT_NAME
            restored.write_bytes(content)
            return RestoreStepResult.ok(
                PROVIDER,
                evidence={
                    "restored_path": str(restored),
                    "restored_sha256": sha256_file(restored),
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
        artifact = _pick_artifact(artifacts, RELEASES_ARTIFACT_NAME)
        restored = Path(target.location) / PROVIDER / RELEASES_ARTIFACT_NAME
        if artifact is None or not restored.is_file():
            return {"ok": False, "reason": "missing_restored_releases"}
        source = json.loads(
            (Path(backup_set_dir) / artifact.relative_path).read_text(encoding="utf-8")
        )
        restored_payload = json.loads(restored.read_text(encoding="utf-8"))
        ok = source.get("releases") == restored_payload.get("releases")
        return {
            "ok": ok,
            "reason": "" if ok else "release_ledger_mismatch",
            "row_count": str(len(restored_payload.get("releases") or [])),
        }
