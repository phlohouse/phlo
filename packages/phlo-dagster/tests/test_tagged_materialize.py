"""`phlo materialize` runs carry the ref they write, so Observatory can show them."""

from __future__ import annotations

from pathlib import Path
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
    monkeypatch.setattr(materialization_command, "_default_run_target", lambda: ("main", "defs"))
    monkeypatch.setattr(materialization_command, "load_project_env", lambda: {})
    argv = materialization_command.build_materialization_command(
        _Backend(), "dagster", "orders", "2026-02-03"
    )
    assert argv[3:11] == [
        "python",
        "-m",
        "phlo_dagster.tagged_materialize",
        "--location",
        "defs",
        "phlo/ref=main",
        "--",
        "-m",
    ]
    assert argv[-4:] == ["--select", "orders", "--partition", "2026-02-03"]


def test_command_without_environments_uses_the_plain_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a project without environments keeps the plain Dagster command."""
    monkeypatch.setattr(materialization_command, "_default_run_target", lambda: None)
    monkeypatch.setattr(materialization_command, "load_project_env", lambda: {})
    argv = materialization_command.build_materialization_command(
        _Backend(), "dagster", "orders", None
    )
    assert argv[3:6] == ["dagster", "asset", "materialize"]


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "workspace.yaml").write_text(
        "load_from:\n  - grpc_server:\n      host: code\n      port: 4000\n"
        "      location_name: defs\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DAGSTER_HOME", str(tmp_path))


def test_entry_point_records_tags_and_the_code_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the run gets the tags, the CLI's own tags win, and the run has a location."""
    import dagster as dg
    import dagster._cli as dagster_cli
    import dagster._cli.asset as asset_cli

    _workspace(tmp_path, monkeypatch)
    seen: dict = {}

    @dg.asset
    def orders() -> None:
        return None

    job = dg.define_asset_job("__ASSET_JOB", selection=[orders])
    job_def = dg.Definitions(assets=[orders], jobs=[job]).resolve_job_def("__ASSET_JOB")

    def fake_execute_job(*_args, tags=None, **_kwargs):
        seen["tags"] = tags
        with dg.DagsterInstance.ephemeral() as instance:
            run = instance.create_run_for_job(job_def, tags=tags)
            seen["origin"] = run.remote_job_origin
        return SimpleNamespace(success=True)

    def fake_main():
        seen["argv"] = list(tagged_materialize.sys.argv)
        asset_cli.execute_job(None, tags={"dagster/partition": "2026-02-03", "phlo/ref": "cli"})
        return 0

    monkeypatch.setattr(asset_cli, "execute_job", fake_execute_job)
    monkeypatch.setattr(dagster_cli, "main", fake_main)
    monkeypatch.setattr(
        dg.DagsterInstance, "create_run_for_job", dg.DagsterInstance.create_run_for_job
    )
    monkeypatch.setattr(tagged_materialize.sys, "argv", list(tagged_materialize.sys.argv))
    argv = ["--location", "defs", "phlo/ref=main", "extra=1", "--", "--select", "orders"]
    assert tagged_materialize.main(argv) == 0
    assert seen["argv"] == ["dagster", "asset", "materialize", "--select", "orders"]
    assert seen["tags"] == {"phlo/ref": "cli", "extra": "1", "dagster/partition": "2026-02-03"}
    assert seen["origin"].location_name == "defs"
    assert seen["origin"].repository_origin.repository_name == "__repository__"
    assert seen["origin"].job_name == "__ASSET_JOB"


def test_entry_point_refuses_an_unknown_code_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a location the workspace does not serve fails before Dagster starts."""
    _workspace(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="does not name code location 'other'"):
        tagged_materialize.main(["--location", "other", "phlo/ref=main", "--"])


@pytest.mark.parametrize(
    "argv", [["phlo/ref=main"], ["bad", "--"], ["=x", "--"], ["--location", "--"]]
)
def test_entry_point_rejects_malformed_arguments(argv: list[str]) -> None:
    """Verify missing separators and malformed tags fail before Dagster starts."""
    with pytest.raises(SystemExit):
        tagged_materialize.main(argv)
