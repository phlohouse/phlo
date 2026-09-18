"""Tests for the ``pretty`` drain wiring in ``phlo.telemetry.configure``.

``OBSERVE_DRAINS=pretty`` (or ``PHLO_OBSERVE_PRETTY``) attaches Phlo's
human-readable drain to the configured runtime. ``pretty`` is not an
observe-core drain name, so it is filtered out of the ``drains`` override
passed to the SDK — init values beat the env var, which is never mutated —
and the drain registers through ``Runtime.add_drain``.
"""

from __future__ import annotations

import os

import pytest

observe_core = pytest.importorskip("observe_core", reason="phlo-observe SDK not installed")
pytest.importorskip("phlo_observe_plugin.presentation", reason="observe plugin not installed")

import phlo.telemetry as phlo_observe  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OBSERVE_DRAINS",
        "OBSERVE_HTTP_ENDPOINT",
        "OBSERVE_JSONL_PATH",
        "PHLO_OBSERVE_ENABLED",
        "PHLO_OBSERVE_PRETTY",
        "OBSERVE_HTTP_TOKEN",
        "OBSERVE_HTTP_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    phlo_observe.reset_for_tests()
    yield
    phlo_observe.reset_for_tests()


def _drain_names() -> list[str]:
    runtime = observe_core.runtime.get_runtime()
    return sorted(getattr(drain, "name", "?") for drain in runtime.drains)


def test_pretty_via_observe_drains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVE_DRAINS", "pretty")
    assert phlo_observe.configure() is True
    # pretty alone replaces the SDK's console-drain default.
    assert _drain_names() == ["pretty"]
    assert phlo_observe.enabled() is True
    # The user's env is never mutated — the SDK saw an explicit override.
    assert os.environ["OBSERVE_DRAINS"] == "pretty"


def test_pretty_alongside_other_drains(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("OBSERVE_DRAINS", "pretty,jsonl")
    monkeypatch.setenv("OBSERVE_JSONL_PATH", str(tmp_path / "events.jsonl"))
    assert phlo_observe.configure() is True
    assert _drain_names() == ["jsonl", "pretty"]
    assert os.environ["OBSERVE_DRAINS"] == "pretty,jsonl"


def test_pretty_env_flag_replaces_console_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    assert phlo_observe.configure() is True
    assert _drain_names() == ["pretty"]
    # The flag alone must leave the runtime enabled — a drain on a disabled
    # runtime is never called.
    assert phlo_observe.enabled() is True


def test_pretty_env_flag_with_empty_drains_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setenv("OBSERVE_DRAINS", " ")
    assert phlo_observe.configure() is True
    assert _drain_names() == ["pretty"]
    assert phlo_observe.enabled() is True


def test_pretty_flag_keeps_explicit_drains(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "true")
    monkeypatch.setenv("OBSERVE_DRAINS", "jsonl")
    monkeypatch.setenv("OBSERVE_JSONL_PATH", str(tmp_path / "events.jsonl"))
    assert phlo_observe.configure() is True
    assert _drain_names() == ["jsonl", "pretty"]
    assert phlo_observe.enabled() is True


def test_no_pretty_without_opt_in() -> None:
    assert phlo_observe.configure(enabled=True, drains=[]) is True
    assert _drain_names() == []


def test_pretty_implies_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """OBSERVE_DRAINS=pretty counts as configured drains for the enabled check."""
    monkeypatch.setenv("OBSERVE_DRAINS", "pretty")
    phlo_observe.configure()
    assert phlo_observe.enabled() is True
