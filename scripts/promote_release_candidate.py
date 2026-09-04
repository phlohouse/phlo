#!/usr/bin/env python3
"""Gate Phlo release promotion on repeated qualifying evidence.

It consumes one immutable staged candidate BOM (``phlo.release-candidate-bom/v1``)
and a set of real evidence bundles (``phlo.release-candidate-evidence/v1``).
It allows promotion only when at least three successful qualifying runs are
bound to the same canonical candidate digest, ran on three distinct clean
hosts across at least two distinct UTC days, and the newest run is no older
than seven days at authorization time.

Those checks allow promotion of the exact staged bytes.

Promotion is a change of visibility and namespace, never a rebuild: every step
is digest-verified against the BOM before it acts, runs in the fixed order
(release tag → PyPI → images by digest → GitHub Release finalisation),
and requires an explicit, recorded release-owner authorization bound to the
candidate identity, the exact qualifying evidence bundle checksums, and the
target channel. The result is a checksummed promotion receipt reconciled to
BOM-bound public identities; a partial publication can only produce a
non-success ``partial_publication`` receipt.

Every operation is bounded, non-publishing verification by default (dry run).
Real publication additionally requires ``--execute`` together with a validated
authorization record; without both, nothing is tagged, pushed, uploaded, or
finalised.

Support-status promotion is explicitly out of scope:
``registry/support/v1.json`` and ``scripts/validate_support_manifest.py`` are
neither a promotion gate nor a promotion output and are not touched here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_candidate_bom  # noqa: E402
import release_evidence  # noqa: E402

AUTHORIZATION_SCHEMA = "phlo.release-promotion-authorization/v1"
RECEIPT_SCHEMA = "phlo.release-promotion-receipt/v1"
REJECTION_SCHEMA = "phlo.release-promotion-rejection/v1"
LOCK_SCHEMA = "phlo.release-candidate-lock/v1"

#: Qualifying-evidence thresholds. These values define the promotion contract
#: and must not be tuned at this call site.
MIN_QUALIFYING_RUNS = 3
MIN_DISTINCT_HOSTS = 3
MIN_DISTINCT_DAYS = 2
MAX_EVIDENCE_AGE_DAYS = 7

#: Fixed publish ordering.
STEP_ORDER = ("release_tag", "pypi_publish", "image_promotion", "release_finalisation")

STATUS_PROMOTED = "promoted"
STATUS_PARTIAL = "partial_publication"
STATUS_DRY_RUN = "dry_run"
STEP_COMPLETED = "completed"
STEP_PLANNED = "planned"
STEP_FAILED = "failed"
STEP_NOT_RUN = "not_run"
RECONCILE_MATCHED = "matched"
RECONCILE_MISMATCHED = "mismatched"

UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class PromotionGateError(ValueError):
    """A candidate, evidence set, authorization, or promotion is not qualifying."""

    def __init__(self, reason: str, message: str) -> None:
        """Create a gate failure with a stable machine-readable reason code."""
        super().__init__(message)
        self.reason = reason


class PublishBlockedError(RuntimeError):
    """A real publish step failed; the candidate is left partially published."""


def parse_utc(timestamp: str) -> datetime:
    """Parse a ``YYYY-MM-DDTHH:MM:SSZ`` UTC timestamp."""
    try:
        return datetime.strptime(timestamp, UTC_FORMAT).replace(tzinfo=UTC)  # noqa: DTZ007
    except (TypeError, ValueError) as exc:
        raise PromotionGateError(
            "invalid_timestamp", f"timestamp {timestamp!r} is not an ISO-8601 UTC instant"
        ) from exc


def format_utc(moment: datetime) -> str:
    """Format one UTC instant as a canonical timestamp."""
    return moment.astimezone(UTC).strftime(UTC_FORMAT)


def utc_now() -> datetime:
    """Return the current UTC instant."""
    return datetime.now(UTC)


def canonical_json(value: object) -> bytes:
    """Return the canonical encoding of one JSON value (sorted keys, no whitespace)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_of_value(value: object) -> str:
    """Return the SHA-256 hex digest of one JSON value's canonical encoding."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def load_json_object(path: Path) -> dict[str, object]:
    """Load one JSON document from disk, requiring a top-level object."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionGateError("unreadable_document", f"could not read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise PromotionGateError("unreadable_document", f"{path} is not a JSON object")
    return document


def load_candidate_bom(path: Path) -> dict[str, object]:
    """Load and fully validate one staged candidate BOM document."""
    try:
        return release_candidate_bom.load_bom(path)
    except release_candidate_bom.BomError as exc:
        raise PromotionGateError("invalid_bom", f"invalid candidate BOM {path}: {exc}") from exc


def load_evidence_bundle(path: Path) -> dict[str, object]:
    """Load one evidence bundle from disk without validating it yet."""
    try:
        return release_evidence.load_bundle(path)
    except release_evidence.EvidenceError as exc:
        raise PromotionGateError("unreadable_document", f"could not read {path}: {exc}") from exc


