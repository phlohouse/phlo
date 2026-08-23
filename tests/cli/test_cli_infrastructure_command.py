"""Tests for run_command error reporting.

CommandError must render command and stderr, and credential-bearing arguments
and output must be redacted before anything is exposed.
"""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest

from phlo.cli.infrastructure.command import CommandError, run_command


def test_command_error_renders_stderr() -> None:
    error = CommandError(
        cmd=("docker", "compose", "up"),
        returncode=1,
        stdout="ignored\n",
        stderr="compose failed\n",
    )

    assert error.args == (error.cmd, error.returncode, error.stdout, error.stderr)
    assert str(error) == "Command failed (1): docker compose up\ncompose failed"


def test_command_error_redacts_sensitive_output() -> None:
    error = CommandError(
        cmd=("psql", "connection string=postgresql://user:secret@localhost/db"),
        returncode=1,
        stdout="token=abc123\n",
        stderr="password=hunter2 postgres://user:secret@localhost/db\n",
    )

    rendered = str(error)

    assert error.stdout == "token=<redacted>\n"
    assert "password=<redacted>" in error.stderr
    assert "postgres://user:<redacted>@localhost/db" in error.stderr
    assert "secret" not in rendered
    assert "hunter2" not in rendered
    assert all("secret" not in part for part in error.cmd)


def test_command_error_redacts_split_sensitive_command_arguments() -> None:
    error = CommandError(
        cmd=("tool", "--token", "ghp_secret", "--name", "public"),
        returncode=1,
        stdout="",
        stderr="",
    )

    assert error.cmd == ("tool", "--token", "<redacted>", "--name", "public")
    assert "ghp_secret" not in str(error)


def test_run_command_forwards_options(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def _fake_run(args, **kwargs):
        recorded["args"] = args
        recorded.update(kwargs)
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout="ok",
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = run_command(
        ("docker", "compose", "ps"),
        timeout_seconds=5,
        cwd="/tmp/demo",
        env={"PHLO_ENV": "dev"},
    )

    assert result.stdout == "ok"
    assert recorded == {
        "args": ["docker", "compose", "ps"],
        "capture_output": True,
        "text": True,
        "timeout": 5,
        "cwd": "/tmp/demo",
        "env": {"PHLO_ENV": "dev"},
        "check": False,
    }


def test_run_command_raises_command_error_on_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(args, **kwargs):
        return CompletedProcess(
            args=args,
            returncode=17,
            stdout="",
            stderr="broken\n",
        )

    monkeypatch.setattr("subprocess.run", _fake_run)

    with pytest.raises(CommandError, match="Command failed \\(17\\): docker compose ps"):
        run_command(("docker", "compose", "ps"))
