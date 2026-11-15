"""Tests for phlo_dagster.framework.definitions.

Executor selection (platform-derived vs forced flags), definition assembly with
user-workflow discovery, capability setup errors, and WAP sensors that honour
the current project policy on every tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import dagster as dg
import pytest

from phlo.exceptions import PhloCapabilitySetupError


@dataclass
class _Settings:
    """Minimal settings stub used to control executor selection in tests."""

    phlo_force_in_process_executor: bool = False
    phlo_force_multiprocess_executor: bool = False
    phlo_host_platform: str | None = None
    phlo_orchestrator: str = "dagster"
    phlo_wap_enabled: bool = True


def test_default_executor_honors_platform() -> None:
    """Selects executor type from detected platform when no force flags are set."""
    with patch("phlo_dagster.framework.definitions.get_settings", return_value=_Settings()):
        with patch("platform.system", return_value="Darwin"):
            from phlo_dagster.framework.definitions import _default_executor

            executor = _default_executor()
            assert executor is not None
            assert executor.name == "in_process"

        with patch("platform.system", return_value="Linux"):
            from phlo_dagster.framework.definitions import _default_executor

            executor = _default_executor()
            assert executor is not None
            assert executor.name == "multiprocess"


def test_default_executor_honors_force_flags() -> None:
    """Prefers explicit force flags over platform-derived defaults."""
    settings = _Settings(phlo_force_in_process_executor=True)
    with patch("phlo_dagster.framework.definitions.get_settings", return_value=settings):
        from phlo_dagster.framework.definitions import _default_executor

        executor = _default_executor()
        assert executor is not None
        assert executor.name == "in_process"

    settings = _Settings(phlo_force_multiprocess_executor=True)
    with patch("phlo_dagster.framework.definitions.get_settings", return_value=settings):
        from phlo_dagster.framework.definitions import _default_executor

        executor = _default_executor()
        assert executor is not None
        assert executor.name == "multiprocess"


def test_build_definitions_raises_required_capability_setup_error() -> None:
    with (
        patch("phlo_dagster.framework.definitions.get_settings", return_value=_Settings()),
        patch(
            "phlo_dagster.framework.definitions.discover_user_workflows",
            side_effect=PhloCapabilitySetupError(
                capability="dbt",
                required=True,
                message="dbt asset discovery failed: manifest_unavailable",
            ),
        ),
        patch("phlo_dagster.framework.definitions._default_executor", return_value=None),
    ):
        from phlo_dagster.framework.definitions import build_definitions

        with pytest.raises(PhloCapabilitySetupError, match="manifest_unavailable"):
            build_definitions(workflows_path="workflows")


def test_build_definitions_merges_wap_sensors_when_versioned_catalog_present() -> None:
    empty_defs = dg.Definitions()
    versioned_catalog = MagicMock()

    @dg.op
    def _noop() -> None:
        return None

    @dg.job
    def _wap_job() -> None:
        _noop()

    @dg.sensor(job=_wap_job)
    def wap_sensor(context: dg.SensorEvaluationContext) -> dg.SkipReason:
        return dg.SkipReason("test")

    with (
        patch("phlo_dagster.framework.definitions.get_settings", return_value=_Settings()),
        patch(
            "phlo_dagster.framework.definitions.discover_user_workflows", return_value=empty_defs
        ),
        patch("phlo_dagster.framework.definitions._default_executor", return_value=None),
        patch(
            "phlo_dagster.framework.definitions.resolve_capability",
            return_value=MagicMock(
                name="nessie",
                provider=versioned_catalog,
                support=MagicMock(supports_refs=True, supports_promote=True),
            ),
        ),
        patch(
            "phlo_dagster.framework.definitions.VersionedCatalog", MagicMock(return_value=object)
        ),
        patch(
            "phlo_dagster.framework.definitions._collect_wap_definitions",
            return_value=dg.Definitions(sensors=[wap_sensor]),
        ) as collect_wap,
    ):
        from phlo_dagster.framework.definitions import build_definitions

        result = build_definitions(workflows_path="workflows")
        assert isinstance(result, dg.Definitions)
        collect_wap.assert_called_once()
        assert [sensor.name for sensor in result.sensors].count("wap_sensor") == 1


def test_wap_sensors_follow_project_policy_changed_after_definitions_load(
    monkeypatch, tmp_path
) -> None:
    """A long-lived code location must pick up WAP enabled after it loaded (#1261)."""
    from phlo.capabilities.resolver import ResolutionResult
    from phlo.capabilities.support import CapabilitySupport
    from phlo.infrastructure import clear_config_cache

    class _Catalog:
        def list_branches(self): ...
        def get_branch_hash(self, name): ...
        def create_branch(self, name, from_ref="main"): ...
        def merge_branch(self, source, target="main", **kwargs): ...
        def delete_branch(self, name, **kwargs): ...

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    config = tmp_path / "phlo.yaml"
    config.write_text("name: warehouse\n", encoding="utf-8")
    clear_config_cache()
    resolution = ResolutionResult(
        "catalog",
        "nessie",
        _Catalog(),
        {},
        CapabilitySupport(supports_refs=True, supports_promote=True),
    )
    with patch("phlo_dagster.framework.definitions.resolve_capability", return_value=resolution):
        from phlo_dagster.framework.definitions import _collect_wap_definitions

        wap_defs = _collect_wap_definitions()
    assert wap_defs is not None
    sensors = {sensor.name: sensor for sensor in wap_defs.sensors}
    assert set(sensors) == {
        "wap_auto_promotion_sensor",
        "wap_branch_cleanup_sensor",
        "wap_candidate_cleanup_sensor",
    }

    from phlo_dagster import wap_sensors

    context = MagicMock(cursor=None)
    for sensor in sensors.values():
        assert isinstance(sensor._raw_fn(context), dg.SkipReason)
    context.instance.get_runs.assert_not_called()

    config.write_text("name: warehouse\nwap:\n  enabled: true\n", encoding="utf-8")
    context.instance.get_runs.return_value = []
    with patch.object(wap_sensors, "_load_versioned_catalog", return_value=MagicMock()):
        assert sensors["wap_auto_promotion_sensor"]._raw_fn(context) is None
    context.instance.get_runs.assert_called_once()
    # A branch project never runs the snapshot cleanup sensor.
    assert isinstance(sensors["wap_candidate_cleanup_sensor"]._raw_fn(context), dg.SkipReason)
    clear_config_cache()


def test_snapshot_catalog_serves_wap_sensors_enabled_after_load(monkeypatch, tmp_path) -> None:
    """A snapshot-only catalog loaded with WAP disabled still registers sensors (#1261)."""
    from phlo_dagster import wap_sensors

    from phlo.capabilities.resolver import ResolutionResult
    from phlo.capabilities.support import CapabilitySupport
    from phlo.infrastructure import clear_config_cache

    class _SnapshotCatalog:
        def create_candidate(self, *, table_name, run_id): ...
        def list_candidates(self, *, namespace): ...
        def promote_candidates(self, **kwargs): ...
        def resolve_release(self, *, table_name): ...
        def release_revision(self): ...
        def abort_candidates(self, *, namespace): ...
        def prune_candidates(self, *, older_than): ...

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    config = tmp_path / "phlo.yaml"
    config.write_text("name: warehouse\n", encoding="utf-8")
    clear_config_cache()
    resolution = ResolutionResult(
        "catalog",
        "polaris",
        _SnapshotCatalog(),
        {},
        CapabilitySupport(supports_refs=False, supports_snapshots=True, supports_promote=True),
    )
    with patch("phlo_dagster.framework.definitions.resolve_capability", return_value=resolution):
        from phlo_dagster.framework.definitions import _collect_wap_definitions

        wap_defs = _collect_wap_definitions()
    assert wap_defs is not None
    sensors = {sensor.name: sensor for sensor in wap_defs.sensors}

    config.write_text(
        "name: warehouse\nwap:\n  enabled: true\n  strategy: snapshot\n", encoding="utf-8"
    )
    context = MagicMock(cursor=None)
    # The branch cleanup sensor would raise on a snapshot-only provider; it must skip.
    with patch.object(wap_sensors, "_load_versioned_catalog", side_effect=RuntimeError):
        assert isinstance(sensors["wap_branch_cleanup_sensor"]._raw_fn(context), dg.SkipReason)
    with (
        patch.object(wap_sensors, "_load_snapshot_promotion_catalog") as load_catalog,
        patch.object(wap_sensors, "_iter_snapshot_strategy_reports", return_value=[]),
    ):
        assert sensors["wap_candidate_cleanup_sensor"]._raw_fn(context) is None
    load_catalog.assert_called_once()
    clear_config_cache()