def bundle_checksum(bundle: dict[str, object]) -> str:
    """Return the recorded checksum of one evidence bundle."""
    checksum = bundle.get("checksum")
    if isinstance(checksum, dict):
        value = checksum.get("value")
        if isinstance(value, str):
            return value
    return ""


def load_authorization(path: Path) -> dict[str, object]:
    """Load one release-owner authorization record from disk."""
    record = load_json_object(path)
    if record.get("schema") != AUTHORIZATION_SCHEMA:
        raise PromotionGateError(
            "invalid_authorization",
            f"authorization {path} must have schema {AUTHORIZATION_SCHEMA!r}, "
            f"got {record.get('schema')!r}",
        )
    return record


def validate_authorization(
    record: dict[str, object],
    bom: dict[str, object],
    qualifying_checksums: set[str],
) -> dict[str, object]:
    """Re-derive every authorization invariant against the locked candidate.

    The record must name exactly the candidate identity pair being promoted,
    carry an explicit ``authorized: true`` decision by a named release owner,
    reference a signed approval record, and list exactly the checksums of the
    qualifying evidence bundles the gate accepted. Any mismatch fails closed.
    """
    candidate = record.get("candidate")
    if not isinstance(candidate, dict):
        raise PromotionGateError(
            "invalid_authorization", "authorization candidate must be an object"
        )
    if candidate.get("release_commit") != bom.get("release_commit") or candidate.get(
        "canonical_candidate_digest"
    ) != bom.get("canonical_candidate_digest"):
        raise PromotionGateError(
            "wrong_candidate",
            "authorization names candidate "
            f"({candidate.get('release_commit')}, {candidate.get('canonical_candidate_digest')}) "
            f"but the staged BOM is candidate ({bom.get('release_commit')}, "
            f"{bom.get('canonical_candidate_digest')})",
        )
    if record.get("authorized") is not True:
        raise PromotionGateError(
            "not_authorized",
            "publish workflow refuses to run: the authorization record does not carry "
            "an explicit authorized=true release-owner decision",
        )
    for field_name in ("release_owner", "target_channel", "approval_reference"):
        value = record.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise PromotionGateError(
                "invalid_authorization",
                f"authorization field {field_name!r} must be a non-empty string",
            )
    named = record.get("evidence_bundle_checksums")
    if not isinstance(named, list) or not all(isinstance(item, str) for item in named):
        raise PromotionGateError(
            "invalid_authorization",
            "authorization evidence_bundle_checksums must be a list of bundle checksums",
        )
    if set(named) != qualifying_checksums:
        raise PromotionGateError(
            "authorization_evidence_mismatch",
            "authorization must name exactly the qualifying evidence bundles: authorized "
            f"{sorted(set(named))!r} != gate-accepted {sorted(qualifying_checksums)!r}",
        )
    authorized_utc = record.get("authorized_utc")
    if not isinstance(authorized_utc, str):
        raise PromotionGateError(
            "invalid_authorization", "authorization must carry an authorized_utc instant"
        )
    parse_utc(authorized_utc)
    return record


def authorization_digest(record: dict[str, object]) -> str:
    """Return the SHA-256 digest of one authorization record's canonical content."""
    return sha256_of_value(record)


def bundle_host(bundle: dict[str, object]) -> str:
    """Return the clean-host identity one bundle ran on."""
    environment = bundle.get("environment")
    if not isinstance(environment, dict):
        raise PromotionGateError(
            "wrong_environment", f"bundle {bundle_checksum(bundle)!r} carries no environment"
        )
    host = environment.get("host")
    if not isinstance(host, str) or not host.strip():
        raise PromotionGateError(
            "wrong_environment",
            f"bundle {bundle_checksum(bundle)!r} records no executing host identity",
        )
    return host


def _check_bundle_environment(bundle: dict[str, object]) -> None:
    environment = bundle.get("environment")
    if not isinstance(environment, dict):
        raise PromotionGateError(
            "wrong_environment", f"bundle {bundle_checksum(bundle)!r} carries no environment"
        )
    if environment.get("clean_host") is not True:
        raise PromotionGateError(
            "wrong_environment",
            f"bundle {bundle_checksum(bundle)!r} did not execute on a clean host",
        )
    if environment.get("source_checkout") is not False:
        raise PromotionGateError(
            "wrong_environment",
            f"bundle {bundle_checksum(bundle)!r} ran from a source checkout, not the "
            "installed artifacts",
        )
    if environment.get("promoting") is True:
        raise PromotionGateError(
            "wrong_environment",
            f"bundle {bundle_checksum(bundle)!r} was produced by the promotion automation; "
            "no qualifying-run evidence may be added by the automation that will later "
            "perform promotion",
        )


@dataclass(frozen=True)
class Qualification:
    """The adjudicated result of one evidence-set qualification."""

    qualifying: list[dict[str, object]]
    rejected: list[dict[str, object]]
    hosts: list[str]
    distinct_utc_days: int
    newest_run_utc: str
    oldest_run_utc: str

    @property
    def checksums(self) -> set[str]:
        """Return the checksums of every qualifying bundle."""
        return {bundle_checksum(bundle) for bundle in self.qualifying}


