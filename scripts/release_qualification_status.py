#!/usr/bin/env python3
"""Report how close a staged release candidate is to qualifying.

Read-only companion to ``promote_release_candidate.py``: where promotion
adjudicates an evidence set in one pass and fails on the first broken rule,
this report evaluates every staged bundle independently, records a
per-bundle verdict with its reason, and summarizes the remaining gap to
qualification (run count, distinct hosts, distinct UTC days, freshness,
no-predating-staging). It consumes the same staged candidate BOM and
``phlo.release-candidate-evidence/v1`` bundles; it never tags, publishes,
or edits support status.

Usage::

    python scripts/release_qualification_status.py \
        --candidate-bom bom.json --evidence-dir bundles/
    python scripts/release_qualification_status.py \
        --candidate-bom bom.json --evidence run1.json run2.json --json

Exit code is ``0`` when the set qualifies, ``1`` when it does not, and
``2`` on usage errors. The verdict is advisory: only the promotion gate
itself adjudicates promotion.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import promote_release_candidate as promote  # noqa: E402
import release_evidence  # noqa: E402

STATUS_QUALIFYING = "qualifying"
STATUS_REJECTED = "rejected"


@dataclass
class BundleVerdict:
    """One staged bundle's standalone verdict."""

    path: Path | None
    checksum: str
    status: str
    reason: str
    detail: str = ""
    host: str = ""
    started_utc: str = ""
    finished_utc: str = ""


@dataclass
class StatusReport:
    """The full qualification picture for one candidate."""

    bom: dict[str, object]
    verdicts: list[BundleVerdict] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    @property
    def qualifying(self) -> list[BundleVerdict]:
        """Return the verdicts that passed every per-bundle rule."""
        return [verdict for verdict in self.verdicts if verdict.status == STATUS_QUALIFYING]

    @property
    def qualified(self) -> bool:
        """Return whether the set currently satisfies every threshold."""
        return not self.gaps


def evaluate_bundle(bundle: dict[str, object], bom: dict[str, object]) -> tuple[str, str]:
    """Return ``(reason, detail)`` — empty reason means the bundle qualifies."""
    checksum = promote.bundle_checksum(bundle)
    if not checksum:
        return "invalid_bundle", "bundle carries no usable checksum"
    candidate = bundle.get("candidate")
    if (
        not isinstance(candidate, dict)
        or candidate.get("canonical_candidate_digest") != bom.get("canonical_candidate_digest")
        or candidate.get("release_commit") != bom.get("release_commit")
    ):
        return "wrong_candidate", (
            "bound to candidate "
            f"({candidate.get('release_commit') if isinstance(candidate, dict) else None}, "
            f"{candidate.get('canonical_candidate_digest') if isinstance(candidate, dict) else None})"
        )
    try:
        release_evidence.validate_bundle(bundle, bom)
    except release_evidence.EvidenceError as exc:
        return "invalid_bundle", str(exc)
    if bundle.get("conclusion") != release_evidence.CONCLUSION_PASSED:
        return "failed_run", f"concluded {bundle.get('conclusion')!r}"
    try:
        promote._check_bundle_environment(bundle)
    except promote.PromotionGateError as exc:
        return "wrong_environment", str(exc)
    return "", ""


def _timestamps(bundle: dict[str, object]) -> tuple[str, str]:
    """Extract started/finished instants when present and parseable."""
    started = bundle.get("started_utc")
    finished = bundle.get("finished_utc")
    for value in (started, finished):
        if not isinstance(value, str):
            return "", ""
        try:
            promote.parse_utc(value)
        except promote.PromotionGateError:
            return "", ""
    return str(started), str(finished)


