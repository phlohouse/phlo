"""Verify `phlo dev` exports project and workflow environment variables to
the spawned Dagster subprocess."""

from __future__ import annotations

from subprocess import CompletedProcess

from click.testing import CliRunner

from phlo_dagster.cli_dev import dev


def test_phlo_dev_loads_project_env_for_dagster_subprocess(monkeypatch, tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1.0'\n")
    (tmp_path / "phlo.yaml").write_text(
        "env:\n  CLIENT_EXPORTS_KE_QC_OUTPUT_DIR: data/client_exports/ke_qc\n"
    )
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLIENT_EXPORTS_KE_QC_OUTPUT_DIR", raising=False)

    captured_env: dict[str, str | None] = {}

    def fake_run(cmd: list[str], check: bool, env: dict[str, str]) -> CompletedProcess[str]:
        captured_env.update(
            {
                key: env.get(key)
                for key in (
                    "PHLO_WORKFLOWS_PATH",
                    "WORKFLOWS_PATH",
                    "PHLO_PROJECT_PATH",
                    "CLIENT_EXPORTS_KE_QC_OUTPUT_DIR",
                )
            }
        )
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr("phlo_dagster.cli_dev.subprocess.run", fake_run)

    result = CliRunner().invoke(dev, ["--workflows-path", "workflows"])

    assert result.exit_code == 0
    assert captured_env == {
        "PHLO_WORKFLOWS_PATH": "workflows",
        "WORKFLOWS_PATH": "workflows",
        "PHLO_PROJECT_PATH": str(tmp_path),
        "CLIENT_EXPORTS_KE_QC_OUTPUT_DIR": "data/client_exports/ke_qc",
    }


def test_phlo_dev_does_not_mutate_parent_environment(monkeypatch, tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1.0'\n")
    (tmp_path / "workflows").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_PROJECT_PATH", "/previous/project")
    monkeypatch.setenv("PHLO_WORKFLOWS_PATH", "previous_workflows")

    def fake_run(cmd: list[str], check: bool, env: dict[str, str]) -> CompletedProcess[str]:
        assert env["PHLO_PROJECT_PATH"] == str(tmp_path)
        assert env["PHLO_WORKFLOWS_PATH"] == "workflows"
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr("phlo_dagster.cli_dev.subprocess.run", fake_run)

    result = CliRunner().invoke(dev, [])

    assert result.exit_code == 0
    assert __import__("os").environ["PHLO_PROJECT_PATH"] == "/previous/project"
    assert __import__("os").environ["PHLO_WORKFLOWS_PATH"] == "previous_workflows"
