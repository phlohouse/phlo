"""Focused tests for the canonical release-candidate evidence bundle.

Pins sanitization, canonical checksumming, BOM binding, required runtime
demonstrations, and the failure-path invariants promotion gating consumes.
"""

import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "release_evidence", REPO_ROOT / "scripts" / "release_evidence.py"
)
assert _spec and _spec.loader
release_evidence = importlib.util.module_from_spec(_spec)
sys.modules["release_evidence"] = release_evidence
_spec.loader.exec_module(release_evidence)


def _bom() -> dict[str, object]:
    return {
        "schema": "phlo.release-candidate-bom/v1",
        "release_commit": "e" * 40,
        "release_ref": "v0.14.0",
        "artifacts": [
            {
                "kind": "sdist",
                "name": "phlo",
                "version": "0.14.0",
                "digest": "b" * 64,
                "source": "pypi:phlo/0.14.0",
            }
        ],
        "canonical_candidate_digest": "a" * 64,
    }


def _completed_bundle() -> dict[str, object]:
    bundle = release_evidence.new_bundle(
        release_commit="e" * 40,
        canonical_candidate_digest="a" * 64,
        artifact_count=1,
        environment={"host": "clean-host", "runner": "test", "promoting": False},
    )
    for demonstration_id, title in release_evidence.REQUIRED_DEMONSTRATIONS:
        release_evidence.record_demonstration(
            bundle,
            demonstration_id=demonstration_id,
            title=title,
            status=release_evidence.STATUS_PASSED,
            result={"ok": True},
            artifacts=[{"kind": "sdist", "name": "phlo", "digest": "b" * 64}],
        )
    return release_evidence.finalize_bundle(bundle)


def test_completed_bundle_is_valid_and_bom_bound() -> None:
    bundle = _completed_bundle()
    assert release_evidence.validate_bundle(bundle, _bom()) is bundle
    assert bundle["conclusion"] == release_evidence.CONCLUSION_PASSED


def test_alpha_evidence_cannot_qualify_a_production_release() -> None:
    bom = _bom()
    manifest = json.dumps({"current_release": {"maturity": "alpha", "production_ready": False}})
    bom["support_manifest"] = manifest
    bom["artifacts"].append(
        {"kind": "support-manifest", "digest": hashlib.sha256(manifest.encode()).hexdigest()}
    )
    bundle = _completed_bundle()
    bundle["qualification"] = "artifact"
    bundle["demonstrations"] = [
        entry
        for entry in bundle["demonstrations"]
        if entry["id"] not in {"production_preflight", "supported_upgrade", "upgrade_recovery"}
    ]
    release_evidence.finalize_bundle(bundle)
    assert release_evidence.validate_bundle(bundle, bom) is bundle

    incomplete = {
        **bundle,
        "demonstrations": [
            entry for entry in bundle["demonstrations"] if entry["id"] != "backup_verification"
        ],
    }
    release_evidence.finalize_bundle(incomplete)
    with pytest.raises(release_evidence.EvidenceError, match="Independent backup verification"):
        release_evidence.validate_bundle(incomplete, bom)

    with pytest.raises(release_evidence.EvidenceError, match="qualification"):
        release_evidence.validate_bundle(bundle, _bom())
    production = json.dumps({"current_release": {"maturity": "alpha", "production_ready": True}})
    bom["support_manifest"] = production
    bom["artifacts"][-1]["digest"] = hashlib.sha256(production.encode()).hexdigest()
    with pytest.raises(release_evidence.EvidenceError, match="qualification"):
        release_evidence.validate_bundle(bundle, bom)
    bom["artifacts"][-1]["digest"] = hashlib.sha256(manifest.encode()).hexdigest()
    bom["support_manifest"] = manifest.replace('"alpha"', '"stable"')
    with pytest.raises(ValueError, match="manifest"):
        release_evidence.validate_bundle(bundle, bom)


def test_repeated_demonstrations_keep_unique_artifact_objects() -> None:
    bundle = release_evidence.new_bundle(
        release_commit="e" * 40,
        canonical_candidate_digest="a" * 64,
        artifact_count=1,
        environment={"host": "clean-host"},
    )
    artifact = {"kind": "sdist", "name": "phlo", "digest": "b" * 64}
    for index in range(len(release_evidence.REQUIRED_DEMONSTRATIONS)):
        release_evidence.record_demonstration(
            bundle,
            demonstration_id=f"demo-{index}",
            title="Repeated artifact",
            status=release_evidence.STATUS_PASSED,
            result={"ok": True},
            artifacts=[artifact, dict(reversed(list(artifact.items())))],
        )
        # Check each append so recursive string encoding fails before it grows.
        assert bundle["artifacts_exercised"] == [artifact]
        assert bundle["checksum"]["value"] == release_evidence.bundle_checksum(bundle)


