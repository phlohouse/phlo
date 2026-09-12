"""Tests for the release-candidate qualification status report.

Pins the read-only report over staged evidence bundles: per-bundle verdicts
with reasons, set-level gap summaries (runs, hosts, UTC days, freshness,
predating staging), the qualified/not-qualified verdict, and the JSON shape.
"""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


promote = _load("promote_release_candidate")
status = _load("release_qualification_status")
release_evidence = promote.release_evidence
release_candidate_bom = promote.release_candidate_bom

COMMIT = "e" * 40
IMAGE_DIGEST = "sha256:" + "d" * 64
PROVIDER_DIGEST = "sha256:" + "1" * 64
SUPPORT_DIGEST = "2" * 64
NOW = datetime(2026, 9, 12, tzinfo=UTC)


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
            "source": f"local-build:{COMMIT}/phlo-0.15.0.tar.gz",
        },
        {
            "kind": "wheel",
            "name": "phlo",
            "version": "0.15.0",
            "digest": "b" * 64,
            "source": f"local-build:{COMMIT}/phlo-0.15.0-py3-none-any.whl",
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
    bom: dict[str, object], *, host: str, started: str, finished: str | None = None
) -> dict[str, object]:
    wheel_digest = next(str(a["digest"]) for a in bom["artifacts"] if a["kind"] == "wheel")
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
    bundle["checksum"] = {"algorithm": "sha256", "value": release_evidence.bundle_checksum(bundle)}
    release_evidence.validate_bundle(bundle, bom)
    return bundle


def _day_bundle(bom: dict[str, object], host: str, day: int, hour: int = 12):
    stamp = f"2026-09-{day:02d}T{hour:02d}:00:00Z"
    return _bundle(bom, host=host, started=stamp, finished=stamp)


def _qualifying_set(bom: dict[str, object]) -> list[dict[str, object]]:
    return [
        _day_bundle(bom, "clean-host-a", 10),
        _day_bundle(bom, "clean-host-b", 10, 18),
        _day_bundle(bom, "clean-host-c", 11),
    ]


class TestReportStatus:
    """Per-bundle verdicts and set-level gaps."""

    def test_fully_qualifying_set_reports_no_gaps(self) -> None:
        bom = _bom()
        report = status.report_status(_qualifying_set(bom), bom, now_utc=NOW)
        assert report.qualified
        assert report.gaps == []
        assert all(v.status == status.STATUS_QUALIFYING for v in report.verdicts)

    def test_empty_set_reports_missing_runs_hosts_days(self) -> None:
        report = status.report_status([], _bom(), now_utc=NOW)
        assert not report.qualified
        assert len(report.gaps) == 3

    def test_failed_bundle_is_rejected_but_others_evaluate(self) -> None:
        bom = _bom()
        bundles = _qualifying_set(bom)
        bad = _day_bundle(bom, "clean-host-a", 11, 20)
        bad["conclusion"] = "failed"
        bad["failure"] = {"demonstration": None, "error": "boom"}
        bad["checksum"] = {"algorithm": "sha256", "value": release_evidence.bundle_checksum(bad)}
        report = status.report_status(bundles + [bad], bom, now_utc=NOW)
        assert report.qualified  # three good bundles still qualify the set
        rejected = [v for v in report.verdicts if v.status == status.STATUS_REJECTED]
        assert len(rejected) == 1 and rejected[0].reason == "failed_run"

    def test_wrong_candidate_bundle_rejected(self) -> None:
        bom = _bom()
        other = _bom()
        other["release_commit"] = "f" * 40
        stray = _bundle(bom, host="clean-host-a", started="2026-09-10T12:00:00Z")
        stray["candidate"]["release_commit"] = "f" * 40
        stray["checksum"] = {
            "algorithm": "sha256",
            "value": release_evidence.bundle_checksum(stray),
        }
        report = status.report_status([stray], bom, now_utc=NOW)
        assert report.verdicts[0].reason == "wrong_candidate"

    def test_same_day_runs_report_day_gap(self) -> None:
        bom = _bom()
        bundles = [
            _day_bundle(bom, f"clean-host-{c}", 10, h) for c, h in (("a", 8), ("b", 12), ("c", 20))
        ]
        report = status.report_status(bundles, bom, now_utc=NOW)
        assert not report.qualified
        assert any("distinct UTC days" in gap for gap in report.gaps)
        assert not any("distinct hosts" in gap for gap in report.gaps)

    def test_two_hosts_report_host_gap(self) -> None:
        bom = _bom()
        bundles = [
            _day_bundle(bom, "clean-host-a", 10),
            _day_bundle(bom, "clean-host-b", 10, 18),
            _day_bundle(bom, "clean-host-a", 11),
        ]
        report = status.report_status(bundles, bom, now_utc=NOW)
        assert not report.qualified
        assert any("2 of 3 required distinct hosts" in gap for gap in report.gaps)

    def test_duplicate_checksum_rejected(self) -> None:
        bom = _bom()
        bundle = _day_bundle(bom, "clean-host-a", 10)
        report = status.report_status([bundle, dict(bundle)], bom, now_utc=NOW)
        assert report.verdicts[1].reason == "duplicate_evidence"

    def test_stale_evidence_reports_freshness_gap(self) -> None:
        bom = _bom()
        stale_now = datetime(2026, 10, 1, tzinfo=UTC)
        report = status.report_status(_qualifying_set(bom), bom, now_utc=stale_now)
        assert not report.qualified
        assert any("freshness" in gap for gap in report.gaps)

    def test_predating_staging_reports_gap(self) -> None:
        bom = _bom()
        report = status.report_status(
            _qualifying_set(bom), bom, now_utc=NOW, staged_utc="2026-09-11T00:00:00Z"
        )
        assert not report.qualified
        assert any("predate candidate staging" in gap for gap in report.gaps)

    def test_missing_host_is_rejected_not_counted(self) -> None:
        bom = _bom()
        bundles = _qualifying_set(bom)
        no_host = _day_bundle(bom, "clean-host-d", 11, 20)
        del no_host["environment"]["host"]
        no_host["checksum"] = {
            "algorithm": "sha256",
            "value": release_evidence.bundle_checksum(no_host),
        }
        report = status.report_status(bundles + [no_host], bom, now_utc=NOW)
        rejected = [v for v in report.verdicts if v.status == status.STATUS_REJECTED]
        assert len(rejected) == 1 and rejected[0].reason == "wrong_environment"

    def test_missing_timestamps_rejected_and_report_emits(self) -> None:
        bom = _bom()
        bundles = _qualifying_set(bom)
        no_ts = _day_bundle(bom, "clean-host-d", 11, 20)
        del no_ts["started_utc"]
        no_ts["checksum"] = {
            "algorithm": "sha256",
            "value": release_evidence.bundle_checksum(no_ts),
        }
        report = status.report_status(bundles + [no_ts], bom, now_utc=NOW)
        rejected = [v for v in report.verdicts if v.status == status.STATUS_REJECTED]
        assert len(rejected) == 1 and rejected[0].reason == "invalid_bundle"
        assert report.qualified  # three good bundles still qualify the set

    def test_replayed_evidence_rejected(self) -> None:
        bom = _bom()
        bundles = _qualifying_set(bom)
        consumed = {promote.bundle_checksum(bundles[0])}
        report = status.report_status(bundles, bom, now_utc=NOW, prior_receipt_bundles=consumed)
        assert report.verdicts[0].reason == "replayed_evidence"
        assert len(report.qualifying) == 2


