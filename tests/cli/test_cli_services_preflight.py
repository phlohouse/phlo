"""Tests for ``phlo services preflight`` (production readiness command)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner


def _setup_project(tmp_path: Path, *, environment: str = "production") -> Path:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text(f"PHLO_ENVIRONMENT={environment}\n")
    (phlo_dir / ".env.local").write_text(
        "POSTGRES_PASSWORD=independent-postgres\nMINIO_ROOT_PASSWORD=independent-minio\n"
    )
    (phlo_dir / ".env.local").chmod(0o600)
    (phlo_dir / "docker-compose.yml").write_text(
        "# Dev mode: false\nservices:\n  postgres:\n    image: x\n"
    )
    return phlo_dir


@pytest.fixture()
def isolated_config(monkeypatch: pytest.MonkeyPatch):
    """Make config-dependent checks deterministic regardless of ambient setup."""
    monkeypatch.setattr(
        "phlo.infrastructure.config.get_configured_authorization_backend_name",
        lambda: "canonical",
    )
    monkeypatch.setattr(
        "phlo.capabilities.resolve_capability",
        lambda capability, backend: (
            object() if capability == "authorization_policy_backend" else None
        ),
    )

    class _FakeRbacLoader:
        def load(self) -> dict:
            return {"policies": []}

    monkeypatch.setattr(
        "phlo.security.validation._project_rbac_loader",
        lambda: _FakeRbacLoader(),
    )


def _invoke_preflight(tmp_path: Path, *args: str, monkeypatch: pytest.MonkeyPatch, isolated_config):
    from phlo.cli.commands.services import preflight as preflight_module

    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(preflight_module.preflight_cmd, list(args))


def test_preflight_production_failure_is_nonzero_and_emits_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_config
) -> None:
    _setup_project(tmp_path)
    result = _invoke_preflight(
        tmp_path, "--production", "--json", monkeypatch=monkeypatch, isolated_config=isolated_config
    )

    assert result.exit_code != 0
    assert "production readiness failed" in result.output
    payload = json.loads(result.output.rsplit("Error:", 1)[0].strip())
    assert payload["schema_version"] == "1"
    assert payload["environment"] == "production"
    assert payload["passed"] is False


def test_preflight_json_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_config
) -> None:
    _setup_project(tmp_path)

    def _run() -> dict:
        result = _invoke_preflight(
            tmp_path,
            "--production",
            "--json",
            monkeypatch=monkeypatch,
            isolated_config=isolated_config,
        )
        raw = result.output.split("Error:", 1)[0].strip()
        payload = json.loads(raw)
        payload.pop("generated_at")
        payload.pop("report_id")
        for check in payload["checks"]:
            check.pop("observation_time")
        return payload

    first = _run()
    second = _run()
    assert first == second


def test_preflight_output_file_is_written_at_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_config
) -> None:
    _setup_project(tmp_path)
    output = tmp_path / "preflight.json"
    result = _invoke_preflight(
        tmp_path,
        "--production",
        "--json",
        "--output",
        str(output),
        monkeypatch=monkeypatch,
        isolated_config=isolated_config,
    )
    assert result.exit_code != 0
    assert output.exists()
    assert output.stat().st_mode & 0o7777 == 0o600
    payload = json.loads(output.read_text())
    assert payload["schema_version"] == "1"


def test_preflight_environment_defaults_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_config
) -> None:
    _setup_project(tmp_path, environment="production")
    result = _invoke_preflight(
        tmp_path, "--json", monkeypatch=monkeypatch, isolated_config=isolated_config
    )
    payload = json.loads(result.output.split("Error:", 1)[0].strip())
    assert payload["environment"] == "production"


def test_preflight_dev_environment_fails_env_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_config
) -> None:
    _setup_project(tmp_path, environment="dev")
    result = _invoke_preflight(
        tmp_path, "--json", monkeypatch=monkeypatch, isolated_config=isolated_config
    )
    assert result.exit_code != 0
    payload = json.loads(result.output.split("Error:", 1)[0].strip())
    env_check = next(c for c in payload["checks"] if c["id"] == "env.production")
    assert env_check["state"] == "failed"
