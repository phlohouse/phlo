"""Tests for "phlo services init" and Compose generation.

Covers credential validation, dev versus production profile boundaries, environment
file generation with secret preservation, and service selection by default and profile.
"""

from __future__ import annotations

import os
import re
from typing import cast

import click
import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from phlo.cli.commands.services.utils import detect_phlo_source_path
from phlo.cli.infrastructure.selection import select_services_to_install
from phlo.config_schema import ServiceOverride
from phlo.plugins.compose import generator as generator_module
from phlo.plugins.compose.env import generate_env, generate_env_local
from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery
from tests.helpers import FakeDiscovery, _service


def test_production_credentials_reject_defaults_and_require_safe_usernames() -> None:
    from phlo.cli.commands.services.init import _validate_production_credentials

    with pytest.raises(click.ClickException, match="MINIO_ROOT_USER, POSTGRES_USER"):
        _validate_production_credentials({}, {})

    with pytest.raises(click.ClickException, match="POSTGRES_PASSWORD"):
        _validate_production_credentials(
            {"POSTGRES_USER": "lakehouse", "MINIO_ROOT_USER": "object-admin"},
            {"POSTGRES_PASSWORD": "phlo", "MINIO_ROOT_PASSWORD": "independent-secret"},
        )


def test_production_credentials_allow_generated_passwords_and_safe_usernames() -> None:
    from phlo.cli.commands.services.init import _validate_production_credentials

    _validate_production_credentials(
        {"POSTGRES_USER": "lakehouse", "MINIO_ROOT_USER": "object-admin"},
        {},
    )


@pytest.mark.parametrize(
    ("env_values", "expected_openid"),
    (
        ({}, {}),
        ({"MINIO_OIDC_CONFIG_URL": ""}, {}),
        ({"MINIO_OIDC_CONFIG_URL": "   \t"}, {}),
        ({"MINIO_OIDC_CONFIG_URL": '""'}, {}),
        ({"MINIO_OIDC_CONFIG_URL": "''"}, {}),
        (
            {"MINIO_OIDC_CONFIG_URL": "https://identity.example/.well-known/openid-configuration"},
            {
                "MINIO_IDENTITY_OPENID_CONFIG_URL": "${MINIO_OIDC_CONFIG_URL}",
                "MINIO_IDENTITY_OPENID_CLIENT_ID": "${MINIO_OIDC_CLIENT_ID:-}",
                "MINIO_IDENTITY_OPENID_CLIENT_SECRET": "${MINIO_OIDC_CLIENT_SECRET:-}",
                "MINIO_IDENTITY_OPENID_CLAIM_NAME": "${MINIO_OIDC_CLAIM_NAME:-policy}",
                "MINIO_IDENTITY_OPENID_SCOPES": "${MINIO_OIDC_SCOPES:-openid}",
            },
        ),
    ),
)
def test_minio_compose_only_includes_openid_settings_with_a_discovery_url(
    tmp_path, env_values, expected_openid
) -> None:
    discovery = ServiceDiscovery()
    minio = discovery.get_service("minio")

    assert minio is not None
    rendered = yaml.safe_load(
        ComposeGenerator(discovery).generate_compose(
            [minio], output_dir=tmp_path, env_values=env_values
        )
    )
    environment = rendered["services"]["minio"]["environment"]

    assert {
        key: value for key, value in environment.items() if key.startswith("MINIO_IDENTITY_OPENID_")
    } == expected_openid


@pytest.mark.parametrize("quoted_empty", ('""', "''"))
def test_compose_omits_optional_environment_values_set_to_windows_quoted_empty(
    tmp_path, quoted_empty
) -> None:
    discovery = ServiceDiscovery()
    services = [
        service
        for name in ("minio", "dagster", "clickstack")
        if (service := discovery.get_service(name)) is not None
    ]

    rendered = yaml.safe_load(
        ComposeGenerator(discovery).generate_compose(
            services,
            output_dir=tmp_path,
            env_values={
                "MINIO_SERVER_URL": quoted_empty,
                "MINIO_LDAP_SERVER": quoted_empty,
                "MINIO_LDAP_BIND_DN": quoted_empty,
                "MINIO_LDAP_BIND_PASSWORD": quoted_empty,
                "MINIO_LDAP_USER_BASE_DN": quoted_empty,
                "MINIO_LDAP_USER_FILTER": quoted_empty,
                "MINIO_AUDIT_ENDPOINT": quoted_empty,
                "PHLO_DAGSTER_OIDC_ISSUER": quoted_empty,
                "PHLO_DAGSTER_OIDC_AUDIENCE": quoted_empty,
                "PHLO_DAGSTER_OIDC_JWKS_URL": quoted_empty,
                "PHLO_DAGSTER_OIDC_CA_FILE": quoted_empty,
                "CLICKSTACK_QUERY_URL": quoted_empty,
            },
        )
    )

    minio_environment = rendered["services"]["minio"]["environment"]
    assert not {
        "MINIO_SERVER_URL",
        "MINIO_IDENTITY_LDAP_SERVER_ADDR",
        "MINIO_IDENTITY_LDAP_LOOKUP_BIND_DN",
        "MINIO_IDENTITY_LDAP_LOOKUP_BIND_PASSWORD",
        "MINIO_IDENTITY_LDAP_USER_DN_SEARCH_BASE_DN",
        "MINIO_IDENTITY_LDAP_USER_DN_SEARCH_FILTER",
        "MINIO_AUDIT_WEBHOOK_ENDPOINT",
    }.intersection(minio_environment)

    dagster_environment = rendered["services"]["dagster"]["environment"]
    assert not {
        "PHLO_DAGSTER_OIDC_ISSUER",
        "PHLO_DAGSTER_OIDC_AUDIENCE",
        "PHLO_DAGSTER_OIDC_JWKS_URL",
        "PHLO_DAGSTER_OIDC_CA_FILE",
    }.intersection(dagster_environment)

    clickstack_environment = rendered["services"]["clickstack"]["environment"]
    assert "CLICKSTACK_QUERY_URL" not in clickstack_environment


