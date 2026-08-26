#!/usr/bin/env python3
"""Derive container inventories, validate Trivy reports, and enforce Phlo scan policy.

Findings with an available fix block publication until an image rebuild picks
them up. Findings without any available fix belong to the upstream base image
and are reported as warnings instead of blocking; waivers remain available for
tracking time-limited exceptions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import yaml

REQUIRED_WAIVER_FIELDS = {
    "id",
    "image",
    "vulnerability_id",
    "severity",
    "reachability",
    "rationale",
    "compensating_control",
    "owner",
    "approval",
    "approval_date",
    "expiry_date",
    "remediation_issue",
}
BLOCKING_SEVERITIES = {"CRITICAL", "HIGH"}
PUBLISHED_SERVICE_REPOSITORIES = {
    "phlo-api": "ghcr.io/phlohouse/phlo-api:",
    "dagster": "ghcr.io/phlohouse/phlo-dagster:",
    "dagster-daemon": "ghcr.io/phlohouse/phlo-dagster:",
    "observatory": "ghcr.io/phlohouse/phlo-observatory:",
}
BROAD_IMAGE_PATHS = {
    ".github/workflows/build-core-services.yml",
    ".github/workflows/container-rescan.yml",
    ".github/workflows/container-security.yml",
    "pyproject.toml",
    "scripts/container_security.py",
    "scripts/generated_image_matrix.py",
    "uv.lock",
}
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
IMMUTABLE_IMAGE_PATTERN = re.compile(
    r"(?P<repository>[a-z0-9][a-z0-9._/-]*(?::[0-9]+)?):"
    r"(?P<tag>[^:@\s{}]+)@(?P<digest>sha256:[0-9a-f]{64})"
)
ENV_IMAGE_PATTERN = re.compile(r"\$\{(?P<env>[A-Z][A-Z0-9_]*)\:-(?P<reference>[^{}]+)\}")
UPSTREAM_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


def _date(value: Any, field: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date") from exc
    raise ValueError(f"{field} must be an ISO date")


def load_waivers(path: Path) -> list[dict[str, Any]]:
    """Load the waiver register from YAML, raising ValueError on any structural violation."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot parse waiver register {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("waiver register must be a mapping with version: 1")
    waivers = document.get("waivers")
    if not isinstance(waivers, list):
        raise ValueError("waiver register must contain a waivers list")
    if not all(isinstance(waiver, dict) for waiver in waivers):
        raise ValueError("every waiver must be a mapping")
    return waivers


def validate_waivers(waivers: list[dict[str, Any]], today: dt.date | None = None) -> list[str]:
    """Check each waiver against schema and policy rules; return a list of error messages."""
    today = today or dt.datetime.now(dt.UTC).date()
    errors: list[str] = []
    active_findings: set[tuple[str, str]] = set()
    ids: set[str] = set()
    for index, waiver in enumerate(waivers, start=1):
        label = str(waiver.get("id") or f"entry {index}")
        missing = sorted(field for field in REQUIRED_WAIVER_FIELDS if not waiver.get(field))
        if missing:
            errors.append(f"{label}: missing required fields: {', '.join(missing)}")
            continue
        if label in ids:
            errors.append(f"{label}: duplicate waiver id")
        ids.add(label)
        severity = str(waiver["severity"]).upper()
        if severity not in BLOCKING_SEVERITIES:
            errors.append(f"{label}: severity must be HIGH or CRITICAL")
        try:
            approved = _date(waiver["approval_date"], f"{label}.approval_date")
            expires = _date(waiver["expiry_date"], f"{label}.expiry_date")
            if expires < approved:
                errors.append(f"{label}: expiry_date precedes approval_date")
            if expires > approved + dt.timedelta(days=30):
                errors.append(f"{label}: waiver duration exceeds 30 days")
            if expires < today:
                errors.append(f"{label}: waiver expired on {expires.isoformat()}")
            else:
                finding = (str(waiver["image"]), str(waiver["vulnerability_id"]))
                if finding in active_findings:
                    errors.append(
                        f"{label}: duplicate active image/finding waiver {finding[0]} / {finding[1]}"
                    )
                active_findings.add(finding)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def render_waivers(waivers: list[dict[str, Any]], today: dt.date | None = None) -> str:
    """Render waivers grouped as active, expiring within 7 days, and expired."""
    today = today or dt.datetime.now(dt.UTC).date()
    groups: dict[str, list[dict[str, Any]]] = {"active": [], "expiring": [], "expired": []}
    for waiver in waivers:
        try:
            expiry = _date(waiver.get("expiry_date"), "expiry_date")
        except ValueError:
            groups["expired"].append(waiver)
            continue
        if expiry < today:
            groups["expired"].append(waiver)
        elif expiry <= today + dt.timedelta(days=7):
            groups["expiring"].append(waiver)
        else:
            groups["active"].append(waiver)

    lines = [
        "<!-- Generated by scripts/container_security.py render-waivers from security/container-waivers.yml; do not edit directly. -->",
        "",
        "# Container vulnerability waivers",
        "",
        "This report is generated from [`container-waivers.yml`](container-waivers.yml).",
        "It records temporary exceptions only. Fixable critical findings, and fixable",
        "high findings in production images, block publication. Critical/high findings",
        "without a fix require an active approved waiver; expired waivers block.",
        "",
    ]
    for title, key in (
        ("Active waivers", "active"),
        ("Expiring within 7 days", "expiring"),
        ("Expired waivers", "expired"),
    ):
        lines.extend((f"## {title}", ""))
        entries = sorted(groups[key], key=lambda item: str(item.get("expiry_date", "")))
        if not entries:
            lines.extend(("None.", ""))
            continue
        lines.extend(
            (
                "| ID | Image/package | Finding | Severity | Owner | Expires | Remediation |",
                "|---|---|---|---|---|---|---|",
            )
        )
        for entry in entries:
            lines.append(
                "| {id} | {image} | {vuln} | {severity} | {owner} | {expires} | {issue} |".format(
                    id=entry.get("id", "invalid"),
                    image=entry.get("image", "invalid"),
                    vuln=entry.get("vulnerability_id", "invalid"),
                    severity=entry.get("severity", "invalid"),
                    owner=entry.get("owner", "invalid"),
                    expires=entry.get("expiry_date", "invalid"),
                    issue=entry.get("remediation_issue", "invalid"),
                )
            )
        lines.append("")
    return "\n".join(lines)


