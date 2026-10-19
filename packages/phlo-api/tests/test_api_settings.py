"""Tests for the typed phlo-api settings model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from phlo_api.settings import DEFAULT_CORS_ORIGINS, get_deployment_settings, get_settings


def test_defaults_match_previous_call_site_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    for name in (
        "PHLO_API_CORS_ORIGINS",
        "PHLO_API_AUDIT_MAX_BYTES",
        "PHLO_API_AUDIT_MAX_FILES",
        "PHLO_API_RATE_LIMIT_MATERIALIZE",
        "PHLO_API_RATE_LIMIT_RETRY",
        "PHLO_API_RATE_LIMIT_CANCEL",
        "PHLO_API_RATE_LIMIT_MUTATION",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = get_settings()
    assert settings.phlo_api_cors_origins == DEFAULT_CORS_ORIGINS
    assert settings.phlo_api_audit_max_bytes == 10 * 1024 * 1024
    assert settings.phlo_api_audit_max_files == 5
    assert (
        settings.phlo_api_rate_limit_materialize,
        settings.phlo_api_rate_limit_retry,
        settings.phlo_api_rate_limit_cancel,
        settings.phlo_api_rate_limit_mutation,
    ) == (10, 30, 60, 60)


def test_cors_origins_split_on_commas_and_drop_blanks(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_API_CORS_ORIGINS", " https://a.example , ,https://b.example,")
    assert get_settings().cors_origins() == ["https://a.example", "https://b.example"]


def test_settings_are_read_per_call(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_API_RATE_LIMIT_RETRY", "7")
    assert get_settings().phlo_api_rate_limit_retry == 7
    monkeypatch.setenv("PHLO_API_RATE_LIMIT_RETRY", "8")
    assert get_settings().phlo_api_rate_limit_retry == 8


def test_generated_api_environment_keeps_shared_controls_opt_in(tmp_path, monkeypatch) -> None:
    from phlo.plugins.compose.env import generate_env, generate_env_local
    from phlo.plugins.discovery.services import ServiceDiscovery

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("PHLO_API_OPERATION_CONTROLS_DB_URL", raising=False)
    monkeypatch.delenv("PHLO_API_OPERATION_CONTROLS_NAMESPACE", raising=False)
    service = ServiceDiscovery().get_service("phlo-api")
    assert service is not None
    state = tmp_path / ".phlo"
    state.mkdir()
    (state / ".env").write_text(generate_env([service]), encoding="utf-8")
    local = state / ".env.local"
    local.write_text(generate_env_local([service]), encoding="utf-8")
    local.chmod(0o600)
    shared_configured = bool(get_settings().phlo_api_operation_controls_db_url)
    assert shared_configured is False

    # Explicit connection strings stay in the operator's protected local overrides.
    dsn = "postgresql://fixture:fixture@postgres:5432/fixture"
    local.write_text(
        generate_env_local([service], existing_values={"PHLO_API_OPERATION_CONTROLS_DB_URL": dsn}),
        encoding="utf-8",
    )
    assert get_settings().phlo_api_operation_controls_db_url == dsn


@pytest.mark.parametrize(
    "name",
    [
        "PHLO_API_AUDIT_MAX_BYTES",
        "PHLO_API_AUDIT_MAX_FILES",
        "PHLO_API_RATE_LIMIT_MATERIALIZE",
        "PHLO_API_RATE_LIMIT_RETRY",
        "PHLO_API_RATE_LIMIT_CANCEL",
        "PHLO_API_RATE_LIMIT_MUTATION",
    ],
)
@pytest.mark.parametrize("value", ["many", "0.0", "7.0"])
def test_invalid_integer_fails_closed(tmp_path, monkeypatch, name, value) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        get_settings()


@pytest.mark.parametrize("value, expected", [("0", 0), (" +7 ", 7), ("-2", -2), ("1_024", 1024)])
def test_integer_strings_keep_int_parsing(tmp_path, monkeypatch, value, expected) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_BYTES", value)
    assert get_settings().phlo_api_audit_max_bytes == expected


@pytest.mark.parametrize(
    "name, field, enabled",
    [
        ("PHLO_V1_ACTIONS_SINGLE_REPLICA", "actions_single_replica", "1"),
        ("PHLO_V1_ACTIONS_SINGLE_PROCESS", "actions_single_process", "1"),
        ("PHLO_V1_ACTIONS_REF_TAG_CONTRACT", "actions_ref_tag_contract", "1"),
        ("PHLO_V1_QUERY_SINGLE_REPLICA", "query_single_replica", "1"),
        ("PHLO_V1_QUERY_SINGLE_PROCESS", "query_single_process", "1"),
        ("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "preview_server_limits_configured", "1"),
        ("PHLO_STAGING_SINGLE_REPLICA", "staging_single_replica", "true"),
    ],
)
def test_deployment_assertions_keep_exact_process_only_parsing(
    tmp_path, monkeypatch, name, field, enabled
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text(f"{name}={enabled}\n", encoding="utf-8")
    (phlo_dir / ".env.local").write_text(f"{name}={enabled}\n", encoding="utf-8")
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(name.lower(), enabled)
    assert getattr(get_deployment_settings(), field) is False

    for value in ("", "0", "false", "true", "TRUE", "1", "yes", "on", " 1 ", " true ", "invalid"):
        monkeypatch.setenv(name, value)
        assert getattr(get_deployment_settings(), field) is (value == enabled)
    monkeypatch.setenv(name, enabled)
    assert getattr(get_deployment_settings(), field) is True
    monkeypatch.delenv(name)
    assert getattr(get_deployment_settings(), field) is False
