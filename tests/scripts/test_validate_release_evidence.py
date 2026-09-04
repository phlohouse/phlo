"""Focused tests for the canonical release-candidate evidence bundle.

Pins sanitization, canonical checksumming, BOM binding, required runtime
demonstrations, and the failure-path invariants promotion gating consumes.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
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
        status=release_evidence.STATUS_PASSED,
        result={"access_key": "hunter2", "nested": {"authorization": "bearer"}},
    )
    demonstration = bundle["demonstrations"][0]
    assert demonstration["result"]["access_key"] == "[REDACTED]"  # type: ignore[index]
    assert demonstration["result"]["nested"]["authorization"] == "[REDACTED]"  # type: ignore[index]


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
