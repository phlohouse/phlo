"""Tests phlo-dbt CLI hooks.

Pins the Dagster container lookup contract (legacy names, daemon exclusion)
and that compile_dbt runs against the discovered nested project path with an
ensured profile.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace

from phlo_dbt import hooks


def test_find_dagster_container_uses_core_service_lookup(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_find_service_container(**kwargs):
        captured.update(kwargs)
        return "demo-dagster-1"

    monkeypatch.setattr("phlo_dbt.hooks.find_service_container", _fake_find_service_container)

    assert hooks._find_dagster_container("demo") == "demo-dagster-1"
    assert captured["project_name"] == "demo"
    assert captured["service_name"] == "dagster"
    assert captured["legacy_names"] == ("demo-dagster-webserver-1",)
    assert captured["exclude_substrings"] == ("daemon",)


def test_compile_dbt_uses_discovered_nested_project_path(tmp_path: Path, monkeypatch) -> None:
    dbt_project = tmp_path / "workflows" / "client_exports" / "transforms" / "dbt"
    profiles_dir = tmp_path / ".phlo" / "dbt-profiles"
    dbt_project.mkdir(parents=True)
    (dbt_project / "dbt_project.yml").write_text("name: client_exports\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hooks.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(hooks, "get_project_name", lambda: "demo")
    monkeypatch.setattr(hooks, "_find_dagster_container", lambda _project_name: "demo-dagster-1")
    monkeypatch.setattr(
        hooks,
        "get_settings",
        lambda: SimpleNamespace(dbt_project_path=dbt_project, dbt_profiles_path=profiles_dir),
    )
    ensured_profiles: list[Path] = []
    commands: list[list[str]] = []
    monkeypatch.setattr(hooks, "ensure_dbt_profile", lambda path: ensured_profiles.append(path))
    monkeypatch.setattr(
        hooks,
        "run_command",
        lambda command, **_kwargs: (
            commands.append(command) or SimpleNamespace(returncode=0, stderr="")
        ),
    )

    assert hooks.compile_dbt() == 0

    assert ensured_profiles == [profiles_dir]

    def _parsed_remote_command(command_token: str) -> tuple[str, list[str]]:
        """Split the composed 'cd <dir> && dbt <argv>' payload."""
        change_dir, _, dbt_invocation = command_token.partition("&&")
        return change_dir.removeprefix("cd ").strip(), shlex.split(dbt_invocation)

    workdir, deps_argv = _parsed_remote_command(commands[0][-1])
    assert workdir == "/app/workflows/client_exports/transforms/dbt"
    assert deps_argv[:2] == ["dbt", "deps"]
    assert deps_argv[deps_argv.index("--profiles-dir") + 1] == "/app/.phlo/dbt-profiles"

    compile_workdir, compile_argv = _parsed_remote_command(commands[1][-1])
    assert compile_workdir == workdir
    assert compile_argv[:2] == ["dbt", "compile"]
    assert compile_argv[compile_argv.index("--profiles-dir") + 1] == ("/app/.phlo/dbt-profiles")
