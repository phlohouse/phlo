"""Tests for core Phlo configuration.

Covers env-driven settings loading, cache identity within one project root,
and isolation of cached config between different project roots selected by
cwd or environment.
"""

import os
from unittest.mock import patch

import pytest
from pydantic import BaseModel

import phlo
from phlo.config import Settings, _get_config, get_settings, workflow_settings

pytestmark = pytest.mark.core_regression


class TestConfigUnitTests:
    """Unit tests for core configuration loading and caching."""

    def test_config_loads_environment_variables_correctly(self):
        """Test that core settings load environment variables correctly."""
        env_vars = {
            "PHLO_LOG_LEVEL": "DEBUG",
            "PHLO_LOG_FORMAT": "json",
            "PLUGINS_ENABLED": "false",
            "PHLO_ORCHESTRATOR": "custom",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            test_config = Settings()

            assert test_config.phlo_log_level == "DEBUG"
            assert test_config.phlo_log_format == "json"
            assert test_config.plugins_enabled is False
            assert test_config.phlo_orchestrator == "custom"

    def test_config_handles_caching_and_returns_same_instance(self):
        """Test that config handles caching and returns the same instance."""
        env_vars = {
            "PHLO_LOG_LEVEL": "INFO",
            "PLUGINS_ENABLED": "true",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            _get_config.cache_clear()

            config1 = _get_config()
            config2 = _get_config()

            assert config1 is config2
            assert id(config1) == id(config2)


@pytest.mark.parametrize("selection", ["cwd", "env"])
def test_cached_config_isolated_between_project_roots(tmp_path, monkeypatch, selection) -> None:
    """Configuration caches must distinguish projects selected by cwd or env."""
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    for project, level in ((project_a, "INFO"), (project_b, "DEBUG")):
        (project / ".phlo").mkdir(parents=True)
        (project / ".phlo" / ".env.local").write_text(f"PHLO_LOG_LEVEL={level}\n")

    _get_config.cache_clear()
    monkeypatch.delenv("PHLO_LOG_LEVEL", raising=False)
    monkeypatch.delenv("PHLO_PROJECT_PATH", raising=False)

    if selection == "cwd":
        monkeypatch.chdir(project_a)
        first = get_settings()
        monkeypatch.chdir(project_b)
        second = get_settings()
    else:
        monkeypatch.setenv("PHLO_PROJECT_PATH", str(project_a))
        first = get_settings()
        monkeypatch.setenv("PHLO_PROJECT_PATH", str(project_b))
        second = get_settings()

    assert first.phlo_log_level == "INFO"
    assert second.phlo_log_level == "DEBUG"
    assert first is not second
    assert get_settings(project_root=project_a) is first
    assert get_settings(project_root=project_b) is second


class WorkflowSettings(BaseModel):
    endpoint: str
    batch_size: int
    debug: bool = False
    api_token: str


def test_workflow_settings_load_committed_defaults_and_coerces_types(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "phlo.yaml").write_text(
        """
settings:
  endpoint: https://api.example.test
  batch_size: "250"
  debug: "true"
  api_token: committed-placeholder
"""
    )
    monkeypatch.chdir(tmp_path)

    config = workflow_settings(schema=WorkflowSettings)

    assert config.endpoint == "https://api.example.test"
    assert config.batch_size == 250
    assert config.debug is True
    assert config.api_token == "committed-placeholder"


def test_workflow_settings_local_env_and_os_override_phlo_yaml(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / ".phlo" / ".env.local").write_text(
        "API_TOKEN=local-secret\nPHLO_SETTINGS__BATCH_SIZE=500\n"
    )
    (tmp_path / "phlo.yaml").write_text(
        """
settings:
  endpoint: https://api.example.test
  batch_size: 250
  api_token: committed-placeholder
"""
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_SETTINGS__API_TOKEN", "os-secret")

    config = workflow_settings(schema=WorkflowSettings)

    assert config.batch_size == 500
    assert config.api_token == "os-secret"


def test_workflow_settings_reports_missing_required_values(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "phlo.yaml").write_text(
        """
settings:
  batch_size: 250
  api_token: token
"""
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="workflow settings missing required value.*endpoint"):
        workflow_settings(schema=WorkflowSettings)


def test_workflow_settings_reads_namespace_defaults_and_overrides(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / ".phlo" / ".env.local").write_text("PHLO_SETTINGS__INGEST__API_TOKEN=local\n")
    (tmp_path / "phlo.yaml").write_text(
        """
settings:
  api_token: shared-token
  ingest:
    endpoint: https://ingest.example.test
    batch_size: "100"
    debug: false
workflows:
  ingest:
    settings:
      debug: true
"""
    )
    monkeypatch.chdir(tmp_path)

    config = workflow_settings("ingest", schema=WorkflowSettings)

    assert config.endpoint == "https://ingest.example.test"
    assert config.batch_size == 100
    assert config.debug is True
    assert config.api_token == "local"


def test_top_level_settings_export_is_lazy_helper() -> None:
    assert phlo.settings is workflow_settings
