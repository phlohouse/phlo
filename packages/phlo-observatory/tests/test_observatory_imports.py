"""Unit tests for the phlo-observatory import surface.

Locks the public import contract: neutral settings re-export through both the
package root and the settings service module, the plugin module imports, and
no app module is exposed.
"""

import importlib

import pytest

pytestmark = pytest.mark.unit


def test_observatory_importable():
    """Test that phlo_observatory is importable."""
    import phlo_observatory

    assert phlo_observatory is not None


def test_observatory_reexports_neutral_settings_contract() -> None:
    """The public package surface exposes the neutral settings contract."""
    import phlo_observatory
    from phlo.plugins.observatory_settings import SettingsStore

    assert phlo_observatory.SettingsStore is SettingsStore
    assert "SettingsStore" in phlo_observatory.__all__
    assert "SettingsService" not in phlo_observatory.__all__


def test_settings_service_module_reexports_neutral_settings_contract() -> None:
    """The compatibility module remains importable without concrete storage."""
    from phlo.plugins.observatory_settings import SettingsStore
    from phlo_observatory import settings_service

    assert settings_service.SettingsStore is SettingsStore
    assert "SettingsService" not in settings_service.__all__


def test_observatory_plugin_module_importable():
    """Test that the service plugin module is importable."""
    plugin_module = importlib.import_module("phlo_observatory.plugin")
    assert plugin_module is not None


def test_observatory_app_module_absent():
    """Test that legacy app module is not exposed by this package."""
    with pytest.raises(ModuleNotFoundError, match="phlo_observatory.app"):
        importlib.import_module("phlo_observatory.app")
