"""Dependency resolution for discovered services.

Kahn's topological sort over service depends_on edges; dependencies naming
unknown services are ignored rather than failing. A dependency cycle raises
ValueError with the cycle path instead of returning a partial order.

Private discovery helper used by service_manifest and services. Graph construction,
ordering and cycle diagnostics stay together so callers only supply definitions.
"""

from __future__ import annotations

from collections import deque

from phlo.plugins.discovery._service_definition import ServiceDefinition


def resolve_service_dependencies(
    services: list[ServiceDefinition],
) -> list[ServiceDefinition]:
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

        for dependent in sorted(graph[node]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(ordered_names) != len(services):
        remaining = set(in_degree) - set(ordered_names)
        cycles = _find_cycles(remaining, graph)
        cycle_detail = (
            "; ".join(" → ".join(cycle) for cycle in cycles) if cycles else str(remaining)
        )
        raise ValueError(f"Circular dependency detected: {cycle_detail}")

    name_to_service = {service.name: service for service in services}
    return [name_to_service[name] for name in ordered_names]


def _find_cycles(nodes: set[str], graph: dict[str, set[str]]) -> list[list[str]]:
    """Extract closed dependency cycles from a graph subset."""
    dependencies: dict[str, set[str]] = {name: set() for name in nodes}
    for dependency, dependents in graph.items():
        for dependent in dependents & nodes:
            dependencies[dependent].add(dependency)

    cycles: list[list[str]] = []
    seen_signatures: set[tuple[str, ...]] = set()

    for start in sorted(nodes):
        cycle = _find_cycle_from_start(start, dependencies, nodes)
        if not cycle:
            continue

        signature = _canonical_cycle(cycle[:-1])
        if signature in seen_signatures:
            continue

        seen_signatures.add(signature)
        cycles.append(cycle)

    return cycles


def _find_cycle_from_start(
    start: str, dependencies: dict[str, set[str]], allowed: set[str]
) -> list[str] | None:
    stack: list[tuple[str, list[str], set[str]]] = [(start, [start], {start})]

    while stack:
        current, path, seen = stack.pop()
        for dependency in sorted(dependencies.get(current, set())):
            if dependency not in allowed:
                continue
            if dependency == start and len(path) > 1:
                return path + [start]
            if dependency in seen:
                continue
            stack.append((dependency, path + [dependency], seen | {dependency}))

    return None


def _canonical_cycle(cycle_nodes: list[str]) -> tuple[str, ...]:
    if not cycle_nodes:
        return ()

    rotations = [tuple(cycle_nodes[i:] + cycle_nodes[:i]) for i in range(len(cycle_nodes))]
    reverse = list(reversed(cycle_nodes))
    reverse_rotations = [tuple(reverse[i:] + reverse[:i]) for i in range(len(reverse))]
    return min(rotations + reverse_rotations)
