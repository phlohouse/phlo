"""Tests for Hasura CLI output contracts.

Raw client exceptions stay out of user output; commands log internally and show
recoverable errors pointing at `phlo services status`.
"""

from __future__ import annotations

from click.testing import CliRunner

from phlo_hasura import cli as hasura_cli


def test_hasura_status_hides_raw_client_exception(monkeypatch) -> None:
    """Hasura commands should log raw exceptions and show recoverable user errors."""

    class FailingClient:
        def get_tracked_tables(self):
            raise RuntimeError("http://internal:8080/v1/metadata exploded")

    monkeypatch.setattr(hasura_cli, "HasuraClient", FailingClient)

    result = CliRunner().invoke(hasura_cli.hasura, ["status"])

    assert result.exit_code != 0
    assert "http://internal:8080/v1/metadata exploded" not in result.output
    assert "Error: could not read Hasura status" in result.output
    assert "Check that Hasura and Postgres services are running." in result.output
    assert "Run: phlo services status" in result.output