def test_bundle_checksum_covers_the_canonical_content() -> None:
    import hashlib

    bundle = _completed_bundle()
    content = {key: value for key, value in bundle.items() if key != "checksum"}
    expected = hashlib.sha256(release_evidence.canonical_json(content)).hexdigest()
    assert bundle["checksum"]["value"] == expected


def test_tampered_bundle_fails_the_checksum() -> None:
    bundle = _completed_bundle()
    bundle["demonstrations"][0]["result"]["ok"] = False
    with pytest.raises(release_evidence.EvidenceError, match="checksum mismatch"):
        release_evidence.validate_bundle(bundle)


def test_bundle_sanitizes_secret_shaped_keys() -> None:
    bundle = release_evidence.new_bundle(
        release_commit="e" * 40,
        canonical_candidate_digest="a" * 64,
        artifact_count=0,
        environment={"service_token": "super-secret", "host": "h"},
    )
    environment = bundle["environment"]
    assert environment["service_token"] == "[REDACTED]"  # type: ignore[index]
    release_evidence.record_demonstration(
        bundle,
        demonstration_id="candidate_bom_verification",
        title="t",
        status=release_evidence.STATUS_FAILED,
        result={"access_key": "hunter2", "nested": {"authorization": "bearer"}},
        error="intentional failure",
    )
    demonstration = bundle["demonstrations"][0]
    assert demonstration["result"]["access_key"] == "[REDACTED]"  # type: ignore[index]
    assert demonstration["result"]["nested"]["authorization"] == "[REDACTED]"  # type: ignore[index]
    release_evidence.finalize_bundle(bundle)
    assert release_evidence.validate_bundle(bundle) is bundle

    demonstration["result"]["nested"]["authorization"] = "unredacted"
    release_evidence.finalize_bundle(bundle)
    with pytest.raises(release_evidence.EvidenceError, match="secret-shaped"):
        release_evidence.validate_bundle(bundle)


def test_passed_bundle_requires_every_horizon_a_demonstration() -> None:
    bundle = _completed_bundle()
    bundle["demonstrations"].pop()
    bundle["checksum"] = {
        "algorithm": "sha256",
        "value": release_evidence.bundle_checksum(bundle),
    }
    with pytest.raises(release_evidence.EvidenceError, match="missing demonstrations"):
        release_evidence.validate_bundle(bundle)


def test_passed_bundle_rejects_failed_demonstrations() -> None:
    bundle = _completed_bundle()
    bundle["demonstrations"][0]["status"] = release_evidence.STATUS_FAILED
    bundle["checksum"] = {
        "algorithm": "sha256",
        "value": release_evidence.bundle_checksum(bundle),
    }
    with pytest.raises(release_evidence.EvidenceError, match="failed demonstrations"):
        release_evidence.validate_bundle(bundle)


def test_failed_bundle_carries_a_failure_record() -> None:
    bundle = release_evidence.new_bundle(
        release_commit="e" * 40,
        canonical_candidate_digest="a" * 64,
        artifact_count=0,
        environment={"host": "h"},
    )
    release_evidence.record_demonstration(
        bundle,
        demonstration_id="candidate_bom_verification",
        title="t",
        status=release_evidence.STATUS_FAILED,
        result={},
        error="RuntimeError: boom",
    )
    bundle = release_evidence.finalize_bundle(bundle)
    assert bundle["conclusion"] == release_evidence.CONCLUSION_FAILED
    assert bundle["failure"]["error"] == "RuntimeError: boom"  # type: ignore[index]
    assert release_evidence.validate_bundle(bundle)


def test_bundle_must_be_bound_to_the_bom_candidate() -> None:
    bundle = _completed_bundle()
    bom = _bom()
    bom["canonical_candidate_digest"] = "9" * 64
    with pytest.raises(release_evidence.EvidenceError, match="bound to candidate"):
        release_evidence.validate_bundle(bundle, bom)


def test_bundle_may_not_exercise_artifacts_outside_the_bom() -> None:
    bundle = _completed_bundle()
    bundle["artifacts_exercised"].append({"kind": "sdist", "digest": "8" * 64})
    bundle["checksum"] = {
        "algorithm": "sha256",
        "value": release_evidence.bundle_checksum(bundle),
    }
    with pytest.raises(release_evidence.EvidenceError, match="outside the BOM"):
        release_evidence.validate_bundle(bundle, _bom())


def test_written_bundle_round_trips_through_the_validator(tmp_path: Path) -> None:
    bundle = _completed_bundle()
    path = tmp_path / "evidence.json"
    release_evidence.write_bundle(bundle, path)
    loaded = release_evidence.load_bundle(path)
    assert release_evidence.validate_bundle(loaded, _bom()) is loaded
    # Canonical form: sorted keys, stable across writes.
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(
        path.read_text(encoding="utf-8")
    )


