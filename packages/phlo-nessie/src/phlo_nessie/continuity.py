"""Nessie catalog backup contribution (ADR 0049 §3, Plan 011 Step 2).

The contributor exports the Nessie catalog revision state (branches and
hashes) as a JSON artifact beneath its owned staging prefix. It never
finalizes a set and never touches another provider's prefix.
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

PROVIDER = "nessie"
CATALOG_ARTIFACT_NAME = "catalog.json"


def _pick_artifact(artifacts: Sequence[BackupArtifact], suffix: str) -> BackupArtifact | None:
    return next((artifact for artifact in artifacts if artifact.name.endswith(suffix)), None)


class NessieBackupContributor:
    """Provider-owned contributor producing a catalog revision export."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def contribute(self, destination: Path, operation_id: str) -> BackupContributorResult:
        """Capture the catalog export beneath ``destination`` (nessie prefix)."""
        destination = Path(destination)
        try:
            client = self._client
            if client is None:
                from phlo_nessie.resource import NessieResource

                client = NessieResource()
            branches = [
                {"name": branch.name, "hash": branch.hash}
                for branch in client.list_branches()
                if isinstance(getattr(branch, "name", None), str) and branch.name
            ]
            branches.sort(key=lambda branch: branch["name"])
            payload = {
                "schema_version": "1",
                "operation_id": operation_id,
                "branches": branches,
            }
            destination.mkdir(parents=True, exist_ok=True)
            artifact_path = destination / CATALOG_ARTIFACT_NAME
            artifact_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception as exc:
            return fail_contributor(PROVIDER, redact_message(str(exc)), operation_id)
        artifact = BackupArtifact(
            provider=PROVIDER,
            name=CATALOG_ARTIFACT_NAME,
            relative_path=f"{PROVIDER}/{CATALOG_ARTIFACT_NAME}",
            size_bytes=artifact_path.stat().st_size,
            sha256=sha256_file(artifact_path),
            metadata={"operation_id": operation_id, "branch_count": str(len(branches))},
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
        artifact = _pick_artifact(artifacts, CATALOG_ARTIFACT_NAME)
        if artifact is None:
            return RestoreStepResult.fail_step(
                PROVIDER, RestoreStepPhase.PREFLIGHT, "missing nessie catalog artifact"
            )
        try:
            content = (Path(backup_set_dir) / artifact.relative_path).read_bytes()
            target_dir = Path(target.location) / PROVIDER
            target_dir.mkdir(parents=True, exist_ok=True)
            restored = target_dir / CATALOG_ARTIFACT_NAME
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
        artifact = _pick_artifact(artifacts, CATALOG_ARTIFACT_NAME)
        restored = Path(target.location) / PROVIDER / CATALOG_ARTIFACT_NAME
        if artifact is None or not restored.is_file():
            return {"ok": False, "reason": "missing_restored_catalog"}
        source = json.loads(
            (Path(backup_set_dir) / artifact.relative_path).read_text(encoding="utf-8")
        )
        restored_payload = json.loads(restored.read_text(encoding="utf-8"))
        ok = source.get("branches") == restored_payload.get("branches")
        return {
            "ok": ok,
            "reason": "" if ok else "catalog_branch_mismatch",
            "branch_count": str(len(restored_payload.get("branches") or [])),
        }
