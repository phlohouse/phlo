"""Tests for the ``pretty`` drain wiring in ``phlo.telemetry.configure``.

``OBSERVE_DRAINS=pretty`` (or ``PHLO_OBSERVE_PRETTY``) attaches Phlo's
human-readable drain to the configured runtime. ``pretty`` is not an
observe-core drain name, so it is filtered out of the ``drains`` override
passed to the SDK — init values beat the env var, which is never mutated —
and the drain registers through ``Runtime.add_drain``.
"""

from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace
from typing import Any

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
        "PHLO_OBSERVE_PRETTY_VERBOSE",
        "PHLO_LOG_LEVEL",
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


# -- pretty as the primary terminal surface -------------------------------------


def _emit_lineage() -> None:
    """Emit a secondary-visibility event: verbose renders it, pretty hides it."""
    phlo_observe.emit(
        "phlo.lineage",
        outcome="success",
        attributes={
            "edges": [["raw.users", "bronze.users"]],
            "asset_keys": ["bronze.users"],
        },
    )


def test_pretty_verbose_env_renders_secondary_events(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PHLO_OBSERVE_PRETTY_VERBOSE renders the drain in verbose mode:
    secondary events appear in the output stream."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY_VERBOSE", "1")
    assert phlo_observe.configure() is True
    _emit_lineage()
    observe_core.runtime.get_runtime().flush()
    assert "Lineage" in capsys.readouterr().err


def test_pretty_drain_defaults_to_pretty_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default pretty mode hides secondary events while primary ones render."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    assert phlo_observe.configure() is True
    _emit_lineage()
    phlo_observe.emit(
        "asset.materialize",
        outcome="success",
        correlation={"asset_key": "bronze.users"},
        attributes={"rows_out": 5},
    )
    observe_core.runtime.get_runtime().flush()
    err = capsys.readouterr().err
    assert "Materialize" in err
    assert "Lineage" not in err


def test_framework_console_quiets_when_pretty_is_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pretty enabled: framework lifecycle chatter drops to WARNING."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    assert phlo_observe.dagster_console_log_level() == "WARNING"
    assert phlo_observe.dagster_loggers_config() == {
        "console": {"config": {"log_level": "WARNING"}}
    }


def test_framework_console_quiets_for_drains_pretty(monkeypatch: pytest.MonkeyPatch) -> None:
    """OBSERVE_DRAINS=pretty counts the same as the env flag."""
    monkeypatch.setenv("OBSERVE_DRAINS", "pretty")
    assert phlo_observe.dagster_console_log_level() == "WARNING"


def test_framework_console_preserved_under_pretty_verbose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verbose pretty is a verbose configuration: full framework logs stay."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY_VERBOSE", "1")
    assert phlo_observe.dagster_console_log_level() == "DEBUG"
    assert phlo_observe.dagster_loggers_config() is None


def test_framework_console_preserved_under_debug_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHLO_LOG_LEVEL=DEBUG is a debug configuration: full logs stay."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setenv("PHLO_LOG_LEVEL", "DEBUG")
    assert phlo_observe.dagster_console_log_level() == "DEBUG"


def test_framework_console_default_without_pretty() -> None:
    """No pretty drain: Dagster's own defaults apply, nothing injected."""
    assert phlo_observe.dagster_console_log_level() == "DEBUG"
    assert phlo_observe.dagster_loggers_config() is None


def test_framework_console_default_when_observe_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pretty configured but disabled: never quiet the only remaining stream."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setenv("PHLO_OBSERVE_ENABLED", "false")
    assert phlo_observe.dagster_console_log_level() == "DEBUG"
    assert phlo_observe.dagster_loggers_config() is None


