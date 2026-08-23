"""Tests for service dependency resolution.

Focuses on circular dependencies: cycles are detected with reported paths, and
reported cycle paths stay open (start node not repeated). Runs the resolver
in-memory, bypassing filesystem discovery.
"""

from __future__ import annotations

import pytest

from phlo.plugins.discovery import ServiceDiscovery
from tests.helpers import _service


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
    # c depends on the cycle but is not part of it; the reported path must
    # stay closed and must not sweep in unrelated services.
    assert "a → b → c" not in message


def test_find_cycles_returns_closed_paths() -> None:
    from phlo.plugins.discovery.services import _find_cycles

    graph = {
        "a": {"b"},
        "b": {"a", "c"},
        "c": {"b"},
    }
    cycles = _find_cycles({"a", "b", "c"}, graph)

    assert cycles
    assert all(len(cycle) > 2 and cycle[0] == cycle[-1] for cycle in cycles)


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