def _image_default(value: str) -> str:
    if value.startswith("${") and ":-" in value and value.endswith("}"):
        return value.split(":-", 1)[1][:-1]
    return value


def affected_images(changed: Iterable[str], root: Path) -> dict[str, list[dict[str, str]]]:
    """Map changed paths to the generated service images CI must scan as GitHub matrix output."""
    paths = set(changed)
    service_files = sorted((root / "packages").glob("*/src/*/service.yaml"))
    targets: list[dict[str, str]] = []
    # Shared infrastructure files do not map to one package, so any touch
    # triggers a scan of every built image instead of the per-package filter.
    broad_change = any(path in BROAD_IMAGE_PATHS or path.startswith("security/") for path in paths)
    for service_file in service_files:
        service = yaml.safe_load(service_file.read_text(encoding="utf-8"))
        if not isinstance(service, dict) or not isinstance(service.get("build"), dict):
            continue
        package_root = service_file.parents[2]
        relative = package_root.relative_to(root).as_posix()
        if not broad_change and not any(
            path == relative or path.startswith(relative + "/") for path in paths
        ):
            continue
        image = service.get("image")
        if not isinstance(image, str) or not _image_default(image).startswith(
            "ghcr.io/phlohouse/phlo-"
        ):
            continue
        targets.append(
            {
                "service": str(service.get("name", service_file.parent.name)),
                "image": _image_default(image),
                "package": relative,
                # CI uses checked-in Dockerfiles for fast PR feedback. Publication
                # continues to use rendered contexts because service templates may
                # alter their build inputs.
                "context": relative,
                "dockerfile": service_file.parent.relative_to(package_root)
                .joinpath("Dockerfile")
                .as_posix(),
            }
        )
    return {"include": targets}


