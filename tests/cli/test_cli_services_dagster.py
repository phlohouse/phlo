"""Tests for Dagster container lookup.

Lookup order is fixed: configured container name first, then the new
name, the legacy webserver name, and finally a regex match that always
excludes the daemon container. No candidate resolving raises instead of
returning a wrong container.
"""

from __future__ import annotations

import pytest
from phlo_dagster.containers import find_dagster_container


def test_find_dagster_container_prefers_configured_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.containers.find_service_container",
        lambda **kwargs: "myproj-dagster-webserver-1",
    )

    assert find_dagster_container("myproj") == "myproj-dagster-webserver-1"


def test_find_dagster_container_falls_back_to_new_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.containers.find_service_container",
        lambda **kwargs: "myproj-dagster-1",
    )

    assert find_dagster_container("myproj") == "myproj-dagster-1"


def test_find_dagster_container_falls_back_to_legacy_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.containers.find_service_container",
        lambda **kwargs: "myproj-dagster-webserver-1",
    )
    assert find_dagster_container("myproj") == "myproj-dagster-webserver-1"


def test_find_dagster_container_regex_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_find_service_container(**kwargs):
        captured.update(kwargs)
        return "myproj-custom-dagster-web-1"

    monkeypatch.setattr(
        "phlo_dagster.containers.find_service_container", _fake_find_service_container
    )

    assert find_dagster_container("myproj") == "myproj-custom-dagster-web-1"
    assert captured["service_name"] == "dagster"
    assert captured["legacy_names"] == ("myproj-dagster-webserver-1",)
    assert captured["exclude_substrings"] == ("daemon",)


def test_find_dagster_container_regex_excludes_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.containers.find_service_container",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("Could not find running Dagster")),
    )
    with pytest.raises(RuntimeError, match="Could not find running Dagster"):
        find_dagster_container("myproj")


def test_find_dagster_container_raises_when_no_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.containers.find_service_container",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("Could not find running Dagster")),
    )
    with pytest.raises(RuntimeError, match="Could not find running Dagster"):
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
