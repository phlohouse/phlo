"""Tests for the release promotion gate.

Pins the negative evidence matrix — missing, failed, stale, duplicated,
replayed, wrong-BOM, wrong-environment, incomplete, and insufficient evidence
each blocks before any publish step — plus the qualifying path: authorized,
rebuild-free promotion of the exact staged bytes in bounded dry-run form, the
promotion receipt, partial-publication semantics, candidate locking, and the
fail-closed authorization rules.
"""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "promote_release_candidate", REPO_ROOT / "scripts" / "promote_release_candidate.py"
)
assert _spec and _spec.loader
promote_release_candidate = importlib.util.module_from_spec(_spec)
sys.modules["promote_release_candidate"] = promote_release_candidate
_spec.loader.exec_module(promote_release_candidate)

release_evidence = promote_release_candidate.release_evidence
release_candidate_bom = promote_release_candidate.release_candidate_bom

COMMIT = "e" * 40
IMAGE_DIGEST = "sha256:" + "d" * 64
PROVIDER_DIGEST = "sha256:" + "1" * 64
SUPPORT_DIGEST = "2" * 64
HOST_A = "clean-host-a"
HOST_B = "clean-host-b"
HOST_C = "clean-host-c"


@pytest.fixture(autouse=True)
def isolated_release_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Exercise the real tag gate without inheriting the checkout's release tags.

    Promotion inspects Git even in dry-run mode. Each test needs its own refs:
    publishing v0.15.0 must not break the fixture's hypothetical v0.15.0 candidate.
    Tests can create conflicting refs here without changing the source checkout.
    """
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(variable, raising=False)
    repository = tmp_path / "release-repository"
    subprocess.run(["git", "init", "--quiet", "--template=", str(repository)], check=True)
    monkeypatch.chdir(repository)
    return repository


def _bom() -> dict[str, object]:
    artifacts = [
        {
            "kind": "source",
            "name": "phlohouse/phlo",
            "version": "0.15.0",
            "digest": COMMIT,
            "source": "git",
        },
        {
            "kind": "support-manifest",
            "name": "registry/support/v1.json",
            "version": "0.15.0",
            "digest": SUPPORT_DIGEST,
            "source": "git:registry/support/v1.json",
        },
        {
            "kind": "sdist",
            "name": "phlo",
            "version": "0.15.0",
            "digest": "c" * 64,
            "source": "local-build:" + COMMIT + "/phlo-0.15.0.tar.gz",
        },
        {
            "kind": "wheel",
            "name": "phlo",
            "version": "0.15.0",
            "digest": "b" * 64,
            "source": "local-build:" + COMMIT + "/phlo-0.15.0-py3-none-any.whl",
        },
        {
            "kind": "first-party-image",
            "name": "ghcr.io/phlohouse/phlo-api",
            "version": "0.15.0",
            "digest": IMAGE_DIGEST,
            "source": "packages/phlo-api/src/phlo_api/service.yaml",
        },
        {
            "kind": "provider-image",
            "name": "postgres",
            "version": "18.4-alpine3.24",
            "digest": PROVIDER_DIGEST,
            "source": "packages/phlo-postgres/src/phlo_postgres/service.yaml",
        },
    ]
    return {
        "schema": release_candidate_bom.BOM_SCHEMA,
        "release_commit": COMMIT,
        "release_ref": "v0.15.0",
        "artifacts": artifacts,
        "canonical_candidate_digest": release_candidate_bom.canonical_candidate_digest(artifacts),
    }


def _bundle(
    bom: dict[str, object],
    *,
    host: str,
    started: str,
    finished: str | None = None,
) -> dict[str, object]:
    wheel_digest = next(
        str(artifact["digest"]) for artifact in bom["artifacts"] if artifact["kind"] == "wheel"
    )
    bundle = release_evidence.new_bundle(
        release_commit=str(bom["release_commit"]),
        canonical_candidate_digest=str(bom["canonical_candidate_digest"]),
        artifact_count=len(bom["artifacts"]),
        environment={
            "host": host,
            "runner": "scripts/release_golden_path.py --candidate-bom",
            "platform": "Linux x86_64",
            "python": "3.11.0",
            "promoting": False,
        },
    )
    for demonstration_id, title in release_evidence.REQUIRED_DEMONSTRATIONS:
        release_evidence.record_demonstration(
            bundle,
            demonstration_id=demonstration_id,
            title=title,
            status=release_evidence.STATUS_PASSED,
            result={"ok": True},
            artifacts=[{"kind": "wheel", "name": "phlo", "digest": wheel_digest}],
        )
    release_evidence.finalize_bundle(bundle)
    bundle["started_utc"] = started
    bundle["finished_utc"] = finished or started
    _seal(bundle)
    release_evidence.validate_bundle(bundle, bom)
    return bundle


def _seal(bundle: dict[str, object]) -> dict[str, object]:
    bundle["checksum"] = {
        "algorithm": "sha256",
        "value": release_evidence.bundle_checksum(bundle),
    }
    return bundle


def _day_bundle(bom: dict[str, object], host: str, day: int, hour: int = 12) -> dict[str, object]:
    stamp = f"2026-09-{day:02d}T{hour:02d}:00:00Z"
    return _bundle(bom, host=host, started=stamp, finished=stamp)


def _qualifying_bundles(bom: dict[str, object] | None = None) -> list[dict[str, object]]:
    # Three distinct clean hosts across two distinct UTC days, all fresh.
    bom = bom or _bom()
    return [
        _day_bundle(bom, HOST_A, 1),
        _day_bundle(bom, HOST_B, 1),
        _day_bundle(bom, HOST_C, 2),
    ]


def _now() -> datetime:
    return datetime(2026, 9, 3, tzinfo=UTC)


def _authorization(bom: dict[str, object], bundles: list[dict[str, object]], **overrides: object):
    record = {
        "schema": promote_release_candidate.AUTHORIZATION_SCHEMA,
        "candidate": {
            "release_commit": bom["release_commit"],
            "canonical_candidate_digest": bom["canonical_candidate_digest"],
        },
        "evidence_bundle_checksums": sorted(
            promote_release_candidate.bundle_checksum(bundle) for bundle in bundles
        ),
        "target_channel": "pypi+ghcr+github-releases",
        "release_owner": "release-owner",
        "authorized": True,
        "authorized_utc": "2026-09-03T00:00:00Z",
        "approval_reference": "signed-approval-2026-09-03",
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# Qualifying evidence set


def test_qualifying_set_passes_and_reports_adr_thresholds() -> None:
    bom = _bom()
    qualification = promote_release_candidate.qualify_evidence_set(
        _qualifying_bundles(bom), bom, now_utc=_now()
    )
    assert len(qualification.qualifying) == 3
    assert qualification.hosts == [HOST_A, HOST_B, HOST_C]
    assert qualification.distinct_utc_days == 2
    assert qualification.newest_run_utc == "2026-09-02T12:00:00Z"
    assert qualification.rejected == []
    assert len(qualification.checksums) == 3


def test_freshness_boundary_exactly_seven_days_qualifies() -> None:
    bom = _bom()
    bundles = [
        _bundle(bom, host=HOST_A, started="2026-08-27T00:00:00Z"),
        _bundle(bom, host=HOST_B, started="2026-08-27T06:00:00Z"),
        _bundle(bom, host=HOST_C, started="2026-08-28T00:00:00Z"),
    ]
    # Newest run (2026-08-28T00:00:00Z) is exactly 7 days old at the
    # authorization instant (2026-09-04T00:00:00Z): still inside the window.
    promote_release_candidate.qualify_evidence_set(
        bundles, bom, now_utc=datetime(2026, 9, 4, tzinfo=UTC)
    )


def test_bundle_from_promotion_automation_is_wrong_environment() -> None:
    bom = _bom()
    bundle = _bundle(bom, host=HOST_A, started="2026-09-01T00:00:00Z")
    bundle["environment"]["promoting"] = True
    _seal(bundle)
    with pytest.raises(
        promote_release_candidate.PromotionGateError, match="promotion automation"
    ) as excinfo:
        promote_release_candidate.qualify_evidence_set([bundle], bom, now_utc=_now())
    assert excinfo.value.reason == "wrong_environment"


# ---------------------------------------------------------------------------
# Negative evidence matrix: every shape blocks with a stable reason


def test_missing_evidence_blocks() -> None:
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set([], _bom(), now_utc=_now())
    assert excinfo.value.reason == "missing_evidence"


def test_failed_run_blocks() -> None:
    bom = _bom()
    failed = _seal(
        {
            **_bundle(bom, host=HOST_A, started="2026-09-01T00:00:00Z"),
            "conclusion": "failed",
            "failure": {
                "demonstration": "production_preflight",
                "error": "backend readiness unavailable",
            },
        }
    )
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(
            [failed, *_qualifying_bundles(bom)[1:]], bom, now_utc=_now()
        )
    assert excinfo.value.reason in ("failed_run", "insufficient_runs")


def test_stale_evidence_blocks() -> None:
    bom = _bom()
    bundles = [
        _bundle(bom, host=HOST_A, started="2026-08-01T00:00:00Z"),
        _bundle(bom, host=HOST_B, started="2026-08-01T06:00:00Z"),
        _bundle(bom, host=HOST_C, started="2026-08-02T00:00:00Z"),
    ]
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(bundles, bom, now_utc=_now())
    assert excinfo.value.reason == "stale_evidence"


def test_duplicate_evidence_blocks() -> None:
    bom = _bom()
    bundles = _qualifying_bundles(bom)
    bundles.append(json.loads(json.dumps(bundles[0])))
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(bundles, bom, now_utc=_now())
    assert excinfo.value.reason == "duplicate_evidence"


def test_replayed_evidence_from_prior_receipt_blocks() -> None:
    bom = _bom()
    bundles = _qualifying_bundles(bom)
    consumed = {promote_release_candidate.bundle_checksum(bundles[0])}
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(
            bundles, bom, now_utc=_now(), prior_receipt_bundles=consumed
        )
    assert excinfo.value.reason == "replayed_evidence"


def test_wrong_candidate_bom_blocks() -> None:
    bom = _bom()
    other = _bom()
    other["artifacts"][2]["digest"] = "f" * 64
    other["canonical_candidate_digest"] = release_candidate_bom.canonical_candidate_digest(
        other["artifacts"]
    )
    bundles = [
        _bundle(other, host=host, started="2026-09-01T00:00:00Z")
        for host in (HOST_A, HOST_B, HOST_C)
    ]
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(bundles, bom, now_utc=_now())
    assert excinfo.value.reason == "wrong_candidate"


def test_wrong_environment_blocks() -> None:
    bom = _bom()
    bundle = _bundle(bom, host=HOST_A, started="2026-09-01T00:00:00Z")
    bundle["environment"]["clean_host"] = False
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(
            [_seal(bundle), *_qualifying_bundles(bom)[1:]], bom, now_utc=_now()
        )
    assert excinfo.value.reason in ("wrong_environment", "insufficient_runs")


def test_incomplete_bundle_blocks() -> None:
    bom = _bom()
    bundle = _bundle(bom, host=HOST_A, started="2026-09-01T00:00:00Z")
    bundle["demonstrations"] = bundle["demonstrations"][:-1]
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(
            [_seal(bundle), *_qualifying_bundles(bom)[1:]], bom, now_utc=_now()
        )
    assert excinfo.value.reason in ("invalid_bundle", "insufficient_runs")


def test_insufficient_run_count_blocks() -> None:
    bom = _bom()
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(
            _qualifying_bundles(bom)[:2], bom, now_utc=_now()
        )
    assert excinfo.value.reason == "insufficient_runs"


def test_insufficient_distinct_hosts_blocks() -> None:
    bom = _bom()
    bundles = [
        _day_bundle(bom, HOST_A, 1),
        _day_bundle(bom, HOST_A, 1, hour=13),
        _day_bundle(bom, HOST_A, 2),
    ]
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(bundles, bom, now_utc=_now())
    assert excinfo.value.reason == "insufficient_hosts"


def test_insufficient_distinct_days_blocks() -> None:
    bom = _bom()
    bundles = [
        _day_bundle(bom, HOST_A, 1),
        _day_bundle(bom, HOST_B, 1),
        _day_bundle(bom, HOST_C, 1),
    ]
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(bundles, bom, now_utc=_now())
    assert excinfo.value.reason == "insufficient_days"


def test_run_predating_staging_blocks() -> None:
    bom = _bom()
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set(
            _qualifying_bundles(bom),
            bom,
            now_utc=_now(),
            staged_utc="2026-09-02T00:00:00Z",
        )
    assert excinfo.value.reason == "predates_staging"


def test_all_bundles_invalid_blocks() -> None:
    bom = _bom()
    bundle = _bundle(bom, host=HOST_A, started="2026-09-01T00:00:00Z")
    bundle["demonstrations"] = bundle["demonstrations"][:-1]
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.qualify_evidence_set([_seal(bundle)], bom, now_utc=_now())
    assert excinfo.value.reason == "invalid_bundle"


# ---------------------------------------------------------------------------
# Authorization fail-closed rules


def test_authorization_must_name_exactly_the_qualifying_bundles() -> None:
    bom = _bom()
    bundles = _qualifying_bundles(bom)
    correct = promote_release_candidate.qualify_evidence_set(bundles, bom, now_utc=_now()).checksums
    record = _authorization(bom, bundles)
    promote_release_candidate.validate_authorization(record, bom, correct)
    for tampered in (
        {**record, "authorized": False},
        {**record, "evidence_bundle_checksums": ["0" * 64]},
        {**record, "release_owner": ""},
        {**record, "approval_reference": ""},
        {
            **record,
            "candidate": {
                "release_commit": "0" * 40,
                "canonical_candidate_digest": "0" * 64,
            },
        },
    ):
        with pytest.raises(promote_release_candidate.PromotionGateError):
            promote_release_candidate.validate_authorization(tampered, bom, correct)


# ---------------------------------------------------------------------------
# Candidate locking (one canonical BOM, immutable)


def test_candidate_lock_is_append_only(tmp_path: Path) -> None:
    bom_path = tmp_path / "bom.json"
    bom_path.write_text(json.dumps(_bom(), sort_keys=True) + "\n", encoding="utf-8")
    lock_dir = tmp_path / "locks"
    first = promote_release_candidate.lock_candidate(bom_path, lock_dir)
    again = promote_release_candidate.lock_candidate(bom_path, lock_dir)
    assert first.record == again.record

    tampered = _bom()
    tampered["artifacts"][2]["digest"] = "9" * 64
    tampered["canonical_candidate_digest"] = release_candidate_bom.canonical_candidate_digest(
        tampered["artifacts"]
    )
    other_bom_path = tmp_path / "other-bom.json"
    other_bom_path.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.lock_candidate(other_bom_path, lock_dir)
    assert excinfo.value.reason == "candidate_locked"


# ---------------------------------------------------------------------------
# Promotion: bounded dry run, receipt, no rebuild, partial publication


def _stage_candidate(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    """Stage one candidate with real bytes whose digests are pinned in the BOM."""
    staging = tmp_path / "staging"
    distributions = staging / "distributions"
    distributions.mkdir(parents=True)
    wheel = distributions / "phlo-0.15.0-py3-none-any.whl"
    sdist = distributions / "phlo-0.15.0.tar.gz"
    wheel.write_bytes(b"wheel-bytes")
    sdist.write_bytes(b"sdist-bytes")
    bom = _bom()
    bom["artifacts"][2]["digest"] = release_candidate_bom.file_sha256(sdist)
    bom["artifacts"][3]["digest"] = release_candidate_bom.file_sha256(wheel)
    bom["canonical_candidate_digest"] = release_candidate_bom.canonical_candidate_digest(
        bom["artifacts"]
    )
    bom_path = staging / "bom.json"
    bom_path.write_text(json.dumps(bom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return staging, bom_path, bom


def _write_bundles(tmp_path: Path, bundles: list[dict[str, object]]) -> Path:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    for index, bundle in enumerate(bundles):
        (evidence_dir / f"bundle-{index}.json").write_text(
            json.dumps(bundle, sort_keys=True), encoding="utf-8"
        )
    return evidence_dir


def _run_promotion(tmp_path: Path, bundles: list[dict[str, object]], **kwargs: object):
    staging = tmp_path / "staging"
    bom_path = staging / "bom.json"
    evidence_dir = _write_bundles(tmp_path, bundles)
    args = [
        "promote",
        "--candidate-bom",
        str(bom_path),
        "--staging-dir",
        str(staging),
        "--evidence",
        str(evidence_dir),
        "--now",
        str(kwargs.pop("now", "2026-09-03T00:00:00Z")),
        "--receipt-output",
        str(tmp_path / "receipt.json"),
    ]
    if kwargs.pop("execute", False):
        args.append("--execute")
    authorization = kwargs.pop("authorization", None)
    if authorization is not None:
        authorization_path = tmp_path / "authorization.json"
        authorization_path.write_text(json.dumps(authorization, sort_keys=True), encoding="utf-8")
        args += ["--authorization", str(authorization_path)]
    assert not kwargs, f"unused kwargs: {kwargs}"
    code = promote_release_candidate.main(args)
    return code, tmp_path / "receipt.json"


def test_qualifying_dry_run_promotes_identical_bytes_without_publishing(tmp_path: Path) -> None:
    staging, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    code, receipt_path = _run_promotion(
        tmp_path, bundles, authorization=_authorization(bom, bundles)
    )
    assert code == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    promote_release_candidate.validate_receipt(receipt)

    # Bounded dry run: nothing was published, so the receipt is never a
    # success receipt.
    assert receipt["mode"] == "dry-run"
    assert receipt["status"] == "dry_run"
    assert receipt["success"] is False

    # The evidence record carries exactly the real qualifying bundles.
    assert receipt["evidence"]["qualifying_runs"] == 3
    assert receipt["evidence"]["hosts"] == [HOST_A, HOST_B, HOST_C]
    assert receipt["evidence"]["distinct_utc_days"] == 2

    # Fixed ordering, and every step only ever plans commands.
    assert [step["step_id"] for step in receipt["steps"]] == list(
        promote_release_candidate.STEP_ORDER
    )
    assert all(step["status"] == "planned" for step in receipt["steps"])

    # No rebuild: no planned command builds anything; publication consumes the
    # exact staged bytes, digest-identical to the BOM.
    commands = [command for step in receipt["steps"] for command in step["commands"]]
    assert all("build" not in command for command in commands)
    publish_command = next(command for command in commands if command[0] == "uv")
    staged_wheel = staging / "distributions" / "phlo-0.15.0-py3-none-any.whl"
    assert [Path(path).name for path in publish_command[2:]] == [
        "phlo-0.15.0.tar.gz",
        "phlo-0.15.0-py3-none-any.whl",
    ]
    assert release_candidate_bom.file_sha256(staged_wheel) == next(
        artifact["digest"] for artifact in bom["artifacts"] if artifact["kind"] == "wheel"
    )

    # Reconciliation binds every public identity to a BOM digest.
    assert receipt["reconciliation"]["status"] == "matched"

    # The receipt is checksummed and independently verifiable.
    assert receipt["checksum"]["value"] != ""


def test_gate_failure_blocks_before_any_publish(tmp_path: Path) -> None:
    staging, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    bundles[0]["candidate"]["canonical_candidate_digest"] = "0" * 64
    _seal(bundles[0])
    code, receipt_path = _run_promotion(
        tmp_path, bundles, authorization=_authorization(bom, bundles)
    )
    assert code == 1
    assert not receipt_path.exists()


def test_promotion_without_authorization_record_is_dry_run(tmp_path: Path) -> None:
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    code, receipt_path = _run_promotion(tmp_path, bundles)
    assert code == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["authorization"] is None
    assert receipt["status"] == "dry_run"


def test_execute_without_authorization_fails_closed(tmp_path: Path) -> None:
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    code, _ = _run_promotion(tmp_path, bundles, execute=True)
    assert code == 1


def test_partial_publication_cannot_yield_a_success_receipt(tmp_path: Path) -> None:
    staging, bom_path, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    record = _authorization(bom, bundles)
    now_utc = promote_release_candidate.parse_utc("2026-09-03T00:00:00Z")
    qualification = promote_release_candidate.qualify_evidence_set(bundles, bom, now_utc=now_utc)
    promote_release_candidate.validate_authorization(record, bom, qualification.checksums)

    class FailingExecutor(promote_release_candidate.ExecutingExecutor):
        def run(self, command: list[str]) -> str:
            raise promote_release_candidate.PublishBlockedError(
                f"command {' '.join(command)!r} failed: simulated publish outage"
            )

    steps = promote_release_candidate.promote(bom, bom_path, staging, FailingExecutor())
    reconciliation = promote_release_candidate.reconcile_publication(bom, steps, FailingExecutor())
    receipt = promote_release_candidate.build_receipt(
        bom=bom,
        bom_path=bom_path,
        qualification=qualification,
        authorization=record,
        steps=steps,
        reconciliation=reconciliation,
        target_channel="pypi+ghcr+github-releases",
        mode="publish",
    )
    promote_release_candidate.validate_receipt(receipt)
    assert receipt["status"] == "partial_publication"
    assert receipt["success"] is False
    statuses = {step["step_id"]: step["status"] for step in receipt["steps"]}
    assert statuses["release_tag"] == "failed"
    assert statuses["pypi_publish"] == "not_run"
    assert statuses["release_finalisation"] == "not_run"
    assert reconciliation["status"] == "mismatched"


def test_staged_bytes_tampering_halts_before_any_publish_step(tmp_path: Path) -> None:
    staging, bom_path, bom = _stage_candidate(tmp_path)
    wheel = staging / "distributions" / "phlo-0.15.0-py3-none-any.whl"
    wheel.write_bytes(b"tampered-bytes")
    bundles = _qualifying_bundles(bom)
    record = _authorization(bom, bundles)
    qualification = promote_release_candidate.qualify_evidence_set(
        bundles, bom, now_utc=promote_release_candidate.parse_utc("2026-09-03T00:00:00Z")
    )
    promote_release_candidate.validate_authorization(record, bom, qualification.checksums)
    with pytest.raises(promote_release_candidate.PromotionGateError) as excinfo:
        promote_release_candidate.promote(
            bom, bom_path, staging, promote_release_candidate.DryRunExecutor()
        )
    assert excinfo.value.reason == "staged_bytes_mismatch"


def test_receipt_tampering_is_detected(tmp_path: Path) -> None:
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    code, receipt_path = _run_promotion(
        tmp_path, bundles, authorization=_authorization(bom, bundles)
    )
    assert code == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["candidate"]["canonical_candidate_digest"] = "0" * 64
    with pytest.raises(promote_release_candidate.PromotionGateError):
        promote_release_candidate.validate_receipt(receipt)


def test_verify_receipt_cli(tmp_path: Path) -> None:
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    code, receipt_path = _run_promotion(
        tmp_path, bundles, authorization=_authorization(bom, bundles)
    )
    assert code == 0
    assert promote_release_candidate.main(["verify-receipt", "--receipt", str(receipt_path)]) == 0


def test_nested_evidence_directories_are_collected(tmp_path: Path) -> None:
    """Workflow artifact downloads nest bundles in per-run directories."""
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    evidence_dir = _write_bundles(tmp_path, bundles[:2])
    nested = evidence_dir / "run-1234" / f"release-candidate-evidence-{COMMIT}"
    nested.mkdir(parents=True)
    (nested / "bundle-3.json").write_text(json.dumps(bundles[2], sort_keys=True), encoding="utf-8")
    code, receipt_path = _run_promotion(
        tmp_path, bundles[:2], authorization=_authorization(bom, bundles)
    )
    assert code == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["evidence"]["qualifying_runs"] == 3


def test_existing_release_tag_still_blocks_promotion(tmp_path: Path) -> None:
    """An isolated test repository must still exercise the actual tag-exists gate."""
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input="existing release fixture\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    subprocess.run(["git", "update-ref", "refs/tags/v0.15.0", blob], check=True)
    staging, bom_path, bom = _stage_candidate(tmp_path)
    executor = promote_release_candidate.DryRunExecutor()
    with pytest.raises(promote_release_candidate.PromotionGateError) as failure:
        promote_release_candidate.promote(bom, bom_path, staging, executor)
    assert failure.value.reason == "tag_exists"
