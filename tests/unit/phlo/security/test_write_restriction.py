"""Tests for write-restricted service gating.

Hasura and PostgREST are write-restricted: in regulated mode writes are
blocked unless explicitly opted out, while non-regulated mode, opt-out
configuration, and non-restricted services pass through; matching is
case-insensitive.
"""

from __future__ import annotations

import pytest

from phlo.security.gating import (
    WRITE_RESTRICTED_SERVICES,
    get_write_restricted_services,
    is_write_restricted,
)


@pytest.fixture(autouse=True)
def _reset_config_cache():
    from phlo.infrastructure.config import load_project_config

    load_project_config.cache_clear()
    yield
    load_project_config.cache_clear()


def test_write_restricted_services_contains_hasura_postgrest():
    assert "hasura" in WRITE_RESTRICTED_SERVICES
    assert "postgrest" in WRITE_RESTRICTED_SERVICES


def test_get_write_restricted_services():
    result = get_write_restricted_services()
    assert "hasura" in result
    assert "postgrest" in result


def test_not_restricted_when_not_regulated(monkeypatch):
    monkeypatch.setenv("PHLO_REGULATED", "false")
    assert is_write_restricted("hasura") is False


def test_restricted_when_regulated_no_opt_in(monkeypatch):
    monkeypatch.setenv("PHLO_REGULATED", "true")
    assert is_write_restricted("hasura", regulated=True) is True


def test_not_restricted_for_non_write_restricted_service():
    assert is_write_restricted("phlo-api", regulated=True) is False
    assert is_write_restricted("superset", regulated=True) is False


def test_opt_in_disables_restriction(monkeypatch, tmp_path):
    """When phlo.yaml has surfaces.hasura.allow_writes: true, restriction is lifted."""

    config_file = tmp_path / "phlo.yaml"
    config_file.write_text("surfaces:\n  hasura:\n    allow_writes: true\n")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_REGULATED", "true")

    assert is_write_restricted("hasura", regulated=True) is False


def test_restriction_without_opt_in(monkeypatch, tmp_path):
    """When phlo.yaml exists but no opt-in, restriction stays."""

    config_file = tmp_path / "phlo.yaml"
    config_file.write_text("regulated: true\n")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_REGULATED", "true")

    assert is_write_restricted("hasura", regulated=True) is True


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("PHLO_REGULATED", "true")
    assert is_write_restricted("Hasura", regulated=True) is True
    assert is_write_restricted("PostgREST", regulated=True) is True
