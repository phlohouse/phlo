"""Tests for the root ``phlo test`` command.

Verifies runner selection (uv run pytest when available, direct pytest
otherwise), that integration tests are deselected in the uv path, and that
a fresh project with no collected tests still exits successfully.
"""

from click.testing import CliRunner

from phlo.cli.main import cli


def test_phlo_test_prefers_uv_project_environment(monkeypatch, tmp_path) -> None:
    """Use `uv run pytest` so generated project dev dependencies are honored."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo.cli.main.shutil.which", lambda name: "/usr/bin/uv")
    calls: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(args: list[str], check: bool = False):
        calls.append(args)
        return Result()

    monkeypatch.setattr("phlo.cli.main.subprocess.run", fake_run)

    result = CliRunner().invoke(cli, ["test", "--local"])

    assert result.exit_code == 0
    assert calls == [["uv", "run", "pytest", "-m", "not integration"]]


def test_phlo_test_keeps_direct_pytest_fallback(monkeypatch, tmp_path) -> None:
    """Fallback to direct pytest when uv is unavailable."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo.cli.main.shutil.which", lambda name: None)
    calls: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(args: list[str], check: bool = False):
        calls.append(args)
        return Result()

    monkeypatch.setattr("phlo.cli.main.subprocess.run", fake_run)

    result = CliRunner().invoke(cli, ["test"])

    assert result.exit_code == 0
    assert calls == [["pytest"]]


def test_phlo_test_treats_empty_fresh_suite_as_success(monkeypatch, tmp_path) -> None:
    """Fresh generated projects should not fail their first `phlo test` solely for no tests."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo.cli.main.shutil.which", lambda name: "/usr/bin/uv")

    class Result:
        returncode = 5

    monkeypatch.setattr("phlo.cli.main.subprocess.run", lambda *args, **kwargs: Result())

    result = CliRunner().invoke(cli, ["test"])

    assert result.exit_code == 0
    assert "No tests were collected" in result.output
