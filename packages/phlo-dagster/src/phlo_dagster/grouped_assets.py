"""Plan compatible asset groups without changing execution policies or graph order.

Groups share one provider contract and equal partitions, resources, and routing.
Contraction cycles retain individual runners across the complete asset graph.
"""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter

from phlo.capabilities.specs import AssetSpec


def _compatible(left: AssetSpec, right: AssetSpec) -> bool:
    return (
        left.run is not None
        and right.run is not None
        and left.run.group is right.run.group
        and left.partitions == right.partitions
        and left.capability_overrides == right.capability_overrides
        and left.resources == right.resources
    )


def _eligible(spec: AssetSpec) -> bool:
    run = spec.run
    return (
        run is not None
        and run.group is not None
        and all(
            value is None
            for value in (
                run.max_runtime_seconds,
                run.max_retries,
                run.retry_delay_seconds,
                run.cron,
                run.freshness_hours,
                run.isolated_write,
            )
        )
    )


def group_asset_specs(assets: list[AssetSpec]) -> list[list[AssetSpec]]:
    """Coalesce compatible provider groups, retaining singleton fallback specs."""
    groups: list[list[AssetSpec]] = []
    singles = []
    for spec in assets:
        if spec.run is None:
            continue
        if not _eligible(spec):
            singles.append([spec])
            continue
        for members in groups:
            if _compatible(spec, members[0]):
                members.append(spec)
                break
        else:
            groups.append([spec])
    representatives = {spec.key: members[0].key for members in groups for spec in members}
    graph: dict[str, set[str]] = {}
    for spec in assets:
        key = representatives.get(spec.key, spec.key)
        graph.setdefault(key, set()).update(
            representatives.get(dep, dep)
            for dep in spec.deps
            if representatives.get(dep, dep) != key
        )
    try:
        TopologicalSorter(graph).prepare()
    except CycleError:
        return [[spec] for spec in assets if spec.run is not None]
    return singles + groups
