"""Tests the nessie branch CLI, including diff rendering when the API
falls back to empty output."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from click.testing import CliRunner

from phlo_nessie import cli_branch


def test_diff_skips_empty_render_when_api_falls_back(monkeypatch) -> None:
    """Fallback warning should not also render an empty diff result."""
    refs = [SimpleNamespace(name="feature"), SimpleNamespace(name="main")]
    fake_client = SimpleNamespace(list_references=lambda: refs)
    print_spy = MagicMock()

    monkeypatch.setattr(cli_branch, "get_nessie_client", lambda: fake_client)
    monkeypatch.setattr(
        cli_branch,
        "get_nessie_settings",
        lambda: SimpleNamespace(nessie_host="localhost", nessie_port=19120),
    )
    monkeypatch.setattr(cli_branch.requests, "get", MagicMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(cli_branch.console, "print", print_spy)

    result = CliRunner().invoke(cli_branch.branch, ["diff", "feature", "main"])

    assert result.exit_code == 0
    rendered_messages = [call.args[0] for call in print_spy.call_args_list]
    assert "[yellow]Diff not supported by this Nessie version[/yellow]" in rendered_messages
    assert "[yellow]No differences found[/yellow]" not in rendered_messages
