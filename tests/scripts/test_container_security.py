"""Focused contracts for the container waiver and scan-policy helper.

Validates waiver rules (expiry, length, duplicates), the allow-only-waived-
unfixed-findings policy, affected-image selection from changed paths, rescan
manifest completeness, upstream runtime inventory provenance, and that image
candidate upgrades require a Pareto improvement.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "container_security", REPO_ROOT / "scripts" / "container_security.py"
)
assert _spec and _spec.loader
container_security = importlib.util.module_from_spec(_spec)
sys.modules["container_security"] = container_security
_spec.loader.exec_module(container_security)


def _published_images_by_service() -> dict[str, str]:
    support_manifest = json.loads(
        (REPO_ROOT / "registry/support/v1.json").read_text(encoding="utf-8")
    )
    release_images = {
        entry["name"]: entry["image_reference"]
        for entry in support_manifest["release_set"]["services"]
    }
    return {
        service: release_images[service]
        for service in ("phlo-api", "dagster", "dagster-daemon", "observatory")
    }


def _rescan_records() -> list[dict[str, object]]:
    return [
        {
            "image": entry["image"],
            "digest": f"sha256:{letter * 64}",
            "services": entry["services"],
        }
        for letter, entry in zip("abc", container_security.published_fleet(REPO_ROOT), strict=True)
    ]


def _waiver(**overrides: object) -> dict[str, object]:
    waiver: dict[str, object] = {
        "id": "CW-001",
        "image": "ghcr.io/phlohouse/phlo-api:1",
        "vulnerability_id": "CVE-2026-0001",
        "severity": "HIGH",
        "reachability": "not reachable",
        "rationale": "Upstream has no fix.",
        "compensating_control": "Network isolation.",
        "owner": "platform@example.test",
        "approval": "Security Team",
        "approval_date": "2026-08-01",
        "expiry_date": "2026-08-15",
        "remediation_issue": "https://example.test/issues/1",
    }
    waiver.update(overrides)
    return waiver


def test_waiver_validation_rejects_expired_long_and_duplicate_entries() -> None:
    errors = container_security.validate_waivers(
        [
            _waiver(expiry_date="2026-07-31"),
            _waiver(id="CW-002", expiry_date="2026-09-01"),
            _waiver(id="CW-003"),
        ],
        dt.date(2026, 8, 1),
    )
    assert any("expired" in error for error in errors)
    assert any("exceeds 30 days" in error for error in errors)
    assert any("duplicate active image/finding" in error for error in errors)


def test_policy_allows_only_waived_unfixed_blocking_findings() -> None:
    report = {
        "Results": [
            {
                "Vulnerabilities": [
                    {"VulnerabilityID": "CVE-2026-0001", "Severity": "HIGH", "FixedVersion": ""},
                    {
                        "VulnerabilityID": "CVE-2026-0002",
                        "Severity": "CRITICAL",
                        "FixedVersion": "2.0",
                    },
                    {"VulnerabilityID": "CVE-2026-0003", "Severity": "LOW", "FixedVersion": "2.0"},
                ]
            }
        ]
    }
    errors = container_security.apply_policy(report, "ghcr.io/phlohouse/phlo-api:1", [_waiver()])
    assert errors == ["ghcr.io/phlohouse/phlo-api:1: fixable CRITICAL CVE-2026-0002 (fixed in 2.0)"]


def test_affected_images_ignores_docs_and_selects_changed_service() -> None:
    assert container_security.affected_images(["docs/index.md"], REPO_ROOT) == {"include": []}
    images = _published_images_by_service()
    targets = container_security.affected_images(
        ["packages/phlo-api/src/phlo_api/Dockerfile"], REPO_ROOT
    )["include"]
    assert targets == [
        {
            "service": "phlo-api",
            "image": images["phlo-api"],
            "package": "packages/phlo-api",
            "context": "packages/phlo-api",
            "dockerfile": "src/phlo_api/Dockerfile",
        }
    ]


def test_affected_images_selects_exact_unique_fleet_for_all_and_broad_changes() -> None:
    images = _published_images_by_service()
    expected = {
        ("phlo-api", images["phlo-api"]),
        ("dagster", images["dagster"]),
        ("observatory", images["observatory"]),
    }
    all_targets = container_security.affected_images(["pyproject.toml"], REPO_ROOT)["include"]
    security_targets = container_security.affected_images(
        ["scripts/container_security.py"], REPO_ROOT
    )["include"]

    assert {(target["service"], target["image"]) for target in all_targets} == expected
    assert security_targets == all_targets


def test_published_fleet_is_derived_from_source_with_required_shared_mapping() -> None:
    images = _published_images_by_service()
    assert container_security.published_fleet(REPO_ROOT) == [
        {
            "image": images["phlo-api"],
            "services": ["phlo-api"],
        },
        {
            "image": images["dagster"],
            "services": ["dagster", "dagster-daemon"],
        },
        {
            "image": images["observatory"],
            "services": ["observatory"],
        },
    ]


def test_rescan_manifest_assembly_sorts_and_validates_complete_fleet() -> None:
    records = list(reversed(_rescan_records()))

    manifest = container_security.assemble_rescan_manifest(records, REPO_ROOT)

    assert [entry["image"] for entry in manifest] == sorted(entry["image"] for entry in records)
    assert manifest[1]["services"] == ["dagster", "dagster-daemon"]


def test_rescan_manifest_rejects_incomplete_duplicate_unexpected_and_malformed_records() -> None:
    valid = _rescan_records()
    invalid_cases = {
        "missing": valid[:-1],
        "duplicate": [*valid, valid[0]],
        "unexpected": [
            *valid,
            {
                "image": "ghcr.io/phlohouse/phlo-extra:1.0.0",
                "digest": "sha256:" + "d" * 64,
                "services": ["extra"],
            },
        ],
        "bad digest": [{**valid[0], "digest": "latest"}, *valid[1:]],
        "wrong Dagster mapping": [
            valid[0],
            {**valid[1], "services": ["dagster"]},
            valid[2],
        ],
        "conflicting service": [
            valid[0],
            valid[1],
            {**valid[2], "services": ["phlo-api"]},
        ],
        "malformed": [json.loads('{"image": 1}'), *valid[1:]],
    }

    for label, records in invalid_cases.items():
        try:
            container_security.assemble_rescan_manifest(records, REPO_ROOT)
        except ValueError:
            continue
        raise AssertionError(f"{label} records were accepted")


def test_upstream_runtime_inventory_is_complete_deduplicated_and_provenanced() -> None:
    inventory = container_security.upstream_runtime_inventory(REPO_ROOT)
    image_documents = []
    for path in sorted((REPO_ROOT / "packages").glob("*/src/**/*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "image" in document:
            image_documents.append((path.relative_to(REPO_ROOT).as_posix(), document))
    build_arg_paths = {
        path for path, document in image_documents if isinstance(document.get("build"), dict)
    }
    expected_sources: dict[str, list[str]] = {}
    skipped_published_paths: list[str] = []
    for path, document in image_documents:
        if path in build_arg_paths:
            continue
        reference = document["image"]
        if reference.startswith("${") and ":-" in reference and reference.endswith("}"):
            reference = reference.split(":-", 1)[1][:-1]
        if reference.startswith("ghcr.io/phlohouse/phlo-"):
            skipped_published_paths.append(path)
            continue
        expected_sources.setdefault(reference, []).append(path)
    derived_source_count = sum(len(sources) for sources in expected_sources.values())

    assert inventory["version"] == 1
    assert len(image_documents) == (
        len(build_arg_paths) + len(skipped_published_paths) + derived_source_count
    )
    assert len(inventory["images"]) == len(expected_sources)
    assert {image["reference"] for image in inventory["images"]} == set(expected_sources)
    assert sum(len(image["sources"]) for image in inventory["images"]) == derived_source_count
    assert inventory["images"] == sorted(inventory["images"], key=lambda item: item["reference"])
    for image in inventory["images"]:
        assert re.fullmatch(r".+:[^@]+@sha256:[0-9a-f]{64}", image["reference"])
        assert not image["reference"].startswith("ghcr.io/phlohouse/phlo-")
        assert re.fullmatch(r"[0-9a-f]{64}\.json", image["report"])
        assert image["sources"] == sorted(
            image["sources"], key=lambda source: (source["path"], source["service"])
        )

    clickhouse = next(
        image for image in inventory["images"] if image["reference"].startswith("clickhouse/")
    )
    assert len(clickhouse["sources"]) == 2
    assert {source["env"] for source in clickhouse["sources"]} == {"CLICKHOUSE_IMAGE"}


@pytest.mark.parametrize(
    "image,build,error",
    [
        ("alpine:3.24", None, "immutable tag and digest"),
        ("alpine@sha256:" + "a" * 64, None, "immutable tag and digest"),
        ("alpine:3.24@sha256:ABC", None, "immutable tag and digest"),
        ("${IMAGE-alpine:3.24@sha256:" + "a" * 64 + "}", None, "environment default"),
        (123, None, "image must be a string"),
        ("alpine:3.24", {"context": "."}, ""),
    ],
)
def test_upstream_runtime_inventory_rejects_malformed_runtime_images(
    tmp_path: Path, image: object, build: object, error: str
) -> None:
    service_dir = tmp_path / "packages/example/src/example"
    service_dir.mkdir(parents=True)
    document: dict[str, object] = {"name": "example", "image": image}
    if build is not None:
        document["build"] = build
    (service_dir / "service.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    if build is not None:
        assert container_security.upstream_runtime_inventory(tmp_path) == {
            "version": 1,
            "images": [],
        }
    else:
        with pytest.raises(ValueError, match=error):
            container_security.upstream_runtime_inventory(tmp_path)


def _trivy_report(*findings: dict[str, object]) -> dict[str, object]:
    return {"SchemaVersion": 2, "Results": [{"Target": "fixture", "Vulnerabilities": findings}]}


def test_upstream_report_summary_counts_findings_without_blocking_on_severity(
    tmp_path: Path,
) -> None:
    reference = "alpine:3.24@sha256:" + "a" * 64
    report_name = hashlib.sha256(reference.encode()).hexdigest() + ".json"
    inventory = {
        "version": 1,
        "images": [
            {
                "reference": reference,
                "report": report_name,
                "sources": [{"path": "packages/a/src/a/service.yaml", "service": "a"}],
            }
        ],
    }
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / report_name).write_text(
        json.dumps(
            _trivy_report(
                {"VulnerabilityID": "CVE-1", "Severity": "CRITICAL", "FixedVersion": "2"},
                {"VulnerabilityID": "CVE-2", "Severity": "HIGH", "FixedVersion": ""},
                {"VulnerabilityID": "CVE-3", "Severity": "LOW", "FixedVersion": "3"},
            )
        ),
        encoding="utf-8",
    )

    summary = container_security.summarize_upstream_reports(inventory, reports)

    assert "non-blocking visibility" in summary.lower()
    assert "| CRITICAL | 1 | 1 | 0 |" in summary
    assert "| HIGH | 1 | 0 | 1 |" in summary
    assert "| LOW | 1 | 1 | 0 |" in summary
    assert "packages/a/src/a/service.yaml (a)" in summary


@pytest.mark.parametrize("failure", ["missing", "unexpected", "malformed"])
def test_upstream_report_summary_rejects_incomplete_or_invalid_report_sets(
    tmp_path: Path, failure: str
) -> None:
    reference = "alpine:3.24@sha256:" + "a" * 64
    report_name = hashlib.sha256(reference.encode()).hexdigest() + ".json"
    inventory = {
        "version": 1,
        "images": [
            {
                "reference": reference,
                "report": report_name,
                "sources": [{"path": "packages/a/src/a/service.yaml", "service": "a"}],
            }
        ],
    }
    reports = tmp_path / "reports"
    reports.mkdir()
    if failure != "missing":
        (reports / report_name).write_text(
            "not json" if failure == "malformed" else json.dumps(_trivy_report()),
            encoding="utf-8",
        )
    if failure == "unexpected":
        (reports / ("b" * 64 + ".json")).write_text(json.dumps(_trivy_report()), encoding="utf-8")

    with pytest.raises(ValueError, match=failure):
        container_security.summarize_upstream_reports(inventory, reports)


def test_upstream_candidate_comparison_requires_pareto_improvement_and_reports_id_deltas(
    tmp_path: Path,
) -> None:
    base = "alpine:3.23@sha256:" + "a" * 64
    candidate = "alpine:3.24@sha256:" + "b" * 64
    manifest = {
        "version": 1,
        "images": [
            {
                "base": base,
                "candidate": candidate,
                "base_report": hashlib.sha256(base.encode()).hexdigest() + ".json",
                "candidate_report": hashlib.sha256(candidate.encode()).hexdigest() + ".json",
                "sources": [
                    {"path": "packages/a/src/a/service.yaml", "service": "a"},
                    {"path": "packages/b/src/b/service.yaml", "service": "b"},
                ],
            }
        ],
    }
    base_reports = tmp_path / "base"
    candidate_reports = tmp_path / "candidate"
    base_reports.mkdir()
    candidate_reports.mkdir()
    (base_reports / manifest["images"][0]["base_report"]).write_text(
        json.dumps(
            _trivy_report(
                {"VulnerabilityID": "CVE-1", "Severity": "CRITICAL", "FixedVersion": ""},
                {"VulnerabilityID": "CVE-2", "Severity": "HIGH", "FixedVersion": "2"},
                {"VulnerabilityID": "CVE-3", "Severity": "HIGH", "FixedVersion": ""},
            )
        ),
        encoding="utf-8",
    )
    (candidate_reports / manifest["images"][0]["candidate_report"]).write_text(
        json.dumps(
            _trivy_report(
                {"VulnerabilityID": "CVE-2", "Severity": "HIGH", "FixedVersion": "2"},
                {"VulnerabilityID": "CVE-4", "Severity": "LOW", "FixedVersion": "3"},
            )
        ),
        encoding="utf-8",
    )

    summary, errors = container_security.compare_upstream_candidate_reports(
        manifest, base_reports, candidate_reports
    )

    assert errors == []
    assert "| CRITICAL | 1 | 0 | 1 | 0 |" in summary
    assert "| HIGH | 2 | 1 | 2 | 1 |" in summary
    assert "Added IDs: `CVE-4`" in summary
    assert "Removed IDs: `CVE-1`, `CVE-3`" in summary
    assert "Unchanged IDs: `CVE-2`" in summary


@pytest.mark.parametrize(
    ("base_findings", "candidate_findings"),
    [
        (
            [{"VulnerabilityID": "CVE-1", "Severity": "CRITICAL", "FixedVersion": ""}],
            [{"VulnerabilityID": "CVE-2", "Severity": "CRITICAL", "FixedVersion": ""}],
        ),
        (
            [{"VulnerabilityID": "CVE-1", "Severity": "HIGH", "FixedVersion": ""}],
            [
                {"VulnerabilityID": "CVE-1", "Severity": "HIGH", "FixedVersion": ""},
                {"VulnerabilityID": "CVE-2", "Severity": "HIGH", "FixedVersion": ""},
            ],
        ),
    ],
)
def test_upstream_candidate_comparison_rejects_equal_or_worse_critical_high(
    tmp_path: Path,
    base_findings: list[dict[str, object]],
    candidate_findings: list[dict[str, object]],
) -> None:
    base = "alpine:3.23@sha256:" + "a" * 64
    candidate = "alpine:3.24@sha256:" + "b" * 64
    manifest = {
        "version": 1,
        "images": [
            {
                "base": base,
                "candidate": candidate,
                "base_report": hashlib.sha256(base.encode()).hexdigest() + ".json",
                "candidate_report": hashlib.sha256(candidate.encode()).hexdigest() + ".json",
                "sources": [{"path": "packages/a/src/a/service.yaml", "service": "a"}],
            }
        ],
    }
    base_reports = tmp_path / "base"
    candidate_reports = tmp_path / "candidate"
    base_reports.mkdir()
    candidate_reports.mkdir()
    (base_reports / manifest["images"][0]["base_report"]).write_text(
        json.dumps(_trivy_report(*base_findings)), encoding="utf-8"
    )
    (candidate_reports / manifest["images"][0]["candidate_report"]).write_text(
        json.dumps(_trivy_report(*candidate_findings)), encoding="utf-8"
    )

    _, errors = container_security.compare_upstream_candidate_reports(
        manifest, base_reports, candidate_reports
    )

    assert errors == [f"{candidate}: candidate does not strictly improve CRITICAL/HIGH findings"]


def test_upstream_runtime_candidates_reject_cross_repository_replacements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "packages/example/src/example/service.yaml"
    base = "alpine:3.23@sha256:" + "a" * 64
    candidate = "busybox:1.37@sha256:" + "b" * 64

    def check_output(command: list[str], *, text: bool) -> str:
        if command[:3] == ["git", "diff", "--name-only"]:
            return path + "\n"
        if command == ["git", "show", f"base:{path}"]:
            return f"name: example\nimage: ${{IMAGE:-{base}}}\n"
        if command == ["git", "show", f"head:{path}"]:
            return f"name: example\nimage: ${{IMAGE:-{candidate}}}\n"
        raise AssertionError(f"unexpected command: {command!r}")

    monkeypatch.setattr(container_security.subprocess, "check_output", check_output)

    with pytest.raises(ValueError, match="must retain upstream repository alpine; got busybox"):
        container_security.upstream_runtime_candidates("base", "head")
