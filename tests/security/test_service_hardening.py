"""Security contracts for bundled manifests and their generated Compose stack."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery

ROOT = Path(__file__).resolve().parents[2]


def manifests():
    for path in (ROOT / "packages").glob("*/src/*/*.yaml"):
        document = yaml.safe_load(path.read_text())
        if isinstance(document, dict) and "name" in document and "compose" in document:
            yield path, document


def test_bundled_manifests_have_no_known_secret_fallbacks() -> None:
    # Empty optional auth values and references to another required secret are
    # not known credentials. Check nested expressions too, without logging values.
    expression = re.compile(r"\$\{([A-Z0-9_]+):-([^}]*)")
    failures = []
    for path, document in manifests():
        for variable, fallback in expression.findall(path.read_text()):
            if (
                re.search(r"PASSWORD|SECRET|TOKEN|CREDENTIAL", variable)
                and fallback
                and not fallback.startswith("${")
            ):
                failures.append(f"{path.relative_to(ROOT)}: {variable}")
        for name, config in document.get("env_vars", {}).items():
            if config.get("secret") and config.get("default"):
                failures.append(f"{path.relative_to(ROOT)}: declared {name}")
    assert not failures, failures


def test_socket_and_root_exceptions_are_bounded_by_runtime_waivers() -> None:
    register = yaml.safe_load((ROOT / "security/container-waivers.yml").read_text())
    waivers = {item["service"]: item for item in register["runtime_waivers"]}
    used = set()
    for path, document in manifests():
        compose = document.get("compose", {})
        socket = any("/var/run/docker.sock" in mount for mount in compose.get("volumes", []))
        root = str(compose.get("user", "")).split(":")[0] == "0"
        if not socket and (not root or compose.get("restart") == "no"):
            continue
        name = document["name"]
        assert name in waivers, path
        waiver = waivers[name]
        used.add(name)
        assert waiver["image"] == document["image"]
        assert waiver["review_by"] >= datetime.now(UTC).date()
        assert waiver["rationale"] and waiver["compensating_control"]
        if socket:
            assert name in {"alloy-docker", "traefik-docker"}
            assert not compose.get("ports")
            assert compose["read_only"] and compose["cap_drop"] == ["ALL"]
            assert all(document["networks"][n]["internal"] for n in compose["networks"])
    assert used == set(waivers)


@pytest.mark.parametrize(
    "service_dev,production,expected",
    [(False, False, "false"), (True, False, "true"), (False, True, "false")],
)
def test_hasura_console_requires_explicit_service_development(
    tmp_path, service_dev, production, expected
):
    service = ServiceDefinition.from_yaml(
        ROOT / "packages/phlo-hasura/src/phlo_hasura/service.yaml"
    )
    rendered = yaml.safe_load(
        ComposeGenerator(ServiceDiscovery()).generate_compose(
            [service],
            tmp_path,
            service_dev_mode=service_dev,
            deployment_profile="production" if production else "development",
        )
    )
    environment = rendered["services"]["hasura"]["environment"]
    assert environment["HASURA_GRAPHQL_ENABLE_CONSOLE"] == expected
    assert environment["HASURA_GRAPHQL_DEV_MODE"] == expected


def test_generated_proxy_networks_and_one_shot_dependencies(tmp_path):
    discovery = ServiceDiscovery()
    # Use the public rendering contract with the bundled authored definitions.
    definitions = list(discovery.discover().values())
    rendered = yaml.safe_load(ComposeGenerator(discovery).generate_compose(definitions, tmp_path))
    for client in ("alloy", "traefik"):
        proxy = f"{client}-docker"
        assert rendered["networks"][proxy] == {"internal": True}
        assert proxy in rendered["services"][client]["depends_on"]
        assert rendered["services"][proxy]["networks"] == [proxy]
        assert rendered["services"][proxy]["profiles"] == rendered["services"][client]["profiles"]
    for service, setup in (
        ("loki", "loki-setup"),
        ("prometheus", "prometheus-setup"),
        ("polaris", "polaris-bootstrap"),
    ):
        assert (
            rendered["services"][service]["depends_on"][setup]["condition"]
            == "service_completed_successfully"
        )