def published_fleet(root: Path) -> list[dict[str, Any]]:
    """Derive and validate the complete unique published fleet from service source."""
    by_service: dict[str, str] = {}
    for service_file in sorted((root / "packages").glob("*/src/*/*.yaml")):
        service = yaml.safe_load(service_file.read_text(encoding="utf-8"))
        if not isinstance(service, dict) or not isinstance(service.get("build"), dict):
            continue
        service_name = service.get("name")
        image = service.get("image")
        if not isinstance(service_name, str) or not isinstance(image, str):
            raise ValueError(f"built service in {service_file} has invalid name or image")
        image = _image_default(image).split("@", 1)[0]
        if service_name in by_service:
            raise ValueError(f"duplicate published service {service_name!r}")
        by_service[service_name] = image

    expected_services = set(PUBLISHED_SERVICE_REPOSITORIES)
    actual_services = set(by_service)
    if actual_services != expected_services:
        missing = sorted(expected_services - actual_services)
        unexpected = sorted(actual_services - expected_services)
        raise ValueError(
            f"published service fleet mismatch; missing={missing!r}, unexpected={unexpected!r}"
        )
    for service_name, repository in PUBLISHED_SERVICE_REPOSITORIES.items():
        if not by_service[service_name].startswith(repository):
            raise ValueError(
                f"published service {service_name!r} must use a versioned {repository!r} image"
            )
    if by_service["dagster"] != by_service["dagster-daemon"]:
        raise ValueError("dagster and dagster-daemon must share one published image")

    grouped: dict[str, list[str]] = {}
    for service_name, image in by_service.items():
        grouped.setdefault(image, []).append(service_name)
    if len(grouped) != 3:
        raise ValueError(
            f"published fleet must contain exactly three unique images, found {len(grouped)}"
        )
    return [
        {"image": image, "services": sorted(services)}
        for image, services in sorted(grouped.items())
    ]


def assemble_rescan_manifest(records: Any, root: Path) -> list[dict[str, Any]]:
    """Validate resolved registry records against source and return a stable manifest."""
    if not isinstance(records, list):
        raise ValueError("resolved registry records must be a list")
    expected = {entry["image"]: entry["services"] for entry in published_fleet(root)}
    resolved: dict[str, dict[str, Any]] = {}
    seen_services: set[str] = set()
    for index, raw_record in enumerate(records, start=1):
        if not isinstance(raw_record, dict) or set(raw_record) != {"image", "digest", "services"}:
            raise ValueError(f"resolved registry record {index} is malformed")
        record = cast(dict[str, Any], raw_record)
        image = record["image"]
        digest = record["digest"]
        services = record["services"]
        if (
            not isinstance(image, str)
            or not isinstance(digest, str)
            or DIGEST_PATTERN.fullmatch(digest) is None
            or not isinstance(services, list)
            or not services
            or not all(isinstance(service, str) and service for service in services)
            or len(services) != len(set(services))
        ):
            raise ValueError(f"resolved registry record {index} is malformed")
        if image in resolved:
            raise ValueError(f"duplicate resolved registry image {image!r}")
        normalized_services = sorted(services)
        conflicting = seen_services.intersection(normalized_services)
        if conflicting:
            raise ValueError(f"services mapped to multiple images: {sorted(conflicting)!r}")
        seen_services.update(normalized_services)
        resolved[image] = {
            "image": image,
            "digest": digest,
            "services": normalized_services,
        }

    if set(resolved) != set(expected):
        missing = sorted(set(expected) - set(resolved))
        unexpected = sorted(set(resolved) - set(expected))
        raise ValueError(
            f"resolved image fleet mismatch; missing={missing!r}, unexpected={unexpected!r}"
        )
    for image, services in expected.items():
        if resolved[image]["services"] != services:
            raise ValueError(
                f"resolved image {image!r} has services {resolved[image]['services']!r}; "
                f"expected {services!r}"
            )
    return [resolved[image] for image in sorted(resolved)]


def _upstream_image_default(value: str, path: Path) -> tuple[str, str | None]:
    """Strictly unwrap one shell-style environment default."""
    if value.startswith("${"):
        match = ENV_IMAGE_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError(f"{path}: image has malformed environment default")
        return match.group("reference"), match.group("env")
    return value, None


def upstream_runtime_inventory(root: Path) -> dict[str, Any]:
    """Derive immutable vendor runtime images and their package-source provenance."""
    by_reference: dict[str, list[dict[str, str]]] = {}
    for service_file in sorted((root / "packages").glob("*/src/**/*.yaml")):
        try:
            document = yaml.safe_load(service_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"cannot parse package service {service_file}: {exc}") from exc
        if not isinstance(document, dict) or "image" not in document:
            continue
        if isinstance(document.get("build"), dict):
            continue
        image = document["image"]
        if not isinstance(image, str):
            raise ValueError(f"{service_file}: runtime image must be a string")
        reference, env = _upstream_image_default(image, service_file)
        if reference.startswith("ghcr.io/phlohouse/phlo-"):
            continue
        if IMMUTABLE_IMAGE_PATTERN.fullmatch(reference) is None:
            raise ValueError(
                f"{service_file}: vendor runtime image must contain an immutable tag and digest"
            )
        service = document.get("name")
        if not isinstance(service, str) or not service:
            raise ValueError(f"{service_file}: vendor runtime service must have a name")
        source = {
            "path": service_file.relative_to(root).as_posix(),
            "service": service,
        }
        if env is not None:
            source["env"] = env
        by_reference.setdefault(reference, []).append(source)

    images = []
    for reference, sources in sorted(by_reference.items()):
        images.append(
            {
                "reference": reference,
                # Deterministic name derived from the reference keeps inventory,
                # scan artifacts, and later comparisons linkable without a registry.
                "report": hashlib.sha256(reference.encode()).hexdigest() + ".json",
                "sources": sorted(
                    sources,
                    key=lambda source: (source["path"], source["service"]),
                ),
            }
        )
    return {"version": 1, "images": images}


