"""Tests nessie branch creation: reference response shape parsing and
head reporting in CLI output."""

from types import SimpleNamespace

from click.testing import CliRunner

from phlo_nessie import cli_branch


def test_create_uses_references_response_shape(monkeypatch) -> None:
    refs = SimpleNamespace(
        references=[
            SimpleNamespace(name="main", hash_="1234567890abcdef"),
        ]
    )
    created_branch = SimpleNamespace(name="feature/demo", hash_="fedcba0987654321")
    fake_client = SimpleNamespace(
        list_references=lambda: refs,
        create_branch=lambda **_kwargs: created_branch,
    )

    monkeypatch.setattr(cli_branch, "get_nessie_client", lambda: fake_client)

    result = CliRunner().invoke(cli_branch.branch, ["create", "feature/demo"])

    assert result.exit_code == 0
    assert "Created branch: feature/demo" in result.output
    assert "Head: fedcba09" in result.output
