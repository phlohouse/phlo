"""Tests for Dagster container lookup.

Lookup order is fixed: configured container name first, then the new
name, the legacy webserver name, and finally a regex match that always
excludes the daemon container. No candidate resolving raises instead of
returning a wrong container.
"""

from __future__ import annotations

import pytest
from phlo_dagster.containers import find_dagster_container


def _install_container_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    running_containers: list[str],
    configured_names: dict[str, str] | None = None,
) -> dict[str, object]:
    """Patch the runtime boundary so the real resolution chain executes.

    The fakes record the inputs they receive and derive their outputs from
    those inputs instead of echoing back a canned result.
    """
    configured_names = configured_names or {}
    recorded: dict[str, object] = {}

    def fake_resolve_container_name(service_name: str, project_name: str) -> str:
        recorded["resolve_service_name"] = service_name
        recorded["resolve_project_name"] = project_name
        if service_name in configured_names:
            return configured_names[service_name]
        # Mirror an infrastructure config whose naming pattern omits the -1 suffix,
        # so the pattern-derived name is distinct from the compose default name.
        return f"{project_name}-{service_name}"

    def fake_list_running_containers(project_name: str) -> list[str]:
        recorded["list_project_name"] = project_name
        return list(running_containers)

    monkeypatch.setattr(
        "phlo.infrastructure.containers.resolve_container_name",
        fake_resolve_container_name,
    )
    monkeypatch.setattr(
        "phlo.infrastructure.containers.list_running_containers",
        fake_list_running_containers,
    )
    return recorded


def test_find_dagster_container_prefers_configured_name(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _install_container_runtime(
        monkeypatch,
        running_containers=[
            "myproj-dagster-webserver-gold",
            "myproj-dagster-1",
            "myproj-dagster-webserver-1",
        ],
        configured_names={"dagster": "myproj-dagster-webserver-gold"},
    )

    assert find_dagster_container("myproj") == "myproj-dagster-webserver-gold"
    assert recorded["resolve_service_name"] == "dagster"
    assert recorded["resolve_project_name"] == "myproj"


def test_find_dagster_container_falls_back_to_new_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container_runtime(
        monkeypatch,
        running_containers=[
            "myproj-postgres-1",
            "myproj-dagster-1",
            "myproj-dagster-webserver-1",
        ],
    )

    assert find_dagster_container("myproj") == "myproj-dagster-1"


def test_find_dagster_container_falls_back_to_legacy_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container_runtime(
        monkeypatch,
        running_containers=["myproj-postgres-1", "myproj-dagster-webserver-1"],
    )

    assert find_dagster_container("myproj") == "myproj-dagster-webserver-1"


def test_find_dagster_container_regex_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _install_container_runtime(
        monkeypatch,
        running_containers=["myproj-weird-dagster-web-9f2e"],
    )

    assert find_dagster_container("myproj") == "myproj-weird-dagster-web-9f2e"
    assert recorded["list_project_name"] == "myproj"


def test_find_dagster_container_regex_excludes_daemon_but_finds_webserver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daemon containers match the include regex yet must never be candidates."""
    _install_container_runtime(
        monkeypatch,
        # The daemon name deliberately appears before the webserver-like candidate,
        # so a regex scan without exclusion would pick the daemon first.
        running_containers=[
            "myproj-dagster-daemon-9c1f",
            "myproj-fallback-dagster-webserver-77aa",
        ],
    )

    chosen = find_dagster_container("myproj")

    assert chosen == "myproj-fallback-dagster-webserver-77aa"
    assert "daemon" not in chosen


def test_find_dagster_container_raises_when_no_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container_runtime(
        monkeypatch,
        # Only the daemon matches the include regex; excluding it must leave
        # nothing to choose instead of returning a wrong container.
        running_containers=["myproj-postgres-1", "myproj-dagster-daemon-9c1f"],
    )

    with pytest.raises(
        RuntimeError,
        match=r"Could not find running dagster container for project 'myproj'",
    ):
        find_dagster_container("myproj")


def test_dagster_container_candidates_structure() -> None:
    from phlo_dagster.containers import dagster_container_candidates

    candidates = dagster_container_candidates("demo", "demo-dagster-webserver-1")
    assert candidates.configured == "demo-dagster-webserver-1"
    assert candidates.new == "demo-dagster-1"
    assert candidates.legacy == "demo-dagster-webserver-1"


def test_dagster_container_candidates_no_configured() -> None:
    from phlo_dagster.containers import dagster_container_candidates

    candidates = dagster_container_candidates("demo", None)
    assert candidates.configured == ""
    assert candidates.new == "demo-dagster-1"


def test_select_first_existing_returns_first_match() -> None:
    from phlo.infrastructure import select_first_existing

    result = select_first_existing(
        ["a", "b", "c"],
        ["c", "b"],
    )
    assert result == "b"


def test_select_first_existing_returns_none_when_no_match() -> None:
    from phlo.infrastructure import select_first_existing

    result = select_first_existing(["a", "b"], ["x", "y"])
    assert result is None


def test_select_first_existing_skips_empty_candidates() -> None:
    from phlo.infrastructure import select_first_existing

    result = select_first_existing(["", "b"], ["b"])
    assert result == "b"
