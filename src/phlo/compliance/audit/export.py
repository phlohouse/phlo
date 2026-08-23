"""Audit export to JSONL with chain verification.

Provides export functionality for sealed audit records.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phlo.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from phlo.compliance.audit.sealed import AuditStore, SealedAuditRecord


def export_jsonl(
    records: list[SealedAuditRecord],
    output_path: Path,
) -> None:
    """Export sealed audit records to JSONL file.

    Each line is a JSON object with the sealed record metadata
    and the original event data.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w") as f:
        for record in records:
            line = json.dumps(record.to_dict(), sort_keys=True)
            f.write(line + "\n")

    logger.info(
        "audit_export_completed",
        output=str(output_path),
        record_count=len(records),
    )


def verify_and_export(
    store: AuditStore,
    surface: str,
    output_dir: Path,
    after: int | None = None,
    before: int | None = None,
    hmac_key: bytes | None = None,
) -> dict[str, Any]:
    """Verify chain integrity and export records."""
    records = store.query(surface, after=after, before=before, limit=100000)
    verification = store.verify_chain(surface, hmac_key=hmac_key)

    output_dir.mkdir(parents=True, exist_ok=True)

    audit_file = output_dir / f"{surface}-audit.jsonl"
    export_jsonl(records, audit_file)

    chain_file = output_dir / f"{surface}-chain-verification.json"
    chain_result: dict[str, Any] = {
        "surface": surface,
        "valid": verification.valid,
        "total_records": verification.total_records,
    }
    if not verification.valid:
        chain_result["first_invalid_sequence"] = verification.first_invalid_sequence
        chain_result["error_message"] = verification.error_message

    with chain_file.open("w") as f:
        json.dump(chain_result, f, indent=2)

    return {
        "verification": verification,
        "audit_file": str(audit_file),
        "chain_file": str(chain_file),
        "record_count": len(records),
    }