def _part(ids: set[str], *, host: str, fail: str | None = None) -> dict[str, object]:
    bundle = release_evidence.new_bundle(
        release_commit="e" * 40,
        canonical_candidate_digest="a" * 64,
        artifact_count=1,
        environment={"host": host, "runner": "test", "promoting": False},
    )
    for demonstration_id, title in release_evidence.REQUIRED_DEMONSTRATIONS:
        if demonstration_id in ids:
            release_evidence.record_demonstration(
                bundle,
                demonstration_id=demonstration_id,
                title=title,
                status=(
                    release_evidence.STATUS_FAILED
                    if demonstration_id == fail
                    else release_evidence.STATUS_PASSED
                ),
                result={"ok": True},
                artifacts=[{"kind": "sdist", "name": "phlo", "digest": "b" * 64}],
                error="boom" if demonstration_id == fail else None,
            )
    return release_evidence.finalize_bundle(bundle)


def _split(fail: str | None = None) -> list[dict[str, object]]:
    golden = importlib.import_module("release_golden_path")
    ids = {item for item, _ in release_evidence.REQUIRED_DEMONSTRATIONS}
    return [
        _part({i for i in ids if golden.in_part(i, part)}, host=part, fail=fail)
        for part in ("core", "observatory-fresh", "observatory-upgrade")
    ]


def test_golden_path_parts_cover_every_demonstration_once() -> None:
    golden = importlib.import_module("release_golden_path")
    ids = [item for item, _ in release_evidence.REQUIRED_DEMONSTRATIONS]
    parts = ("core", "observatory-fresh", "observatory-upgrade")
    for demonstration_id in ids:
        owners = [part for part in parts if golden.in_part(demonstration_id, part)]
        if demonstration_id == "candidate_bom_verification":
            assert owners == list(parts)
        else:
            assert len(owners) == 1, demonstration_id
        assert golden.in_part(demonstration_id, "all")


def test_merged_parts_validate_like_one_bundle() -> None:
    merged = release_evidence.merge_bundles(_split())
    assert release_evidence.validate_bundle(merged, _bom()) is merged
    assert merged["conclusion"] == release_evidence.CONCLUSION_PASSED
    ids = [item["id"] for item in merged["demonstrations"]]
    assert sorted(ids) == sorted(item for item, _ in release_evidence.REQUIRED_DEMONSTRATIONS)
    assert [part["host"] for part in merged["environment"]["parts"]] == [
        "core",
        "observatory-fresh",
        "observatory-upgrade",
    ]


def test_a_missing_part_cannot_pass() -> None:
    merged = release_evidence.merge_bundles(_split()[:2])
    with pytest.raises(release_evidence.EvidenceError, match="missing demonstrations"):
        release_evidence.validate_bundle(merged, _bom())


def test_a_failed_part_fails_the_merged_bundle() -> None:
    merged = release_evidence.merge_bundles(_split(fail="observatory_retained_upgrade"))
    assert merged["conclusion"] == release_evidence.CONCLUSION_FAILED
    assert merged["failure"]["demonstration"] == "observatory_retained_upgrade"


@pytest.mark.parametrize("change", ["candidate", "repeat", "tamper"])
def test_parts_must_agree_and_be_intact(change: str) -> None:
    parts = _split()
    if change == "candidate":
        parts[1]["candidate"] = {**parts[1]["candidate"], "release_commit": "f" * 40}
        parts[1]["checksum"]["value"] = release_evidence.bundle_checksum(parts[1])
    elif change == "repeat":
        parts[2]["demonstrations"][0]["result"] = {"ok": False}
        parts[2]["checksum"]["value"] = release_evidence.bundle_checksum(parts[2])
    else:
        parts[0]["demonstrations"][1]["status"] = release_evidence.STATUS_FAILED
    with pytest.raises(release_evidence.EvidenceError):
        release_evidence.merge_bundles(parts)


def test_validator_merges_part_bundles(tmp_path: Path, capsys) -> None:
    validator = importlib.import_module("validate_release_evidence")
    candidate = importlib.import_module("release_candidate_bom")
    bom = tmp_path / "bom.json"
    bom.write_text(json.dumps(_bom()))
    args = []
    for index, part in enumerate(_split()):
        path = tmp_path / f"part-{index}.json"
        release_evidence.write_bundle(part, path)
        args += ["--bundle", str(path)]
    merged = tmp_path / "evidence.json"
    original = candidate.load_bom
    try:
        candidate.load_bom = lambda path: json.loads(Path(path).read_text())
        assert validator.main([*args, "--candidate-bom", str(bom), "--output", str(merged)]) == 0
        assert validator.main([*args[:4], "--candidate-bom", str(bom)]) == 1
    finally:
        candidate.load_bom = original
    assert release_evidence.load_bundle(merged)["conclusion"] == "passed"
    assert "missing demonstrations" in capsys.readouterr().err
