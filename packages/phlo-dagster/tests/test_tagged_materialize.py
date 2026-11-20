"""`phlo materialize` runs carry the ref they write, so Observatory can show them."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from phlo_dagster import materialization_command, tagged_materialize


class _Backend:
    name = "docker"

    def container_exec_cmd(self, *, container_name, user, env, workdir, command):
        return ["docker", "exec", container_name, *command]


def test_command_tags_runs_with_the_default_environment_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the run is started through the tagging entry point with the ref."""
    monkeypatch.setattr(materialization_command, "_default_run_ref", lambda: "main")
    monkeypatch.setattr(materialization_command, "load_project_env", lambda: {})
    argv = materialization_command.build_materialization_command(
        _Backend(), "dagster", "orders", "2026-02-03"
    )
    assert argv[3:9] == [
        "python",
        "-m",
        "phlo_dagster.tagged_materialize",
        "phlo/ref=main",
        "--",
        "-m",
    ]
    assert argv[-4:] == ["--select", "orders", "--partition", "2026-02-03"]


def test_command_without_environments_uses_the_plain_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a project without environments keeps the plain Dagster command."""
    monkeypatch.setattr(materialization_command, "_default_run_ref", lambda: None)
    monkeypatch.setattr(materialization_command, "load_project_env", lambda: {})
    argv = materialization_command.build_materialization_command(
        _Backend(), "dagster", "orders", None
    )
    assert argv[3:6] == ["dagster", "asset", "materialize"]


def test_entry_point_merges_tags_into_the_cli_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the tags reach execute_job and the CLI's own tags still win."""
    import dagster._cli as dagster_cli
    import dagster._cli.asset as asset_cli

    seen: dict = {}

    def fake_execute_job(*_args, tags=None, **_kwargs):
        seen["tags"] = tags
        return SimpleNamespace(success=True)

    def fake_main():
        seen["argv"] = list(tagged_materialize.sys.argv)
        asset_cli.execute_job(None, tags={"dagster/partition": "2026-02-03", "phlo/ref": "cli"})
        return 0

    monkeypatch.setattr(asset_cli, "execute_job", fake_execute_job)
    monkeypatch.setattr(dagster_cli, "main", fake_main)
    monkeypatch.setattr(tagged_materialize.sys, "argv", list(tagged_materialize.sys.argv))
    assert tagged_materialize.main(["phlo/ref=main", "extra=1", "--", "--select", "orders"]) == 0
    assert seen["argv"] == ["dagster", "asset", "materialize", "--select", "orders"]
    assert seen["tags"] == {"phlo/ref": "cli", "extra": "1", "dagster/partition": "2026-02-03"}


@pytest.mark.parametrize("argv", [["phlo/ref=main"], ["bad", "--"], ["=x", "--"]])
def test_entry_point_rejects_malformed_arguments(argv: list[str]) -> None:
    """Verify missing separators and malformed tags fail before Dagster starts."""
    with pytest.raises(SystemExit):
        tagged_materialize.main(argv)