def report_status(
    bundles: list[dict[str, object]],
    bom: dict[str, object],
    *,
    now_utc: datetime,
    staged_utc: str | None = None,
    prior_receipt_bundles: set[str] | None = None,
) -> StatusReport:
    """Evaluate every bundle independently and summarize the remaining gap."""
    report = StatusReport(bom=bom)
    seen: set[str] = set()
    for bundle in bundles:
        checksum = promote.bundle_checksum(bundle) or "<no checksum>"
        reason, detail = evaluate_bundle(bundle, bom)
        if not reason and checksum in seen:
            reason, detail = "duplicate_evidence", "same checksum as an earlier bundle"
        seen.add(checksum)
        if not reason and prior_receipt_bundles and checksum in prior_receipt_bundles:
            reason, detail = "replayed_evidence", "already consumed by a prior receipt"
        host = ""
        if not reason:
            environment = bundle.get("environment")
            host = str(environment.get("host", "")) if isinstance(environment, dict) else ""
        started, finished = _timestamps(bundle) if not reason else ("", "")
        report.verdicts.append(
            BundleVerdict(
                path=None,
                checksum=checksum,
                status=STATUS_REJECTED if reason else STATUS_QUALIFYING,
                reason=reason,
                detail=detail,
                host=host,
                started_utc=started,
                finished_utc=finished,
            )
        )

    qualifying = report.qualifying
    if len(qualifying) < promote.MIN_QUALIFYING_RUNS:
        report.gaps.append(
            f"{len(qualifying)} of {promote.MIN_QUALIFYING_RUNS} required qualifying runs"
        )
    hosts = sorted({verdict.host for verdict in qualifying})
    if len(hosts) < promote.MIN_DISTINCT_HOSTS:
        report.gaps.append(
            f"{len(hosts)} of {promote.MIN_DISTINCT_HOSTS} required distinct hosts "
            f"({', '.join(hosts) or 'none'})"
        )
    days = {promote.parse_utc(verdict.started_utc).date() for verdict in qualifying}
    if len(days) < promote.MIN_DISTINCT_DAYS:
        report.gaps.append(
            f"{len(days)} of {promote.MIN_DISTINCT_DAYS} required distinct UTC days "
            f"({', '.join(sorted(str(day) for day in days)) or 'none'})"
        )
    finished = [promote.parse_utc(verdict.finished_utc) for verdict in qualifying]
    if finished:
        newest = max(finished)
        if now_utc - newest > promote.timedelta(days=promote.MAX_EVIDENCE_AGE_DAYS):
            report.gaps.append(
                f"newest qualifying run finished {promote.format_utc(newest)}, older than "
                f"the {promote.MAX_EVIDENCE_AGE_DAYS}-day freshness window"
            )
    if staged_utc is not None:
        staged_at = promote.parse_utc(staged_utc)
        predating = [
            verdict.checksum
            for verdict in qualifying
            if promote.parse_utc(verdict.started_utc) < staged_at
        ]
        if predating:
            report.gaps.append(
                f"{len(predating)} qualifying run(s) predate candidate staging at "
                f"{staged_utc}: {', '.join(predating)}"
            )
    return report


def _render_human(report: StatusReport) -> str:
    lines = [
        f"candidate: {report.bom.get('release_commit')} "
        f"(digest {str(report.bom.get('canonical_candidate_digest'))[:16]}…)",
        f"bundles: {len(report.verdicts)} staged — {len(report.qualifying)} qualifying, "
        f"{len(report.verdicts) - len(report.qualifying)} rejected",
    ]
    for verdict in report.verdicts:
        if verdict.status == STATUS_QUALIFYING:
            lines.append(
                f"  qualifying  {verdict.checksum[:12]}…  host={verdict.host}  "
                f"finished {verdict.finished_utc}"
            )
        else:
            lines.append(
                f"  rejected    {verdict.checksum[:12]}…  {verdict.reason}: {verdict.detail}"
            )
    if report.gaps:
        lines.append("gap:")
        lines.extend(f"  {gap}" for gap in report.gaps)
    lines.append(f"status: {'qualified' if report.qualified else 'not_qualified'}")
    return "\n".join(lines)


def _render_json(report: StatusReport) -> str:
    document = {
        "candidate": {
            "release_commit": report.bom.get("release_commit"),
            "canonical_candidate_digest": report.bom.get("canonical_candidate_digest"),
        },
        "thresholds": {
            "min_qualifying_runs": promote.MIN_QUALIFYING_RUNS,
            "min_distinct_hosts": promote.MIN_DISTINCT_HOSTS,
            "min_distinct_days": promote.MIN_DISTINCT_DAYS,
            "max_evidence_age_days": promote.MAX_EVIDENCE_AGE_DAYS,
        },
        "bundles": [
            {
                "path": str(verdict.path) if verdict.path else None,
                "checksum": verdict.checksum,
                "status": verdict.status,
                "reason": verdict.reason,
                "detail": verdict.detail,
                "host": verdict.host,
                "started_utc": verdict.started_utc,
                "finished_utc": verdict.finished_utc,
            }
            for verdict in report.verdicts
        ],
        "gaps": report.gaps,
        "qualified": report.qualified,
    }
    return json.dumps(document, indent=2)


def _evidence_paths(args: argparse.Namespace) -> list[Path]:
    paths = list(args.evidence or [])
    if args.evidence_dir:
        paths.extend(sorted(args.evidence_dir.glob("*.json")))
    return paths


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse qualification-status CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-bom", type=Path, required=True, help="Staged candidate BOM")
    parser.add_argument(
        "--evidence", type=Path, nargs="+", default=None, help="Evidence bundle paths"
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="Directory scanned for *.json evidence bundles",
    )
    parser.add_argument(
        "--staged-utc",
        default=None,
        help="Instant the canonical digest was staged (enables the no-predating-staging gap)",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Current UTC instant for freshness evaluation (testing; default: real clock)",
    )
    parser.add_argument("--json", action="store_true", help="Emit the JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Evaluate staged bundles and print the qualification status report."""
    args = parse_args(argv)
    paths = _evidence_paths(args)
    try:
        bom = promote.load_candidate_bom(args.candidate_bom)
        bundles = [promote.load_evidence_bundle(path) for path in paths]
    except promote.PromotionGateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    now_utc = promote.parse_utc(args.now) if args.now else datetime.now(UTC)
    report = report_status(bundles, bom, now_utc=now_utc, staged_utc=args.staged_utc)
    for verdict, path in zip(report.verdicts, paths, strict=True):
        verdict.path = path
    print(_render_json(report) if args.json else _render_human(report))
    return 0 if report.qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
