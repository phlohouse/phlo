"""Tests for the typed phlo-api settings model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from phlo_api.settings import DEFAULT_CORS_ORIGINS, get_settings


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