def _runtime_reference_from_document(document: Any, path: str) -> tuple[str, dict[str, str]] | None:
    if (
        not isinstance(document, dict)
        or "image" not in document
        or isinstance(document.get("build"), dict)
    ):
        return None
    image = document["image"]
    if not isinstance(image, str):
        raise ValueError(f"{path}: runtime image must be a string")
    reference, env = _upstream_image_default(image, Path(path))
    if reference.startswith("ghcr.io/phlohouse/phlo-"):
        return None
    if IMMUTABLE_IMAGE_PATTERN.fullmatch(reference) is None:
        raise ValueError(f"{path}: vendor runtime image must contain an immutable tag and digest")
    service = document.get("name")
    if not isinstance(service, str) or not service:
        raise ValueError(f"{path}: vendor runtime service must have a name")
    source = {"path": path, "service": service}
    if env is not None:
        source["env"] = env
    return reference, source


def _git_yaml(revision: str, path: str) -> Any:
    try:
        contents = subprocess.check_output(["git", "show", f"{revision}:{path}"], text=True)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"{path}: missing from {revision}") from exc
    try:
        return yaml.safe_load(contents)
    except yaml.YAMLError as exc:
        raise ValueError(f"cannot parse package service {path} at {revision}: {exc}") from exc


def _immutable_repository(reference: str) -> str:
    match = IMMUTABLE_IMAGE_PATTERN.fullmatch(reference)
    if match is None:
        raise ValueError(f"invalid immutable image reference {reference!r}")
    return match.group("repository")


def upstream_runtime_candidates(base: str, head: str) -> dict[str, Any]:
    """Derive changed immutable vendor image pairs from exact Git source revisions."""
    try:
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}...{head}", "--", "packages"], text=True
        ).splitlines()
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot compare upstream runtime images: {exc}") from exc
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for path in sorted(
        path for path in changed if re.fullmatch(r"packages/[^/]+/src/.+\.ya?ml", path)
    ):
        base_image = _runtime_reference_from_document(_git_yaml(base, path), path)
        candidate_image = _runtime_reference_from_document(_git_yaml(head, path), path)
        if base_image is None and candidate_image is None:
            continue
        if base_image is None or candidate_image is None:
            raise ValueError(
                f"{path}: upstream runtime image must exist in both base and candidate"
            )
        base_reference, _ = base_image
        candidate_reference, source = candidate_image
        if base_reference == candidate_reference:
            continue
        base_repository = _immutable_repository(base_reference)
        candidate_repository = _immutable_repository(candidate_reference)
        if base_repository != candidate_repository:
            raise ValueError(
                f"{path}: candidate must retain upstream repository {base_repository}; "
                f"got {candidate_repository}"
            )
        grouped.setdefault((base_reference, candidate_reference), []).append(source)
    images = []
    for (base_reference, candidate_reference), sources in sorted(grouped.items()):
        if len({(source["path"], source["service"]) for source in sources}) != len(sources):
            raise ValueError(f"duplicate upstream runtime source for {candidate_reference}")
        images.append(
            {
                "base": base_reference,
                "candidate": candidate_reference,
                "base_report": hashlib.sha256(base_reference.encode()).hexdigest() + ".json",
                "candidate_report": hashlib.sha256(candidate_reference.encode()).hexdigest()
                + ".json",
                "sources": sorted(sources, key=lambda source: (source["path"], source["service"])),
            }
        )
    return {"version": 1, "images": images}


