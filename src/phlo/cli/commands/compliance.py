"""Click commands for compliance evidence packs.

``export-evidence`` bundles audit records and signatures into a signed pack;
``verify-evidence`` re-checks pack integrity. Both exit nonzero on failure.

Wired into the phlo CLI main as the compliance command group.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from phlo.compliance.evidence import EvidenceKeyError, create_evidence_pack, verify_evidence_pack
from phlo.logging import get_logger

logger = get_logger(__name__)


def _ensure_compliance_capabilities() -> None:
    """Ensure compliance capabilities are available."""


@click.group(name="compliance")
def compliance_group():
    """Manage compliance features and evidence.

    This command group provides tools for:
    - Exporting compliance evidence packs
    - Verifying evidence integrity
    - Managing access governance

    For regulated deployments, evidence packs are used to demonstrate
    compliance during audits.
    """
    _ensure_compliance_capabilities()


@compliance_group.command(name="export-evidence")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path for the evidence pack ZIP file.",
)
@click.option(
    "--created-by",
    required=True,
    help="Subject creating the evidence pack (email or user ID).",
)
@click.option(
    "--domain",
    help="Compliance domain (e.g., sox, hipaa, pci).",
)
@click.option(
    "--description",
    "-d",
    help="Description of the evidence pack.",
)
@click.option(
    "--audit-records",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to JSONL file containing audit records.",
)
@click.option(
    "--signatures",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to JSONL file containing signature records.",
)
@click.option(
    "--manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to JSON file containing system manifest.",
)
def export_evidence(
    output: Path,
    created_by: str,
    domain: str | None,
    description: str | None,
    audit_records: Path | None,
    signatures: Path | None,
    manifest: Path | None,
):
    """Export a compliance evidence pack.

    Creates a tamper-evident ZIP archive containing audit records,
    signatures, and system manifest data for compliance submission.

    The archive is authenticated with HMAC-SHA256 using key material
    from the ``PHLO_EVIDENCE_HMAC_KEY`` or ``PHLO_AUDIT_HMAC_KEY``
    environment variable.  Export fails when no key is available.

    Examples:
        phlo compliance export-evidence -o evidence.zip --created-by admin@example.com
        phlo compliance export-evidence -o evidence.zip --domain sox --audit-records audit.jsonl

    Exit code 0 means export succeeded. Non-zero means failures occurred.
    """
    audit_data = None
    sig_data = None
    manifest_data = None

    if audit_records:
        try:
            import json

            records = []
            with audit_records.open() as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            audit_data = records
        except Exception as e:
            click.echo(f"Error reading audit records: {e}", err=True)
            sys.exit(1)

    if signatures:
        try:
            import json

            sigs = []
            with signatures.open() as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sigs.append(json.loads(line))
            sig_data = sigs
        except Exception as e:
            click.echo(f"Error reading signatures: {e}", err=True)
            sys.exit(1)

    if manifest:
        try:
            import json

            with manifest.open() as f:
                manifest_data = json.load(f)
        except Exception as e:
            click.echo(f"Error reading manifest: {e}", err=True)
            sys.exit(1)

    try:
        pack = create_evidence_pack(
            created_by=created_by,
            compliance_domain=domain,
            description=description,
            audit_records=audit_data,
            signatures=sig_data,
            manifest_data=manifest_data,
        )

        output.parent.mkdir(parents=True, exist_ok=True)
        pack.write_zip(output)

        click.echo(f"Evidence pack created: {output}")
        click.echo(f"  Pack ID: {pack.manifest.pack_id}")
        click.echo(f"  Created: {pack.manifest.created_at}")
        click.echo(f"  Records: {pack.manifest.record_count}")
        click.echo(f"  Files: {pack.manifest.file_count}")

    except EvidenceKeyError as e:
        click.echo(f"Evidence export failed: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("evidence_export_failed")
        click.echo(f"Evidence export failed: {e}", err=True)
        sys.exit(1)

    sys.exit(0)


@compliance_group.command(name="verify-evidence")
@click.argument("zip_path", type=click.Path(exists=True, path_type=Path))
def verify_evidence(zip_path: Path):
    """Verify the integrity of an evidence pack.

    Checks that the evidence pack has not been tampered with by
    validating the HMAC-SHA256 signature over the canonical
    ``checksums.json`` bytes using key material from the
    ``PHLO_EVIDENCE_HMAC_KEY`` or ``PHLO_AUDIT_HMAC_KEY`` environment
    variable.

    ``valid`` means the pack's integrity is externally authenticated,
    not merely internally checksum-consistent.  Unsigned version-1
    packs and packs with missing key material fail verification.

    Examples:
        phlo compliance verify-evidence evidence.zip

    Exit code 0 means verification passed. Non-zero means failures occurred.
    """
    try:
        result = verify_evidence_pack(zip_path)

        if result.get("valid"):
            click.echo(f"Evidence pack is valid: {zip_path}")
            click.echo(f"  Pack ID: {result.get('pack_id', 'unknown')}")
            click.echo(f"  Created: {result.get('created_at', 'unknown')}")
            click.echo(f"  Files: {result.get('file_count', 0)}")
            click.echo(f"  Records: {result.get('record_count', 0)}")
            click.echo(f"  Format: v{result.get('format_version', '?')}")
            click.echo(f"  Key ID: {result.get('key_id', 'unknown')}")
            sys.exit(0)
        else:
            error = result.get("error")
            fmt = result.get("format_version")
            if fmt == 1:
                click.echo(f"Evidence pack is unsigned (format version 1): {zip_path}", err=True)
                click.echo(f"  {error}", err=True)
            else:
                click.echo(f"Evidence pack verification failed: {error}", err=True)
            sys.exit(1)

    except Exception as e:
        logger.exception("evidence_verification_failed")
        click.echo(f"Evidence verification failed: {e}", err=True)
        sys.exit(1)
