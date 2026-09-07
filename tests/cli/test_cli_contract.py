"""Behavioural contracts for agent output and native terminal/pipe invocations.

Exercise the actual Click boundary, including parse errors before callbacks and
passthrough arguments. The tests assert that unsupported JSON requests cannot
execute work and that machine consumers never need to parse human diagnostics.
"""

import json

import click
from click.testing import CliRunner

from phlo.cli.contract import PhloCommand, PhloGroup
from phlo.cli.output import json_envelope, user_error


def test_machine_parse_error_is_one_json_document():
    @click.command(cls=PhloCommand)
    @click.option("--json", "output_json", is_flag=True)
    @click.option("--count", type=int)
    def command(output_json, count):
        raise AssertionError("Must not execute on invalid input")

    result = CliRunner().invoke(command, ["--count", "bad", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == payload["exit_code"] == 2
    assert payload["reason_code"] == "invalid_arguments"
    assert payload["status"] == "error"
    assert "count" in payload["errors"][0]


def test_machine_failure_carries_same_recovery_as_human():
    @click.command(cls=PhloCommand)
    @click.option("--json", "output_json", is_flag=True)
    def command(output_json):
        raise user_error("Missing project", reason_code="project_missing", run="phlo init")

    runner = CliRunner()
    human = runner.invoke(command, [])
    machine = runner.invoke(command, ["--json"])
    payload = json.loads(machine.stdout)
    assert human.exit_code == machine.exit_code == 1
    assert "phlo init" in human.stderr
    assert payload["reason_code"] == "project_missing"
    assert payload["next_steps"][0]["command"] == "phlo init"


def test_global_json_selects_registered_plugin_renderer():
    @click.group(cls=PhloGroup)
    def root():
        pass

    @click.group()
    def plugin():
        pass

    root.add_command(plugin)

    # Late registration is common for built-in service and optional packages.
    @plugin.command()
    @click.option("--json", "output_json", is_flag=True)
    def status(output_json):
        click.echo(json.dumps({"running": True}) if output_json else "Running")

    result = CliRunner().invoke(root, ["--json", "plugin", "status"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"] == {"running": True}


def test_global_json_refuses_unsupported_command_before_mutation():
    @click.group(cls=PhloGroup)
    def root():
        pass

    @root.command()
    def mutate():
        raise AssertionError("Must not execute unsupported commands")

    result = CliRunner().invoke(root, ["--json", "mutate"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["reason_code"] == "json_not_supported"


def test_native_passthrough_json_flag_is_not_phlo_output():
    @click.group(cls=PhloGroup)
    def root():
        pass

    @root.command(context_settings={"ignore_unknown_options": True})
    @click.argument("args", nargs=-1, type=click.UNPROCESSED)
    def child(args):
        click.echo("|".join(args))

    for args in [["child", "--", "program", "--json"], ["child", "program", "--json"]]:
        result = CliRunner().invoke(root, args)
        assert result.exit_code == 0
        assert result.stdout == "program|--json\n"


def test_json_as_option_value_does_not_change_output_mode():
    @click.command(cls=PhloCommand)
    @click.option("--json", "output_json", is_flag=True)
    @click.option("--value")
    def command(output_json, value):
        click.echo(value)

    result = CliRunner().invoke(command, ["--value", "--json"])
    assert result.exit_code == 0
    assert result.stdout == "--json\n"


def test_native_json_format_remains_a_raw_document():
    @click.command(cls=PhloCommand)
    @click.option("--format", "output_format")
    def command(output_format):
        click.echo('{"raw": true}')

    result = CliRunner().invoke(command, ["--format", "json"])
    assert json.loads(result.stdout) == {"raw": True}


def test_partial_result_preserved_and_never_exits_successfully():
    @click.command(cls=PhloCommand)
    @click.option("--json", "output_json", is_flag=True)
    def command(output_json):
        click.echo(
            json_envelope(data={"completed": ["one"]}, status="partial", errors=["two failed"])
        )

    result = CliRunner().invoke(command, ["--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["data"]["completed"] == ["one"]
    assert payload["status"] == "partial"


def test_internal_exception_does_not_expose_secret_details():
    @click.command(cls=PhloCommand)
    @click.option("--json", "output_json", is_flag=True)
    def command(output_json):
        raise ValueError("password=very-secret")

    result = CliRunner().invoke(command, ["--json"])
    assert "very-secret" not in result.output
    assert json.loads(result.stdout)["reason_code"] == "internal_error"


def test_no_json_or_noninteractive_policy_leaks_between_invocations():
    @click.group(cls=PhloGroup)
    def root():
        pass

    @root.command()
    @click.option("--json", "output_json", is_flag=True)
    def status(output_json):
        click.echo(json_envelope(data=True) if output_json else "human")

    runner = CliRunner()
    assert runner.invoke(root, ["--json", "status"]).exit_code == 0
    assert runner.invoke(root, ["status"]).stdout == "human\n"


def test_adaptation_preserves_package_callback_identity_and_direct_calls():
    @click.group(cls=PhloGroup)
    def root():
        pass

    @click.command()
    @click.argument("value")
    def plugin(value):
        return value

    original_callback = plugin.callback
    root.add_command(plugin)
    assert plugin.callback is original_callback
    assert plugin.callback("direct") == "direct"


def test_authorization_reason_survives_machine_boundary(monkeypatch):
    from types import SimpleNamespace

    from phlo.cli import authorization, authorization_wrappers
    from phlo.cli.authorization_wrappers import require_mutation_authorization

    monkeypatch.setattr(authorization_wrappers, "check_cli_surface_active", lambda: True)
    denial = SimpleNamespace(allowed=False, reason_code="forbidden", explanation="Access denied")
    adapter = SimpleNamespace(enforce_mutation=lambda *_args: denial)
    monkeypatch.setattr(authorization, "get_cli_adapter", lambda: adapter)

    @click.command(cls=PhloCommand)
    @click.option("--json", "output_json", is_flag=True)
    @require_mutation_authorization("services.reset")
    def command(output_json):
        raise AssertionError("Denied commands must not run")

    result = CliRunner().invoke(command, ["--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["reason_code"] == "forbidden"
    assert "Access denied" in payload["errors"][0]


def test_variadic_command_path_json_still_uses_boundary():
    @click.command(cls=PhloCommand)
    @click.argument("path", nargs=-1)
    @click.option("--json", "output_json", is_flag=True)
    def command(path, output_json):
        raise click.BadParameter("Unknown command path", param_hint="PATH")

    result = CliRunner().invoke(command, ["services", "oops", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["reason_code"] == "invalid_arguments"
