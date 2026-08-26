"""Contracts for generated service image publication and remote CI scanning.

Every generated build pins a versioned ghcr image; vendor runtime defaults pin
immutable digests while upstream images such as Prometheus and Trino stay
unpublished, safe full-image overrides are limited, and publication runs only
after digest scans produce attestations.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.discovery import ServiceDiscovery

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_SPEC = importlib.util.spec_from_file_location(
    "generated_image_matrix", REPO_ROOT / "scripts" / "generated_image_matrix.py"
)
assert MATRIX_SPEC and MATRIX_SPEC.loader
GENERATED_IMAGE_MATRIX = importlib.util.module_from_spec(MATRIX_SPEC)
sys.modules["generated_image_matrix"] = GENERATED_IMAGE_MATRIX
MATRIX_SPEC.loader.exec_module(GENERATED_IMAGE_MATRIX)


def _published_image(raw_image: str) -> str:
    match = re.fullmatch(r"\$\{[^:}]+:-(.+)}", raw_image)
    return match.group(1) if match else raw_image


def test_every_generated_build_uses_a_versioned_ghcr_image() -> None:
    build_definitions: list[tuple[Path, str]] = []
    for service_file in sorted((REPO_ROOT / "packages").glob("*/src/*/*.yaml")):
        service = yaml.safe_load(service_file.read_text(encoding="utf-8"))
        if not isinstance(service, dict) or not service.get("build"):
            continue
        image = service.get("image")
        assert isinstance(image, str), f"{service_file} has a build but no image"
        build_definitions.append((service_file, _published_image(image)))

    assert build_definitions
    support_manifest = json.loads(
        (REPO_ROOT / "registry/support/v1.json").read_text(encoding="utf-8")
    )
    release_images = {
        entry["image_reference"]
        for entry in support_manifest["release_set"]["services"]
        if entry["name"] in {"phlo-api", "dagster", "observatory"}
    }
    assert {image for _, image in build_definitions} == release_images
    for service_file, image in build_definitions:
        assert image.startswith("ghcr.io/phlohouse/phlo-"), service_file
        assert ":" in image.rsplit("/", 1)[-1], service_file
        assert not image.endswith(":latest"), service_file

    published_images = {image for _, image in build_definitions}
    waiver_register = yaml.safe_load(
        (REPO_ROOT / "security/container-waivers.yml").read_text(encoding="utf-8")
    )
    for waiver in waiver_register["waivers"]:
        waiver_image = str(waiver["image"])
        assert any(
            image == waiver_image or image.startswith((f"{waiver_image}:", f"{waiver_image}@"))
            for image in published_images
        ), f"{waiver['id']} targets an image that is no longer published"


def test_generated_prometheus_and_trino_use_upstream_images_and_are_not_published(
    tmp_path: Path, monkeypatch
) -> None:
    discovery = ServiceDiscovery()
    services = [
        service
        for name in (
            "prometheus",
            "trino",
            "postgres",
            "phlo-api",
            "dagster",
            "dagster-daemon",
            "observatory",
        )
        if (service := discovery.get_service(name)) is not None
    ]
    generated_root = tmp_path / ".phlo"
    generated_root.mkdir()
    compose = yaml.safe_load(
        ComposeGenerator(discovery).generate_compose(services, output_dir=generated_root)
    )

    assert compose["services"]["prometheus"]["image"] == (
        "${PROMETHEUS_IMAGE:-prom/prometheus:v3.13.1@"
        "sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893}"
    )
    assert compose["services"]["trino"]["image"] == (
        "trinodb/trino:483@sha256:db58cc93e593a2706553745f276bb119c9810e69918be56ecde088ba7ccb0534"
    )
    assert "build" not in compose["services"]["prometheus"]
    assert "build" not in compose["services"]["trino"]

    monkeypatch.chdir(generated_root)
    targets = GENERATED_IMAGE_MATRIX.publication_matrix(compose, generated_root, REPO_ROOT)[
        "include"
    ]
    published_services = {target["service"] for target in targets}

    assert published_services == {"phlo-api", "dagster", "observatory"}


_VENDOR_UPSTREAM_SERVICES = (
    "alloy",
    "loki",
    "oauth2-proxy",
    "grafana",
    "clickstack",
    "minio",
    "minio-setup",
    "nessie",
    "openmetadata",
    "openmetadata-setup",
    "openmetadata-mysql",
    "openmetadata-elasticsearch",
    "pgweb",
    "postgres",
    "postgres-exporter",
    "postgrest",
    "superset",
)


def test_remaining_vendor_services_use_upstream_images_and_are_not_published(
    tmp_path: Path, monkeypatch
) -> None:
    discovery = ServiceDiscovery()
    vendor_services = []
    for name in _VENDOR_UPSTREAM_SERVICES:
        service = discovery.get_service(name)
        assert service is not None, name
        vendor_services.append(service)
    services = [*vendor_services]
    for name in ("phlo-api", "dagster", "dagster-daemon", "observatory"):
        service = discovery.get_service(name)
        assert service is not None, name
        services.append(service)

    generated_root = tmp_path / ".phlo"
    generated_root.mkdir()
    compose = yaml.safe_load(
        ComposeGenerator(discovery).generate_compose(services, output_dir=generated_root)
    )

    for service in vendor_services:
        rendered = compose["services"][service.name]
        assert rendered["image"] == service.image
        assert "@sha256:" in _published_image(rendered["image"])
        assert "build" not in rendered

    monkeypatch.chdir(generated_root)
    targets = GENERATED_IMAGE_MATRIX.publication_matrix(compose, generated_root, REPO_ROOT)[
        "include"
    ]
    published_services = {target["service"] for target in targets}
    assert published_services == {"phlo-api", "dagster", "observatory"}


def test_every_vendor_runtime_default_is_pinned_to_an_immutable_digest() -> None:
    tag_only: list[str] = []
    for service_file in sorted((REPO_ROOT / "packages").glob("*/src/*/*.yaml")):
        service = yaml.safe_load(service_file.read_text(encoding="utf-8"))
        if not isinstance(service, dict) or not isinstance(service.get("image"), str):
            continue
        image = _published_image(service["image"])
        if image.startswith("ghcr.io/phlohouse/phlo-") and service.get("build"):
            continue
        if "@sha256:" not in image:
            tag_only.append(f"{service_file.relative_to(REPO_ROOT)}: {image}")

    assert tag_only == []


def test_hasura_and_clickhouse_accept_safe_full_image_overrides() -> None:
    hasura = yaml.safe_load(
        (REPO_ROOT / "packages/phlo-hasura/src/phlo_hasura/service.yaml").read_text()
    )
    clickhouse = yaml.safe_load(
        (REPO_ROOT / "packages/phlo-clickhouse/src/phlo_clickhouse/service.yaml").read_text()
    )
    clickhouse_setup = yaml.safe_load(
        (
            REPO_ROOT / "packages/phlo-clickhouse/src/phlo_clickhouse/clickhouse-setup.yaml"
        ).read_text()
    )

    assert hasura["image"] == (
        "${HASURA_IMAGE:-hasura/graphql-engine:v2.49.5@"
        "sha256:a9f427a9078b75c5f43ea40abd4ba4e426f45777f862eff7265f411a5ac96086}"
    )
    clickhouse_image = (
        "${CLICKHOUSE_IMAGE:-clickhouse/clickhouse-server:26.5.6.64-alpine@"
        "sha256:446c9d82443b926a5aacb952448dd632672606acc691ce1b3c2292b68a1197c2}"
    )
    assert clickhouse["image"] == clickhouse_image
    assert clickhouse_setup["image"] == clickhouse_image
    assert "HASURA_VERSION" not in hasura["env_vars"]
    assert hasura["env_vars"]["HASURA_IMAGE"]["default"] == _published_image(hasura["image"])
    assert "CLICKHOUSE_VERSION" not in clickhouse["env_vars"]
    assert clickhouse["env_vars"]["CLICKHOUSE_IMAGE"]["default"] == _published_image(
        clickhouse["image"]
    )


def _workflow_triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """A bare ``on:`` mapping key parses as boolean True."""
    triggers = workflow.get("on") or workflow.get(True) or {}
    assert isinstance(triggers, dict)
    return triggers


def _job_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job.get("steps") or [] if isinstance(step, dict)]


def _step_using(steps: list[dict[str, Any]], action: str) -> dict[str, Any]:
    for step in steps:
        if str(step.get("uses", "")).startswith(action):
            return step
    raise AssertionError(f"No step uses {action!r}")


def _step_named(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"No step named {name!r}")


def _step_with_id(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any]:
    for step in steps:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"No step with id {step_id!r}")


def test_publication_workflow_publishes_attested_images_after_digest_scans() -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/build-core-services.yml").read_text(encoding="utf-8")
    )
    assert isinstance(workflow, dict)
    triggers = _workflow_triggers(workflow)
    jobs = workflow["jobs"]

    assert {"push", "workflow_dispatch", "release"} <= set(triggers)
    assert "pull_request" not in triggers

    prepare_steps = _job_steps(jobs["prepare"])
    checkout = _step_using(prepare_steps, "actions/checkout@")
    assert (checkout.get("with") or {}).get("fetch-depth") == 0
    matrix_step = _step_with_id(prepare_steps, "matrix")
    assert "PUBLISH_SERVICES" in (matrix_step.get("env") or {})
    for job_name in ("build", "merge"):
        assert jobs[job_name]["permissions"]["packages"] == "write"

    build_job = jobs["build"]
    build_steps = _job_steps(build_job)
    build_step = _step_using(build_steps, "docker/build-push-action@")
    all_steps = [step for job in jobs.values() for step in _job_steps(job)]
    assert not any("setup-qemu-action" in str(step.get("uses", "")) for step in all_steps)

    architectures = build_job["strategy"]["matrix"]["architecture"]
    assert {"linux/amd64", "linux/arm64"} <= {arch["platform"] for arch in architectures}
    assert "ubuntu-24.04-arm" in {arch["runner"] for arch in architectures}

    outputs_spec = build_step["with"]["outputs"]
    assert "type=image" in outputs_spec
    assert "name=${{ steps.image.outputs.repository }}" in outputs_spec
    assert "push-by-digest=true" in outputs_spec
    assert "matrix.target.image" not in outputs_spec

    assert build_job["timeout-minutes"] == 45
    assert build_job["strategy"]["max-parallel"] == 8
    assert (
        "org.opencontainers.image.source=https://github.com/${{ github.repository }}"
        in build_step["with"]["labels"]
    )

    merge_job = jobs["merge"]
    assert str(merge_job.get("if", "")).startswith("always() && needs.prepare.result == 'success'")
    digest_uploads = [
        step
        for step in build_steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        and str((step.get("with") or {}).get("name", "")).startswith("digest-")
    ]
    assert len(digest_uploads) == 1
    assert digest_uploads[0]["with"]["name"] == (
        "digest-${{ matrix.target.service }}-${{ matrix.architecture.name }}"
    )
    merge_downloads = [
        step
        for step in _job_steps(merge_job)
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    ]
    downloaded_names = {(step.get("with") or {}).get("name") for step in merge_downloads}
    assert {
        "digest-${{ matrix.target.service }}-amd64",
        "digest-${{ matrix.target.service }}-arm64",
    } <= downloaded_names
    assert not any("pattern" in (step.get("with") or {}) for step in merge_downloads)
    manifest_script = _step_with_id(_job_steps(merge_job), "manifest")["run"]
    assert "docker buildx imagetools create" in manifest_script

    scan_step = _step_named(build_steps, "Scan immutable architecture digest")
    assert scan_step["env"]["PLATFORM"] == "${{ matrix.architecture.platform }}"
    scan_script = scan_step["run"]
    assert 'image --platform "$PLATFORM"' in scan_script
    assert '-v "$PWD:/work" -w /work' in scan_script
    report_arg = (
        '"/work/security-reports/${{ matrix.target.service }}-${{ matrix.architecture.name }}.json"'
    )
    assert report_arg in scan_script
    assert "apply-policy" in scan_script


def test_container_security_replaces_legacy_remote_image_scan() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    container_workflow = (REPO_ROOT / ".github/workflows/container-security.yml").read_text(
        encoding="utf-8"
    )

    assert "generated-container-checks" not in workflow
    assert "--allow-vulnerable-image" not in workflow
    assert "--remote-images" not in workflow
    assert "generated-files" in container_workflow
    assert "docker build" not in container_workflow
    assert "aquasec/trivy" not in container_workflow