def _validate_upstream_inventory(inventory: Any) -> list[dict[str, Any]]:
    if not isinstance(inventory, dict) or set(inventory) != {"version", "images"}:
        raise ValueError("upstream inventory is malformed")
    if inventory["version"] != 1 or not isinstance(inventory["images"], list):
        raise ValueError("upstream inventory is malformed")
    images: list[dict[str, Any]] = []
    references: set[str] = set()
    reports: set[str] = set()
    for index, raw_image in enumerate(inventory["images"], start=1):
        if not isinstance(raw_image, dict) or set(raw_image) != {
            "reference",
            "report",
            "sources",
        }:
            raise ValueError(f"upstream inventory image {index} is malformed")
        image = cast(dict[str, Any], raw_image)
        reference = image["reference"]
        report = image["report"]
        sources = image["sources"]
        if (
            not isinstance(reference, str)
            or IMMUTABLE_IMAGE_PATTERN.fullmatch(reference) is None
            or reference.startswith("ghcr.io/phlohouse/phlo-")
            or not isinstance(report, str)
            or re.fullmatch(r"[0-9a-f]{64}\.json", report) is None
            or report != hashlib.sha256(reference.encode()).hexdigest() + ".json"
            or not isinstance(sources, list)
            or not sources
        ):
            raise ValueError(f"upstream inventory image {index} is malformed")
        if reference in references or report in reports:
            raise ValueError(f"upstream inventory image {index} is duplicate")
        references.add(reference)
        reports.add(report)
        for source in sources:
            if (
                not isinstance(source, dict)
                or not set(source).issubset({"path", "service", "env"})
                or not {"path", "service"}.issubset(source)
                or not all(isinstance(value, str) and value for value in source.values())
            ):
                raise ValueError(f"upstream inventory image {index} source is malformed")
        images.append(image)
    if images != sorted(images, key=lambda image: image["reference"]):
        raise ValueError("upstream inventory images are not sorted")
    return images


def _report_findings(report: Any, report_name: str) -> list[dict[str, Any]]:
    if not isinstance(report, dict) or not isinstance(report.get("SchemaVersion"), int):
        raise ValueError(f"malformed Trivy report {report_name}")
    results = report.get("Results") or []
    if not isinstance(results, list):
        raise ValueError(f"malformed Trivy report {report_name}")
    findings: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError(f"malformed Trivy report {report_name}")
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise ValueError(f"malformed Trivy report {report_name}")
        for finding in vulnerabilities:
            if (
                not isinstance(finding, dict)
                or not isinstance(finding.get("VulnerabilityID"), str)
                or str(finding.get("Severity", "")).upper() not in UPSTREAM_SEVERITIES
                or not isinstance(finding.get("FixedVersion", ""), str)
            ):
                raise ValueError(f"malformed Trivy report {report_name}")
            findings.append(finding)
    return findings


def summarize_upstream_reports(inventory: Any, reports_dir: Path) -> str:
    """Validate a complete Trivy report set and render non-blocking visibility."""
    images = _validate_upstream_inventory(inventory)
    expected = {image["report"] for image in images}
    actual = {path.name for path in reports_dir.iterdir() if path.is_file()}
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        raise ValueError(f"missing upstream Trivy reports: {missing!r}")
    if unexpected:
        raise ValueError(f"unexpected upstream Trivy reports: {unexpected!r}")

    aggregate = {severity: {"total": 0, "fixable": 0} for severity in UPSTREAM_SEVERITIES}
    per_image: list[tuple[dict[str, Any], dict[str, dict[str, int]]]] = []
    for image in images:
        report_path = reports_dir / image["report"]
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed Trivy report {image['report']}: {exc}") from exc
        counts = {severity: {"total": 0, "fixable": 0} for severity in UPSTREAM_SEVERITIES}
        for finding in _report_findings(report, image["report"]):
            severity = str(finding["Severity"]).upper()
            counts[severity]["total"] += 1
            aggregate[severity]["total"] += 1
            if finding.get("FixedVersion"):
                counts[severity]["fixable"] += 1
                aggregate[severity]["fixable"] += 1
        per_image.append((image, counts))

    lines = [
        "# Upstream runtime image vulnerability visibility",
        "",
        "> Generated non-blocking visibility. Findings do not apply Phlo's first-party",
        "> waiver policy and do not fail this workflow; malformed or incomplete scans do.",
        "",
        "## Aggregate findings",
        "",
        "| Severity | Total | Fixable | Unfixed |",
        "|---|---:|---:|---:|",
    ]
    for severity in UPSTREAM_SEVERITIES:
        counts = aggregate[severity]
        lines.append(
            f"| {severity} | {counts['total']} | {counts['fixable']} | "
            f"{counts['total'] - counts['fixable']} |"
        )
    lines.extend(("", "## Inventory and per-image findings", ""))
    for image, counts in per_image:
        lines.extend((f"### `{image['reference']}`", "", "Sources:"))
        for source in image["sources"]:
            provenance = f"{source['path']} ({source['service']})"
            if source.get("env"):
                provenance += f" via ${{{source['env']}}}"
            lines.append(f"- `{provenance}`")
        lines.extend(("", "| Severity | Total | Fixable | Unfixed |", "|---|---:|---:|---:|"))
        for severity in UPSTREAM_SEVERITIES:
            severity_counts = counts[severity]
            lines.append(
                f"| {severity} | {severity_counts['total']} | {severity_counts['fixable']} | "
                f"{severity_counts['total'] - severity_counts['fixable']} |"
            )
        lines.append("")
    return "\n".join(lines)


