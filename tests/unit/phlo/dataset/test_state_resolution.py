"""Durable-vs-test mode resolution tests."""

from __future__ import annotations

import pytest

from phlo.dataset_state import (
    MODE_DURABLE,
    MODE_MEMORY,
    DatasetStoreResolutionError,
    MemoryDatasetStateStore,
    memory_store,
    reset_memory_store,
    resolve_dataset_state_store,
    resolve_store_mode,
)


@pytest.fixture(autouse=True)
def _clean_memory_singleton():
    reset_memory_store()
    yield
    reset_memory_store()


def test_mode_defaults_to_durable(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_DATASET_STATE_STORE", raising=False)
    assert resolve_store_mode() == MODE_DURABLE


def test_env_selects_explicit_test_mode(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_DATASET_STATE_STORE", "memory")
    assert resolve_store_mode() == MODE_MEMORY
    monkeypatch.setenv("PHLO_DATASET_STATE_STORE", "durable")
    assert resolve_store_mode() == MODE_DURABLE
    with pytest.raises(ValueError, match="Unknown dataset state store mode"):
        monkeypatch.setenv("PHLO_DATASET_STATE_STORE", "sqlite")
        resolve_store_mode()


def test_explicit_argument_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_DATASET_STATE_STORE", "memory")
    assert resolve_store_mode("durable") == MODE_DURABLE


def test_memory_mode_returns_process_local_singleton() -> None:
    first = resolve_dataset_state_store("/projects/demo", mode=MODE_MEMORY)
    second = resolve_dataset_state_store("/projects/other", mode=MODE_MEMORY)
    assert first is second
    assert isinstance(first, MemoryDatasetStateStore)
    assert first is memory_store()


def test_durable_mode_fails_closed_without_a_provider(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_DATASET_STATE_STORE", raising=False)
    from phlo.capabilities.registry import get_capability_registry

    registry = get_capability_registry()
    registry.clear("dataset_state_store")
    with pytest.raises(DatasetStoreResolutionError, match="No durable dataset state store"):
        resolve_dataset_state_store("/projects/demo")
