"""Tests for the release promotion gate.

Pins the negative evidence matrix — missing, failed, stale, duplicated,
replayed, wrong-BOM, wrong-environment, incomplete, and insufficient evidence
each blocks before any publish step — plus the qualifying path: authorized,
rebuild-free promotion of the exact staged bytes in bounded dry-run form, the
promotion receipt, partial-publication semantics, candidate locking, and the
fail-closed authorization rules.
"""

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import zipfile
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
            "python": "3.12.0",
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
    (staging / "provenance.json").write_text(
        json.dumps(
            {
                "candidate_sha": COMMIT,
                "canonical_candidate_digest": bom["canonical_candidate_digest"],
                "bom_sha256": release_candidate_bom.file_sha256(bom_path),
                "staged_utc": "2026-09-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return staging, bom_path, bom


def _write_bundles(tmp_path: Path, bundles: list[dict[str, object]]) -> Path:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    for index, bundle in enumerate(bundles):
        attempt = evidence_dir / f"attempt-{index}"
        attempt.mkdir(exist_ok=True)
        (attempt / "bundle.json").write_text(json.dumps(bundle, sort_keys=True), encoding="utf-8")
    return evidence_dir


def _write_qualification_archive(tmp_path: Path, fault: str = "valid") -> Path:
    archive = tmp_path / "qualification.tar.gz"
    files = {
        "bom.json": tmp_path / "staging/bom.json",
        "provenance.json": tmp_path / "staging/provenance.json",
        "authorization.json": tmp_path / "authorization.json",
        **{
            f"evidence/{path.relative_to(tmp_path / 'evidence').as_posix()}": path
            for path in sorted((tmp_path / "evidence").rglob("*.json"))
        },
    }
    contents = {name: path.read_bytes() for name, path in files.items()}
    operation, _, target = fault.partition(":")
    if operation == "missing":
        del contents[target]
    elif operation == "changed":
        value = json.loads(contents[target])
        value["tampered"] = True
        contents[target] = json.dumps(value).encode()
    elif fault == "same_size":
        contents["bom.json"] = contents["bom.json"].replace(b'"phlo"', b'"evil"')
    elif operation == "provenance":
        value = json.loads(contents["provenance.json"])
        value[target] = "0" * 64
        contents["provenance.json"] = json.dumps(value).encode()
        files["provenance.json"].write_bytes(contents["provenance.json"])
    with tarfile.open(archive, "w:gz") as package:
        for directory in (tmp_path / "evidence", *sorted((tmp_path / "evidence").iterdir())):
            member = tarfile.TarInfo(directory.relative_to(tmp_path).as_posix())
            member.type = tarfile.DIRTYPE
            package.addfile(member)
        for name, data in contents.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            package.addfile(member, io.BytesIO(data))
        if operation == "unsafe":
            member = tarfile.TarInfo(target)
            package.addfile(member, io.BytesIO())
        elif fault == "duplicate":
            data = contents["bom.json"]
            member = tarfile.TarInfo("bom.json")
            member.size = len(data)
            package.addfile(member, io.BytesIO(data))
        elif fault in ("symlink", "hardlink", "fifo"):
            member = tarfile.TarInfo("link")
            member.type = {
                "symlink": tarfile.SYMTYPE,
                "hardlink": tarfile.LNKTYPE,
                "fifo": tarfile.FIFOTYPE,
            }[fault]
            member.linkname = "bom.json"
            package.addfile(member)
    if fault == "non_archive":
        archive.write_text("# README, not qualification evidence", encoding="utf-8")
    elif fault == "truncated":
        archive.write_bytes(archive.read_bytes()[:32])
    elif fault == "corrupt_compression":
        archive.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\xff" * 64)
    elif fault == "after_end":
        with archive.open("ab") as stream, tarfile.open(fileobj=stream, mode="w:gz") as package:
            package.addfile(tarfile.TarInfo("../escape"))
    return archive


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
    archive = kwargs.pop("qualification_archive", None)
    if callable(archive):
        archive = archive(tmp_path)
    if archive is not None:
        args += ["--qualification-archive", str(archive)]
    assert not kwargs, f"unused kwargs: {kwargs}"
    code = promote_release_candidate.main(args)
    return code, tmp_path / "receipt.json"


def test_qualifying_dry_run_promotes_identical_bytes_without_publishing(tmp_path: Path) -> None:
    staging, bom_path, bom = _stage_candidate(tmp_path)
    bom["artifacts"].append(
        {
            "kind": "provider-image",
            "name": "ghcr.io/phlohouse/phlo-minio",
            "version": "0.29.1",
            "digest": "sha256:" + "1" * 64,
            "source": "packages/phlo-minio/src/phlo_minio/service.yaml",
        }
    )
    bom["canonical_candidate_digest"] = release_candidate_bom.canonical_candidate_digest(
        bom["artifacts"]
    )
    bom_path.write_text(json.dumps(bom), encoding="utf-8")
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
    assert all("phlo-minio" not in " ".join(command) for command in commands)
    publish_command = next(command for command in commands if command[0] == "uv")
    staged_wheel = staging / "distributions" / "phlo-0.15.0-py3-none-any.whl"
    assert publish_command[2:4] == ["--trusted-publishing", "always"]
    assert [Path(path).name for path in publish_command[4:]] == [
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


def test_execute_with_hand_written_authorization_cannot_publish(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    code, receipt = _run_promotion(
        tmp_path,
        bundles,
        execute=True,
        authorization=_authorization(bom, bundles),
        qualification_archive=_write_qualification_archive,
    )
    assert code == 1
    assert not receipt.exists()
    assert "release dispatch authorization failed" in capsys.readouterr().err


@pytest.mark.parametrize("supplied", [False, True])
def test_execute_requires_existing_qualification_archive(tmp_path, capsys, supplied):
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    code, receipt = _run_promotion(
        tmp_path,
        bundles,
        execute=True,
        authorization=_authorization(bom, bundles),
        qualification_archive=tmp_path / "missing.tar.gz" if supplied else None,
    )
    assert code == 1
    assert not receipt.exists()
    assert "missing_qualification_archive" in capsys.readouterr().err


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


class PublishedSystems(promote_release_candidate.ExecutingExecutor):
    """Offline public state, including uploads that succeed before an outage."""

    def __init__(self, monkeypatch):
        self.tag = ""
        self.files = {}
        self.images = {}
        self.release = None
        self.outage = True
        self.asset_outage = False
        self.signature_failure = False
        self.signatures = set()
        self.commands = []
        monkeypatch.setattr(
            release_candidate_bom, "_pypi_release_files", lambda *a, **kw: self.files
        )
        monkeypatch.setattr(release_candidate_bom, "resolve_image_digest", self.resolve)

    def resolve(self, reference):
        if reference not in self.images:
            raise release_candidate_bom.BomError("manifest unknown")
        return self.images[reference]

    def run(self, command):
        self.commands.append(command)
        if command[:2] == ["git", "ls-remote"]:
            return f"{self.tag}\trefs/tags/v0.15.0\n" if self.tag else ""
        if command[:2] == ["git", "push"]:
            self.tag = COMMIT
        elif command[:2] == ["uv", "publish"]:
            assert command[2:4] == ["--trusted-publishing", "always"]
            for filename in command[4:]:
                path = Path(filename)
                assert path.name not in self.files
                self.files[path.name] = (
                    release_candidate_bom.file_sha256(path),
                    "https://example.test/file",
                )
                if self.outage:
                    self.outage = False
                    raise promote_release_candidate.PublishBlockedError(
                        "outage after first PyPI file"
                    )
        elif command[0] == "docker":
            assert "--prefer-index=false" in command
            self.images[command[5]] = command[-1].split("@", 1)[1]
        elif command[:2] == ["cosign", "sign"]:
            self.signatures.add(command[-1])
        elif command[:2] == ["cosign", "verify"]:
            assert command[2:6] == [
                "--certificate-identity",
                "https://github.com/phlohouse/phlo/.github/workflows/release-promotion.yml@refs/heads/main",
                "--certificate-oidc-issuer",
                "https://token.actions.githubusercontent.com",
            ]
            if self.signature_failure or command[-1] not in self.signatures:
                raise promote_release_candidate.PublishBlockedError(
                    "untrusted or missing signature"
                )
            return json.dumps([{"critical": {"image": {"docker-manifest-digest": IMAGE_DIGEST}}}])
        elif command[:2] == ["gh", "api"]:
            return json.dumps([[self.release] if self.release else []])
        elif command[:3] == ["gh", "release", "create"]:
            assert self.release is None
            self.release = {"tag_name": command[3], "draft": True, "assets": []}
        elif command[:3] == ["gh", "release", "upload"]:
            path = Path(command[4])
            assert path.name not in {a["name"] for a in self.release["assets"]}
            self.release["assets"].append(
                {"name": path.name, "digest": "sha256:" + release_candidate_bom.file_sha256(path)}
            )
            if self.asset_outage:
                self.asset_outage = False
                raise promote_release_candidate.PublishBlockedError(
                    "outage after first GitHub asset"
                )
        elif command[:3] == ["gh", "release", "edit"]:
            self.release["draft"] = False
        return ""


@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "non_archive",
        "truncated",
        "corrupt_compression",
        "after_end",
        "provenance:candidate_sha",
        "provenance:canonical_candidate_digest",
        "provenance:bom_sha256",
        *[
            f"{operation}:{member}"
            for operation in ("missing", "changed")
            for member in (
                "bom.json",
                "provenance.json",
                "authorization.json",
                "evidence/attempt-0/bundle.json",
                "evidence/attempt-3/bundle.json",
            )
        ],
        "same_size",
        "duplicate",
        "unsafe:../escape",
        "unsafe:/escape",
        "unsafe:evidence/../../escape",
        "unsafe:evidence\\escape",
        "unsafe:./bom.json",
        "unsafe:evidence//extra.json",
        "unsafe:unexpected.json",
        "symlink",
        "hardlink",
        "fifo",
    ],
)
def test_cli_binds_archive_to_complete_qualified_inputs(tmp_path, monkeypatch, capsys, fault):
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    authorization = _authorization(bom, bundles)
    # A non-qualifying collected attempt must still be retained, not omitted
    # merely because the other three attempts suffice for authorization.
    rejected = json.loads(json.dumps(bundles[0]))
    rejected["demonstrations"].pop()
    _seal(rejected)
    bundles.append(rejected)
    systems = PublishedSystems(monkeypatch)
    systems.outage = False

    class OfflineExecutor(promote_release_candidate.ExecutingExecutor):
        def run(self, command):
            return systems.run(command)

    monkeypatch.setattr(promote_release_candidate, "ExecutingExecutor", OfflineExecutor)
    monkeypatch.setattr(
        promote_release_candidate.release_provenance, "verify_live_authorization", lambda _: None
    )
    code, receipt_path = _run_promotion(
        tmp_path,
        bundles,
        execute=True,
        authorization=authorization,
        qualification_archive=lambda root: _write_qualification_archive(root, fault),
    )
    if fault == "valid":
        assert code == 0
        receipt = json.loads(receipt_path.read_text())
        assert receipt["status"] == "promoted"
        assert receipt["success"] is True
        assert receipt["evidence"]["qualifying_runs"] == 3
        assert systems.release["draft"] is False
        assert {asset["name"] for asset in systems.release["assets"]} == {
            "bom.json",
            "phlo-0.15.0.tar.gz",
            "phlo-0.15.0-py3-none-any.whl",
            "qualification.tar.gz",
        }
        # Reuse the same archived inputs and public bytes, never republish them.
        code, receipt_path = _run_promotion(
            tmp_path,
            bundles,
            execute=True,
            authorization=authorization,
            qualification_archive=tmp_path / "qualification.tar.gz",
        )
        assert code == 0
        assert json.loads(receipt_path.read_text())["success"] is True
    else:
        assert code == 1
        assert "invalid_qualification_archive" in capsys.readouterr().err
        assert not receipt_path.exists()
        assert systems.tag == ""
        assert systems.files == systems.images == {}
        assert systems.release is None
        assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("timing", ["during_validation", "after_validation", "during_upload"])
def test_cli_archive_replacement_cannot_finalise_release(tmp_path, monkeypatch, timing):
    _, _, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    systems = PublishedSystems(monkeypatch)
    systems.outage = False
    archive = tmp_path / "qualification.tar.gz"
    original_read = Path.read_bytes
    validated = {}

    def create_archive(root):
        _write_qualification_archive(root)
        validated["digest"] = hashlib.sha256(original_read(archive)).hexdigest()
        return archive

    def read_then_replace(path):
        payload = original_read(path)
        if path == archive:
            archive.write_bytes(b"replacement after the validated byte snapshot")
        return payload

    def authorize(_):
        if timing == "after_validation":
            archive.write_bytes(b"replacement after qualification")

    class OfflineExecutor(promote_release_candidate.ExecutingExecutor):
        def run(self, command):
            if timing == "during_upload" and command[:5] == [
                "gh",
                "release",
                "upload",
                "v0.15.0",
                str(archive),
            ]:
                archive.write_bytes(b"replacement during upload")
            return systems.run(command)

    if timing == "during_validation":
        monkeypatch.setattr(Path, "read_bytes", read_then_replace)
    monkeypatch.setattr(promote_release_candidate, "ExecutingExecutor", OfflineExecutor)
    monkeypatch.setattr(
        promote_release_candidate.release_provenance, "verify_live_authorization", authorize
    )
    code, receipt_path = _run_promotion(
        tmp_path,
        bundles,
        execute=True,
        authorization=_authorization(bom, bundles),
        qualification_archive=create_archive,
    )
    assert code == 1
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "partial_publication"
    assert receipt["success"] is False
    assert receipt["steps"][-1]["assets"][archive.name] == validated["digest"]
    assert systems.release["draft"] is True
    remote = next(asset for asset in systems.release["assets"] if asset["name"] == archive.name)
    assert remote["digest"] != "sha256:" + validated["digest"]


def test_partial_pypi_publication_completes_forward(tmp_path, monkeypatch):
    staging, bom_path, bom = _stage_candidate(tmp_path)
    systems = PublishedSystems(monkeypatch)
    first = promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert [s.status for s in first] == ["completed", "failed", "not_run", "not_run"]
    assert (
        promote_release_candidate.reconcile_publication(bom, first, systems)["status"]
        == "mismatched"
    )
    second = promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert all(s.status == "completed" for s in second)
    assert second[0].commands == []
    assert len(second[1].commands[0]) == 5  # OIDC options and only the missing distribution.
    assert (
        promote_release_candidate.reconcile_publication(bom, second, systems)["status"] == "matched"
    )
    third = promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert all(s.commands == [] for s in third if s.step_id != "image_promotion")
    assert all(c[0] == "cosign" for c in third[2].commands)
    assert (
        promote_release_candidate.reconcile_publication(bom, third, systems)["status"] == "matched"
    )


@pytest.mark.parametrize(
    "drift", ["tag", "pypi", "image", "signature", "asset", "draft", "missing_asset"]
)
def test_public_drift_blocks_reconciliation_and_forward_completion(tmp_path, monkeypatch, drift):
    staging, bom_path, bom = _stage_candidate(tmp_path)
    systems = PublishedSystems(monkeypatch)
    systems.outage = False
    steps = promote_release_candidate.promote(bom, bom_path, staging, systems)
    if drift == "tag":
        systems.tag = "0" * 40
    elif drift == "pypi":
        name = next(iter(systems.files))
        systems.files[name] = ("0" * 64, "https://example.test/file")
    elif drift == "image":
        systems.images[next(iter(systems.images))] = "sha256:" + "0" * 64
    elif drift == "signature":
        systems.signature_failure = True
    elif drift == "asset":
        systems.release["assets"][0]["digest"] = "sha256:" + "0" * 64
    elif drift == "draft":
        systems.release["draft"] = True
    else:
        systems.release["assets"].pop()
    result = promote_release_candidate.reconcile_publication(bom, steps, systems)
    assert result["status"] == "mismatched"
    receipt = promote_release_candidate.build_receipt(
        bom=bom,
        bom_path=bom_path,
        qualification=promote_release_candidate.qualify_evidence_set(
            _qualifying_bundles(bom),
            bom,
            now_utc=promote_release_candidate.parse_utc("2026-09-03T00:00:00Z"),
        ),
        authorization=None,
        steps=steps,
        reconciliation=result,
        target_channel="pypi+ghcr+github-releases",
        mode="publish",
    )
    assert receipt["status"] == "partial_publication"
    assert receipt["success"] is False
    retry = promote_release_candidate.promote(bom, bom_path, staging, systems)
    if drift in ("draft", "missing_asset"):
        assert all(s.status == "completed" for s in retry)
        assert (
            promote_release_candidate.reconcile_publication(bom, retry, systems)["status"]
            == "matched"
        )
    else:
        assert any(s.status == "failed" for s in retry)


def test_signature_failure_blocks_release_and_retry_signs_existing_digest(tmp_path, monkeypatch):
    staging, bom_path, bom = _stage_candidate(tmp_path)
    for kind, filename, payload in (
        ("wheel", "phlo_minio-0.15.0-py3-none-any.whl", b"independent-minio-wheel"),
        ("sdist", "phlo_minio-0.15.0.tar.gz", b"independent-minio-sdist"),
    ):
        path = staging / "distributions" / filename
        path.write_bytes(payload)
        bom["artifacts"].append(
            {
                "kind": kind,
                "name": "phlo-minio",
                "version": "0.15.0",
                "digest": hashlib.sha256(payload).hexdigest(),
                "source": f"local-build:{COMMIT}/{filename}",
            }
        )
    bom["canonical_candidate_digest"] = release_candidate_bom.canonical_candidate_digest(
        bom["artifacts"]
    )
    bom_path.write_text(json.dumps(bom), encoding="utf-8")
    systems = PublishedSystems(monkeypatch)
    systems.outage = False
    systems.signature_failure = True
    first = promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert [s.status for s in first] == ["completed", "completed", "failed", "not_run"]
    assert systems.release is None
    assert systems.images == {"ghcr.io/phlohouse/phlo-api:0.15.0": IMAGE_DIGEST}
    systems.signature_failure = False
    second = promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert all(s.status == "completed" for s in second)
    assert not any(c[0] == "docker" for c in second[2].commands)
    report = promote_release_candidate.reconcile_publication(bom, second, systems)
    assert report["status"] == "matched"
    comparisons = [c for step in report["checked"] for c in step["comparisons"]]
    assert len(comparisons) == 5
    image = next(c for c in comparisons if "candidate_digest" in c)
    assert image["candidate_digest"] == image["published_digest"] == IMAGE_DIGEST
    assert image["verified_signatures"]
    for distribution in (c for c in comparisons if "candidate_sha256" in c):
        filename = distribution["identity"].rsplit("/", 1)[-1]
        expected = hashlib.sha256((staging / "distributions" / filename).read_bytes()).hexdigest()
        assert distribution["candidate_sha256"] == distribution["published_sha256"] == expected


@pytest.mark.parametrize("variable", ["UV_PUBLISH_TOKEN", "UV_PUBLISH_PASSWORD", "PYPI_API_TOKEN"])
def test_long_lived_credentials_block_before_any_publication(tmp_path, monkeypatch, variable):
    staging, bom_path, bom = _stage_candidate(tmp_path)
    systems = PublishedSystems(monkeypatch)
    monkeypatch.setenv(variable, "not-a-real-token")
    with pytest.raises(promote_release_candidate.PromotionGateError, match="OIDC"):
        promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert systems.commands == []


def test_read_only_publication_audit_checks_exact_bytes_and_digests(tmp_path, monkeypatch):
    staging, bom_path, bom = _stage_candidate(tmp_path)
    systems = PublishedSystems(monkeypatch)
    systems.outage = False
    assert all(
        s.status == "completed"
        for s in promote_release_candidate.promote(bom, bom_path, staging, systems)
    )
    monkeypatch.setattr(promote_release_candidate, "ExecutingExecutor", lambda: systems)
    systems.commands.clear()
    output = tmp_path / "audit.json"
    args = [
        "verify-published",
        "--candidate-bom",
        str(bom_path),
        "--staging-dir",
        str(staging),
        "--report-output",
        str(output),
    ]
    assert promote_release_candidate.main(args) == 0
    report = json.loads(output.read_text())
    assert report["canonical_candidate_digest"] == bom["canonical_candidate_digest"]
    assert report["status"] == "matched" and len(report["comparisons"]) == 3
    assert all(
        c[:2] in (["git", "ls-remote"], ["gh", "api"], ["cosign", "verify"])
        for c in systems.commands
    )
    output.unlink()
    systems.signature_failure = True
    assert promote_release_candidate.main(args) == 1
    assert not output.exists()


@pytest.mark.parametrize("drift", ["pypi", "image"])
def test_wrong_public_bytes_stop_before_release_finalisation(tmp_path, monkeypatch, drift):
    staging, bom_path, bom = _stage_candidate(tmp_path)

    class CorruptUpload(PublishedSystems):
        def run(self, command):
            output = super().run(command)
            if drift == "pypi" and command[:2] == ["uv", "publish"]:
                name = next(iter(self.files))
                self.files[name] = ("0" * 64, "https://example.test/corrupt")
            elif drift == "image" and command[0] == "docker":
                self.images[command[5]] = "sha256:" + "0" * 64
            return output

    systems = CorruptUpload(monkeypatch)
    systems.outage = False
    steps = promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert steps[1 if drift == "pypi" else 2].status == "failed"
    assert steps[-1].status == "not_run"
    assert systems.release is None


def test_partial_github_assets_complete_without_reupload(tmp_path, monkeypatch):
    staging, bom_path, bom = _stage_candidate(tmp_path)
    systems = PublishedSystems(monkeypatch)
    systems.outage = False
    systems.asset_outage = True
    first = promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert first[-1].status == "failed"
    assert systems.release["draft"] is True
    second = promote_release_candidate.promote(bom, bom_path, staging, systems)
    assert all(s.status == "completed" for s in second)
    assert len(second[-1].commands) == 3  # Two missing distributions, then finalise.
    assert (
        promote_release_candidate.reconcile_publication(bom, second, systems)["status"] == "matched"
    )


@pytest.mark.parametrize("failure", ["upload", "missing", "digest"])
def test_qualification_archive_failure_keeps_release_draft(tmp_path, monkeypatch, failure):
    staging, bom_path, bom = _stage_candidate(tmp_path)
    archive = tmp_path / "qualification-evidence-123-2.tar.gz"
    archive.write_bytes(b"offline qualification archive fixture")
    validated_archive = archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    class ArchiveFailure(PublishedSystems):
        fail_archive = True

        def run(self, command):
            output = super().run(command)
            if (
                self.fail_archive
                and command[:3] == ["gh", "release", "upload"]
                and Path(command[4]) == archive
            ):
                self.fail_archive = False
                if failure == "upload":
                    raise promote_release_candidate.PublishBlockedError("archive upload outage")
                if failure == "missing":
                    self.release["assets"].pop()
                else:
                    self.release["assets"][-1]["digest"] = "sha256:" + "0" * 64
            return output

    systems = ArchiveFailure(monkeypatch)
    systems.outage = False
    first = promote_release_candidate.promote(
        bom, bom_path, staging, systems, qualification_archive=validated_archive
    )
    assert first[-1].status == "failed"
    assert systems.release["draft"] is True
    assert not any(command[:3] == ["gh", "release", "edit"] for command in systems.commands)
    # A corrupted asset must not be overwritten or accepted on retry.
    second = promote_release_candidate.promote(
        bom, bom_path, staging, systems, qualification_archive=validated_archive
    )
    if failure == "digest":
        assert second[-1].status == "failed"
        assert systems.release["draft"] is True
    else:
        assert all(step.status == "completed" for step in second)
        assert systems.release["draft"] is False
        assert second[-1].assets[archive.name] == release_candidate_bom.file_sha256(archive)
        assert (
            promote_release_candidate.reconcile_publication(bom, second, systems)["status"]
            == "matched"
        )


@pytest.mark.parametrize("annotated", [False, True])
def test_exact_local_release_tag_is_reused(tmp_path, annotated):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "--allow-empty",
            "-m",
            "candidate",
        ],
        check=True,
        capture_output=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    command = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", "tag"]
    if annotated:
        command += ["-a", "-m", "candidate"]
    subprocess.run([*command, "v0.15.0", commit], check=True)
    staging, bom_path, bom = _stage_candidate(tmp_path)
    bom["release_commit"] = commit
    bom["artifacts"][0]["digest"] = commit
    steps = promote_release_candidate.promote(
        bom, bom_path, staging, promote_release_candidate.DryRunExecutor()
    )
    assert steps[0].commands == [["git", "push", "origin", "refs/tags/v0.15.0"]]


@pytest.mark.parametrize("change", ["none", "evidence", "bom", "mode"])
def test_dispatch_authorization_is_bound_to_authenticated_publication_plan(
    tmp_path, monkeypatch, change
):
    provenance = promote_release_candidate.release_provenance
    staging, bom_path, bom = _stage_candidate(tmp_path)
    bundles = _qualifying_bundles(bom)
    code, receipt_path = _run_promotion(tmp_path, bundles)
    assert code == 0
    plan = json.loads(receipt_path.read_text())
    if change == "evidence":
        _write_bundles(tmp_path, [*bundles, _day_bundle(bom, "fourth-host", 2, 13)])
    elif change == "bom":
        plan["candidate"]["bom_digest"] = "0" * 64
    elif change == "mode":
        plan["mode"] = "publish"
    plan["checksum"]["value"] = promote_release_candidate.receipt_checksum(plan)
    (staging / "provenance.json").write_text(json.dumps({"staged_utc": "2026-09-01T00:00:00Z"}))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("dry-run-receipt.json", json.dumps(plan))
    payload = archive.getvalue()
    workflow = {"id": 3, "path": ".github/workflows/release-promotion.yml"}
    run = {
        "id": 10,
        "run_attempt": 1,
        "workflow_id": 3,
        "path": workflow["path"],
        "repository": {"full_name": provenance.REPOSITORY},
        "head_repository": {"full_name": provenance.REPOSITORY},
        "head_branch": "main",
        "head_sha": COMMIT,
        "event": "workflow_dispatch",
        "actor": {"login": "operator"},
        "triggering_actor": {"login": "operator"},
        "run_started_at": "2026-09-03T00:00:00Z",
        "updated_at": "2026-09-03T01:00:00Z",
    }
    for key, value in {
        "GITHUB_REPOSITORY": provenance.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": COMMIT,
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    responses = {
        f"repos/{provenance.REPOSITORY}/branches/main": {"protected": True},
        f"repos/{provenance.REPOSITORY}/actions/runs/10": run,
        f"repos/{provenance.REPOSITORY}/actions/workflows/release-promotion.yml": workflow,
    }
    monkeypatch.setattr(provenance, "api", lambda path: responses[path])

    def artifacts(path, key):
        assert path == f"repos/{provenance.REPOSITORY}/actions/runs/10/artifacts"
        assert key == "artifacts"
        return [
            {
                "id": 20,
                "name": "dry-run-receipt-10-1",
                "expired": False,
                "workflow_run": {"id": 10, "head_sha": COMMIT},
                "created_at": "2026-09-03T00:10:00Z",
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            }
        ]

    def download(args, **kwargs):
        assert args == ["gh", "api", f"repos/{provenance.REPOSITORY}/actions/artifacts/20/zip"]
        return subprocess.CompletedProcess(args, 0, stdout=payload)

    monkeypatch.setattr(provenance, "pages", artifacts)
    monkeypatch.setattr(provenance.subprocess, "run", download)
    # Authorization imports the registered script, which other script tests also load.
    monkeypatch.setattr(sys.modules["promote_release_candidate"], "utc_now", _now)
    output = tmp_path / "authorization.json"
    if change != "none":
        with pytest.raises(ValueError, match="after the publication plan"):
            provenance.authorize(bom_path, tmp_path / "evidence", output)
        assert not output.exists()
    else:
        provenance.authorize(bom_path, tmp_path / "evidence", output)
        record = json.loads(output.read_text())
        assert record["release_owner"] == "operator"
        provenance.verify_live_authorization(record)
        with pytest.raises(ValueError, match="manual dispatch"):
            provenance.verify_live_authorization({**record, "release_owner": "someone-else"})
        with pytest.raises(ValueError, match="manual dispatch"):
            provenance.verify_live_authorization({**record, "approval_reference": "another-run"})
        assert record["candidate"] == {
            "release_commit": COMMIT,
            "canonical_candidate_digest": bom["canonical_candidate_digest"],
        }
        assert record["evidence_bundle_checksums"] == plan["evidence"]["bundle_checksums"]
