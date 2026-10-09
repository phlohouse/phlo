"""Bundled service manifests never start with repository-known credentials.

Scans every packaged service manifest so a new provider cannot reintroduce a
static secret fallback, a direct Docker socket mount, or an unwaived root
runtime user (phlohouse/phlo#984).
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_GLOB = "packages/*/src/*/*.yaml"
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|SECRET_KEY|SECRET_ACCESS_KEY|TOKEN|CREDENTIALS)$")
# Innermost `${VAR:-default}`: a default that is itself an expression is
# checked when the inner expression is matched.
LITERAL_DEFAULT = re.compile(r"\$\{(?P<var>[A-Z][A-Z0-9_]*):-(?P<default>[^{}$]*)\}")
REQUIRED = re.compile(r"\$\{(?P<var>[A-Z][A-Z0-9_]*):\?")
DOCKER_SOCKET = "/var/run/docker.sock"
# The only services allowed to reach the Docker API: read-only proxies on an
# internal network.
DOCKER_API_PROXIES = {"alloy-docker-proxy", "traefik-docker-proxy"}
ROOT_USERS = {"0", "root"}


@cache
def _manifests() -> dict[str, tuple[Path, dict[str, Any], str]]:
    manifests: dict[str, tuple[Path, dict[str, Any], str]] = {}
    for path in sorted(REPO_ROOT.glob(MANIFEST_GLOB)):
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if isinstance(data, dict) and "name" in data and "compose" in data:
            manifests[data["name"]] = (path, data, text)
    return manifests


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _dependency_closure(name: str, seen: set[str] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    if name in seen or name not in _manifests():
        return seen
    seen.add(name)
    for dependency in _manifests()[name][1].get("depends_on") or []:
        _dependency_closure(dependency, seen)
    return seen


def _generated_secrets(names: set[str]) -> set[str]:
    return {
        variable
        for name in names
        for variable, spec in (_manifests()[name][1].get("env_vars") or {}).items()
        if isinstance(spec, dict) and spec.get("secret") is True
    }


def test_manifest_scan_covers_bundled_services() -> None:
    assert {"postgres", "minio", "superset", "hasura", "polaris"} <= set(_manifests())


def test_no_secret_variable_has_a_literal_fallback() -> None:
    offenders = [
        f"{_relative(path)}: ${{{match['var']}:-{match['default']}}}"
        for path, _data, text in _manifests().values()
        for match in LITERAL_DEFAULT.finditer(text)
        if SECRET_NAME.search(match["var"]) and match["default"]
    ]
    assert offenders == [], "secret variables must use ${VAR:?...} instead of a default"


def test_secret_env_vars_declare_no_default_value() -> None:
    offenders = [
        f"{_relative(path)}: {variable}"
        for path, data, _text in _manifests().values()
        for variable, spec in (data.get("env_vars") or {}).items()
        if isinstance(spec, dict) and spec.get("secret") is True and spec.get("default")
    ]
    assert offenders == [], "generated secrets must not carry a repository-known default"


def test_required_variables_are_generated_by_the_service_or_its_dependencies() -> None:
    """`phlo services init` generates every value a selected manifest requires."""
    missing = []
    for name, (path, _data, text) in _manifests().items():
        required = {match["var"] for match in REQUIRED.finditer(text)}
        absent = required - _generated_secrets(_dependency_closure(name))
        if absent:
            missing.append(f"{_relative(path)}: {sorted(absent)}")
    assert missing == []


def test_only_docker_api_proxies_mount_the_docker_socket() -> None:
    offenders = [
        _relative(path)
        for name, (path, data, _text) in _manifests().items()
        if name not in DOCKER_API_PROXIES
        and any(DOCKER_SOCKET in str(volume) for volume in data["compose"].get("volumes") or [])
    ]
    assert offenders == []


@pytest.mark.parametrize("proxy", sorted(DOCKER_API_PROXIES))
def test_docker_api_proxies_are_read_only_and_internal(proxy: str) -> None:
    _path, data, _text = _manifests()[proxy]
    compose = data["compose"]
    assert compose["environment"]["POST"] == "0"
    assert compose["volumes"] == [f"{DOCKER_SOCKET}:{DOCKER_SOCKET}:ro"]
    assert "ports" not in compose
    (network,) = compose["networks"]
    assert data["networks"][network] == {"internal": True}


def _runtime_exceptions() -> set[str]:
    register = yaml.safe_load(
        (REPO_ROOT / "security/container-waivers.yml").read_text(encoding="utf-8")
    )
    return {
        entry["service"]
        for entry in register.get("runtime_exceptions") or []
        if entry.get("exception") == "root_user"
    }


def test_long_lived_services_run_as_non_root_unless_waived() -> None:
    waived = _runtime_exceptions()
    offenders = [
        _relative(path)
        for name, (path, data, _text) in _manifests().items()
        if data["compose"].get("restart", "unless-stopped") != "no"
        and str(data["compose"].get("user", "")).split(":", 1)[0] in ROOT_USERS
        and name not in waived
    ]
    assert offenders == []


def test_runtime_exceptions_name_bundled_services_that_still_need_them() -> None:
    for service in _runtime_exceptions():
        _path, data, _text = _manifests()[service]
        assert str(data["compose"].get("user", "")).split(":", 1)[0] in ROOT_USERS, service


def test_hasura_console_and_dev_mode_are_local_development_only() -> None:
    _path, data, _text = _manifests()["hasura"]
    environment = data["compose"]["environment"]
    assert environment["HASURA_GRAPHQL_ENABLE_CONSOLE"] == "false"
    assert environment["HASURA_GRAPHQL_DEV_MODE"] == "false"
    assert data["dev"]["environment"]["HASURA_GRAPHQL_ENABLE_CONSOLE"] == "true"