def test_conditional_environment_creates_an_absent_environment(tmp_path) -> None:
    service = ServiceDefinition(
        name="conditional",
        description="conditional",
        category="core",
        default=True,
        compose={
            "conditional_environment": {
                "ENABLE_CONDITIONAL": {"CONDITIONAL_VALUE": "enabled"},
            }
        },
    )

    data = yaml.safe_load(
        ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery())).generate_compose(
            [service], output_dir=tmp_path, env_values={"ENABLE_CONDITIONAL": "true"}
        )
    )

    assert data["services"]["conditional"]["environment"] == {"CONDITIONAL_VALUE": "enabled"}


def test_image_services_use_exact_upstream_image_without_fake_wrapper(tmp_path) -> None:
    service = _service("postgres", default=True)
    service.image = "postgres:18-alpine"
    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery({service.name: service})))

    data = yaml.safe_load(generator.generate_compose([service], output_dir=tmp_path))
    copied = generator.copy_service_files([service], tmp_path)

    assert data["services"]["postgres"]["image"] == "postgres:18-alpine"
    assert "build" not in data["services"]["postgres"]
    assert copied == []
    assert not (tmp_path / "images" / "postgres" / "Dockerfile").exists()


def test_conditional_environment_supports_list_environment_and_user_overrides(tmp_path) -> None:
    service = ServiceDefinition(
        name="conditional",
        description="conditional",
        category="core",
        default=True,
        compose={
            "environment": ["EXISTING=value"],
            "conditional_environment": {
                "ENABLE_CONDITIONAL": {
                    "CONDITIONAL_VALUE": "enabled",
                    "OVERRIDDEN_VALUE": "package",
                },
            },
        },
    )

    data = yaml.safe_load(
        ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery())).generate_compose(
            [service],
            output_dir=tmp_path,
            env_values={"ENABLE_CONDITIONAL": "true"},
            user_overrides={"conditional": {"environment": {"OVERRIDDEN_VALUE": "user"}}},
        )
    )

    assert data["services"]["conditional"]["environment"] == {
        "EXISTING": "value",
        "CONDITIONAL_VALUE": "enabled",
        "OVERRIDDEN_VALUE": "user",
    }


def test_conditional_environment_list_is_not_mutated_between_renders(tmp_path) -> None:
    service = ServiceDefinition(
        name="conditional",
        description="conditional",
        category="core",
        default=True,
        compose={
            "environment": ["EXISTING=value"],
            "conditional_environment": {
                "ENABLE_CONDITIONAL": {"CONDITIONAL_VALUE": "enabled"},
            },
        },
    )
    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))

    # Render the same definition three times (enabled -> disabled -> enabled):
    # if the generator mutated the shared compose list, leakage would surface
    # in one of the later passes.
    enabled = yaml.safe_load(
        generator.generate_compose(
            [service], output_dir=tmp_path, env_values={"ENABLE_CONDITIONAL": "true"}
        )
    )
    disabled = yaml.safe_load(generator.generate_compose([service], output_dir=tmp_path))
    enabled_again = yaml.safe_load(
        generator.generate_compose(
            [service], output_dir=tmp_path, env_values={"ENABLE_CONDITIONAL": "true"}
        )
    )

    assert service.compose["environment"] == ["EXISTING=value"]
    assert enabled["services"]["conditional"]["environment"] == [
        "EXISTING=value",
        "CONDITIONAL_VALUE=enabled",
    ]
    assert disabled["services"]["conditional"]["environment"] == ["EXISTING=value"]
    assert enabled_again["services"]["conditional"]["environment"] == [
        "EXISTING=value",
        "CONDITIONAL_VALUE=enabled",
    ]


def test_service_override_renders_valid_extra_hosts_and_rejects_blank_mappings(tmp_path) -> None:
    """A service can reach a host-only dependency through Compose host-gateway mapping."""
    service = _service("dagster", default=True)
    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery({service.name: service})))

    rendered = yaml.safe_load(
        generator.generate_compose(
            [service],
            output_dir=tmp_path,
            user_overrides={"dagster": {"extra_hosts": ["host.docker.internal:host-gateway"]}},
        )
    )

    assert rendered["services"]["dagster"]["extra_hosts"] == ["host.docker.internal:host-gateway"]
    with pytest.raises(ValidationError, match="extra_hosts mappings must be non-empty"):
        ServiceOverride(extra_hosts=["  "])
    with pytest.raises(ValidationError, match="extra_hosts mappings must be non-empty"):
        generator.generate_compose(
            [service],
            output_dir=tmp_path,
            user_overrides={"dagster": {"extra_hosts": ["  "]}},
        )
    assert (
        "extra_hosts"
        not in yaml.safe_load(generator.generate_compose([service], output_dir=tmp_path))[
            "services"
        ]["dagster"]
    )


def test_infrastructure_service_overrides_are_used_for_compose_generation() -> None:
    """The documented infrastructure.services path reaches the Compose generator."""
    from phlo.cli.commands.services.init import _get_service_overrides

    overrides = _get_service_overrides(
        {
            "services": {"dagster": {"environment": {"LEGACY": "true"}}},
            "infrastructure": {
                "services": {
                    "dagster": {
                        "extra_hosts": ["host.docker.internal:host-gateway"],
                    }
                }
            },
        }
    )

    assert overrides == {
        "dagster": {
            "environment": {"LEGACY": "true"},
            "extra_hosts": ["host.docker.internal:host-gateway"],
        }
    }


def test_nessie_compose_uses_project_warehouse_location(tmp_path) -> None:
    discovery = ServiceDiscovery()
    nessie = discovery.get_service("nessie")

    assert nessie is not None
    generator = ComposeGenerator(discovery)
    rendered = yaml.safe_load(generator.generate_compose([nessie], output_dir=tmp_path))
    environment = rendered["services"]["nessie"]["environment"]
    env = generator.generate_env(
        [nessie], env_overrides={"ICEBERG_WAREHOUSE_PATH": "s3://other-lake/warehouse"}
    )

    assert environment["nessie.catalog.warehouses.warehouse.location"] == (
        "${ICEBERG_WAREHOUSE_PATH:-s3://lake/warehouse}"
    )
    assert "ICEBERG_WAREHOUSE_PATH=s3://other-lake/warehouse" in env


