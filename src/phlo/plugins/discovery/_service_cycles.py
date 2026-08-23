"""Cycle detection utilities for service dependency graphs.

Finds closed cycles within a restricted node subset and deduplicates them
via a canonical rotation/reflection signature, so each cycle is reported
once regardless of start node or direction.
Imported by phlo.plugins.discovery._service_dependency_resolution and services
for cycle detection over resolved service dependency graphs.
"""

from __future__ import annotations


def find_cycles(nodes: set[str], graph: dict[str, set[str]]) -> list[list[str]]:
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
