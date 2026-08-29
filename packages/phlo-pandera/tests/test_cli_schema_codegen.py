"""Unit tests for schema codegen helpers such as identifier snake-casing.

Camel-case segments must split into snake_case words without dropping or
merging characters at segment boundaries.
"""

from __future__ import annotations

from click.testing import CliRunner

from phlo_pandera import cli_schema_codegen
from phlo_pandera.cli_schema_codegen import _snake_case


def test_snake_case_converts_camel_case_segments() -> None:
    """Verify camel-case segments are converted into snake_case."""
    assert _snake_case("MyTestCase") == "my_test_case"
    assert _snake_case("GitHubEvents") == "git_hub_events"


def test_schema_generate_authorizes_before_loading_the_source(monkeypatch) -> None:
    """A denied durable generation must not execute the supplied source reference."""
    authorization_call: tuple[tuple[object, ...], dict[str, object]] | None = None

    def deny_before_side_effect(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        nonlocal authorization_call
        authorization_call = (args, kwargs)
        raise SystemExit(1)

    monkeypatch.setattr(
        cli_schema_codegen,
        "enforce_surface_mutation_authorization",
        deny_before_side_effect,
        raising=False,
    )
    monkeypatch.setattr(
        cli_schema_codegen,
        "_import_object",
        lambda _ref: (_ for _ in ()).throw(AssertionError("source must not load")),
    )

    result = CliRunner().invoke(
        cli_schema_codegen.generate,
        ["--from", "workflows.ingestion:asset", "--domain", "orders", "--overwrite"],
    )

    assert result.exit_code == 1
    assert authorization_call is not None
    assert authorization_call[0][0] == "schema.generate"