def test_services_init_production_selects_the_secure_compose_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    postgres = _service("postgres", default=True)
    discovery = FakeDiscovery({postgres.name: postgres}, default_names=(postgres.name,))
    captured: dict[str, object] = {}

    class FakeComposer:
        def __init__(self, _discovery) -> None:
            pass

        def generate_compose(self, _services, _output_dir, **kwargs) -> str:
            captured.update(kwargs)
            return "services: {}\n"

        def generate_env(self, _services, env_overrides=None) -> str:
            captured["env_overrides"] = env_overrides
            return ""

        def generate_env_local(self, _services, **_kwargs) -> str:
            return ""

        def generate_gitignore(self, _services) -> str:
            return ""

        def copy_service_files(self, _services, _output_dir) -> list[str]:
            return []

    (tmp_path / "phlo.yaml").write_text(
        "env:\n  POSTGRES_USER: lakehouse\n  MINIO_ROOT_USER: object-admin\n"
    )
    monkeypatch.chdir(tmp_path)
    from phlo.cli.commands.services import init as init_module

    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", FakeComposer)

    result = CliRunner().invoke(init_module.init_cmd, ["--production"])

    assert result.exit_code == 0, result.output
    assert captured["deployment_profile"] == "production"
    assert captured["dev_mode"] is False
    assert captured["env_overrides"] == {
        "POSTGRES_USER": "lakehouse",
        "MINIO_ROOT_USER": "object-admin",
        "PHLO_ENVIRONMENT": "production",
    }


def test_select_services_to_install_respects_enabled_disabled_and_profiles() -> None:
    postgres = _service("postgres", default=True)
    minio = _service("minio", default=True)
    prometheus = _service("prometheus", profile="observability")
    grafana = _service("grafana", profile="observability")

    all_services = {s.name: s for s in [postgres, minio, prometheus, grafana]}
    default_services = [postgres, minio]

    services_to_install = select_services_to_install(
        all_services=all_services,
        default_services=default_services,
        enabled_names=["prometheus"],
        disabled_names=["minio"],
    )

    assert [s.name for s in services_to_install] == ["postgres", "prometheus", "grafana"]


def test_detect_phlo_source_path_finds_sibling_phlo_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    phlo_repo = tmp_path / "phlo"
    package_dir = phlo_repo / "src" / "phlo"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("")

    project_dir = tmp_path / "pokemon-lakehouse"
    project_dir.mkdir()

    monkeypatch.chdir(project_dir)
    monkeypatch.delenv("PHLO_DEV_SOURCE", raising=False)

    detected = detect_phlo_source_path()
    expected = os.path.relpath(package_dir, project_dir / ".phlo")
    assert detected == expected


def test_detect_phlo_source_path_accepts_repo_root_in_env_var(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    phlo_repo = tmp_path / "phlo"
    package_dir = phlo_repo / "src" / "phlo"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("")

    project_dir = tmp_path / "pokemon-lakehouse"
    project_dir.mkdir()

    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("PHLO_DEV_SOURCE", str(phlo_repo))

    detected = detect_phlo_source_path()
    expected = os.path.relpath(package_dir, project_dir / ".phlo")
    assert detected == expected


def test_compose_generator_injects_phlo_dev_mounts(tmp_path) -> None:
    class MinimalFakeDiscovery(FakeDiscovery):
        def resolve_dependencies(
            self, services: list[ServiceDefinition]
        ) -> list[ServiceDefinition]:
            return services

    service = ServiceDefinition(
        name="dagster",
        description="dagster",
        category="orchestration",
        default=True,
        phlo_dev=True,
        compose={},
    )
    generator = ComposeGenerator(cast(ServiceDiscovery, MinimalFakeDiscovery()))
    compose = yaml.safe_load(
        generator.generate_compose(
            services=[service],
            output_dir=tmp_path,
            dev_mode=True,
            phlo_src_path="../phlo/src/phlo",
        )
    )

    dagster = compose["services"]["dagster"]
    assert "../phlo/src/phlo/../..:/opt/phlo-dev:rw" in dagster["volumes"]
    assert dagster["environment"]["PHLO_DEV_MODE"] == "true"


def test_compose_generator_dev_mode_builds_phlo_services_from_source(tmp_path) -> None:
    """Dev stacks must not start an incomplete published Dagster image."""
    service = ServiceDefinition(
        name="dagster",
        description="dagster",
        category="orchestration",
        default=True,
        phlo_dev=True,
        image="ghcr.io/phlohouse/phlo-dagster:0.6.0",
        build={"context": ".", "dockerfile": "dagster/Dockerfile"},
        compose={},
    )

    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))
    compose = yaml.safe_load(
        generator.generate_compose(
            services=[service],
            output_dir=tmp_path,
            dev_mode=True,
            phlo_src_path="../phlo/src/phlo",
        )
    )

    assert "image" not in compose["services"]["dagster"]
    assert compose["services"]["dagster"]["build"]["dockerfile"] == "dagster/Dockerfile"


@pytest.mark.parametrize("service_name", ["dagster", "dagster-daemon"])
@pytest.mark.parametrize(
    ("host_platform", "expected_user", "expected_environment"),
    [
        (
            "Linux",
            None,
            {"HOME": "/opt/dagster", "PHLO_RUNTIME_UID": "1234", "PHLO_RUNTIME_GID": "2345"},
        ),
        ("Darwin", None, {}),
        ("Windows", None, {}),
    ],
)
def test_compose_generator_sets_host_user_for_project_writing_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    service_name: str,
    host_platform: str,
    expected_user: str | None,
    expected_environment: dict[str, str],
) -> None:
    monkeypatch.setattr(generator_module.platform, "system", lambda: host_platform)
    monkeypatch.setattr(generator_module.os, "getuid", lambda: 1234, raising=False)
    monkeypatch.setattr(generator_module.os, "getgid", lambda: 2345, raising=False)

    service = ServiceDefinition(
        name=service_name,
        description=service_name,
        category="orchestration",
        default=True,
        compose={"user": "root"},
    )
    unrelated = ServiceDefinition(
        name="trino",
        description="trino",
        category="query",
        default=True,
        compose={"user": "root"},
    )

    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))
    data = yaml.safe_load(
        generator.generate_compose(
            services=[service, unrelated],
            output_dir=tmp_path,
        )
    )

    assert data["services"][service_name].get("user") == expected_user
    environment = data["services"][service_name].get("environment", {})
    for key, value in expected_environment.items():
        assert environment.get(key) == value
    assert data["services"]["trino"]["user"] == "root"


