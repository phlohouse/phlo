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


def test_remaining_vendor_services_use_upstream_images_and_are_not_published(
    tmp_path: Path, monkeypatch
) -> None:
    expected_images = {
        "alloy": "grafana/alloy:v1.18.0@sha256:491b0578c04983fd54fe99b587b6fab4404dc46d0dc16677bd6b00cc1140b308",
        "loki": "grafana/loki:3.7.4@sha256:87f0a067673756a3cede1bcbf0c74875f7df9b09fddb53e399d0c576f756cfcc",
        "oauth2-proxy": "quay.io/oauth2-proxy/oauth2-proxy:v7.15.3@sha256:10a1165743a192e1940b4708fb9647027185ce11a681a1c5519b442ff7f1f561",
        "grafana": "grafana/grafana:13.1.1@sha256:7cb8c64c4d57a57e734073f3cc94620adb24a0acb929bd80ba9f14017e3a975b",
        "clickstack": "docker.io/hyperdx/hyperdx-all-in-one:2.31.0@sha256:b01cc48cb5aaf30d630865a88217c826ab86fb9828374201f6cd7c539d5beed1",
        "minio": "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e",
        "minio-setup": "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727",
        "nessie": "ghcr.io/projectnessie/nessie:0.108.3@sha256:219709df809fcfac7abe0491b2070c8d56178ed29828fb30968a4365b62bcc8a",
        "openmetadata": "docker.io/openmetadata/server:1.13.1@sha256:eaa318584c52d4a492a2c56c95818b5564c6ea28b2e9695ac532c856b2c61bc9",
        "openmetadata-setup": "docker.io/openmetadata/server:1.13.1@sha256:eaa318584c52d4a492a2c56c95818b5564c6ea28b2e9695ac532c856b2c61bc9",
        "openmetadata-mysql": "docker.io/openmetadata/db:1.13.1@sha256:6659446dba183f1e9364602839dd999c06a83f7d2e905d1c3fb22a74f3e27288",
        "openmetadata-elasticsearch": "docker.elastic.co/elasticsearch/elasticsearch:9.3.0@sha256:4f6bdcb742e892539c6ac49b0dd3e4e182e90218546e8c6a22db378c344acb60",
        "pgweb": "sosedoff/pgweb:0.17.0@sha256:a5256d416e2e8b92d69a4459058e3eca33a9f075d8325491644411d0bc3bd70b",
        "postgres": "postgres:18.4-alpine3.24@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15",
        "postgres-exporter": "quay.io/prometheuscommunity/postgres-exporter:v0.20.1@sha256:ac5ec343104fae0e2d84a27bb8d69b38430a11910c5382cad85d478d2bab713e",
        "postgrest": "postgrest/postgrest:v14.15@sha256:2f8e7b656f09db697a8875177694b417b35cb76c21370de07fc54e711e902326",
        "superset": "apache/superset:6.1.0@sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705",
    }
    discovery = ServiceDiscovery()
    services = []
    for name in expected_images:
        service = discovery.get_service(name)
        assert service is not None, name
        services.append(service)
    for name in ("phlo-api", "dagster", "dagster-daemon", "observatory"):
        service = discovery.get_service(name)
        assert service is not None, name
        services.append(service)

    generated_root = tmp_path / ".phlo"
    generated_root.mkdir()
    compose = yaml.safe_load(
        ComposeGenerator(discovery).generate_compose(services, output_dir=generated_root)
    )

    for name, image in expected_images.items():
        assert compose["services"][name]["image"] == image
        assert "build" not in compose["services"][name]

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


def test_publication_workflow_publishes_attested_images_after_digest_scans() -> None:
    workflow = (REPO_ROOT / ".github/workflows/build-core-services.yml").read_text(encoding="utf-8")

    assert "pull_request:" not in workflow
    assert "\n  push:" in workflow
    assert "fetch-depth: 0" in workflow
    assert "workflow_dispatch:" in workflow
    assert "release:" in workflow
    assert "PUBLISH_SERVICES" in workflow
    assert "packages: write" in workflow
    assert "docker/build-push-action@" in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "platform: linux/amd64" in workflow
    assert "platform: linux/arm64" in workflow
    assert "setup-qemu-action@" not in workflow
    assert "push-by-digest=true" in workflow
    assert "name=${{ steps.image.outputs.repository }},push-by-digest=true" in workflow
    assert "name=${{ matrix.target.image }},push-by-digest=true" not in workflow
    assert "if: always() && needs.prepare.result == 'success'" in workflow
    assert "name: digest-${{ matrix.target.service }}-amd64" in workflow
    assert "name: digest-${{ matrix.target.service }}-arm64" in workflow
    assert "pattern: digest-${{ matrix.target.service }}-*" not in workflow
    assert "docker buildx imagetools create" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "max-parallel: 8" in workflow
    assert "org.opencontainers.image.source=https://github.com/${{ github.repository }}" in workflow
    assert "Scan immutable architecture digest" in workflow
    assert "apply-policy" in workflow
    assert '-v "$PWD:/work" -w /work' in workflow
    assert (
        '"/work/security-reports/${{ matrix.target.service }}-${{ matrix.architecture.name }}.json"'
        in workflow
    )
    assert "PLATFORM: ${{ matrix.architecture.platform }}" in workflow
    assert 'image --platform "$PLATFORM"' in workflow


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
