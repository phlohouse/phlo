"""Pytest plugin partitioning core tests by module, preserving module fixtures."""

from __future__ import annotations

import hashlib

import pytest


def shard_for(node_id: str, count: int) -> int:
    """Keep every test from a module on one deterministic shard."""
    module = node_id.split("::", 1)[0]
    return int.from_bytes(hashlib.sha256(module.encode()).digest()[:8], "big") % count


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--ci-shard-index", type=int, default=0)
    parser.addoption("--ci-shard-count", type=int, default=1)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    count = config.getoption("--ci-shard-count")
    index = config.getoption("--ci-shard-index")
    if count < 1 or not 0 <= index < count:
        raise pytest.UsageError("CI shard index must be within a positive shard count")
    selected, deselected = [], []
    for item in items:
        (selected if shard_for(item.nodeid, count) == index else deselected).append(item)
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
