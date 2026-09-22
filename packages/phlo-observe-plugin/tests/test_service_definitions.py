"""Tests for the phlo-observe-plugin service definitions.

``service.yaml`` and ``db-setup.yaml`` must parse through the shared service
plugin machinery and carry the fields compose generation relies on.
"""

from __future__ import annotations

from pathlib import Path

from phlo.plugins.discovery._service_definition import ServiceDefinition

_PKG_SRC = Path(__file__).resolve().parent.parent / "src" / "phlo_observe_plugin"


def test_observer_service_definition_parses() -> None:
    definition = ServiceDefinition.from_yaml(_PKG_SRC / "service.yaml")
    assert definition.name == "phlo-observer"
    assert definition.image is not None
    assert definition.image.startswith("ghcr.io/phlohouse/phlo-observe/phlo-observer:0.2.2@sha256:")
    assert definition.build is None

    compose = definition.compose
    ports = compose.get("ports") or {}
    assert "8080" in str(ports)
    healthcheck = compose.get("healthcheck") or {}
    assert "/health/live" in str(healthcheck)


def test_observer_service_declares_database_url_env() -> None:
    definition = ServiceDefinition.from_yaml(_PKG_SRC / "service.yaml")
    env_names = set(definition.env_vars)
    assert "PHLO_OBSERVER_DB" in env_names
    assert definition.env_vars["OBSERVE_HTTP_ENDPOINT"]["default"] == (
        "http://localhost:10010/v1/events"
    )
    assert "OBSERVE_HTTP_TOKEN" in env_names


def test_db_setup_definition_parses() -> None:
    definition = ServiceDefinition.from_yaml(_PKG_SRC / "db-setup.yaml")
    assert definition.name == "phlo-observer-db-setup"
    assert definition.image is not None
    assert "@sha256:" in definition.image
    assert "postgres" in definition.image
    # Without the profile gate the one-shot setup container would run on every
    # ``docker compose up`` even when observability is not enabled.
    assert definition.profile == "observability"


def test_observer_tokens_are_never_generated() -> None:
    """The observer fails closed on every surface as soon as ANY token set is
    non-empty. ``secret: true`` makes render_env generate an independent
    random value per variable, so a generated read/admin token with empty
    ingest tokens would reject every ingest POST with 401 while producers
    fail open and silently drop events. Auth is all-or-nothing: all token
    vars must stay plain vars with empty defaults (unauthenticated local
    dev); hardened deployments set matching values in .phlo/.env.local.
    """
    definition = ServiceDefinition.from_yaml(_PKG_SRC / "service.yaml")
    env_vars = definition.env_vars

    for name in (
        "OBSERVE_HTTP_TOKEN",
        "OBSERVE_HTTP_API_KEY",
        "PHLO_OBSERVER_INGEST_TOKENS",
        "PHLO_OBSERVER_READ_TOKENS",
        "PHLO_OBSERVER_ADMIN_TOKENS",
    ):
        assert not env_vars[name].get("secret"), f"{name} must not be a generated secret"
        assert env_vars[name].get("default", "") == ""


def test_generate_env_local_writes_no_observer_tokens() -> None:
    """Fresh ``phlo services init --profile observability`` must produce an
    observer that accepts ingest. Any generated token value would flip the
    observer into fail-closed auth with no matching producer credential.
    """
    from phlo.plugins.compose.env import generate_env_local

    definition = ServiceDefinition.from_yaml(_PKG_SRC / "service.yaml")
    rendered = generate_env_local([definition])
    for name in (
        "OBSERVE_HTTP_TOKEN",
        "OBSERVE_HTTP_API_KEY",
        "PHLO_OBSERVER_INGEST_TOKENS",
        "PHLO_OBSERVER_READ_TOKENS",
        "PHLO_OBSERVER_ADMIN_TOKENS",
    ):
        assert f"{name}=" not in rendered


def test_service_plugin_exposes_definition() -> None:
    from phlo_observe_plugin.plugin import (
        PhloObserverDbSetupPlugin,
        PhloObserverServicePlugin,
    )

    observer = PhloObserverServicePlugin()
    assert observer.metadata.name == "phlo-observer"
    assert observer.service_definition["name"] == "phlo-observer"
    setup = PhloObserverDbSetupPlugin()
    assert setup.metadata.name == "phlo-observer-db-setup"
    assert setup.service_definition["name"] == "phlo-observer-db-setup"
