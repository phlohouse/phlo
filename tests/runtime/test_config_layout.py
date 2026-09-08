"""Shared environment paths preserve legacy projects and layer local settings."""

import pytest

from phlo.config.env import load_project_env
from phlo.config.layout import SHARED_LAYOUT_MARKER, env_defaults_path, env_secrets_path


def test_legacy_environment_destinations(tmp_path):
    assert env_defaults_path(tmp_path) == tmp_path / ".env"
    assert env_secrets_path(tmp_path) == tmp_path / ".env.local"


def test_marker_selects_shared_destinations_before_files_exist(tmp_path):
    (tmp_path / ".gitignore").write_text(SHARED_LAYOUT_MARKER + "\noverrides/\nsecrets/\n")
    assert env_defaults_path(tmp_path) == tmp_path / "overrides/.env"
    assert env_secrets_path(tmp_path) == tmp_path / "secrets/.env"


@pytest.mark.parametrize(
    "directory,resolver", [("overrides", env_defaults_path), ("secrets", env_secrets_path)]
)
def test_existing_new_environment_selects_destination(tmp_path, directory, resolver):
    (tmp_path / directory).mkdir()
    target = tmp_path / directory / ".env"
    target.write_text("VALUE=local\n")
    assert resolver(tmp_path) == target


def test_shared_environment_layers_preserve_legacy_and_process_precedence(tmp_path, monkeypatch):
    state = tmp_path / ".phlo"
    (state / "overrides").mkdir(parents=True)
    (state / "secrets").mkdir()
    (state / ".env").write_text("SHARED_TEST_VALUE=base\nLEGACY_ONLY=retained\n")
    (state / ".env.local").write_text("SHARED_TEST_VALUE=legacy-local\n")
    (state / "overrides/.env").write_text("SHARED_TEST_VALUE=override\nOVERRIDE_ONLY=retained\n")
    (state / "secrets/.env").write_text("SHARED_TEST_VALUE=secret\n")
    env = load_project_env(tmp_path, include_os=False)
    assert env["SHARED_TEST_VALUE"] == "secret"
    assert env["LEGACY_ONLY"] == "retained"
    assert env["OVERRIDE_ONLY"] == "retained"
    monkeypatch.setenv("SHARED_TEST_VALUE", "process")
    assert load_project_env(tmp_path)["SHARED_TEST_VALUE"] == "process"