def qualify_evidence_set(
    bundles: list[dict[str, object]],
    bom: dict[str, object],
    *,
    now_utc: datetime,
    staged_utc: str | None = None,
    prior_receipt_bundles: set[str] | None = None,
) -> Qualification:
    """Adjudicate one evidence set against the qualifying rules.

    Every submitted bundle is validated, bound to the BOM, and required to be a
    complete, passed run on a clean host. Set-level rules then enforce the
    frozen repetition counts, host/day distinctness, freshness at ``now_utc``
    (the authorization instant when promoting), and the no-predating-staging
    rule. Replays of bundles already consumed by a prior promotion receipt are
    rejected. Raises :class:`PromotionGateError` with a stable reason code when
    the set does not qualify.
    """
    if not bundles:
        raise PromotionGateError("missing_evidence", "no evidence bundles were submitted")

    rejected: list[dict[str, object]] = []
    valid: list[dict[str, object]] = []
    seen_checksums: set[str] = set()
    for bundle in bundles:
        checksum = bundle_checksum(bundle)
        if not checksum:
            raise PromotionGateError(
                "invalid_bundle", "a submitted evidence bundle carries no usable checksum"
            )
        if checksum in seen_checksums:
            raise PromotionGateError(
                "duplicate_evidence",
                "the same evidence bundle was submitted twice (checksum "
                f"{checksum}); repeated runs must be distinct executions",
            )
        seen_checksums.add(checksum)
        candidate = bundle.get("candidate")
        if (
            not isinstance(candidate, dict)
            or candidate.get("canonical_candidate_digest") != bom.get("canonical_candidate_digest")
            or candidate.get("release_commit") != bom.get("release_commit")
        ):
            raise PromotionGateError(
                "wrong_candidate",
                f"evidence bundle {checksum!r} is bound to candidate "
                f"({candidate.get('release_commit') if isinstance(candidate, dict) else None}, "
                f"{candidate.get('canonical_candidate_digest') if isinstance(candidate, dict) else None}) "
                f"but the staged BOM is candidate ({bom.get('release_commit')}, "
                f"{bom.get('canonical_candidate_digest')}); evidence from any other "
                "canonical digest is non-qualifying",
            )
        try:
            release_evidence.validate_bundle(bundle, bom)
            if bundle.get("conclusion") != release_evidence.CONCLUSION_PASSED:
                raise PromotionGateError(
                    "failed_run",
                    f"evidence bundle {checksum!r} concluded "
                    f"{bundle.get('conclusion')!r}; only fully passed runs qualify",
                )
            _check_bundle_environment(bundle)
        except release_evidence.EvidenceError as exc:
            rejected.append({"checksum": checksum, "reason": "invalid_bundle", "detail": str(exc)})
            continue
        valid.append(bundle)

    if not valid:
        detail = "; ".join(f"{item['reason']}: {item['detail']}" for item in rejected)
        raise PromotionGateError(
            "failed_run" if not rejected else "invalid_bundle",
            f"no submitted evidence bundle qualifies ({len(rejected)} rejected): {detail}",
        )

    if prior_receipt_bundles:
        replayed = sorted({bundle_checksum(bundle) for bundle in valid} & prior_receipt_bundles)
        if replayed:
            raise PromotionGateError(
                "replayed_evidence",
                f"evidence bundle(s) {replayed!r} were already consumed by a prior "
                "promotion receipt for this candidate and cannot be replayed",
            )

    if len(valid) < MIN_QUALIFYING_RUNS:
        raise PromotionGateError(
            "insufficient_runs",
            f"{len(valid)} qualifying run(s) is below the minimum of {MIN_QUALIFYING_RUNS}",
        )

    hosts = sorted({bundle_host(bundle) for bundle in valid})
    if len(hosts) < MIN_DISTINCT_HOSTS:
        raise PromotionGateError(
            "insufficient_hosts",
            f"qualifying runs executed on {len(hosts)} distinct host(s) {hosts!r}, below "
            f"the minimum of {MIN_DISTINCT_HOSTS}",
        )

    def started(bundle: dict[str, object]) -> datetime:
        return parse_utc(str(bundle["started_utc"]))

    finished = [parse_utc(str(bundle["finished_utc"])) for bundle in valid]
    days = {started(bundle).date() for bundle in valid}
    if len(days) < MIN_DISTINCT_DAYS:
        raise PromotionGateError(
            "insufficient_days",
            f"qualifying runs span {len(days)} distinct UTC calendar day(s) "
            f"{sorted(str(day) for day in days)!r}, below the minimum of "
            f"{MIN_DISTINCT_DAYS}",
        )

    newest = max(finished)
    if now_utc - newest > timedelta(days=MAX_EVIDENCE_AGE_DAYS):
        raise PromotionGateError(
            "stale_evidence",
            f"the newest qualifying run finished {format_utc(newest)}, older than the "
            f"freshness window of {MAX_EVIDENCE_AGE_DAYS} days at "
            f"{format_utc(now_utc)}",
        )

    if staged_utc is not None:
        staged_at = parse_utc(staged_utc)
        predating = [bundle_checksum(bundle) for bundle in valid if started(bundle) < staged_at]
        if predating:
            raise PromotionGateError(
                "predates_staging",
                f"qualifying run(s) {predating!r} predate the staging of the canonical "
                f"digest at {staged_utc}; no run may qualify against a candidate before "
                "that candidate existed",
            )

    ordered = sorted(valid, key=started)
    return Qualification(
        qualifying=ordered,
        rejected=rejected,
        hosts=hosts,
        distinct_utc_days=len(days),
        newest_run_utc=format_utc(max(finished)),
        oldest_run_utc=format_utc(min(finished)),
    )


