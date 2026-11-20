"""Pytest plugin partitioning core tests by module, preserving module fixtures."""

from __future__ import annotations

import hashlib

import pytest


def shard_for(node_id: str, count: int) -> int:
    """Keep every test from a module on one deterministic shard."""
    module = node_id.split("::", 1)[0]
    return int.from_bytes(hashlib.sha256(module.encode()).digest()[:8], "big") % count


def balanced_shards(node_ids: list[str], count: int) -> dict[str, int]:
    """Assign whole modules to shards, largest first, onto the lightest shard.

    Every shard collects the same tests, so each computes the same plan. Test
    count stands in for duration; ties break by name so the plan is stable.
    """
    sizes: dict[str, int] = {}
    for node_id in node_ids:
        module = node_id.split("::", 1)[0]
        sizes[module] = sizes.get(module, 0) + 1
    loads = [0] * count
    plan = {}
    for module, size in sorted(sizes.items(), key=lambda item: (-item[1], item[0])):
        shard = min(range(count), key=lambda index: (loads[index], index))
        plan[module] = shard
        loads[shard] += size
    return plan


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--ci-shard-index", type=int, default=0)
    parser.addoption("--ci-shard-count", type=int, default=1)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    count = config.getoption("--ci-shard-count")
    index = config.getoption("--ci-shard-index")
    if count < 1 or not 0 <= index < count:
        raise pytest.UsageError("CI shard index must be within a positive shard count")
    plan = balanced_shards([item.nodeid for item in items], count)
    selected, deselected = [], []
    for item in items:
        module = item.nodeid.split("::", 1)[0]
        (selected if plan[module] == index else deselected).append(item)
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
