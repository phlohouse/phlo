"""Read-only projection over the durable WAP lifecycle reports.

A release candidate is identified by its WAP launch — the report under
``.phlo/wap-reports/{logical_run_id}.json`` plus the content-addressed launch
manifest it points at. A catalog branch named like a WAP staging ref without
that report is retained staging data, not a promotable candidate; a promoted
report with a merge receipt is the only authority for a confirmed release.

Everything here is a read: preview evaluation compares the recorded launch
facts against the catalog's current revisions and reports each check plus a
digest binding them. Promotion itself stays with the WAP sensor/authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from phlo.logging import get_logger

logger = get_logger(__name__)

OWNED_WAP_REF_PREFIX = "pipeline-run-"
WAP_TARGET_BRANCH = "main"

# A candidate the operator can still act on or await.
WAP_PENDING_STATUSES = frozenset({"branch_created", "launched", "success", "promotion_pending"})
# Terminal-but-not-released: retained for audit; never promotable.
WAP_BLOCKED_STATUSES = frozenset(
    {"failed", "cancelled", "rejected", "promotion_blocked", "promotion_failed", "incomplete"}
)
# The governed release receipt — the only "release confirmed" authority.
WAP_RELEASED_STATUSES = frozenset({"promoted", "cleanup_complete"})

Readiness = Literal["ready", "in_progress", "blocked", "awaiting"]


@dataclass(frozen=True)
class WapReport:
    """Parsed durable WAP lifecycle record for one logical run."""

    logical_run_id: str
    status: str
    branch: str | None
    strategy: str
    dagster_run_id: str | None
    source_hash: str | None
    target_branch: str | None
    target_hash_before: str | None
    target_hash_after: str | None
    launch_source_hash: str | None
    launch_target_hash_before: str | None
    launch_manifest_checksum: str | None
    launch_tags: dict[str, str]
    merge_state: str | None
    release_id: str | None
    failure_reason: str | None
    failure_detail: str | None
    source_deleted: bool | None
    candidates: tuple[dict[str, Any], ...]
    created_at: str | None
    updated_at: str | None
    raw: dict[str, Any] = field(repr=False)


def _coerce_str(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int)) else None


def _parse_report(path: Path) -> WapReport | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("wap_report_unreadable", path=str(path))
        return None
    if not isinstance(payload, dict):
        return None
    logical_run_id = _coerce_str(payload.get("run_id")) or path.stem
    launch_tags = payload.get("launch_tags")
    candidates = payload.get("candidates")
    return WapReport(
        logical_run_id=logical_run_id,
        status=_coerce_str(payload.get("status")) or "unknown",
        branch=_coerce_str(payload.get("branch")),
        strategy=_coerce_str(payload.get("strategy")) or "branch",
        dagster_run_id=_coerce_str(payload.get("dagster_run_id")),
        source_hash=_coerce_str(payload.get("source_hash")),
        target_branch=_coerce_str(payload.get("target_branch")),
        target_hash_before=_coerce_str(payload.get("target_hash_before")),
        target_hash_after=_coerce_str(payload.get("target_hash_after")),
        launch_source_hash=_coerce_str(payload.get("launch_source_hash")),
        launch_target_hash_before=_coerce_str(payload.get("launch_target_hash_before")),
        launch_manifest_checksum=_coerce_str(payload.get("launch_manifest_checksum")),
        launch_tags={str(k): str(v) for k, v in launch_tags.items()}
        if isinstance(launch_tags, dict)
        else {},
        merge_state=_coerce_str(payload.get("merge_state")),
        release_id=_coerce_str(payload.get("release_id")),
        failure_reason=_coerce_str(payload.get("failure_reason")),
        failure_detail=_coerce_str(payload.get("failure_detail")),
        source_deleted=payload.get("source_deleted")
        if isinstance(payload.get("source_deleted"), bool)
        else None,
        candidates=tuple(row for row in candidates if isinstance(row, dict))
        if isinstance(candidates, list)
        else (),
        created_at=_coerce_str(payload.get("created_at")),
        updated_at=_coerce_str(payload.get("updated_at")),
        raw=payload,
    )


def load_wap_reports(project_root: Path) -> list[WapReport]:
    """Enumerate every parseable WAP lifecycle record, newest first."""
    reports_dir = project_root / ".phlo" / "wap-reports"
    if not reports_dir.is_dir():
        return []
    reports = [
        report
        for path in sorted(reports_dir.glob("*.json"))
        if (report := _parse_report(path)) is not None
    ]
    reports.sort(key=lambda report: report.updated_at or "", reverse=True)
    return reports


def _launch_manifest_path(project_root: Path, logical_run_id: str, checksum: str) -> Path:
    """Mirror ``phlo_dagster.wap_launch._launch_manifest_path``.

    The launches directory layout is a durable contract shared with the
    writer; the reader verifies contents by digest before trusting them, so a
    drifted or forged manifest fails closed rather than being believed.
    """
    run_key = hashlib.sha256(logical_run_id.encode("utf-8")).hexdigest()[:24]
    return project_root / ".phlo" / "wap-reports" / "launches" / f"{run_key}.{checksum}.json"


def verify_launch_binding(project_root: Path, report: WapReport) -> bool | None:
    """Content-verify the immutable launch manifest this report binds to.

    ``None`` means the launch never recorded a manifest checksum (pre-launch
    or pre-manifest reports); ``False`` means the manifest is missing, corrupt,
    or its payload no longer matches the recorded launch facts.
    """
    checksum = report.launch_manifest_checksum
    if not checksum:
        return None
    path = _launch_manifest_path(project_root, report.logical_run_id, checksum)
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    if hashlib.sha256(raw).hexdigest() != checksum:
        return False
    try:
        binding = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(binding, dict):
        return False
    expected = {
        "schema_version": "phlo.wap_launch_manifest.v1",
        "logical_run_id": report.logical_run_id,
        "dagster_run_id": report.dagster_run_id,
        "branch": report.branch,
        "tags": report.launch_tags,
        "source_hash": report.launch_source_hash,
        "target_branch": WAP_TARGET_BRANCH,
        "target_hash_before": report.launch_target_hash_before,
    }
    return binding == expected


# Failure codes recorded by the promotion sequence (src/phlo/wap_promotion.py)
# mapped to what the refusal means and what an operator can do next. Unknown
# codes pass through verbatim rather than being explained away.
_FAILURE_EXPLANATIONS: dict[str, tuple[str, str]] = {
    "merge_branch_returned_false": (
        "the catalog refused the merge",
        "a concurrent publish may have moved the target, or a conflicting "
        "change touched the same tables — the staging branch is retained; "
        "inspect it, then retry the promotion",
    ),
    "merge_outcome_unknown": (
        "the merge acknowledgement was lost in transit",
        "the catalog may or may not have committed — check the target ref "
        "before retrying the promotion",
    ),
    "reconciliation_pending": (
        "the merge committed but post-merge reconciliation has not confirmed",
        "wait for the reconciliation pass, then re-check the candidate",
    ),
    "release_pointer_conflict": (
        "the release pointer moved while promoting",
        "another publisher advanced the release — refresh the candidate and retry",
    ),
    "release_promotion_failed": (
        "the release promotion step failed after the merge",
        "inspect provider health and the release pointer, then retry",
    ),
}


def explain_failure(reason: str | None) -> str:
    """Render one recorded failure code as operator-readable text.

    Known codes become "what happened [code] — what to do next"; the raw code
    is always retained for diagnosis. The provider's own detail travels
    separately as ``failure_detail`` so blockers stay single-line.
    """
    code = reason or "unspecified"
    explanation = _FAILURE_EXPLANATIONS.get(code)
    return f"{explanation[0]} [{code}] — {explanation[1]}" if explanation else code


def _failure_text(report: WapReport) -> str:
    return explain_failure(report.failure_reason)


def candidate_readiness(
    report: WapReport, *, manifest_valid: bool | None
) -> tuple[Readiness, list[str]]:
    """Reduce one report to (readiness, blockers).

    Blockers are specific: each names the evidence that is missing, invalid,
    or failed — a candidate is never "ready" on branch presence alone.
    """
    blockers: list[str] = []
    status = report.status

    if not report.branch or not report.branch.startswith(OWNED_WAP_REF_PREFIX):
        blockers.append("Staging ref is not an owned WAP ref")
    if manifest_valid is False:
        blockers.append("Launch manifest missing or no longer matches recorded launch facts")
    if not report.dagster_run_id:
        blockers.append("No orchestrator run id recorded for this launch")
    if status == "promotion_blocked":
        blockers.append(f"Promotion blocked: {_failure_text(report)}")
    elif status == "promotion_failed":
        blockers.append(f"Promotion failed: {_failure_text(report)}")
    elif status in WAP_BLOCKED_STATUSES:
        blockers.append(
            f"Run ended {status.replace('_', ' ')}"
            + (f" — {_failure_text(report)}" if report.failure_reason else "")
        )

    if status == "promotion_pending":
        return "in_progress", blockers
    if blockers:
        return "blocked", blockers
    if status in {"launched", "success"}:
        return "ready", []
    return "awaiting", blockers


@dataclass(frozen=True)
class PromotionCheck:
    """One read-only preview verdict over the current WAP evidence."""

    name: str
    outcome: str
    detail: str
    passed: bool | None


@dataclass(frozen=True)
class PromotionPreview:
    """The preview an operator confirms against: checks + binding digest."""

    candidate_id: str
    eligible: bool
    checks: tuple[PromotionCheck, ...]
    digest: str
    strategy: str


def evaluate_promotion_preview(
    report: WapReport,
    *,
    project_root: Path,
    configured_strategy: str,
    current_source_hash: str | None,
    current_target_revision: str | None,
    quality_decision: str | None,
) -> PromotionPreview:
    """Evaluate the same gates the WAP promotion path enforces — read-only.

    Mirrors the sensor's order: owned staging ref, verified launch binding,
    strategy match, recorded audit decision, and the revision guard comparing
    the launch-time target revision to the catalog's current one. The digest
    binds every input so a stale preview cannot be replayed as confirmation.
    """
    checks: list[PromotionCheck] = []

    owned = bool(report.branch and report.branch.startswith(OWNED_WAP_REF_PREFIX))
    checks.append(
        PromotionCheck(
            name="Owned staging ref",
            outcome="passed" if owned else "failed",
            detail=(
                f"{report.branch} is a WAP-owned staging ref"
                if owned
                else f"{report.branch or 'unknown'} was not created by the WAP launch lifecycle"
            ),
            passed=owned,
        )
    )

    manifest_valid = verify_launch_binding(project_root, report)
    checks.append(
        PromotionCheck(
            name="Prepared launch",
            outcome="passed"
            if manifest_valid
            else "failed"
            if manifest_valid is False
            else "unavailable",
            detail=(
                "Launch manifest verified against recorded launch facts"
                if manifest_valid
                else "No launch manifest checksum recorded"
                if manifest_valid is None
                else "Launch manifest missing or mismatched"
            ),
            passed=manifest_valid,
        )
    )

    strategy_ok = report.strategy == configured_strategy
    checks.append(
        PromotionCheck(
            name="Strategy",
            outcome="passed" if strategy_ok else "failed",
            detail=(
                f"Report strategy '{report.strategy}' matches the configured strategy"
                if strategy_ok
                else f"Report strategy '{report.strategy}' != configured '{configured_strategy}'"
            ),
            passed=strategy_ok,
        )
    )

    audit_ok = quality_decision in {"passed", "passed_with_warnings"}
    checks.append(
        PromotionCheck(
            name="Audit decision",
            outcome="passed"
            if audit_ok
            else "failed"
            if quality_decision == "rejected"
            else "unavailable",
            detail=(
                f"Aggregate quality decision: {quality_decision}"
                if quality_decision
                else "No durable aggregate quality decision recorded"
            ),
            passed=audit_ok if quality_decision else None,
        )
    )

    revision_ok = (
        report.launch_target_hash_before is not None
        and current_target_revision is not None
        and report.launch_target_hash_before == current_target_revision
    )
    checks.append(
        PromotionCheck(
            name="Target revision",
            outcome="passed"
            if revision_ok
            else "failed"
            if current_target_revision is not None
            else "unavailable",
            detail=(
                f"Target still at launch-time revision {report.launch_target_hash_before}"
                if revision_ok
                else f"Target moved: launch expected {report.launch_target_hash_before}, "
                f"current is {current_target_revision}"
                if current_target_revision is not None
                else "Target revision unreadable"
            ),
            passed=revision_ok if current_target_revision is not None else None,
        )
    )

    # The audited source is the lifecycle hash stamped when the run's audit
    # completed — launch_source_hash is the pre-run fact kept for binding, not
    # the revision the audit verified.
    recorded_source = report.source_hash or report.launch_source_hash
    source_current = (
        current_source_hash is not None
        and recorded_source is not None
        and current_source_hash == recorded_source
    )
    checks.append(
        PromotionCheck(
            name="Source revision",
            outcome="passed"
            if source_current
            else "failed"
            if current_source_hash is not None and recorded_source is not None
            else "unavailable",
            detail=(
                "Staging ref head matches the audited revision"
                if source_current
                else "Staging ref moved since the audited revision"
                if current_source_hash is not None and recorded_source is not None
                else "Source revision unreadable or unrecorded"
            ),
            passed=source_current
            if current_source_hash is not None and recorded_source is not None
            else None,
        )
    )

    digest_payload = {
        "candidate_id": report.logical_run_id,
        "checks": [
            {"name": check.name, "outcome": check.outcome, "detail": check.detail}
            for check in checks
        ],
        "inputs": {
            "branch": report.branch,
            "strategy": report.strategy,
            "launch_manifest_checksum": report.launch_manifest_checksum,
            "launch_source_hash": report.launch_source_hash,
            "source_hash": report.source_hash,
            "launch_target_hash_before": report.launch_target_hash_before,
            "current_source_hash": current_source_hash,
            "current_target_revision": current_target_revision,
            "quality_decision": quality_decision,
        },
    }
    digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode("utf-8")).hexdigest()
    eligible = all(check.passed is True for check in checks)
    return PromotionPreview(
        candidate_id=report.logical_run_id,
        eligible=eligible,
        checks=tuple(checks),
        digest=digest,
        strategy=report.strategy,
    )