@pytest.mark.parametrize(
    ("host_platform", "expected_user"),
    [
        ("Linux", "1234:2345"),
        ("Darwin", None),
        ("Windows", None),
    ],
)
def test_compose_generator_runs_phlo_api_as_host_user_on_linux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    host_platform: str,
    expected_user: str | None,
) -> None:
    monkeypatch.setattr(generator_module.platform, "system", lambda: host_platform)
    monkeypatch.setattr(generator_module.os, "getuid", lambda: 1234, raising=False)
    monkeypatch.setattr(generator_module.os, "getgid", lambda: 2345, raising=False)

    service = ServiceDefinition(
        name="phlo-api",
        description="phlo-api",
        category="api",
        default=False,
        phlo_dev=True,
        image="ghcr.io/phlohouse/phlo-api:0.14.0",
        compose={},
    )

    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))
    data = yaml.safe_load(
        generator.generate_compose(
            services=[service],
            output_dir=tmp_path,
        )
    )

    # The API reads host-owned mode-0600 secrets (.phlo/.env, .phlo/.env.local)
    # from the read-only project mount, so on Linux it runs as the host user.
    assert data["services"]["phlo-api"].get("user") == expected_user


def test_compose_generator_adds_home_to_list_environment_on_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(generator_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(generator_module.os, "getuid", lambda: 1234, raising=False)
    monkeypatch.setattr(generator_module.os, "getgid", lambda: 2345, raising=False)
    service = ServiceDefinition(
        name="dagster",
        description="dagster",
        category="orchestration",
        default=True,
        compose={"environment": ["EXISTING=value"]},
    )

    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))
    data = yaml.safe_load(generator.generate_compose([service], output_dir=tmp_path))

    assert data["services"]["dagster"]["environment"] == [
        "EXISTING=value",
        "HOME=/opt/dagster",
        "PHLO_RUNTIME_UID=1234",
        "PHLO_RUNTIME_GID=2345",
    ]


def test_compose_generator_passthrough_compose_keys(tmp_path) -> None:
    class MinimalFakeDiscovery(FakeDiscovery):
        def resolve_dependencies(
            self, services: list[ServiceDefinition]
        ) -> list[ServiceDefinition]:
            return services

    service = ServiceDefinition(
        name="trino",
        description="trino",
        category="query",
        default=True,
        compose={
            "mem_limit": "3g",
            "cpus": "2.0",
            "ulimits": {"nofile": {"soft": 16384, "hard": 16384}},
        },
    )

    generator = ComposeGenerator(cast(ServiceDiscovery, MinimalFakeDiscovery()))
    compose_yaml = generator.generate_compose(
        services=[service],
        output_dir=tmp_path,
    )

    data = yaml.safe_load(compose_yaml)
    trino = data["services"]["trino"]
    assert trino["mem_limit"] == "3g"
    assert trino["cpus"] == "2.0"
    assert trino["ulimits"] == {"nofile": {"soft": 16384, "hard": 16384}}


def test_compose_generator_production_profile_hides_core_host_ports_and_requires_credentials(
    tmp_path,
) -> None:
    class MinimalFakeDiscovery(FakeDiscovery):
        def resolve_dependencies(
            self, services: list[ServiceDefinition]
        ) -> list[ServiceDefinition]:
            return services

    services = [
        ServiceDefinition(
            name=name,
            description=name,
            category="core",
            default=True,
            compose={
                "ports": ["10000:5432"],
                "environment": {
                    "POSTGRES_USER": "${POSTGRES_USER:-phlo}",
                    "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD:-phlo}",
                    "MINIO_ROOT_USER": "${MINIO_ROOT_USER:-minio}",
                    "MINIO_ROOT_PASSWORD": "${MINIO_ROOT_PASSWORD:-minio123}",
                },
            },
        )
        for name in ("postgres", "minio", "nessie", "trino", "dagster")
    ]

    generator = ComposeGenerator(cast(ServiceDiscovery, MinimalFakeDiscovery()))
    data = yaml.safe_load(
        generator.generate_compose(
            services=services,
            output_dir=tmp_path,
            deployment_profile="production",
        )
    )

    for name in ("postgres", "minio", "nessie", "trino"):
        assert "ports" not in data["services"][name]
        environment = data["services"][name]["environment"]
        assert (
            environment["POSTGRES_USER"]
            == "${POSTGRES_USER:?Phlo production requires POSTGRES_USER}"
        )
        assert environment["POSTGRES_PASSWORD"] == (
            "${POSTGRES_PASSWORD:?Phlo production requires POSTGRES_PASSWORD}"
        )
        assert (
            environment["MINIO_ROOT_USER"]
            == "${MINIO_ROOT_USER:?Phlo production requires MINIO_ROOT_USER}"
        )
        assert environment["MINIO_ROOT_PASSWORD"] == (
            "${MINIO_ROOT_PASSWORD:?Phlo production requires MINIO_ROOT_PASSWORD}"
        )

    assert data["services"]["dagster"]["ports"] == ["10000:5432"]


def test_compose_generator_development_profile_keeps_core_host_ports(tmp_path) -> None:
    service = ServiceDefinition(
        name="postgres",
        description="postgres",
        category="core",
        default=True,
        compose={"ports": ["10000:5432"]},
    )

    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))
    data = yaml.safe_load(generator.generate_compose([service], output_dir=tmp_path))

    assert data["services"]["postgres"]["ports"] == ["10000:5432"]