def _validate_candidate_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or set(manifest) != {"version", "images"}:
        raise ValueError("upstream candidate manifest is malformed")
    if manifest["version"] != 1 or not isinstance(manifest["images"], list):
        raise ValueError("upstream candidate manifest is malformed")
    images: list[dict[str, Any]] = []
    pairs: set[tuple[str, str]] = set()
    reports: set[str] = set()
    for index, raw_image in enumerate(manifest["images"], start=1):
        required = {"base", "candidate", "base_report", "candidate_report", "sources"}
        if not isinstance(raw_image, dict) or set(raw_image) != required:
            raise ValueError(f"upstream candidate image {index} is malformed")
        image = cast(dict[str, Any], raw_image)
        base, candidate = image["base"], image["candidate"]
        base_report, candidate_report = image["base_report"], image["candidate_report"]
        sources = image["sources"]
        if (
            not isinstance(base, str)
            or not isinstance(candidate, str)
            or base == candidate
            or IMMUTABLE_IMAGE_PATTERN.fullmatch(base) is None
            or IMMUTABLE_IMAGE_PATTERN.fullmatch(candidate) is None
            or not isinstance(base_report, str)
            or not isinstance(candidate_report, str)
            or base_report != hashlib.sha256(base.encode()).hexdigest() + ".json"
            or candidate_report != hashlib.sha256(candidate.encode()).hexdigest() + ".json"
            or not isinstance(sources, list)
            or not sources
        ):
            raise ValueError(f"upstream candidate image {index} is malformed")
        if (base, candidate) in pairs or base_report in reports or candidate_report in reports:
            raise ValueError(f"upstream candidate image {index} is duplicate")
        pairs.add((base, candidate))
        reports.update((base_report, candidate_report))
        for source in sources:
            if (
                not isinstance(source, dict)
                or not set(source).issubset({"path", "service", "env"})
                or not {"path", "service"}.issubset(source)
                or not all(isinstance(value, str) and value for value in source.values())
            ):
                raise ValueError(f"upstream candidate image {index} source is malformed")
        if sources != sorted(sources, key=lambda source: (source["path"], source["service"])):
            raise ValueError(f"upstream candidate image {index} sources are not sorted")
        images.append(image)
    if images != sorted(images, key=lambda image: (image["base"], image["candidate"])):
        raise ValueError("upstream candidate images are not sorted")
    return images