class TestCli:
    """End-to-end CLI behaviour over bundle files on disk."""

    def _write(self, directory: Path, bundles: list[dict[str, object]]) -> Path:
        directory.mkdir(exist_ok=True)
        for index, bundle in enumerate(bundles):
            release_evidence.write_bundle(bundle, directory / f"run-{index}.json")
        return directory

    def test_main_reports_qualified(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        bom = _bom()
        bom_path = tmp_path / "bom.json"
        bom_path.write_text(json.dumps(bom))
        evidence_dir = self._write(tmp_path / "bundles", _qualifying_set(bom))
        code = status.main(
            [
                "--candidate-bom",
                str(bom_path),
                "--evidence-dir",
                str(evidence_dir),
                "--now",
                "2026-09-12T00:00:00Z",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "3 qualifying" in out and "status: qualified" in out

    def test_main_json_shape(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        bom = _bom()
        bom_path = tmp_path / "bom.json"
        bom_path.write_text(json.dumps(bom))
        evidence_dir = self._write(tmp_path / "bundles", _qualifying_set(bom)[:1])
        code = status.main(
            [
                "--candidate-bom",
                str(bom_path),
                "--evidence-dir",
                str(evidence_dir),
                "--now",
                "2026-09-12T00:00:00Z",
                "--json",
            ]
        )
        document = json.loads(capsys.readouterr().out)
        assert code == 1
        assert document["qualified"] is False
        assert document["bundles"][0]["status"] == "qualifying"
        assert document["thresholds"]["min_qualifying_runs"] == 3
        assert any("qualifying runs" in gap for gap in document["gaps"])

    def test_main_missing_bom_is_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        code = status.main(["--candidate-bom", str(tmp_path / "nope.json")])
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_main_invalid_now_is_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        bom = _bom()
        bom_path = tmp_path / "bom.json"
        bom_path.write_text(json.dumps(bom))
        code = status.main(["--candidate-bom", str(bom_path), "--now", "not-a-timestamp"])
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_main_prior_receipt_marks_consumed_bundles(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        bom = _bom()
        bom_path = tmp_path / "bom.json"
        bom_path.write_text(json.dumps(bom))
        bundles = _qualifying_set(bom)
        evidence_dir = self._write(tmp_path / "bundles", bundles)
        receipt = tmp_path / "receipt.json"
        receipt.write_text(
            json.dumps({"evidence": {"bundle_checksums": [promote.bundle_checksum(bundles[0])]}})
        )
        code = status.main(
            [
                "--candidate-bom",
                str(bom_path),
                "--evidence-dir",
                str(evidence_dir),
                "--prior-receipt",
                str(receipt),
                "--now",
                "2026-09-12T00:00:00Z",
            ]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "replayed_evidence" in out
