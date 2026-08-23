"""Tests for phlo_dagster.framework.definitions.

Executor selection (platform-derived vs forced flags), definition assembly with
user-workflow discovery, capability setup errors, and WAP sensor collection
gated by project policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dagster as dg
import pytest

from phlo.exceptions import PhloCapabilitySetupError

pytestmark = pytest.mark.integration


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


def test_build_definitions_merges_user_defs() -> None:
    """Builds Dagster definitions when workflow discovery returns user definitions."""
    empty_defs = dg.Definitions()
    with (
        patch("phlo_dagster.framework.definitions.get_settings", return_value=_Settings()),
        patch(
            "phlo_dagster.framework.definitions.discover_user_workflows", return_value=empty_defs
        ),
        patch("phlo_dagster.framework.definitions._default_executor", return_value=None),
    ):
        from phlo_dagster.framework.definitions import build_definitions

        result = build_definitions(workflows_path="workflows")
        assert isinstance(result, dg.Definitions)


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
            return_value=dg.Definitions(sensors=[]),
        ) as collect_wap,
    ):
        from phlo_dagster.framework.definitions import build_definitions

        result = build_definitions(workflows_path="workflows")
        assert isinstance(result, dg.Definitions)
        collect_wap.assert_called_once()


def test_wap_sensors_dev_flag_requires_truthy_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent project WAP policy skips sensors even in dev."""
    monkeypatch.setenv("PHLO_DAGSTER_DEV", "1")
    monkeypatch.setenv("PHLO_WAP_SENSORS_ENABLED", "false")

    with (
        patch(
            "phlo_dagster.framework.definitions.load_wap_config",
            return_value=SimpleNamespace(enabled=False),
        ),
        patch("phlo_dagster.framework.definitions.resolve_capability") as resolve_capability,
    ):
        from phlo_dagster.framework.definitions import _collect_wap_definitions

        assert _collect_wap_definitions() is None
        resolve_capability.assert_not_called()


def test_wap_sensors_can_be_disabled_in_project_config() -> None:
    with (
        patch(
            "phlo_dagster.framework.definitions.load_wap_config",
            return_value=SimpleNamespace(enabled=False),
        ),
        patch("phlo_dagster.framework.definitions.resolve_capability") as resolve_capability,
    ):
        from phlo_dagster.framework.definitions import _collect_wap_definitions

        assert _collect_wap_definitions() is None
        resolve_capability.assert_not_called()
