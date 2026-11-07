"""Persisted WAP identity contracts independent of providers and Dagster."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from phlo.wap_evidence import WapEvidence


@pytest.fixture
def records(tmp_path):
    store = WapEvidence(tmp_path)
    tags = {
        "phlo/run_id": "logical-7",
        "phlo/wap_branch": "pipeline-run-logical-7",
        "phlo/ref": "pipeline-run-logical-7",
        "phlo/project_id": "project-a",
        "phlo/attempt": "2",
    }
    binding = {
        "schema_version": "phlo.wap_launch_manifest.v1",
        "logical_run_id": "logical-7",
        "dagster_run_id": "physical-9",
        "branch": "pipeline-run-logical-7",
        "tags": tags.copy(),
        "source_hash": "source-1",
        "target_branch": "main",
        "target_hash_before": "target-2",
    }
    report = {
        "schema_version": "phlo.wap_report.v2",
        "run_id": "logical-7",
        "dagster_run_id": "physical-9",
        "branch": "pipeline-run-logical-7",
        "launch_tags": tags.copy(),
        "strategy": "branch",
        "status": "launched",
        "updated_at": "2026-11-07T10:00:00Z",
        "launch_source_hash": "source-1",
        "launch_target_hash_before": "target-2",
    }

    def persist():
        raw = json.dumps(binding).encode()
        checksum = hashlib.sha256(raw).hexdigest()
        path = store.binding_path("logical-7", checksum)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        report["launch_manifest_checksum"] = checksum
        store.report_path("logical-7").write_text(json.dumps(report))
        return path

    persist()
    assert store.verify("logical-7", "physical-9", tags, project_id="project-a") is not None
    return store, tags, binding, report, persist


@pytest.mark.parametrize("strict", [None, False, True])
def test_verified_legacy_and_context_records_are_read_only(records, strict):
    store, tags, binding, report, persist = records
    if strict is not None:
        context = {
            "environment": "prod",
            "target_ref": "release",
            "location": "prod_location",
            "backend_id": "a" * 64,
            "strategy": "branch",
            "require_candidate_bound_checks": strict,
        }
        binding.update(
            schema_version="phlo.wap_launch_manifest.v2", context=context, target_branch="release"
        )
        report.update(context=context, target_branch="release")
    del report["launch_source_hash"], report["launch_target_hash_before"]
    report["source_hash"] = "mutable-lifecycle-head"
    persist()
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in store.directory.rglob("*.json")}
    verified = store.verify("logical-7", "physical-9", tags, project_id="project-a")
    assert verified is not None and verified.needs_hash_migration
    assert verified.binding["source_hash"] == "source-1"
    assert verified.reported_at.isoformat() == "2026-11-07T10:00:00+00:00"
    assert {
        p: (p.read_bytes(), p.stat().st_mtime_ns) for p in store.directory.rglob("*.json")
    } == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "different"),
        ("dagster_run_id", "different"),
        ("branch", "pipeline-run-other"),
        ("launch_source_hash", "forged"),
        ("launch_target_hash_before", "forged"),
        ("launch_manifest_checksum", "../unsafe"),
        ("context", {"environment": "forged"}),
        ("launch_tags", {}),
        ("schema_version", "unknown"),
        ("updated_at", "invalid"),
    ],
)
def test_forged_report_identity_is_not_verified(records, field, value):
    store, tags, _, report, _ = records
    report[field] = value
    store.report_path("logical-7").write_text(json.dumps(report))
    assert store.verify("logical-7", "physical-9", tags) is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("phlo/project_id", "other"),
        ("phlo/attempt", "01"),
        ("phlo/attempt", "0"),
        ("phlo/attempt", ""),
        ("phlo/ref", "main"),
        ("phlo/run_id", "other"),
        ("branch", "main"),
        ("ref", "main"),
        ("phlo/wap_binding", "a" * 64),
    ],
)
def test_observed_tags_cannot_redirect_identity(records, key, value):
    store, tags, _, _, _ = records
    tags[key] = value
    assert store.verify("logical-7", "physical-9", tags) is None


def test_hash_pair_project_and_physical_identity_are_required(records):
    store, tags, _, report, persist = records
    assert store.verify("logical-7", "physical-9", tags, project_id="other") is None
    assert store.verify("logical-7", "copied-run", tags) is None
    del report["launch_source_hash"]
    persist()
    assert store.verify("logical-7", "physical-9", tags) is None


@pytest.mark.parametrize("damage", ["digest", "target", "extra", "context", "tags"])
def test_immutable_binding_is_verified_not_just_its_report(records, damage):
    store, tags, binding, _, persist = records
    if damage == "target":
        binding["target_branch"] = "untrusted"
    elif damage == "extra":
        binding["ignored_option"] = True
    elif damage == "context":
        binding["context"] = {"require_candidate_bound_checks": "true"}
    elif damage == "tags":
        binding["tags"].pop("phlo/attempt")
    path = persist()
    if damage == "digest":
        path.write_bytes(path.read_bytes() + b" ")
    assert store.verify("logical-7", "physical-9", tags) is None


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"{",
        b"{}{}",
        b"\xff",
        b'{"run_id":"one","run_id":"two"}',
        b"[" * 2000,
        b" " * (2 * 1024 * 1024 + 1),
    ],
)
def test_malformed_or_oversize_evidence_fails_closed(records, raw):
    store, tags, _, _, _ = records
    checksum = hashlib.sha256(raw).hexdigest()
    store.binding_path("logical-7", checksum).write_bytes(raw)
    assert store.read_binding("logical-7", checksum) is None
    store.report_path("logical-7").write_bytes(raw)
    assert store.read_report("logical-7") is None
    assert store.verify("logical-7", "physical-9", tags) is None


def test_missing_and_unsafe_reads_do_not_create_storage(tmp_path):
    store = WapEvidence(tmp_path)
    for logical_id in ("missing", "../outside", "", "dot.name"):
        assert store.read_report(logical_id) is None
        assert store.verify(logical_id, "physical", {}) is None
    assert not list(tmp_path.iterdir())


def test_evidence_import_and_reads_need_no_provider_or_configuration(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import pathlib
import sys
class BlockProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('dagster', 'phlo_dagster', 'phlo.config', 'phlo.capabilities', 'phlo.plugins', 'phlo.infrastructure')):
            raise AssertionError('unexpected dependency: ' + fullname)
sys.meta_path.insert(0, BlockProviders())
from phlo.wap_evidence import WapEvidence
store = WapEvidence(pathlib.Path(sys.argv[1]))
assert store.verify('missing', 'physical', {}) is None
assert not list(pathlib.Path(sys.argv[1]).iterdir())
""",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