@dataclass(frozen=True)
class CandidateLock:
    """One locked, immutable staged-candidate identity."""

    record: dict[str, object]
    lock_path: Path

    @property
    def canonical_candidate_digest(self) -> str:
        """Return the locked candidate's canonical digest."""
        candidate = self.record["candidate"]
        assert isinstance(candidate, dict)
        return str(candidate["canonical_candidate_digest"])


def lock_candidate(
    bom_path: Path, lock_dir: Path, *, now_utc: datetime | None = None
) -> CandidateLock:
    """Consume and lock one staged candidate identity.

    Writes an append-only lock record naming the release commit, the canonical
    candidate digest, and the staged BOM's own content digest. Locking the same
    identity again is idempotent; locking a different identity under an
    existing lock file for the same release commit is refused.
    """
    bom = load_candidate_bom(bom_path)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"candidate-lock-{bom['release_commit']}.json"
    entry = {
        "schema": LOCK_SCHEMA,
        "candidate": {
            "release_commit": bom["release_commit"],
            "canonical_candidate_digest": bom["canonical_candidate_digest"],
            "artifact_count": len(bom["artifacts"]),
        },
        "bom_digest": release_candidate_bom.file_sha256(bom_path),
        "locked_utc": format_utc(now_utc or utc_now()),
    }
    if lock_path.exists():
        existing = load_json_object(lock_path)
        same_identity = existing.get("candidate") == entry["candidate"]
        same_bom_bytes = existing.get("bom_digest") == entry["bom_digest"]
        if not same_identity or not same_bom_bytes:
            raise PromotionGateError(
                "candidate_locked",
                f"release commit {bom['release_commit']} is already locked to a different "
                f"candidate identity or BOM bytes ({lock_path}); a rebuilt artifact is a "
                "new candidate and must never reuse the lock (staging is append-only)",
            )
        return CandidateLock(record=existing, lock_path=lock_path)
    lock_path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return CandidateLock(record=entry, lock_path=lock_path)


@dataclass
class StepResult:
    """The recorded outcome of one ordered publish step."""

    step_id: str
    order: int
    status: str
    detail: str
    public_identity: str
    bound_digests: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)


class DryRunExecutor:
    """Bounded, non-publishing executor: verifies, plans, publishes nothing."""

    def __init__(self) -> None:
        """Start an empty command plan."""
        self.planned_commands: list[list[str]] = []

    def record(self, command: list[str]) -> None:
        """Record one command that real publication would run."""
        self.planned_commands.append(command)

    def run(self, command: list[str]) -> str:
        """Record the command without executing it."""
        self.record(command)
        return ""


