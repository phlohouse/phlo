"""Check explicit Python import edges, including deferred and type-only imports."""

from __future__ import annotations

import ast
import sys
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / "src/phlo",
    ROOT / "packages/phlo-iceberg/src/phlo_iceberg",
    ROOT / "packages/phlo-dagster/src/phlo_dagster",
)


def import_graph(sources: tuple[Path, ...]) -> dict[str, set[str]]:
    """Resolve import statements to modules, without executing package roots.

    Attribute imports point at their defining import target; submodule imports
    point at the submodule. Implicit parent initialisation is not an explicit
    dependency edge. All AST depths count, including TYPE_CHECKING blocks.
    """
    modules = {}
    for source in sources:
        for path in sorted(source.rglob("*.py")):
            parts = path.relative_to(source.parent).with_suffix("").parts
            name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            modules[name] = path
    graph: dict[str, set[str]] = {name: set() for name in modules}
    roots = {source.name for source in sources}
    for name, path in modules.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    prefix = package.split(".")[: len(package.split(".")) - node.level + 1]
                    base = ".".join([*prefix, *([base] if base else [])])
                targets = [
                    f"{base}.{alias.name}" if f"{base}.{alias.name}" in modules else base
                    for alias in node.names
                ]
            for target in targets:
                if target.split(".")[0] in roots and target not in modules:
                    raise ValueError(f"{path}:{node.lineno}: missing local module {target}")
                if target in modules:
                    graph[name].add(target)
    return graph


def strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return every nontrivial SCC using Tarjan's algorithm."""
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        indices[node] = low[node] = len(indices)
        stack.append(node)
        active.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component = []
            while True:
                target = stack.pop()
                active.remove(target)
                component.append(target)
                if target == node:
                    break
            if len(component) > 1 or node in graph[node]:
                cycles.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return sorted(cycles)


def inward_aggregator_imports(
    graph: dict[str, set[str]], sources: tuple[Path, ...]
) -> list[tuple[str, str]]:
    """Public package roots are for outside consumers, never inward imports."""
    packages = {
        ".".join(path.relative_to(source.parent).parent.parts)
        for source in sources
        for path in source.rglob("__init__.py")
    }
    return sorted(
        (source, target)
        for source, targets in graph.items()
        for target in targets & packages
        if source.split(".")[0] == target.split(".")[0]
    )


def main() -> int:
    """Print complete SCCs and fail when any explicit import cycle exists."""
    graph = import_graph(SOURCES)
    cycles = strongly_connected_components(graph)
    inward = inward_aggregator_imports(graph, SOURCES)
    print(
        f"AST imports: {len(graph)} modules, {sum(map(len, graph.values()))} edges, {len(cycles)} SCCs"
    )
    for component in cycles:
        print(f"SCC ({len(component)} modules): {', '.join(component)}")
        members = set(component)
        for source in component:
            for target in sorted(graph[source] & members):
                print(f"  {source} -> {target}")
    for source, target in inward:
        print(f"Inward aggregator import: {source} -> {target}")
    # Independently cross-check the SCC result with the standard-library DAG checker.
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError:
        return 1
    return int(bool(inward))


if __name__ == "__main__":
    sys.exit(main())