def test_development_profile_keeps_bundled_backends_open(tmp_path) -> None:
    """The regulated production boundary must not change development access."""
    discovery = ServiceDiscovery()
    data = yaml.safe_load(
        ComposeGenerator(discovery).generate_compose(
            discovery.get_default_services(), output_dir=tmp_path
        )
    )

    for name in ("postgres", "minio", "nessie", "trino"):
        assert data["services"][name]["ports"]

    for name in ("minio", "nessie", "trino"):
        labels = data["services"][name]["labels"]
        assert labels["traefik.enable"] == "true"
    assert "traefik.enable" not in data["services"]["postgres"].get("labels", {})


@pytest.mark.parametrize(
    ("dev_mode", "service_dev_mode"),
    [(True, False), (False, True), (True, True)],
)
def test_compose_generator_rejects_dev_options_for_production_profile(
    tmp_path,
    dev_mode: bool,
    service_dev_mode: bool,
) -> None:
    service = ServiceDefinition(
        name="dagster",
        description="dagster",
        category="orchestration",
        default=True,
        phlo_dev=True,
        compose={},
    )
    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))

    with pytest.raises(ValueError, match="production.*dev_mode.*service_dev_mode"):
        generator.generate_compose(
            [service],
            output_dir=tmp_path,
            dev_mode=dev_mode,
            service_dev_mode=service_dev_mode,
            phlo_src_path="../phlo/src/phlo",
            deployment_profile="production",
        )


def test_production_profile_neutralizes_protected_service_environment_overrides(
    tmp_path,
) -> None:
    service = ServiceDefinition(
        name="postgres",
        description="postgres",
        category="core",
        default=True,
        compose={"environment": {}},
    )
    overrides = {
        "postgres": {
            "environment": {
                "POSTGRES_USER": "phlo",
                "POSTGRES_PASSWORD": "literal-password",
                "MINIO_ROOT_USER": "${MINIO_ROOT_USER:-minio}",
                "MINIO_ROOT_PASSWORD": "${MINIO_ROOT_PASSWORD:-override-password}",
                "UNRELATED": "preserved",
            }
        }
    }

    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))
    data = yaml.safe_load(
        generator.generate_compose(
            [service],
            output_dir=tmp_path,
            user_overrides=overrides,
            deployment_profile="production",
        )
    )
    environment = data["services"]["postgres"]["environment"]

    assert environment == {
        "POSTGRES_USER": "${POSTGRES_USER:?Phlo production requires POSTGRES_USER}",
        "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD:?Phlo production requires POSTGRES_PASSWORD}",
        "MINIO_ROOT_USER": "${MINIO_ROOT_USER:?Phlo production requires MINIO_ROOT_USER}",
        "MINIO_ROOT_PASSWORD": (
            "${MINIO_ROOT_PASSWORD:?Phlo production requires MINIO_ROOT_PASSWORD}"
        ),
        "UNRELATED": "preserved",
    }


def test_production_profile_normalizes_list_form_protected_environment_assignments(
    tmp_path,
) -> None:
    service = ServiceDefinition(
        name="postgres",
        description="postgres",
        category="core",
        default=True,
        compose={
            "environment": [
                "POSTGRES_USER=literal-user",
                "POSTGRES_PASSWORD=phlo",
                "MINIO_ROOT_USER=${MINIO_ROOT_USER:-override-user}",
                "MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD:-minio123}",
                "UNRELATED=preserved",
            ]
        },
    )
    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))

    data = yaml.safe_load(
        generator.generate_compose([service], output_dir=tmp_path, deployment_profile="production")
    )

    assert data["services"]["postgres"]["environment"] == [
        "POSTGRES_USER=${POSTGRES_USER:?Phlo production requires POSTGRES_USER}",
        "POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?Phlo production requires POSTGRES_PASSWORD}",
        "MINIO_ROOT_USER=${MINIO_ROOT_USER:?Phlo production requires MINIO_ROOT_USER}",
        "MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD:?Phlo production requires MINIO_ROOT_PASSWORD}",
        "UNRELATED=preserved",
    ]


def test_production_profile_removes_internal_traefik_labels_but_preserves_other_labels(
    tmp_path,
) -> None:
    service = ServiceDefinition(
        name="trino",
        description="trino",
        category="core",
        default=True,
        compose={
            "labels": {
                "traefik.enable": "true",
                "traefik.http.routers.trino.rule": "Host(`trino.phlo.localhost`)",
                "phlo.metrics.enabled": "false",
            }
        },
    )
    generator = ComposeGenerator(cast(ServiceDiscovery, FakeDiscovery()))

    production = yaml.safe_load(
        generator.generate_compose([service], output_dir=tmp_path, deployment_profile="production")
    )
    development = yaml.safe_load(generator.generate_compose([service], output_dir=tmp_path))

    assert production["services"]["trino"]["labels"] == {"phlo.metrics.enabled": "false"}
    assert development["services"]["trino"]["labels"] == service.compose["labels"]


def test_production_profile_renders_bundled_core_without_public_ports_or_credential_defaults(
    tmp_path,
) -> None:
    discovery = ServiceDiscovery()
    generator = ComposeGenerator(discovery)
    compose = generator.generate_compose(
        discovery.get_default_services(),
        output_dir=tmp_path,
        deployment_profile="production",
    )
    data = yaml.safe_load(compose)

    for name in ("postgres", "minio", "nessie", "trino"):
        assert "ports" not in data["services"][name]
        labels = data["services"][name].get("labels", {})
        assert not any(str(label).startswith("traefik.") for label in labels)
    assert data["services"]["dagster"]["ports"] == ["${DAGSTER_PORT:-10006}:3000"]
    assert data["services"]["dagster"]["labels"]["traefik.enable"] == "true"
    assert "${POSTGRES_PASSWORD:-phlo}" not in compose
    assert "${MINIO_ROOT_PASSWORD:-minio123}" not in compose