def _read_expected_reports(
    reports_dir: Path, expected: set[str]
) -> dict[str, list[dict[str, Any]]]:
    actual = {path.name for path in reports_dir.iterdir() if path.is_file()}
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        raise ValueError(f"missing upstream Trivy reports: {missing!r}")
    if unexpected:
        raise ValueError(f"unexpected upstream Trivy reports: {unexpected!r}")
    parsed: dict[str, list[dict[str, Any]]] = {}
    for report_name in expected:
        try:
            report = json.loads((reports_dir / report_name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed Trivy report {report_name}: {exc}") from exc
        parsed[report_name] = _report_findings(report, report_name)
    return parsed


def compare_upstream_candidate_reports(
    manifest: Any, base_reports_dir: Path, candidate_reports_dir: Path
) -> tuple[str, list[str]]:
    """Render an exact upstream-image comparison and return gate failures."""
    images = _validate_candidate_manifest(manifest)
    base_reports = _read_expected_reports(
        base_reports_dir, {image["base_report"] for image in images}
    )
    candidate_reports = _read_expected_reports(
        candidate_reports_dir, {image["candidate_report"] for image in images}
    )
    lines = [
        "# Upstream runtime image candidate security comparison",
        "",
        "A candidate passes only when Critical and High raw occurrence counts do not increase,",
        "and at least one decreases. Malformed or incomplete reports fail closed.",
        "",
    ]
    errors: list[str] = []
    for image in images:
        base_findings = base_reports[image["base_report"]]
        candidate_findings = candidate_reports[image["candidate_report"]]
        lines.extend((f"## `{image['candidate']}`", "", f"Base: `{image['base']}`", "", "Sources:"))
        for source in image["sources"]:
            provenance = f"{source['path']} ({source['service']})"
            if source.get("env"):
                provenance += f" via ${{{source['env']}}}"
            lines.append(f"- `{provenance}`")
        lines.extend(
            (
                "",
                "| Severity | Base occurrences | Candidate occurrences | Base unique IDs | Candidate unique IDs | Base fixable | Base unfixed | Candidate fixable | Candidate unfixed |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            )
        )
        counts: dict[str, tuple[int, int]] = {}
        for severity in UPSTREAM_SEVERITIES:
            base_selected = [item for item in base_findings if item["Severity"].upper() == severity]
            candidate_selected = [
                item for item in candidate_findings if item["Severity"].upper() == severity
            ]
            base_fixable = sum(bool(item["FixedVersion"]) for item in base_selected)
            candidate_fixable = sum(bool(item["FixedVersion"]) for item in candidate_selected)
            lines.append(
                f"| {severity} | {len(base_selected)} | {len(candidate_selected)} | "
                f"{len({item['VulnerabilityID'] for item in base_selected})} | "
                f"{len({item['VulnerabilityID'] for item in candidate_selected})} | "
                f"{base_fixable} | {len(base_selected) - base_fixable} | "
                f"{candidate_fixable} | {len(candidate_selected) - candidate_fixable} |"
            )
            counts[severity] = (len(base_selected), len(candidate_selected))
        base_ids = {str(item["VulnerabilityID"]) for item in base_findings}
        candidate_ids = {str(item["VulnerabilityID"]) for item in candidate_findings}
        for label, values in (
            ("Added IDs", sorted(candidate_ids - base_ids)),
            ("Removed IDs", sorted(base_ids - candidate_ids)),
            ("Unchanged IDs", sorted(base_ids & candidate_ids)),
        ):
            rendered = ", ".join(f"`{value}`" for value in values) or "None"
            lines.append(f"{label}: {rendered}")
        critical_base, critical_candidate = counts["CRITICAL"]
        high_base, high_candidate = counts["HIGH"]
        if not (
            critical_candidate <= critical_base
            and high_candidate <= high_base
            and (critical_candidate < critical_base or high_candidate < high_base)
        ):
            errors.append(
                f"{image['candidate']}: candidate does not strictly improve CRITICAL/HIGH findings"
            )
        lines.append("")
    return "\n".join(lines), errors


def _waived(waivers: list[dict[str, Any]], image: str, vulnerability_id: str) -> bool:
    # Waivers may name either the full digest-pinned reference, the bare
    # repository@tag form with the digest stripped, or a tagged variant of it.
    image_name = image.split("@", 1)[0]
    for waiver in waivers:
        if str(waiver.get("vulnerability_id")) != vulnerability_id:
            continue
        candidate = str(waiver.get("image"))
        if candidate in (image, image_name) or image_name.startswith(candidate + ":"):
            return True
    return False


# A finding with an available fix always blocks until the image is rebuilt on a
# patched base. Unfixed findings are inherited from upstream and reported as
# warnings; waivers document accepted exceptions without changing enforcement.
def apply_policy(
    report: dict[str, Any], image: str, waivers: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Apply blocking-severity policy to one Trivy report.

    Returns unwaived fixable blocking findings as errors and unfixed findings
    (inherited from upstream) as warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for result in report.get("Results", []) or []:
        for finding in result.get("Vulnerabilities", []) or []:
            severity = str(finding.get("Severity", "")).upper()
            vulnerability_id = str(finding.get("VulnerabilityID", "unknown"))
            fixed = bool(finding.get("FixedVersion"))
            production = image.split("@", 1)[0].startswith("ghcr.io/phlohouse/phlo-")
            blocks = severity == "CRITICAL" or (severity == "HIGH" and production)
            if not blocks:
                continue
            if fixed:
                errors.append(
                    f"{image}: fixable {severity} {vulnerability_id} (fixed in {finding['FixedVersion']})"
                )
            elif not _waived(waivers, image, vulnerability_id):
                warnings.append(
                    f"{image}: unfixed {severity} {vulnerability_id} inherited from upstream"
                )
    return errors, warnings


def main() -> int:
    """Dispatch the container-security subcommands; return the process exit code."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-waivers")
    validate.add_argument("--register", type=Path, default=Path("security/container-waivers.yml"))
    render = sub.add_parser("render-waivers")
    render.add_argument("--register", type=Path, default=Path("security/container-waivers.yml"))
    render.add_argument("--output", type=Path, default=Path("security/container-waivers.md"))
    affected = sub.add_parser("affected-images")
    changed_scope = affected.add_mutually_exclusive_group(required=True)
    changed_scope.add_argument("--base")
    changed_scope.add_argument(
        "--all",
        action="store_true",
        help="Return every generated service image for the nightly validation lane.",
    )
    affected.add_argument("--head", default="HEAD")
    fleet = sub.add_parser("published-fleet")
    fleet.add_argument("--root", type=Path, default=Path.cwd())
    assemble = sub.add_parser("assemble-rescan-manifest")
    assemble.add_argument("--records", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    assemble.add_argument("--root", type=Path, default=Path.cwd())
    upstream = sub.add_parser("upstream-runtime-images")
    upstream.add_argument("--root", type=Path, default=Path.cwd())
    write_upstream = sub.add_parser("write-upstream-inventory")
    write_upstream.add_argument("--root", type=Path, default=Path.cwd())
    write_upstream.add_argument("--output", type=Path, required=True)
    write_candidates = sub.add_parser("write-upstream-candidates")
    write_candidates.add_argument("--base", required=True)
    write_candidates.add_argument("--head", required=True)
    write_candidates.add_argument("--output", type=Path, required=True)
    summarize_upstream = sub.add_parser("summarize-upstream-reports")
    summarize_upstream.add_argument("--inventory", type=Path, required=True)
    summarize_upstream.add_argument("--reports", type=Path, required=True)
    summarize_upstream.add_argument("--output", type=Path, required=True)
    compare_upstream = sub.add_parser("compare-upstream-candidates")
    compare_upstream.add_argument("--manifest", type=Path, required=True)
    compare_upstream.add_argument("--base-reports", type=Path, required=True)
    compare_upstream.add_argument("--candidate-reports", type=Path, required=True)
    compare_upstream.add_argument("--output", type=Path, required=True)
    policy = sub.add_parser("apply-policy")
    policy.add_argument("--register", type=Path, default=Path("security/container-waivers.yml"))
    policy.add_argument("--image", required=True)
    policy.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "published-fleet":
        print(json.dumps(published_fleet(args.root), separators=(",", ":")))
        return 0
    if args.command == "assemble-rescan-manifest":
        manifest = assemble_rescan_manifest(
            json.loads(args.records.read_text(encoding="utf-8")), args.root
        )
        args.output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    if args.command in {"upstream-runtime-images", "write-upstream-inventory"}:
        inventory = upstream_runtime_inventory(args.root)
        rendered = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
        if args.command == "upstream-runtime-images":
            print(rendered, end="")
        else:
            args.output.write_text(rendered, encoding="utf-8")
        return 0
    if args.command == "write-upstream-candidates":
        manifest = upstream_runtime_candidates(args.base, args.head)
        args.output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    if args.command == "summarize-upstream-reports":
        summary = summarize_upstream_reports(
            json.loads(args.inventory.read_text(encoding="utf-8")), args.reports
        )
        args.output.write_text(summary, encoding="utf-8")
        return 0
    if args.command == "compare-upstream-candidates":
        summary, errors = compare_upstream_candidate_reports(
            json.loads(args.manifest.read_text(encoding="utf-8")),
            args.base_reports,
            args.candidate_reports,
        )
        args.output.write_text(summary, encoding="utf-8")
        if errors:
            print(
                "Upstream image candidate comparison failed:", *errors, sep="\n- ", file=sys.stderr
            )
            return 1
        return 0
    if args.command == "affected-images":
        if args.all:
            changed = ["pyproject.toml"]
        else:
            changed = subprocess.check_output(
                ["git", "diff", "--name-only", f"{args.base}...{args.head}"], text=True
            ).splitlines()
        print(json.dumps(affected_images(changed, Path.cwd()), separators=(",", ":")))
        return 0
    waivers = load_waivers(args.register)
    if args.command == "validate-waivers":
        errors = validate_waivers(waivers)
        if errors:
            print(
                "Container waiver register validation failed:",
                *[f"- {error}" for error in errors],
                sep="\n",
                file=sys.stderr,
            )
            return 1
        return 0
    if args.command == "render-waivers":
        args.output.write_text(render_waivers(waivers), encoding="utf-8")
        return 0
    errors, warnings = apply_policy(
        json.loads(args.report.read_text(encoding="utf-8")), args.image, waivers
    )
    if warnings:
        print(
            "Container vulnerability notices (non-blocking):",
            *[f"- {warning}" for warning in warnings],
            sep="\n",
        )
    if errors:
        print(
            "Container vulnerability policy failed:",
            *[f"- {error}" for error in errors],
            sep="\n",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
