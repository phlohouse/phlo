"""Dependency resolution for discovered services.

Kahn's topological sort over service depends_on edges; dependencies naming
unknown services are ignored rather than failing. A dependency cycle raises
ValueError with the cycle path instead of returning a partial order.

Private discovery helper used by service_manifest and services to order service dependencies;
topological sort with cycle detection, no phlo imports beyond its own cycle helper.
"""

from __future__ import annotations

from collections import deque
from typing import Protocol, TypeVar

from phlo.plugins.discovery._service_cycles import find_cycles


class _ServiceLike(Protocol):
    """Structural type for service dependency resolution."""

    name: str
    depends_on: list[str]


_ServiceT = TypeVar("_ServiceT", bound=_ServiceLike)


def resolve_service_dependencies(services: list[_ServiceT]) -> list[_ServiceT]:
    """Resolve and sort services by dependencies (topological order)."""
    service_names = {service.name for service in services}
    graph: dict[str, set[str]] = {}
    in_degree: dict[str, int] = {}

    for service in services:
        graph[service.name] = set()
        in_degree[service.name] = 0

    for service in services:
        for dependency in service.depends_on:
            if dependency in service_names:
                graph[dependency].add(service.name)
                in_degree[service.name] += 1

    queue = deque(name for name, degree in in_degree.items() if degree == 0)
    ordered_names: list[str] = []

    while queue:
        node = queue.popleft()
        ordered_names.append(node)

        for dependent in graph[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(ordered_names) != len(services):
        remaining = set(in_degree) - set(ordered_names)
        cycles = find_cycles(remaining, graph)
        cycle_detail = (
            "; ".join(" → ".join(cycle) for cycle in cycles) if cycles else str(remaining)
        )
        raise ValueError(f"Circular dependency detected: {cycle_detail}")

    name_to_service = {service.name: service for service in services}
    return [name_to_service[name] for name in ordered_names]
