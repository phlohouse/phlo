"""Tests for infrastructure CLI helpers such as .env file parsing.

parse_env_file ignores comments and malformed lines and only strips
quotes on request. get_project_config falls back to a derived default
when phlo.yaml is missing or not a mapping; container naming prefers an
explicit override over the "{project}_{service}" pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo.cli.infrastructure import utils as infra_utils
from phlo.infrastructure import containers as infra_containers


def test_parse_env_file_ignores_comments_and_optionally_strips_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "PLAIN=value",
                "QUOTED='quoted value'",
                'DOUBLE="double value"',
                "MALFORMED",
            ]
        )
    )

    assert infra_utils.parse_env_file(env_file) == {
        "PLAIN": "value",
        "QUOTED": "'quoted value'",
        "DOUBLE": '"double value"',
    }
    assert infra_utils.parse_env_file(env_file, strip_quotes=True) == {
        "PLAIN": "value",
        "QUOTED": "quoted value",
        "DOUBLE": "double value",
    }


def test_get_project_config_reads_mapping_and_falls_back_for_invalid_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "phlo.yaml"
    config_path.write_text("name: Demo Project\ndescription: custom\n")
    monkeypatch.chdir(tmp_path)

    assert infra_utils.get_project_config()["name"] == "Demo Project"

    config_path.write_text("- not-a-mapping\n")

    assert infra_utils.get_project_config() == {
        "name": tmp_path.name.lower().replace("_", "-"),
        "description": "Phlo data lakehouse",
    }


def test_resolve_container_name_uses_override_then_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Infra:
        container_naming_pattern = "{project}_{service}"

        def __init__(self, configured: str | None) -> None:
            self._configured = configured

        def get_container_name(self, _service_name: str, _project_name: str) -> str | None:
            return self._configured

    monkeypatch.setattr(
        "phlo.infrastructure.containers.load_infrastructure_config",
        lambda: _Infra("custom-container"),
    )
    assert infra_containers.resolve_container_name("dagster", "demo") == "custom-container"

    monkeypatch.setattr(
        "phlo.infrastructure.containers.load_infrastructure_config",
        lambda: _Infra(None),
    )
    assert infra_containers.resolve_container_name("dagster", "demo") == "demo_dagster"