def test_compose_generator_declares_named_volumes(tmp_path) -> None:
    class MinimalFakeDiscovery(FakeDiscovery):
        def resolve_dependencies(
            self, services: list[ServiceDefinition]
        ) -> list[ServiceDefinition]:
            return services

    service = ServiceDefinition(
        name="postgres",
        description="postgres",
        category="core",
        default=True,
        compose={"volumes": ["postgres-data:/var/lib/postgresql/data", "./logs:/logs"]},
    )

    generator = ComposeGenerator(cast(ServiceDiscovery, MinimalFakeDiscovery()))
    compose_yaml = generator.generate_compose(
        services=[service],
        output_dir=tmp_path,
    )

    data = yaml.safe_load(compose_yaml)
    assert data["volumes"] == {"postgres-data": {}}


def test_generate_env_pins_package_versions_for_service_builds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keeps repeated service builds on the installed Phlo version, not Docker's stale latest."""
    versions = {"phlo": "9.8.7", "phlo-api": "3.2.1"}
    monkeypatch.setattr("phlo.plugins.compose.env.version", versions.__getitem__)
    service = ServiceDefinition(
        name="phlo-api",
        description="api",
        category="api",
        default=True,
        env_vars={
            "PHLO_VERSION": {
                "default": "",
                "package": "phlo",
                "description": "Phlo version to install",
            },
            "PHLO_API_VERSION": {
                "default": "",
                "package": "phlo-api",
                "description": "phlo-api version to install",
            },
        },
    )

    env = generate_env([service])

    assert "PHLO_VERSION=9.8.7" in env
    assert "PHLO_API_VERSION=3.2.1" in env


def test_generate_env_local_keeps_known_non_secret_values_out_of_local_overrides() -> None:
    service = ServiceDefinition(
        name="postgres",
        description="postgres",
        category="core",
        default=True,
        env_vars={
            "POSTGRES_PORT": {
                "default": "5432",
                "description": "Postgres port",
            },
            "POSTGRES_PASSWORD": {
                "default": "postgres",
                "description": "Postgres password",
                "secret": True,
            },
        },
    )

    env_local = generate_env_local(
        [service],
        existing_values={
            "POSTGRES_PORT": "15432",
            "POSTGRES_PASSWORD": "secret",
            "CUSTOM_LOCAL": "kept",
        },
    )

    assert "POSTGRES_PASSWORD=secret" in env_local
    assert "CUSTOM_LOCAL=kept" in env_local
    assert "POSTGRES_PORT=15432" not in env_local


def test_generate_env_local_generates_new_secret_values() -> None:
    service = ServiceDefinition(
        name="postgres",
        description="postgres",
        category="core",
        default=True,
        env_vars={
            "POSTGRES_PASSWORD": {
                "default": "postgres",
                "description": "Postgres password",
                "secret": True,
            },
        },
    )

    env_local = generate_env_local([service])

    assert "POSTGRES_PASSWORD=postgres" not in env_local
    assert re.search(r"POSTGRES_PASSWORD=phlo_[A-Za-z0-9_-]{32,}", env_local)


def test_generate_env_local_uses_s3_safe_minio_root_password() -> None:
    service = ServiceDefinition(
        name="minio",
        description="minio",
        category="core",
        default=True,
        env_vars={
            "MINIO_ROOT_PASSWORD": {
                "default": "minio123",
                "description": "MinIO root password",
                "secret": True,
            },
        },
    )

    env_local = generate_env_local([service])

    assert "MINIO_ROOT_PASSWORD=minio123" not in env_local
    assert re.search(r"MINIO_ROOT_PASSWORD=[a-f0-9]{40}\n", env_local)


def test_generate_env_local_preserves_existing_secret_values() -> None:
    service = ServiceDefinition(
        name="postgres",
        description="postgres",
        category="core",
        default=True,
        env_vars={
            "POSTGRES_PASSWORD": {
                "default": "postgres",
                "description": "Postgres password",
                "secret": True,
            },
        },
    )

    env_local = generate_env_local(
        [service],
        existing_values={"POSTGRES_PASSWORD": "existing-secret"},
    )

    assert "POSTGRES_PASSWORD=existing-secret" in env_local


def test_compose_generator_resolves_source_path_dev_volumes(tmp_path) -> None:
    class MinimalFakeDiscovery(FakeDiscovery):
        def resolve_dependencies(
            self, services: list[ServiceDefinition]
        ) -> list[ServiceDefinition]:
            return services

    service_source = tmp_path / "packages" / "phlo-observatory"
    service_source.mkdir(parents=True)
    service = ServiceDefinition(
        name="observatory",
        description="observatory",
        category="orchestration",
        default=True,
        source_path=service_source,
        dev={"volumes": ["{source_path}:/app", "/app/node_modules"]},
    )

    generator = ComposeGenerator(cast(ServiceDiscovery, MinimalFakeDiscovery()))
    compose_yaml = generator.generate_compose(
        services=[service],
        output_dir=tmp_path / ".phlo",
        dev_mode=True,
        service_dev_mode=True,
    )

    data = yaml.safe_load(compose_yaml)
    observatory = data["services"]["observatory"]
    expected_source = os.path.relpath(service_source, tmp_path / ".phlo")
    assert observatory["volumes"] == [f"{expected_source}:/app", "/app/node_modules"]


def test_services_init_excludes_profile_services_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    postgres = _service("postgres", default=True)
    prometheus = _service("prometheus", profile="observability")
    fake_discovery = FakeDiscovery(
        {postgres.name: postgres, prometheus.name: prometheus},
        default_names=(postgres.name,),
    )

    class FakeComposer:
        def __init__(self, _discovery):
            pass

        def generate_compose(self, services, output_dir, **_kwargs):
            names = ",".join(sorted(s.name for s in services))
            return f"services: {names}\n"

        def generate_env(self, _services, env_overrides=None):
            return ""

        def generate_env_local(self, _services, env_overrides=None, existing_values=None):
            return ""

        def generate_gitignore(self, _services):
            return ""

        def copy_service_files(self, _services, _output_dir):
            return []

    monkeypatch.chdir(tmp_path)
    from phlo.cli.commands.services import init as init_module

    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", FakeComposer)

    result = CliRunner().invoke(init_module.init_cmd, [])
    assert result.exit_code == 0
    compose = (tmp_path / ".phlo" / "docker-compose.yml").read_text()
    assert "postgres" in compose
    assert "prometheus" not in compose