class ExecutingExecutor:
    """Real publication executor: runs each digest-verified command."""

    def record(self, command: list[str]) -> None:
        """Record the command about to be executed (audit trail only)."""

    def run(self, command: list[str]) -> str:
        """Execute one publish command and return its stdout."""
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise PublishBlockedError(
                f"command {' '.join(command)!r} failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout


def _distribution_artifacts(bom: dict[str, object]) -> list[dict[str, object]]:
    return [
        artifact
        for artifact in bom["artifacts"]
        if artifact["kind"] in (release_candidate_bom.KIND_SDIST, release_candidate_bom.KIND_WHEEL)
    ]


def _first_party_images(bom: dict[str, object]) -> list[dict[str, object]]:
    return [
        artifact
        for artifact in bom["artifacts"]
        if artifact["kind"] == release_candidate_bom.KIND_FIRST_PARTY_IMAGE
    ]


def _verify_staged_bytes(bom: dict[str, object], staging_dir: Path) -> list[Path]:
    try:
        return release_candidate_bom.verify_staged_distributions(bom, staging_dir)
    except release_candidate_bom.BomError as exc:
        raise PromotionGateError(
            "staged_bytes_mismatch",
            "the staged distributions do not match the BOM; promotion halted before any "
            f"publish step: {exc}",
        ) from exc


def promote(
    bom: dict[str, object],
    bom_path: Path,
    staging_dir: Path,
    executor: DryRunExecutor | ExecutingExecutor,
) -> list[StepResult]:
    """Run the ordered, digest-verified publish steps for one qualified candidate.

    Promotion never rebuilds: the exact staged distribution bytes are
    digest-verified against the BOM, images are promoted by digest, and the
    draft release is finalised with the final BOM and evidence bundle attached.
    A failed step aborts the ordering; later steps are recorded as ``not_run``
    so the receipt can only ever report partial publication.
    """
    version = next(
        str(artifact["version"])
        for artifact in bom["artifacts"]
        if artifact["kind"] == release_candidate_bom.KIND_SOURCE
    )
    commit = str(bom["release_commit"])
    tag = f"v{version}"
    distributions_dir = staging_dir / "distributions"

    def verify_tag_absent() -> None:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            raise PromotionGateError(
                "tag_exists",
                f"ref refs/tags/{tag} already exists; refusing to create or reuse it "
                "during promotion",
            )

    def verify_distribution_digests() -> None:
        _verify_staged_bytes(bom, staging_dir)
        staged = {
            release_candidate_bom.file_sha256(path)
            for path in sorted(distributions_dir.iterdir())
            if path.is_file()
        }
        for artifact in _distribution_artifacts(bom):
            if str(artifact["digest"]) not in staged:
                raise PromotionGateError(
                    "staged_bytes_mismatch",
                    f"no staged file matches BOM digest {artifact['digest']} for "
                    f"{artifact['name']} {artifact['version']} ({artifact['kind']})",
                )

    def verify_images_digest_pinned() -> None:
        if not _first_party_images(bom):
            raise PromotionGateError("invalid_bom", "BOM carries no first-party release images")

    steps: list[StepResult] = []
    aborted = False

    def attempt(
        step_id: str,
        order: int,
        verify: Callable[[], None],
        *,
        public_identity: str,
        bound_digests: list[str],
        commands: list[list[str]],
        dry_run_detail: str,
    ) -> None:
        nonlocal aborted
        if aborted:
            steps.append(
                StepResult(
                    step_id=step_id,
                    order=order,
                    status=STEP_NOT_RUN,
                    detail="skipped: an earlier publish step failed (forward completion only)",
                    public_identity="",
                )
            )
            return
        try:
            verify()
        except PromotionGateError:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded as a step failure
            aborted = True
            steps.append(
                StepResult(
                    step_id=step_id,
                    order=order,
                    status=STEP_FAILED,
                    detail=str(exc),
                    public_identity="",
                )
            )
            return
        recorded = [list(command) for command in commands]
        for command in recorded:
            executor.record(command)
        if isinstance(executor, ExecutingExecutor):
            try:
                for command in recorded:
                    executor.run(command)
            except PublishBlockedError as exc:
                aborted = True
                steps.append(
                    StepResult(
                        step_id=step_id,
                        order=order,
                        status=STEP_FAILED,
                        detail=str(exc),
                        public_identity="",
                    )
                )
                return
            steps.append(
                StepResult(
                    step_id=step_id,
                    order=order,
                    status=STEP_COMPLETED,
                    detail="executed and digest-verified",
                    public_identity=public_identity,
                    bound_digests=bound_digests,
                    commands=recorded,
                )
            )
            return
        steps.append(
            StepResult(
                step_id=step_id,
                order=order,
                status=STEP_PLANNED,
                detail=dry_run_detail,
                public_identity=public_identity,
                bound_digests=bound_digests,
                commands=recorded,
            )
        )

    # Create the release tag on the release commit first.
    attempt(
        "release_tag",
        1,
        verify_tag_absent,
        public_identity=f"refs/tags/{tag} -> {commit}",
        bound_digests=[commit],
        commands=[["git", "tag", tag, commit], ["git", "push", "origin", f"refs/tags/{tag}"]],
        dry_run_detail=f"dry run: would create and push {tag} at {commit}",
    )

    # Step 2 — publish the exact staged distribution bytes to PyPI, digest-verified.
    staged_files = _verify_staged_bytes(bom, staging_dir)
    by_digest = {release_candidate_bom.file_sha256(path): path for path in staged_files}
    distribution_artifacts = _distribution_artifacts(bom)
    attempt(
        "pypi_publish",
        2,
        verify_distribution_digests,
        public_identity=", ".join(
            f"pypi:{artifact['name']} {artifact['version']} sha256:{artifact['digest']}"
            for artifact in distribution_artifacts
        ),
        bound_digests=[str(artifact["digest"]) for artifact in distribution_artifacts],
        commands=[
            ["uv", "publish", *[str(by_digest[str(a["digest"])]) for a in distribution_artifacts]]
        ],
        dry_run_detail="dry run: would upload the exact staged bytes, digest-verified",
    )

    # Step 3 — promote first-party images by digest; never re-run a Dockerfile.
    image_artifacts = _first_party_images(bom)
    attempt(
        "image_promotion",
        3,
        verify_images_digest_pinned,
        public_identity=", ".join(
            f"{artifact['name']}@{artifact['digest']}" for artifact in image_artifacts
        ),
        bound_digests=[str(artifact["digest"]) for artifact in image_artifacts],
        commands=[
            [
                "docker",
                "buildx",
                "imagetools",
                "create",
                "-t",
                f"{artifact['name']}:{version}",
                f"{artifact['name']}@{artifact['digest']}",
            ]
            for artifact in image_artifacts
        ],
        dry_run_detail="dry run: would re-tag images by digest; no Dockerfile is run",
    )

    # Step 4 — finalise the draft GitHub Release with the final BOM + bytes.
    final_assets = [str(bom_path)] + [
        str(path) for path in sorted(distributions_dir.iterdir()) if path.is_file()
    ]
    attempt(
        "release_finalisation",
        4,
        lambda: _verify_staged_bytes(bom, staging_dir),
        public_identity=f"github-release:{tag} (final; assets: bom.json, staged distributions)",
        bound_digests=[str(bom["canonical_candidate_digest"])],
        commands=[["gh", "release", "edit", tag, "--draft=false"]]
        + [["gh", "release", "upload", tag, str(path)] for path in final_assets],
        dry_run_detail="dry run: would finalise the draft release attaching BOM + bytes",
    )
    return steps


def reconcile_publication(
    bom: dict[str, object],
    steps: list[StepResult],
    executor: DryRunExecutor | ExecutingExecutor,
) -> dict[str, object]:
    """Reconcile the publication's public identities back to the BOM digests.

    Every completed or planned step must be bound only to digests the BOM
    knows, and together the bound digests must cover every BOM artifact digest.
    A mismatch is a reconciliation failure and blocks a success receipt
    before a success receipt is issued.
    """
    bom_digests = {str(artifact["digest"]) for artifact in bom["artifacts"]}
    # The canonical candidate digest is a valid binding target too: it names
    # the whole BOM (the finalisation step attaches it).
    bom_digests.add(str(bom["canonical_candidate_digest"]))
    checked: list[dict[str, object]] = []
    mismatches: list[str] = []
    covered: set[str] = set()
    # Only publishable artifact kinds need a public identity from a publish
    # step: distributions, first-party images, and the source commit (the
    # tag). Provider images are consumed by digest from their existing
    # registry locations and the support manifest is a read-only acceptance
    # input; neither is re-published by promotion.
    publishable = {
        str(artifact["digest"])
        for artifact in bom["artifacts"]
        if artifact["kind"]
        in (
            release_candidate_bom.KIND_SOURCE,
            release_candidate_bom.KIND_SDIST,
            release_candidate_bom.KIND_WHEEL,
            release_candidate_bom.KIND_FIRST_PARTY_IMAGE,
        )
    }
    for step in sorted(steps, key=lambda item: item.order):
        if step.status not in (STEP_COMPLETED, STEP_PLANNED):
            mismatches.append(f"{step.step_id}: step status is {step.status!r}")
            checked.append({"step": step.step_id, "status": step.status, "matched": False})
            continue
        unbound = sorted(set(step.bound_digests) - bom_digests)
        matched = not unbound
        covered.update(step.bound_digests)
        checked.append({"step": step.step_id, "status": step.status, "matched": matched})
        if unbound:
            mismatches.append(
                f"{step.step_id}: public identity is bound to digest(s) outside the BOM: "
                f"{unbound!r}"
            )
    uncovered = sorted(publishable - covered)
    if uncovered:
        mismatches.append(
            f"no completed step reconciles BOM digest(s) {uncovered!r} to a public identity"
        )
    status = RECONCILE_MATCHED if not mismatches else RECONCILE_MISMATCHED
    executor.record(
        [
            "python3",
            "scripts/promote_release_candidate.py",
            "verify-receipt",
            "--receipt",
            "<receipt>",
        ]
    )
    return {
        "status": status,
        "checked": checked,
        "mismatches": mismatches,
        "note": (
            "dry-run reconciliation against BOM digests"
            if isinstance(executor, DryRunExecutor)
            else "post-publication reconciliation"
        ),
    }


def receipt_checksum(receipt: dict[str, object]) -> str:
    """Compute the receipt's SHA-256 over its canonical content without the checksum."""
    content = {key: value for key, value in receipt.items() if key != "checksum"}
    return hashlib.sha256(canonical_json(content)).hexdigest()


def build_receipt(
    *,
    bom: dict[str, object],
    bom_path: Path,
    qualification: Qualification,
    authorization: dict[str, object] | None,
    steps: list[StepResult],
    reconciliation: dict[str, object],
    target_channel: str,
    mode: str,
    started_utc: str | None = None,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Assemble the checksummed promotion receipt for one promotion attempt.

    ``status`` is ``promoted`` only when a real, authorized publication
    completed every ordered step and reconciliation matched. A real publication
    that stopped partway can only yield a non-success ``partial_publication``
    receipt; a bounded dry run yields ``dry_run``, which is
    never a success receipt because nothing was published.
    """
    finished = format_utc(now_utc or utc_now())
    all_steps_done = all(step.status in (STEP_COMPLETED, STEP_PLANNED) for step in steps)
    if mode == "publish":
        promoted = (
            all_steps_done
            and reconciliation["status"] == RECONCILE_MATCHED
            and len(steps) == len(STEP_ORDER)
        )
        status = STATUS_PROMOTED if promoted else STATUS_PARTIAL
    else:
        status = STATUS_DRY_RUN
    receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "mode": mode,
        "candidate": {
            "release_commit": bom["release_commit"],
            "canonical_candidate_digest": bom["canonical_candidate_digest"],
            "artifact_count": len(bom["artifacts"]),
            "bom_digest": release_candidate_bom.file_sha256(bom_path),
        },
        "target_channel": target_channel,
        "authorization": (
            {
                "release_owner": authorization.get("release_owner"),
                "authorized_utc": authorization.get("authorized_utc"),
                "approval_reference": authorization.get("approval_reference"),
                "digest": authorization_digest(authorization),
            }
            if authorization
            else None
        ),
        "evidence": {
            "bundle_checksums": sorted(qualification.checksums),
            "qualifying_runs": len(qualification.qualifying),
            "hosts": qualification.hosts,
            "distinct_utc_days": qualification.distinct_utc_days,
            "oldest_run_utc": qualification.oldest_run_utc,
            "newest_run_utc": qualification.newest_run_utc,
            "rejected_bundles": qualification.rejected,
        },
        "steps": [
            {
                "step_id": step.step_id,
                "order": step.order,
                "status": step.status,
                "detail": step.detail,
                "public_identity": step.public_identity,
                "bound_digests": step.bound_digests,
                "commands": step.commands,
            }
            for step in sorted(steps, key=lambda item: item.order)
        ],
        "reconciliation": reconciliation,
        "status": status,
        "success": status == STATUS_PROMOTED,
        "started_utc": started_utc or finished,
        "finished_utc": finished,
        "checksum": {"algorithm": "sha256", "value": ""},
    }
    receipt["checksum"] = {"algorithm": "sha256", "value": receipt_checksum(receipt)}
    return receipt


def validate_receipt(receipt: object) -> dict[str, object]:
    """Re-derive every receipt invariant; raise :class:`PromotionGateError` on tampering."""
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise PromotionGateError("invalid_receipt", f"receipt schema must be {RECEIPT_SCHEMA!r}")
    checksum = receipt.get("checksum")
    if (
        not isinstance(checksum, dict)
        or checksum.get("algorithm") != "sha256"
        or checksum.get("value") != receipt_checksum(receipt)
    ):
        raise PromotionGateError("invalid_receipt", "receipt checksum mismatch")
    if receipt.get("status") == STATUS_PROMOTED and receipt.get("success") is not True:
        raise PromotionGateError("invalid_receipt", "a promoted receipt must be a success receipt")
    if (
        receipt.get("status") in (STATUS_PARTIAL, STATUS_DRY_RUN)
        and receipt.get("success") is not False
    ):
        raise PromotionGateError(
            "invalid_receipt",
            f"a {receipt.get('status')!r} receipt can never be a success receipt",
        )
    return receipt


def write_rejection_record(
    bom_path: Path,
    evidence: list[Path],
    error: PromotionGateError,
    output: Path,
    *,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Write a pre-publication rejection record for audit."""
    record = {
        "schema": REJECTION_SCHEMA,
        "candidate_bom": str(bom_path),
        "bom_digest": release_candidate_bom.file_sha256(bom_path) if bom_path.exists() else None,
        "evidence_bundles": [str(path) for path in evidence],
        "reason": error.reason,
        "detail": str(error),
        "rejected_utc": format_utc(now_utc or utc_now()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def _collect_bundles(paths: list[Path]) -> list[dict[str, object]]:
    bundles: list[dict[str, object]] = []
    for path in paths:
        if path.is_dir():
            # Workflow artifact downloads nest bundles in per-run directories.
            bundles.extend(load_evidence_bundle(item) for item in sorted(path.rglob("*.json")))
        else:
            bundles.append(load_evidence_bundle(path))
    return bundles


def _prior_bundle_checksums(receipt_paths: list[Path]) -> set[str]:
    prior: set[str] = set()
    for receipt_path in receipt_paths:
        receipt = load_json_object(receipt_path)
        evidence = receipt.get("evidence")
        if isinstance(evidence, dict):
            recorded = evidence.get("bundle_checksums")
            if isinstance(recorded, list):
                prior.update(item for item in recorded if isinstance(item, str))
    return prior


def main(argv: list[str] | None = None) -> int:
    """Run the promotion gate CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    gate_parser = subparsers.add_parser(
        "gate", help="Adjudicate an evidence set against a staged candidate BOM"
    )
    gate_parser.add_argument("--candidate-bom", type=Path, required=True)
    gate_parser.add_argument("--evidence", type=Path, action="append", required=True)
    gate_parser.add_argument("--staged-utc", default=None)
    gate_parser.add_argument("--prior-receipt", type=Path, action="append", default=[])
    gate_parser.add_argument("--now", default=None, help="Override the current UTC instant")
    gate_parser.add_argument("--rejection-output", type=Path, default=None)

    promote_parser = subparsers.add_parser(
        "promote", help="Gate, then promote (dry run by default) one qualified candidate"
    )
    promote_parser.add_argument("--candidate-bom", type=Path, required=True)
    promote_parser.add_argument("--staging-dir", type=Path, required=True)
    promote_parser.add_argument("--evidence", type=Path, action="append", required=True)
    promote_parser.add_argument(
        "--authorization",
        type=Path,
        default=None,
        help="Release-owner authorization record; required for --execute",
    )
    promote_parser.add_argument("--staged-utc", default=None)
    promote_parser.add_argument("--prior-receipt", type=Path, action="append", default=[])
    promote_parser.add_argument(
        "--execute",
        action="store_true",
        help="Really publish; still requires a validated authorization record",
    )
    promote_parser.add_argument("--target-channel", default="pypi+ghcr+github-releases")
    promote_parser.add_argument("--receipt-output", type=Path, default=None)
    promote_parser.add_argument("--now", default=None)

    receipt_parser = subparsers.add_parser("verify-receipt", help="Verify a promotion receipt")
    receipt_parser.add_argument("--receipt", type=Path, required=True)

    lock_parser = subparsers.add_parser(
        "lock", help="Consume and lock one staged candidate identity"
    )
    lock_parser.add_argument("--candidate-bom", type=Path, required=True)
    lock_parser.add_argument("--lock-dir", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "lock":
        try:
            lock = lock_candidate(args.candidate_bom, args.lock_dir)
        except PromotionGateError as exc:
            print(f"promotion gate error [{exc.reason}]: {exc}", file=sys.stderr)
            return 1
        print(
            f"locked candidate {lock.canonical_candidate_digest} at {lock.lock_path} "
            "(append-only; one canonical BOM per release commit)"
        )
        return 0

    if args.command == "verify-receipt":
        try:
            receipt = validate_receipt(load_json_object(args.receipt))
        except PromotionGateError as exc:
            print(f"receipt validation failed [{exc.reason}]: {exc}", file=sys.stderr)
            return 1
        print(
            f"receipt {args.receipt} is valid: candidate "
            f"{receipt['candidate']['canonical_candidate_digest']} "
            f"status={receipt['status']} success={receipt['success']}"
        )
        return 0 if receipt["success"] or receipt["status"] == STATUS_DRY_RUN else 1

    now_utc = parse_utc(args.now) if getattr(args, "now", None) else utc_now()
    started_utc = format_utc(now_utc)
    try:
        bom = load_candidate_bom(args.candidate_bom)
        bundles = _collect_bundles(args.evidence)
        qualification = qualify_evidence_set(
            bundles,
            bom,
            now_utc=now_utc,
            staged_utc=args.staged_utc,
            prior_receipt_bundles=_prior_bundle_checksums(args.prior_receipt),
        )
        print(
            f"evidence set qualifies: {len(qualification.qualifying)} runs, "
            f"hosts {qualification.hosts}, "
            f"{qualification.distinct_utc_days} distinct UTC days, newest "
            f"{qualification.newest_run_utc}"
        )

        if args.command == "gate":
            return 0

        executor: DryRunExecutor | ExecutingExecutor
        if args.execute:
            if args.authorization is None:
                raise PromotionGateError(
                    "not_authorized",
                    "real publication requires an explicit release-owner authorization "
                    "record; refusing to publish without one",
                )
            authorization = load_authorization(args.authorization)
            validate_authorization(authorization, bom, qualification.checksums)
            print(
                f"authorization verified: {authorization.get('release_owner')} approved "
                f"{authorization.get('approval_reference')} at "
                f"{authorization.get('authorized_utc')}"
            )
            executor = ExecutingExecutor()
            mode = "publish"
        else:
            authorization = None
            if args.authorization is not None:
                authorization = load_authorization(args.authorization)
                validate_authorization(authorization, bom, qualification.checksums)
                print(
                    f"authorization verified: {authorization.get('release_owner')} approved "
                    f"{authorization.get('approval_reference')} at "
                    f"{authorization.get('authorized_utc')}"
                )
            else:
                print("no authorization record supplied; bounded dry run only")
            executor = DryRunExecutor()
            mode = "dry-run"
        steps = promote(
            bom,
            args.candidate_bom.resolve(),
            args.staging_dir.resolve(),
            executor,
        )
        reconciliation = reconcile_publication(bom, steps, executor)
        receipt = build_receipt(
            bom=bom,
            bom_path=args.candidate_bom.resolve(),
            qualification=qualification,
            authorization=authorization,
            steps=steps,
            reconciliation=reconciliation,
            target_channel=args.target_channel,
            mode=mode,
            started_utc=started_utc,
            now_utc=now_utc,
        )
        if args.receipt_output:
            args.receipt_output.parent.mkdir(parents=True, exist_ok=True)
            args.receipt_output.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        for step in receipt["steps"]:
            print(f"  step {step['order']} {step['step_id']}: {step['status']} — {step['detail']}")
        print(
            f"promotion receipt: status={receipt['status']} success={receipt['success']} "
            f"mode={mode} receipt_digest={receipt['checksum']['value']}"
        )
        return 0 if receipt["status"] in (STATUS_PROMOTED, STATUS_DRY_RUN) else 1
    except PromotionGateError as exc:
        print(f"promotion gate error [{exc.reason}]: {exc}", file=sys.stderr)
        if getattr(args, "rejection_output", None):
            record = write_rejection_record(
                args.candidate_bom,
                list(args.evidence),
                exc,
                args.rejection_output,
                now_utc=now_utc,
            )
            print(f"rejection record written: {record['reason']} -> {args.rejection_output}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
