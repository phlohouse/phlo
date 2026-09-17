"""Tests for the phlo.telemetry soft-import shim.

The phlo-observe SDK is an optional dependency (Python >=3.12 only). These
tests exercise the shim's degraded path — every helper must no-op cleanly
when the SDK is absent, and instrumentation call sites must be unaffected.
"""

from __future__ import annotations

import contextlib

import pytest

import phlo.telemetry as phlo_observe


@pytest.fixture(autouse=True)
def _reset_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset module caches and clear observe env vars per test."""
    monkeypatch.delenv("OBSERVE_HTTP_ENDPOINT", raising=False)
    monkeypatch.delenv("OBSERVE_DRAINS", raising=False)
    monkeypatch.delenv("PHLO_OBSERVE_ENABLED", raising=False)
    monkeypatch.delenv("OBSERVE_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("OBSERVE_HTTP_API_KEY", raising=False)
    phlo_observe.reset_for_tests()


def test_sdk_unavailable_on_py311() -> None:
    """The marker-gated SDK is absent on Python 3.11 — shim reports so."""
    import sys

    if sys.version_info < (3, 12):
        assert phlo_observe.available() is False


def test_configure_returns_false_without_sdk() -> None:
    if not phlo_observe.available():
        assert phlo_observe.configure() is False
        # Idempotent: a second failure does not re-attempt or raise.
        assert phlo_observe.configure() is False


def test_null_scopes_are_context_managers() -> None:
    """Every scope helper yields a usable no-op builder."""
    for scope in (
        phlo_observe.observe("test.op"),
        phlo_observe.dagster_run_scope(object()),
        phlo_observe.dagster_step(object()),
        phlo_observe.emit_materialization(object()),
        phlo_observe.dlt_pipeline_scope(object()),
        phlo_observe.dlt_pipeline_run(object()),
        phlo_observe.wap_branch_create(branch="b"),
        phlo_observe.iceberg_commit(table="t"),
        phlo_observe.trino_query(),
    ):
        with scope as evt:
            # Ambient-binding scopes (run scope, pipeline scope) yield None;
            # event scopes yield a no-op builder.
            if evt is not None:
                evt.set(rows=1)
                evt.set_correlation(run_id="x")
                evt.set_entity("run", "run://dagster/x")
                evt.set_tag("k", "v")


def test_null_scope_survives_body_exception() -> None:
    """An exception inside a null scope propagates normally to the caller."""
    with pytest.raises(RuntimeError, match="boom"), phlo_observe.dlt_pipeline_run(object()):
        raise RuntimeError("boom")


def test_emit_noop() -> None:
    phlo_observe.emit(
        "wap.promote",
        category="wap",
        outcome="success",
        correlation={"run_id": "r", "branch": "b"},
        entities={"branch": None},
        producer="dagster",
    )
    phlo_observe.emit_dbt_run_results({"results": []})
    phlo_observe.emit_asset_check(object(), check_name="x", passed=True)


def test_enabled_false_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """enabled() gates translation work: False when the SDK cannot import."""
    monkeypatch.setattr(phlo_observe, "_import_optional", lambda *a, **kw: None)
    phlo_observe.reset_for_tests()
    assert phlo_observe.enabled() is False


def test_ambient_helpers_return_none_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Degraded-path assertions must hold whether or not the test env has the
    SDK installed — simulate absence rather than depending on it."""
    monkeypatch.setattr(phlo_observe, "_import_optional", lambda *a, **kw: None)
    phlo_observe.reset_for_tests()

    assert phlo_observe.ambient_run_id() is None
    assert phlo_observe.ambient_producer() is None
    assert phlo_observe.run_entity_id() is None
    assert phlo_observe.run_entity_for("dagster", "x") is None
    assert phlo_observe.branch_entity_id("b") is None
    assert phlo_observe.branch_entity_id("b", system="polaris") is None
    phlo_observe.bind_run_entity(object())


def test_branch_entity_id_forwards_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """The system kwarg must reach observe_core.branch_id so snapshot-catalog
    staging refs get branch://<catalog>/<ns> instead of branch://nessie/<ns>."""
    calls: list[tuple[str, str]] = []

    def _branch_id(system: str, branch: str) -> str:
        calls.append((system, branch))
        return f"branch://{system}/{branch}"

    import types

    fake = types.ModuleType("observe_core.identifiers")
    fake.branch_id = _branch_id  # type: ignore[attr-defined]
    monkeypatch.setattr(
        phlo_observe,
        "_import_optional",
        lambda mod, attr=None: getattr(fake, attr) if attr else fake,
    )
    assert phlo_observe.branch_entity_id("ns-1", system="polaris") == "branch://polaris/ns-1"
    assert phlo_observe.branch_entity_id("ns-2") == "branch://nessie/ns-2"
    assert calls == [("polaris", "ns-1"), ("nessie", "ns-2")]


def test_bind_context_noop() -> None:
    with phlo_observe.bind_context(run_id="x"):
        pass


