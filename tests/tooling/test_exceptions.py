"""Tests for `phlo.exceptions` formatting and suggestion helpers.

PhloError messages carry a stable error code, numbered suggestions,
cause chain, and docs URL, with optional sections omitted when unset.
_redact_sensitive scrubs password/token/key material — including PEM
bodies and token-only URL userinfo — before anything reaches the
rendered message.
"""

from __future__ import annotations

from phlo.exceptions import (
    PhloError,
    PhloErrorCode,
    _redact_sensitive,
    format_field_list,
    suggest_similar_field_names,
)


def test_phlo_error_formats_message_with_code_suggestions_cause_and_docs() -> None:
    """Includes all rich formatting sections when suggestions and cause are present."""
    error = PhloError(
        message="Schema field missing",
        code=PhloErrorCode.SCHEMA_MISMATCH,
        suggestions=[
            "Check your validation schema",
            "Confirm field names match source data",
        ],
        cause=ValueError("missing field: observation_id"),
    )

    message = str(error)

    assert message.startswith("PhloError (PHLO-002): Schema field missing")
    assert "Suggested actions:" in message
    assert "  1. Check your validation schema" in message
    assert "  2. Confirm field names match source data" in message
    assert "Caused by: ValueError: missing field: observation_id" in message
    assert "Documentation: https://docs.phlo.dev/errors/PHLO-002" in message


def test_phlo_error_formats_minimal_message_without_optional_sections() -> None:
    """Omits suggestion/cause sections when not provided."""
    error = PhloError(
        message="DLT source failed",
        code=PhloErrorCode.DLT_SOURCE_ERROR,
    )

    message = str(error)

    assert "Suggested actions:" not in message
    assert "Caused by:" not in message
    assert message == (
        "PhloError (PHLO-301): DLT source failed\n\n"
        "Documentation: https://docs.phlo.dev/errors/PHLO-301"
    )


def test_suggest_similar_field_names_returns_fuzzy_matches() -> None:
    """Returns did-you-mean suggestions when close field matches exist."""
    suggestions = suggest_similar_field_names(
        invalid_field="temperatur",
        valid_fields=["temperature", "timestamp", "city", "humidity"],
        max_suggestions=2,
    )

    assert suggestions
    assert suggestions[0] == "Did you mean 'temperature'?"
    assert len(suggestions) <= 2
    assert all(suggestion.startswith("Did you mean '") for suggestion in suggestions)


def test_suggest_similar_field_names_returns_available_fields_when_no_match() -> None:
    """Falls back to listing available fields when fuzzy matching yields no results."""
    suggestions = suggest_similar_field_names(
        invalid_field="planet",
        valid_fields=["city", "country"],
    )

    assert suggestions == ["Available fields: city, country"]


def test_format_field_list_quotes_and_joins_fields() -> None:
    """Formats each field with single quotes and comma separators."""
    formatted = format_field_list(["id", "city", "temperature"])

    assert formatted == "'id', 'city', 'temperature'"


def test_format_field_list_handles_empty_field_list() -> None:
    """Returns an empty string when there are no fields."""
    formatted = format_field_list([])

    assert formatted == ""


def test_phlo_error_redacts_sensitive_data_in_cause() -> None:
    """Sensitive patterns in cause messages are redacted."""
    error = PhloError(
        message="Connection failed",
        code=PhloErrorCode.INFRASTRUCTURE_ERROR,
        cause=ValueError("connection string: password=secret123"),
    )
    message = str(error)
    assert "Caused by: ValueError: connection string=<redacted>" in message
    assert "secret123" not in message


def test_redact_sensitive_handles_colon_delimited_secret_values() -> None:
    """Common `key: value` secret formats are redacted."""
    assert _redact_sensitive("password: secret123") == "password=<redacted>"
    assert _redact_sensitive("token: abc123") == "token=<redacted>"
    assert _redact_sensitive("connection string: Server=db;Password=hunter2") == (
        "connection string=<redacted>"
    )


def test_redact_sensitive_removes_private_key_material() -> None:
    """Key labels and their body are redacted together."""
    redacted = _redact_sensitive("private_key PEM_BLOCK_BODY_XYZ")

    assert redacted == "private_key=<redacted>"
    assert "PEM_BLOCK_BODY_XYZ" not in redacted


def test_redact_sensitive_removes_token_only_url_userinfo() -> None:
    """URL userinfo without a colon is still a credential."""
    redacted = _redact_sensitive("clone https://ghp_secret123@github.com/org/repo.git")

    assert redacted == "clone https://<redacted>@github.com/org/repo.git"
    assert "ghp_secret123" not in redacted
