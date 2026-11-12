"""Tests for service dependency resolution.

Focuses on circular dependencies: cycles are detected with reported paths, and
reported cycle paths stay closed (start node repeated). Runs the resolver
in-memory, bypassing filesystem discovery.
"""

from __future__ import annotations

import pytest

from phlo.plugins.discovery import ServiceDiscovery
from tests.support import _service


def test_resolve_dependencies_reports_cycle_path() -> None:
    # Bypass __init__: exercise the resolver against an in-memory service
    # list without letting discovery touch the filesystem.
    discovery = ServiceDiscovery.__new__(ServiceDiscovery)
    discovery.services_dir = None
    discovery._services = {}
    discovery._loaded = True

    a = _service("a")
    a.depends_on = ["b"]
    b = _service("b")
    b.depends_on = ["c"]
    c = _service("c")
    c.depends_on = ["a"]

    with pytest.raises(ValueError, match="Circular dependency detected:.*→"):
        discovery.resolve_dependencies([a, b, c])


def test_resolve_dependencies_cycle_path_is_closed() -> None:
    discovery = ServiceDiscovery.__new__(ServiceDiscovery)
    discovery.services_dir = None
    discovery._services = {}
    discovery._loaded = True

    a = _service("a")
    a.depends_on = ["b"]
    b = _service("b")
    b.depends_on = ["a", "c"]
    c = _service("c")
    c.depends_on = ["b"]

    with pytest.raises(ValueError) as exc_info:
        discovery.resolve_dependencies([a, b, c])

    message = str(exc_info.value)
    assert "Circular dependency detected:" in message
    assert "→" in message
    # The reported path must stay closed rather than concatenate both cycles.
    assert "a → b → c" not in message


def test_resolve_dependencies_reports_closed_paths_without_downstream_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovery = ServiceDiscovery()
    monkeypatch.setattr(discovery, "discover", dict)
    a, b, c, downstream = [_service(name) for name in ("a", "b", "c", "downstream")]
    a.depends_on = ["b"]
    b.depends_on = ["a", "c"]
    c.depends_on = ["b"]
    downstream.depends_on = ["c"]

    with pytest.raises(ValueError, match="Circular dependency detected: ") as exc_info:
        discovery.resolve_dependencies([downstream, a, b, c])

    cycles = str(exc_info.value).removeprefix("Circular dependency detected: ").split("; ")
    paths = [cycle.split(" → ") for cycle in cycles]
    assert all(len(path) > 2 and path[0] == path[-1] for path in paths)
    assert {frozenset(path) for path in paths} == {frozenset({"a", "b"}), frozenset({"b", "c"})}
    assert "downstream" not in str(exc_info.value)


def test_resolve_dependencies_no_cycle() -> None:
    discovery = ServiceDiscovery.__new__(ServiceDiscovery)
    discovery.services_dir = None
    discovery._services = {}
    discovery._loaded = True

    a = _service("a")
    b = _service("b")
    b.depends_on = ["a"]
    c = _service("c")
    c.depends_on = ["b"]

    result = discovery.resolve_dependencies([a, b, c])
    names = [s.name for s in result]
    assert names.index("a") < names.index("b") < names.index("c")