def test_emit_degrades_entities_and_tags_on_pre_v2_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A V1 observe-core lacks set_entity/set_tag; emit must still deliver the
    event — dropping the unsupported fields with one warning — rather than
    letting AttributeError discard it wholesale."""
    import types
    from typing import Any

    emitted: list[Any] = []

    class _V1Builder:
        """Pre-V2 EventBuilder surface: no set_entity/set_tag."""

        def __init__(self, name: str, **kwargs: Any) -> None:
            self.name = name
            self.correlation: dict[str, Any] = {}
            self.started_at: Any = None
            self.ended_at: Any = None
            self.duration_ms: Any = None

        def set_correlation(self, **values: Any) -> None:
            self.correlation.update(values)

        def set_outcome(self, outcome: Any) -> None:
            self.outcome = outcome

        def set_severity(self, severity: Any) -> None:
            pass

        def set_delivery(self, delivery: Any) -> None:
            pass

        def set_source(self, source: Any) -> None:
            self.source = source

        def set_error(self, error: Any) -> None:
            pass

    builder_mod = types.ModuleType("observe_core.builder")
    builder_mod.EventBuilder = _V1Builder  # type: ignore[attr-defined]

    models_mod = types.ModuleType("observe_core.models")
    models_mod.SourceInfo = lambda producer: ("source", producer)  # type: ignore[attr-defined]
    models_mod.ErrorInfo = lambda **kw: kw  # type: ignore[attr-defined]

    runtime_mod = types.ModuleType("observe_core.runtime")

    class _Runtime:
        def emit(self, builder: Any, _ctx: Any) -> None:
            emitted.append(builder)

    runtime_mod.get_runtime = lambda: _Runtime()  # type: ignore[attr-defined]

    modules = {
        "observe_core.builder": builder_mod,
        "observe_core.models": models_mod,
        "observe_core.runtime": runtime_mod,
    }
    monkeypatch.setattr(
        phlo_observe,
        "_import_optional",
        lambda mod, attr=None: getattr(modules[mod], attr, None) if attr else modules.get(mod),
    )
    monkeypatch.setattr(phlo_observe, "_observe_core", lambda: object())
    monkeypatch.setattr(phlo_observe, "configure", lambda **kw: True)
    monkeypatch.setattr(phlo_observe, "enabled", lambda: True)

    phlo_observe.emit(
        "ingestion.load",
        category="data",
        outcome="success",
        correlation={"run_id": "r"},
        entities={"table": "table://raw.users"},
        tags={"k": "v"},
    )

    assert len(emitted) == 1
    assert emitted[0].correlation["run_id"] == "r"
    assert phlo_observe._unsupported_surface_warned == {"set_entity", "set_tag"}


def test_env_flag_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert phlo_observe._env_flag("PHLO_OBSERVE_ENABLED") is None
    monkeypatch.setenv("PHLO_OBSERVE_ENABLED", "true")
    assert phlo_observe._env_flag("PHLO_OBSERVE_ENABLED") is True
    monkeypatch.setenv("PHLO_OBSERVE_ENABLED", "0")
    assert phlo_observe._env_flag("PHLO_OBSERVE_ENABLED") is False


def test_configure_forwards_overrides() -> None:
    """configure() passes overrides to configure_phlo when the SDK exists."""
    captured: dict = {}

    class _FakeSdk:
        @staticmethod
        def configure_phlo(**kwargs: object) -> None:
            captured.update(kwargs)

    phlo_observe._sdk = _FakeSdk()
    phlo_observe._sdk_checked = True
    assert phlo_observe.configure(service_name="test") is True
    # No endpoint/drains configured -> stays disabled by default.
    assert captured["enabled"] is False
    assert captured["service_name"] == "test"


def test_configure_endpoint_enables_and_replaces_console() -> None:
    captured: dict = {}

    class _FakeSdk:
        @staticmethod
        def configure_phlo(**kwargs: object) -> None:
            captured.update(kwargs)

    phlo_observe._sdk = _FakeSdk()
    phlo_observe._sdk_checked = True

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OBSERVE_HTTP_ENDPOINT", "http://localhost:10010")
        assert phlo_observe.configure() is True
    assert "enabled" not in captured
    assert captured["drains"] == []
    assert captured["service_name"] == "phlo"


def test_configure_failure_contained() -> None:
    class _BadSdk:
        @staticmethod
        def configure_phlo(**kwargs: object) -> None:
            raise RuntimeError("bad config")

    phlo_observe._sdk = _BadSdk()
    phlo_observe._sdk_checked = True
    assert phlo_observe.configure() is False
    # Cached failure: no second attempt.
    assert phlo_observe.configure() is False


def test_guarded_context_swallows_raising_properties() -> None:
    """Dagster's unpartitioned-run properties raise non-AttributeError."""

    class _Ctx:
        @property
        def partition_key(self) -> str:
            raise ValueError("unpartitioned")

        @property
        def run_id(self) -> str:
            return "run-1"

        job_name = "job"

    guarded = phlo_observe._guarded(_Ctx())
    assert guarded.partition_key is None
    assert guarded.run_id == "run-1"
    assert guarded.job_name == "job"
    assert guarded.missing is None
    # Guarding is idempotent.
    assert phlo_observe._guarded(guarded) is guarded
    assert phlo_observe._guarded(None) is None


def test_null_event_interface() -> None:
    evt = phlo_observe._NullEvent()
    evt.set(a=1)
    evt.set_correlation(run_id="r")
    evt.set_entity("run", "x")
    evt.set_tag("k", "v")
    assert evt.attributes == {"a": 1}
    assert evt.correlation == {"run_id": "r"}
    assert isinstance(phlo_observe._null_scope(), contextlib.AbstractContextManager)
