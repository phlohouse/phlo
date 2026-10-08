"""Fresh-process startup and lazy CLI contract regressions."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from phlo.cli.lazy import LazyPhloGroup

pytestmark = pytest.mark.core_regression


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["--version"],
        ["--help", "external", "--json"],
        ["--version", "external", "--json"],
        ["doctor", "--help"],
        ["support", "--help"],
        ["init", "--help"],
    ],
)
def test_light_startup_does_not_import_providers(args: list[str]) -> None:
    """An in-process test would hide eager imports made during collection."""
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "phlo", *args],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Phlo" in result.stdout or "phlo" in result.stdout
    modules = {
        line.rsplit("|", 1)[-1].strip().split(".", 1)[0]
        for line in result.stderr.splitlines()
        if line.startswith("import time:")
    }
    assert not modules & {"pandas", "dagster", "phlo_dagster", "phlo_dlt", "phlo_pandera"}


def test_help_metadata_matches_loaded_commands() -> None:
    """Static help may not drift from the command owners or omit bundled names."""
    from phlo.cli._plugin_help import _HELP
    from phlo.cli.commands.commands import describe_commands
    from phlo.cli.main import _BUILTIN_COMMANDS, cli
    from phlo.plugins.discovery._entry_points import entry_points_for_group

    extensions = cli.plugin_help()
    before = CliRunner().invoke(cli, ["--help"])
    describe_commands(cli)
    after = CliRunner().invoke(cli, ["--help"])
    assert before.exit_code == after.exit_code == 0
    assert before.output == after.output
    for name, (_, description) in _BUILTIN_COMMANDS.items():
        assert cli.commands[name].get_short_help_str(limit=1000) == description
    for name, description in extensions.items():
        assert cli.commands[name].get_short_help_str(limit=1000) == description
    for entry_point in entry_points_for_group("phlo.plugins.cli"):
        if entry_point.value not in _HELP:
            continue
        commands = entry_point.load()().get_cli_commands()
        advertised = set(_HELP[entry_point.value])
        # Core-owned logs/workflow intentionally win over package contributions.
        assert advertised == {command.name for command in commands} - {"logs", "workflow"}


def test_unknown_extension_resolution_preserves_local_json_option() -> None:
    """Resolve third-party commands on a miss, including pre-parse JSON intent."""
    group = LazyPhloGroup("phlo")
    loads: list[str] = []

    @click.command("external")
    @click.option("--json", "output_json", is_flag=True)
    def external(output_json: bool) -> None:
        click.echo(json.dumps({"answer": 37}) if output_json else "37")

    def load_plugins() -> None:
        loads.append("loaded")
        group.add_command(external)

    group.load_plugins = load_plugins
    help_result = CliRunner().invoke(group, ["--help"])
    assert help_result.exit_code == 0
    assert loads == []
    result = CliRunner().invoke(group, ["external", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"] == {"answer": 37}
    assert loads == ["loaded"]


def test_builtin_lookup_does_not_load_extensions() -> None:
    group = LazyPhloGroup("phlo")
    group.lazy_commands = {
        "doctor": (
            "phlo.cli.commands.doctor:doctor_cmd",
            "Diagnose local Phlo setup and service health.",
        ),
        "support": (
            "phlo.cli.commands.support:support_group",
            "Inspect the bundled, offline support contract.",
        ),
    }
    loads: list[str] = []
    group.load_plugins = lambda: loads.append("loaded")
    result = CliRunner().invoke(group, ["support", "--help"])
    assert result.exit_code == 0, result.output
    assert "status" in result.output
    assert "doctor" in group.lazy_commands
    assert loads == []
