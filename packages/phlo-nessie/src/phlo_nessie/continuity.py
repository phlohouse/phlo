"""Nessie catalog backup contribution (ADR 0049 §3, Plan 011 Step 2).

The contributor exports the Nessie catalog revision state (branches and
hashes) as a JSON artifact beneath its owned staging prefix. It never
finalizes a set and never touches another provider's prefix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phlo.capabilities.continuity import (
    BackupArtifact,
    BackupContributorResult,
    BackupContributorState,
    fail_contributor,
    redact_message,
    sha256_file,
)

PROVIDER = "nessie"
CATALOG_ARTIFACT_NAME = "catalog.json"


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