def test_dagster_run_config_merges_console_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """The merge drops into an arbitrary run config alongside existing keys."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    merged = phlo_observe.dagster_run_config(
        {"ops": {"asset": {"config": {"x": 1}}}, "loggers": {"json": {"config": {}}}}
    )
    assert merged == {
        "ops": {"asset": {"config": {"x": 1}}},
        "loggers": {
            "json": {"config": {}},
            "console": {"config": {"log_level": "WARNING"}},
        },
    }
    # The caller's dict is not mutated.
    base = {"ops": {"asset": {"config": {"x": 1}}}}
    phlo_observe.dagster_run_config(base)
    assert "loggers" not in base


def test_dagster_run_config_respects_explicit_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller's explicit console log_level is itself verbose configuration."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    merged = phlo_observe.dagster_run_config(
        {"loggers": {"console": {"config": {"log_level": "ERROR"}}}}
    )
    assert merged["loggers"]["console"]["config"]["log_level"] == "ERROR"


def test_dagster_run_config_passthrough_without_pretty() -> None:
    """No pretty: the run config round-trips unchanged, no loggers added."""
    assert phlo_observe.dagster_run_config({"ops": {"a": 1}}) == {"ops": {"a": 1}}
    assert phlo_observe.dagster_run_config() == {}


def test_dagster_loggers_config_reads_supplied_env() -> None:
    """Launch sites pass a merged project env to predict the run's env —
    the mapping replaces ``os.environ`` rather than augmenting it."""
    assert phlo_observe.dagster_loggers_config(env={"PHLO_OBSERVE_PRETTY": "1"}) == {
        "console": {"config": {"log_level": "WARNING"}}
    }
    assert (
        phlo_observe.dagster_loggers_config(
            env={"PHLO_OBSERVE_PRETTY": "1", "PHLO_OBSERVE_PRETTY_VERBOSE": "1"}
        )
        is None
    )
    assert phlo_observe.dagster_loggers_config(env={}) is None


def test_framework_console_preserved_when_drain_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pretty requested but the drain cannot attach (no SDK in the run
    environment): quieting the console would leave no run narrative."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setattr(phlo_observe, "_sdk_module", lambda: None)
    assert phlo_observe._pretty_drain_available() is False
    assert phlo_observe.dagster_console_log_level() == "DEBUG"
    assert phlo_observe.dagster_loggers_config() is None


def _run_console_logger(stream: Any = None) -> logging.Logger:
    """An unregistered ``dagster`` console logger, like Dagster's per-run one."""
    run_logger = logging.Logger("dagster")
    run_logger.addHandler(logging.StreamHandler(stream or sys.stderr))
    run_logger.setLevel(logging.DEBUG)
    return run_logger


def _dagster_context(*run_loggers: logging.Logger) -> Any:
    """Minimal stand-in for a Dagster context: ``log._dagster_handler._loggers``."""
    return SimpleNamespace(
        log=SimpleNamespace(
            _dagster_handler=SimpleNamespace(_loggers=list(run_loggers), _handlers=[])
        )
    )


def test_scope_quiets_framework_console_once_drain_attached(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inside the worker the decision is final: a real pretty drain attached,
    so the run's console loggers drop to WARNING and stay quiet."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    run_logger = _run_console_logger()
    context = _dagster_context(run_logger)

    assert phlo_observe.configure() is True
    with phlo_observe.dagster_run_scope(context):
        run_logger.debug("framework chatter")
        run_logger.warning("real warning")

    assert run_logger.level == logging.WARNING
    rendered = capsys.readouterr().err
    assert "framework chatter" not in rendered
    assert "real warning" in rendered


def test_scope_restores_console_when_drain_cannot_attach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pretty requested but the worker's SDK cannot drive the drain: an
    injected WARNING must be undone — the console keeps the run narrative."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setattr(phlo_observe, "_sdk_module", lambda: None)
    run_logger = _run_console_logger()
    run_logger.setLevel(logging.WARNING)  # simulates a stale injected quieting
    context = _dagster_context(run_logger)

    with phlo_observe.dagster_run_scope(context):
        pass

    assert run_logger.level == logging.DEBUG
    assert run_logger.isEnabledFor(logging.DEBUG) is True


def test_scope_keeps_framework_console_under_verbose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verbose/debug configuration wins even when the drain attached."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY_VERBOSE", "1")
    run_logger = _run_console_logger()
    context = _dagster_context(run_logger)

    assert phlo_observe.configure() is True
    with phlo_observe.dagster_run_scope(context):
        pass

    assert run_logger.isEnabledFor(logging.DEBUG) is True


def test_scope_leaves_console_alone_without_pretty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No pretty request: the console is left exactly as configured, even
    when it was explicitly raised by the caller."""
    run_logger = _run_console_logger()
    run_logger.setLevel(logging.ERROR)
    context = _dagster_context(run_logger)

    with phlo_observe.dagster_run_scope(context):
        pass

    assert run_logger.level == logging.ERROR
