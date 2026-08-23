"""Regression tests for orchestrator adapter selection.

Verifies selection precedence (explicit name beats PHLO_ORCHESTRATOR env),
whitespace trimming, and that a missing adapter raises PhloConfigError with
actionable install/config suggestions.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from phlo.config import _get_config
from phlo.exceptions import PhloConfigError
from phlo.orchestrators.selection import get_active_orchestrator

pytestmark = pytest.mark.core_regression


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """Clear cached settings across tests that mutate environment variables."""
    _get_config.cache_clear()
    yield
    _get_config.cache_clear()


def test_get_active_orchestrator_prefers_explicit_name_over_env() -> None:
    """Explicit selector argument should win over PHLO_ORCHESTRATOR."""
    registry = Mock()
    adapter = object()
    registry.get.return_value = adapter

    with (
        patch.dict(os.environ, {"PHLO_ORCHESTRATOR": "env_orchestrator"}, clear=False),
        patch("phlo.orchestrators.selection.discover_plugins") as discover_plugins_mock,
        patch("phlo.orchestrators.selection.get_global_registry", return_value=registry),
    ):
        selected = get_active_orchestrator(" explicit_orchestrator ")

    assert selected is adapter
    discover_plugins_mock.assert_called_once_with(plugin_type="orchestrator", auto_register=True)
    registry.get.assert_called_once_with("orchestrator", "explicit_orchestrator")


def test_get_active_orchestrator_uses_phlo_orchestrator_env_when_name_missing() -> None:
    """Selection should use PHLO_ORCHESTRATOR when no explicit name is provided."""
    registry = Mock()
    adapter = object()
    registry.get.return_value = adapter

    with (
        patch.dict(os.environ, {"PHLO_ORCHESTRATOR": " env_orchestrator "}, clear=False),
        patch("phlo.orchestrators.selection.discover_plugins"),
        patch("phlo.orchestrators.selection.get_global_registry", return_value=registry),
    ):
        selected = get_active_orchestrator()

    assert selected is adapter
    registry.get.assert_called_once_with("orchestrator", "env_orchestrator")


def test_get_active_orchestrator_missing_adapter_raises_guided_config_error() -> None:
    """Missing adapter should raise PhloConfigError with actionable suggestions."""
    registry = Mock()
    registry.get.return_value = None
    registry.list.return_value = ["dagster"]

    with (
        patch(
            "phlo.orchestrators.selection.get_settings",
            return_value=SimpleNamespace(phlo_orchestrator="missing_orchestrator"),
        ),
        patch("phlo.orchestrators.selection.discover_plugins"),
        patch("phlo.orchestrators.selection.get_global_registry", return_value=registry),
        pytest.raises(PhloConfigError) as exc_info,
    ):
        get_active_orchestrator()

    assert "Orchestrator adapter 'missing_orchestrator' is not installed." in str(exc_info.value)
    assert exc_info.value.suggestions == [
        "Install a package that provides 'missing_orchestrator'",
        "Set PHLO_ORCHESTRATOR to an installed adapter name",
    ]
    registry.list.assert_called_once_with("orchestrator")
