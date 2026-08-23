"""Tests for start preflight and service selection planning.

Verifies the selection plan honours defaults, profiles, and explicit
requests while rejecting disabled or unknown service names, and that the
start preflight plan carries the compose inputs and resolved backend.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest

from phlo.cli.commands.services.planner import (
    build_service_selection_plan,
    build_start_preflight_plan,
)
from phlo.plugins.discovery import ServiceDefinition


def _service(
    name: str,
    *,
    default: bool = False,
    profile: str | None = None,
) -> ServiceDefinition:
    return ServiceDefinition.from_dict(
        {"name": name, "image": f"{name}:latest", "default": default, "profile": profile},
        None,
    )


def test_selection_plan_includes_default_and_profile_services() -> None:
    services = {
        "postgres": _service("postgres", default=True),
        "grafana": _service("grafana", profile="observability"),
        "hasura": _service("hasura", profile="api"),
    }

    plan = build_service_selection_plan(
        services=services,
        config={},
        profiles=("observability",),
        requested_names=[],
    )

    assert [service.name for service in plan.selected_services] == ["postgres", "grafana"]
    assert plan.disabled_names == frozenset()


def test_selection_plan_excludes_disabled_services() -> None:
    services = {
        "postgres": _service("postgres", default=True),
        "grafana": _service("grafana", default=True),
    }

    plan = build_service_selection_plan(
        services=services,
        config={"services": {"grafana": {"enabled": False}}},
        profiles=(),
        requested_names=[],
    )

    assert [service.name for service in plan.selected_services] == ["postgres"]
    assert plan.disabled_names == frozenset({"grafana"})


def test_selection_plan_respects_requested_names() -> None:
    services = {
        "postgres": _service("postgres", default=True),
        "grafana": _service("grafana", default=True, profile="observability"),
    }

    plan = build_service_selection_plan(
        services=services,
        config={},
        profiles=("observability",),
        requested_names=["postgres"],
    )

    assert [service.name for service in plan.selected_services] == ["postgres"]
    assert plan.disabled_names == frozenset()


def test_selection_plan_rejects_disabled_requested_service() -> None:
    services = {"grafana": _service("grafana", default=True)}

    with pytest.raises(click.ClickException) as exc:
        build_service_selection_plan(
            services=services,
            config={"services": {"grafana": {"enabled": False}}},
            profiles=(),
            requested_names=["grafana"],
        )

    assert "grafana" in str(exc.value)


def test_selection_plan_rejects_unknown_requested_service() -> None:
    services = {"postgres": _service("postgres", default=True)}

    with pytest.raises(click.ClickException) as exc:
        build_service_selection_plan(
            services=services,
            config={},
            profiles=(),
            requested_names=["missing"],
        )

    assert str(exc.value) == "Unknown service name(s): missing"


def test_start_preflight_plan_contains_compose_inputs(tmp_path: Path) -> None:
    phlo_dir = tmp_path / ".phlo"
    compose_file = phlo_dir / "docker-compose.yml"
    project_root = tmp_path
    service = _service("postgres", default=True)

    plan = build_start_preflight_plan(
        phlo_dir=phlo_dir,
        compose_file=compose_file,
        project_root=project_root,
        project_name="demo",
        services=[service],
        backend_name="docker",
    )

    assert plan.phlo_dir == phlo_dir
    assert plan.compose_file == compose_file
    assert plan.project_root == project_root
    assert plan.project_name == "demo"
    assert plan.service_names == ["postgres"]
    assert plan.backend_name == "docker"
