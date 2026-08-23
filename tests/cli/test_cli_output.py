"""Tests for CLI user-error formatting.

Locks the message contract: summary first, then Missing, detail, and a
Run: hint, so every CLI error tells the user the next actionable command.
"""

from __future__ import annotations

from phlo.cli.output import missing_compose_file_error, missing_query_error, user_error


def test_user_error_orders_summary_detail_action() -> None:
    error = user_error(
        "could not complete operation",
        missing=".phlo/docker-compose.yml",
        details={"Profile": "observability"},
        run="phlo services init",
    )

    assert error.message == (
        "could not complete operation\n"
        "\n"
        "Missing: .phlo/docker-compose.yml\n"
        "\n"
        "Profile: observability\n"
        "\n"
        "Run: phlo services init"
    )


def test_missing_compose_file_error_is_actionable() -> None:
    error = missing_compose_file_error(".phlo/docker-compose.yml")

    assert error.message == (
        "Phlo services have not been initialized\n"
        "\n"
        "Missing: .phlo/docker-compose.yml\n"
        "\n"
        "Run: phlo services init"
    )


def test_missing_query_error_includes_command_hint() -> None:
    error = missing_query_error(command_hint='phlo trino query "SELECT 1"')

    assert error.message == (
        "no SQL query provided\n"
        "\n"
        "Provide an inline query argument or pass --file.\n"
        "\n"
        'Run: phlo trino query "SELECT 1"'
    )