def test_services_init_reports_malformed_phlo_yaml_without_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / "phlo.yaml").write_text("name: [unterminated\n")
    monkeypatch.chdir(tmp_path)

    from phlo.cli.commands.services import init as init_module

    result = CliRunner().invoke(init_module.init_cmd, ["--force", "--no-dev"])

    assert result.exit_code == 1
    assert "invalid phlo.yaml" in result.output
    assert "Traceback" not in result.output
    assert not isinstance(result.exception, yaml.YAMLError)


def test_services_init_allows_logs_only_phlo_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A fresh `phlo init` can create .phlo/logs before infrastructure is rendered."""
    postgres = _service("postgres", default=True)
    fake_discovery = FakeDiscovery({postgres.name: postgres}, default_names=(postgres.name,))

    class FakeComposer:
        def __init__(self, _discovery):
            pass

        def generate_compose(self, services, output_dir, **_kwargs):
            return "services: {}\n"

        def generate_env(self, _services, env_overrides=None):
            return ""

        def generate_env_local(self, _services, env_overrides=None, existing_values=None):
            return ""

        def generate_gitignore(self, _services):
            return ""

        def copy_service_files(self, _services, _output_dir):
            return []

    (tmp_path / ".phlo" / "logs").mkdir(parents=True)
    (tmp_path / ".phlo" / "logs" / "20260503.log").write_text("{}\n")
    monkeypatch.chdir(tmp_path)
    from phlo.cli.commands.services import init as init_module

    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", FakeComposer)

    result = CliRunner().invoke(init_module.init_cmd, [])

    assert result.exit_code == 0
    assert (tmp_path / ".phlo" / "docker-compose.yml").exists()


def test_services_init_includes_requested_profile_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    postgres = _service("postgres", default=True)
    prometheus = _service("prometheus", profile="observability")
    fake_discovery = FakeDiscovery(
        {postgres.name: postgres, prometheus.name: prometheus},
        default_names=(postgres.name,),
    )

    class FakeComposer:
        def __init__(self, _discovery):
            pass

        def generate_compose(self, services, output_dir, **_kwargs):
            names = ",".join(sorted(s.name for s in services))
            return f"services: {names}\n"

        def generate_env(self, _services, env_overrides=None):
            return ""

        def generate_env_local(self, _services, env_overrides=None, existing_values=None):
            return ""

        def generate_gitignore(self, _services):
            return ""

        def copy_service_files(self, _services, _output_dir):
            return []

    monkeypatch.chdir(tmp_path)
    from phlo.cli.commands.services import init as init_module

    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", FakeComposer)

    result = CliRunner().invoke(init_module.init_cmd, ["--profile", "observability"])
    assert result.exit_code == 0
    compose = (tmp_path / ".phlo" / "docker-compose.yml").read_text()
    assert "postgres" in compose
    assert "prometheus" in compose


def test_services_init_uses_lifecycle_planner_for_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    postgres = _service("postgres", default=True)
    grafana = _service("grafana", profile="observability")
    fake_discovery = FakeDiscovery(
        {postgres.name: postgres, grafana.name: grafana},
        default_names=(postgres.name,),
    )
    copied: list[str] = []

    class FakeComposer:
        def __init__(self, _discovery):
            pass

        def generate_compose(self, selected, *_args, **_kwargs):
            copied.extend(service.name for service in selected)
            return "services: {}\n"

        def generate_env(self, _services, env_overrides=None):
            return ""

        def generate_env_local(self, _services, env_overrides=None, existing_values=None):
            return ""

        def generate_gitignore(self, _services):
            return ""

        def copy_service_files(self, *_args, **_kwargs):
            return []

    monkeypatch.chdir(tmp_path)
    from phlo.cli.commands.services import init as init_module

    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", FakeComposer)

    result = CliRunner().invoke(init_module.init_cmd, ["--profile", "observability"])

    assert result.exit_code == 0
    assert copied == ["postgres", "grafana"]


def test_services_init_production_writes_env_local_at_0600(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    postgres = _service("postgres", default=True)
    discovery = FakeDiscovery({postgres.name: postgres}, default_names=(postgres.name,))

    class _FakeComposer:
        def __init__(self, _discovery) -> None:
            pass

        def generate_compose(self, _services, _output_dir, **_kwargs) -> str:
            return "# Dev mode: false\nservices: {}\n"

        def generate_env(self, _services, **kwargs) -> str:
            return ""

        def generate_env_local(self, _services, **kwargs) -> str:
            return "POSTGRES_PASSWORD=independent-secret\n"

        def generate_gitignore(self, _services) -> str:
            return ""

        def copy_service_files(self, _services, _output_dir) -> list[str]:
            return []

    (tmp_path / "phlo.yaml").write_text(
        "env:\n  POSTGRES_USER: lakehouse\n  MINIO_ROOT_USER: object-admin\n"
    )
    monkeypatch.chdir(tmp_path)
    from phlo.cli.commands.services import init as init_module

    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", _FakeComposer)

    result = CliRunner().invoke(init_module.init_cmd, ["--production"])
    assert result.exit_code == 0, result.output

    env_local = tmp_path / ".phlo" / ".env.local"
    assert env_local.exists()
    assert env_local.stat().st_mode & 0o7777 == 0o600
    assert "independent-secret" in env_local.read_text()


def test_services_init_replaces_permissive_env_local_at_0600(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    postgres = _service("postgres", default=True)
    discovery = FakeDiscovery({postgres.name: postgres}, default_names=(postgres.name,))

    class _FakeComposer:
        def __init__(self, _discovery) -> None:
            pass

        def generate_compose(self, _services, _output_dir, **_kwargs) -> str:
            return "# Dev mode: false\nservices: {}\n"

        def generate_env(self, _services, **kwargs) -> str:
            return ""

        def generate_env_local(self, _services, **kwargs) -> str:
            return "POSTGRES_PASSWORD=independent-secret\n"

        def generate_gitignore(self, _services) -> str:
            return ""

        def copy_service_files(self, _services, _output_dir) -> list[str]:
            return []

    (tmp_path / "phlo.yaml").write_text(
        "env:\n  POSTGRES_USER: lakehouse\n  MINIO_ROOT_USER: object-admin\n"
    )
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    env_local = phlo_dir / ".env.local"
    env_local.write_text("POSTGRES_PASSWORD=independent-secret\n")
    env_local.chmod(0o644)

    monkeypatch.chdir(tmp_path)
    from phlo.cli.commands.services import init as init_module

    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", _FakeComposer)

    result = CliRunner().invoke(init_module.init_cmd, ["--force", "--production"])
    assert result.exit_code == 0, result.output
    assert env_local.exists()
    assert env_local.stat().st_mode & 0o7777 == 0o600


def test_production_compose_rejects_shared_or_default_workload_identity_references(
    tmp_path,
) -> None:
    postgres = _service("postgres", default=True)
    discovery = FakeDiscovery({postgres.name: postgres}, default_names=(postgres.name,))
    generator = ComposeGenerator(cast(ServiceDiscovery, discovery))

    with pytest.raises(ValueError, match="production workload identities"):
        generator.generate_compose(
            services=[postgres],
            output_dir=tmp_path,
            deployment_profile="production",
            env_values={
                "TRINO_QUERY_ACCESS_KEY": "root",
            },
        )

    # Distinct non-default references render fine.
    compose = generator.generate_compose(
        services=[postgres],
        output_dir=tmp_path,
        deployment_profile="production",
        env_values={
            "PHLO_SERVICE_CREDENTIALS_FILE": "/run/secrets/workload.json",
            "DAGSTER_MINIO_ACCESS_KEY": "d-access",
            "DAGSTER_MINIO_SECRET_KEY": "d-secret",
            "DAGSTER_TRINO_USER": "d-trino",
            "DAGSTER_POSTGRES_USER": "d-pg",
            "DAGSTER_POSTGRES_PASSWORD": "d-pg-pass",
            "TRINO_QUERY_ACCESS_KEY": "q-access",
            "TRINO_QUERY_SECRET_KEY": "q-secret",
            "TRINO_USER": "q-user",
            "TRINO_ROLE": "q_role",
            "NESSIE_CATALOG_ACCESS_KEY": "c-access",
            "NESSIE_CATALOG_SECRET_KEY": "c-secret",
            "QUARKUS_DATASOURCE_USERNAME": "c-pg",
            "QUARKUS_DATASOURCE_PASSWORD": "c-pg-pass",
            "MAINTENANCE_TRINO_USER": "m-user",
            "MAINTENANCE_TRINO_ROLE": "m_role",
            "MAINTENANCE_ACCESS_KEY": "m-access",
            "MAINTENANCE_SECRET_KEY": "m-secret",
        },
    )
    assert "services:" in compose


class _RecordingComposer:
    """Fake composer that records generated files and env overrides."""

    def __init__(self, _discovery):
        self.env_overrides: dict | None = None

    def generate_compose(self, services, output_dir, **_kwargs):
        return "services: {}\n"

    def generate_env(self, _services, env_overrides=None):
        self.env_overrides = env_overrides
        return ""

    def generate_env_local(self, _services, env_overrides=None, existing_values=None):
        return ""

    def generate_gitignore(self, _services):
        return ""

    def copy_service_files(self, _services, _output_dir):
        return []


def _run_services_init_with_composer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    composer_cls: type,
) -> None:
    postgres = _service("postgres", default=True)
    fake_discovery = FakeDiscovery({postgres.name: postgres}, default_names=(postgres.name,))
    monkeypatch.chdir(tmp_path)
    from phlo.cli.commands.services import init as init_module

    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", composer_cls)
    result = CliRunner().invoke(init_module.init_cmd, [])
    assert result.exit_code == 0, result.output


def test_services_init_stages_uv_lock_metadata_for_lock_aware_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """uv-managed projects get staged lock metadata and a lock-aware build flag."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "lake"\nversion = "0.1.0"\n')
    (tmp_path / "uv.lock").write_text("version = 1\n")
    composer = _RecordingComposer.__new__(_RecordingComposer)

    _run_services_init_with_composer(monkeypatch, tmp_path, lambda discovery: composer)

    phlo_dir = tmp_path / ".phlo"
    assert (phlo_dir / "pyproject.toml").read_text().startswith("[project]")
    assert (phlo_dir / "uv.lock").read_text().startswith("version = 1")
    assert composer.env_overrides is not None
    assert composer.env_overrides.get("PHLO_UV_LOCKED") == "true"


