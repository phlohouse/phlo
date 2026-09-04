#!/usr/bin/env python3
"""Canonical Phlo release-candidate evidence bundles.

One evidence bundle records every required runtime demonstration executed
against one immutable candidate BOM: the candidate identity pair, the digest
of every artifact actually exercised, the executing environment identity, UTC
timestamps, one structured result per demonstration, and a pass/fail
conclusion. The bundle is sanitized (secret-shaped values never enter it),
canonical (sorted keys, no whitespace), BOM-bound (it names the exact
canonical candidate digest it qualifies), and checksummed (a SHA-256 over the
canonical bundle content, excluding the checksum itself).

``validate_bundle`` re-derives every invariant and is exposed through
``scripts/validate_release_evidence.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE_SCHEMA = "phlo.release-candidate-evidence/v1"

#: Every required runtime demonstration the golden-path runner must record
#: before a bundle can carry a ``passed`` conclusion.
REQUIRED_DEMONSTRATIONS: tuple[tuple[str, str], ...] = (
    ("candidate_bom_verification", "Candidate BOM verification"),
    ("operator_installation", "Exact BOM artifact installation"),
    ("project_scaffold", "Project scaffold from installed artifacts"),
    ("production_preflight", "Production readiness preflight"),
    ("negative_security", "Negative security enforcement"),
    ("stack_start", "Exact image digest stack start without build"),
    ("ingestion_materialization", "Ingestion materialization"),
    ("storage_probe", "Object storage readiness and owned write"),
    ("row_query_initial", "Initial row query"),
    ("transformation_materialization", "Transformation materialization"),
    ("row_query_transform", "Transformed row query"),
    ("wap_configuration", "WAP configuration"),
    ("wap_promotion", "WAP materialization and promotion"),
    ("wap_rejection", "WAP quality rejection"),
    ("run_report", "Run report and scoped denial"),
    ("plan_first_maintenance", "Plan-first table maintenance"),
    ("backup_creation", "Verified backup set creation"),
    ("backup_verification", "Independent backup verification"),
    ("restore_explicit_target", "Restore to explicit target"),
    ("supported_upgrade", "Supported pair upgrade"),
    ("upgrade_recovery", "Upgrade recovery reconciliation"),
    ("row_query_final", "Final row query"),
    ("support_boundary_consistency", "Support-boundary consistency"),
)

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
CONCLUSION_PASSED = "passed"
CONCLUSION_FAILED = "failed"
_SECRET_KEY_RE = re.compile(
    r"secret|password|passwd|token|credential|authorization|api[-_]?key|"
    r"private[-_]?key|access[-_]?key|bearer|cookie",
    re.IGNORECASE,
)
_REDACTED = "[REDACTED]"


class EvidenceError(ValueError):
    """An evidence bundle is not canonical, sanitized, or BOM-bound."""


def utc_now() -> str:
    """Return the current UTC time as a sortable ISO-8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value: object) -> bytes:
    """Return the canonical encoding of one JSON value (sorted keys, no whitespace)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sanitize(value: object) -> object:
    """Return a deep copy with every secret-shaped key's value redacted."""
    if isinstance(value, dict):
        return {
            str(key): (_REDACTED if _SECRET_KEY_RE.search(str(key)) else sanitize(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    return value


def bundle_checksum(bundle: dict[str, object]) -> str:
    """Compute the bundle's SHA-256 over its canonical content without the checksum."""
    content = {key: value for key, value in bundle.items() if key != "checksum"}
    return hashlib.sha256(canonical_json(content)).hexdigest()


def new_bundle(
    *,
    release_commit: str,
    canonical_candidate_digest: str,
    artifact_count: int,
    environment: dict[str, object],
) -> dict[str, object]:
    """Start one evidence bundle for a candidate identity pair."""
    started = utc_now()
    bundle: dict[str, object] = {
        "schema": EVIDENCE_SCHEMA,
        "candidate": {
            "release_commit": release_commit,
            "canonical_candidate_digest": canonical_candidate_digest,
            "artifact_count": artifact_count,
        },
        "environment": sanitize(environment) | {"clean_host": True, "source_checkout": False},
        "started_utc": started,
        "finished_utc": started,
        "conclusion": CONCLUSION_FAILED,
        "demonstrations": [],
        "artifacts_exercised": [],
        "failure": None,
        "checksum": {"algorithm": "sha256", "value": ""},
    }
    bundle["checksum"] = {"algorithm": "sha256", "value": bundle_checksum(bundle)}
    return bundle


def record_demonstration(
    bundle: dict[str, object],
    *,
    demonstration_id: str,
    title: str,
    status: str,
    result: dict[str, object],
    artifacts: list[dict[str, object]] | None = None,
    error: str | None = None,
    started_utc: str | None = None,
    finished_utc: str | None = None,
) -> None:
    """Append one structured demonstration result and re-seal the bundle."""
    demonstrations = bundle["demonstrations"]
    if not isinstance(demonstrations, list):
        raise EvidenceError("bundle demonstrations must be a list")
    entry = sanitize(
        {
            "id": demonstration_id,
            "title": title,
            "status": status,
            "started_utc": started_utc or utc_now(),
            "finished_utc": finished_utc or utc_now(),
            "result": result,
            "artifacts": artifacts or [],
            "error": error,
        }
    )
    if not isinstance(entry, dict):
        raise EvidenceError("demonstration entry must be an object")
    demonstrations.append(entry)
    exercised = {
        json.dumps(artifact, sort_keys=True) for artifact in demonstrations[-1]["artifacts"]
    }
    existing = bundle["artifacts_exercised"]
    if isinstance(existing, list):
        for artifact in existing:
            exercised.add(json.dumps(artifact, sort_keys=True))
    bundle["artifacts_exercised"] = sorted(json.dumps(item) for item in exercised)
    bundle["artifacts_exercised"] = [json.loads(item) for item in bundle["artifacts_exercised"]]
    bundle["checksum"] = {"algorithm": "sha256", "value": bundle_checksum(bundle)}


def finalize_bundle(bundle: dict[str, object]) -> dict[str, object]:
    """Set the terminal conclusion and failure record, then re-seal the checksum."""
    demonstrations = bundle.get("demonstrations")
    if not isinstance(demonstrations, list):
        raise EvidenceError("bundle demonstrations must be a list")
    failed = [
        demonstration
        for demonstration in demonstrations
        if isinstance(demonstration, dict) and demonstration.get("status") != STATUS_PASSED
    ]
    if failed:
        bundle["conclusion"] = CONCLUSION_FAILED
        first = failed[0]
        bundle["failure"] = {
            "demonstration": first.get("id"),
            "error": first.get("error"),
        }
    else:
        bundle["conclusion"] = CONCLUSION_PASSED
        bundle["failure"] = None
    if bundle["conclusion"] == CONCLUSION_FAILED and not bundle["failure"]:
        bundle["failure"] = {
            "demonstration": None,
            "error": "no demonstration completed",
        }
    bundle["finished_utc"] = utc_now()
    bundle["checksum"] = {"algorithm": "sha256", "value": bundle_checksum(bundle)}
    return bundle


def validate_bundle(bundle: object, bom: dict[str, object] | None = None) -> dict[str, object]:
    """Re-derive every bundle invariant; optionally bind it to a BOM document.

    Returns the validated bundle. Raises :class:`EvidenceError` when the bundle
    is not canonical, not sanitized, not checksummed, or incomplete.
    """
    if not isinstance(bundle, dict):
        raise EvidenceError("evidence bundle must be a JSON object")
    if bundle.get("schema") != EVIDENCE_SCHEMA:
        raise EvidenceError(f"bundle schema must be {EVIDENCE_SCHEMA!r}")

    candidate = bundle.get("candidate")
    if not isinstance(candidate, dict):
        raise EvidenceError("bundle candidate must be an object")
    for field in ("release_commit", "canonical_candidate_digest", "artifact_count"):
        if field not in candidate:
            raise EvidenceError(f"bundle candidate is missing {field!r}")

    checksum = bundle.get("checksum")
    if (
        not isinstance(checksum, dict)
        or checksum.get("algorithm") != "sha256"
        or not isinstance(checksum.get("value"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(checksum["value"]))
    ):
        raise EvidenceError("bundle checksum must be a sha256 hex value")
    recomputed = bundle_checksum(bundle)
    if checksum["value"] != recomputed:
        raise EvidenceError(
            f"bundle checksum mismatch: recorded {checksum['value']!r}, "
            f"canonical content hashes to {recomputed!r}"
        )

    demonstrations = bundle.get("demonstrations")
    if not isinstance(demonstrations, list):
        raise EvidenceError("bundle demonstrations must be a list")
    seen: set[str] = set()
    for index, demonstration in enumerate(demonstrations):
        if not isinstance(demonstration, dict):
            raise EvidenceError(f"demonstration {index} must be an object")
        for field in ("id", "title", "status", "started_utc", "finished_utc", "result"):
            if field not in demonstration:
                raise EvidenceError(f"demonstration {index} is missing {field!r}")
        if not isinstance(demonstration["result"], dict):
            raise EvidenceError(f"demonstration {index} result must be an object")
        if demonstration["status"] not in (STATUS_PASSED, STATUS_FAILED):
            raise EvidenceError(
                f"demonstration {index} status must be {STATUS_PASSED!r} or {STATUS_FAILED!r}"
            )
        if demonstration["id"] in seen:
            raise EvidenceError(f"demonstration {demonstration['id']!r} is recorded twice")
        seen.add(str(demonstration["id"]))
        if not _is_sanitized(demonstration):
            raise EvidenceError(f"demonstration {index} carries a secret-shaped value")

    conclusion = bundle.get("conclusion")
    if conclusion not in (CONCLUSION_PASSED, CONCLUSION_FAILED):
        raise EvidenceError(
            f"bundle conclusion must be {CONCLUSION_PASSED!r} or {CONCLUSION_FAILED!r}"
        )
    if conclusion == CONCLUSION_PASSED:
        missing = [
            title
            for demonstration_id, title in REQUIRED_DEMONSTRATIONS
            if demonstration_id not in seen
        ]
        if missing:
            raise EvidenceError(f"passed bundle is missing demonstrations: {missing!r}")
        failed = [
            str(demonstration["id"])
            for demonstration in demonstrations
            if demonstration["status"] != STATUS_PASSED
        ]
        if failed:
            raise EvidenceError(f"passed bundle records failed demonstrations: {failed!r}")
    elif not bundle.get("failure"):
        raise EvidenceError("failed bundle must carry a failure record")

    exercised = bundle.get("artifacts_exercised")
    if not isinstance(exercised, list):
        raise EvidenceError("bundle artifacts_exercised must be a list")

    if bom is not None:
        canonical_digest = str(bom.get("canonical_candidate_digest", ""))
        if candidate["canonical_candidate_digest"] != canonical_digest:
            raise EvidenceError(
                "bundle is bound to candidate "
                f"{candidate['canonical_candidate_digest']!r}, but the BOM is candidate "
                f"{canonical_digest!r}"
            )
        if candidate.get("release_commit") != bom.get("release_commit"):
            raise EvidenceError("bundle release_commit does not match the BOM release_commit")
        bom_digests = {str(artifact["digest"]) for artifact in bom.get("artifacts", [])}
        unknown = [
            str(artifact.get("digest"))
            for artifact in exercised
            if isinstance(artifact, dict) and str(artifact.get("digest")) not in bom_digests
        ]
        if unknown:
            raise EvidenceError(
                f"bundle exercised artifacts outside the BOM: {sorted(set(unknown))!r}"
            )
    return bundle


def _is_sanitized(value: object) -> bool:
    """Return True when no dict key in the value carries a secret-shaped name."""
    if isinstance(value, dict):
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)) and item not in (None, "", [], {}):
                return False
            if not _is_sanitized(item):
                return False
        return True
    if isinstance(value, list):
        return all(_is_sanitized(item) for item in value)
    return True


def load_bundle(path: Path) -> dict[str, object]:
    """Load one evidence bundle from disk."""
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"could not read evidence bundle {path}: {exc}") from exc
    if not isinstance(bundle, dict):
        raise EvidenceError(f"evidence bundle {path} is not a JSON object")
    return bundle


def write_bundle(bundle: dict[str, object], path: Path) -> None:
    """Write one bundle as canonical, sorted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(rendered, encoding="utf-8")