def test_services_init_without_uv_metadata_keeps_default_build_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Projects without uv metadata are not flagged lock-aware and stage nothing."""
    composer = _RecordingComposer.__new__(_RecordingComposer)

    _run_services_init_with_composer(monkeypatch, tmp_path, lambda discovery: composer)

    phlo_dir = tmp_path / ".phlo"
    assert not (phlo_dir / "pyproject.toml").exists()
    assert not (phlo_dir / "uv.lock").exists()
    assert composer.env_overrides is not None
    assert "PHLO_UV_LOCKED" not in composer.env_overrides


def test_services_init_respects_explicit_uv_lock_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """An explicit phlo.yaml env override wins over the staged-lock default."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "lake"\nversion = "0.1.0"\n')
    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / "phlo.yaml").write_text(
        'name: lake\ndescription: lake\nenv:\n  PHLO_UV_LOCKED: "false"\n'
    )
    composer = _RecordingComposer.__new__(_RecordingComposer)

    _run_services_init_with_composer(monkeypatch, tmp_path, lambda discovery: composer)

    assert composer.env_overrides is not None
    assert composer.env_overrides.get("PHLO_UV_LOCKED") == "false"


def test_generate_gitignore_ignores_staged_uv_lock_metadata(tmp_path) -> None:
    """Staged lock copies are regenerable artifacts and stay out of version control."""
    discovery = ServiceDiscovery()
    dagster = discovery.get_service("dagster")
    assert dagster is not None

    content = ComposeGenerator(discovery).generate_gitignore([dagster])

    assert "# Staged uv lock metadata (source of truth lives at the project root)" in content
    assert "pyproject.toml" in content
    assert "uv.lock" in content
