"""Branch merge ordering and failure reporting contracts."""

from types import SimpleNamespace
from unittest.mock import Mock

import click
import pytest
from phlo_nessie import cli_branch


@pytest.fixture
def merge_client(monkeypatch):
    """Provide isolated client, authorization and logging calls."""
    client = Mock()
    client.list_references.return_value = [
        SimpleNamespace(name="feature", hash_="feature-hash"),
        SimpleNamespace(name="main", hash_="main-hash"),
    ]
    auth = Mock()
    logger = Mock()
    monkeypatch.setattr(cli_branch, "get_nessie_client", lambda: client)
    monkeypatch.setattr(cli_branch, "enforce_surface_mutation_authorization", auth)
    monkeypatch.setattr(cli_branch, "logger", logger)
    return client, auth, logger


@pytest.mark.parametrize("dry_run,keep", [(True, False), (False, True), (False, False)])
def test_merge_authorization_precedes_mutations(merge_client, dry_run, keep):
    client, auth, _ = merge_client

    def assert_authorized(**kwargs):
        auth.assert_any_call("branch.merge", cli_branch.get_nessie_cli_adapter)
        if not keep:
            auth.assert_any_call(
                "branch.delete", cli_branch.get_nessie_cli_adapter, resource_id="feature"
            )
        assert kwargs == {
            "from_ref": "feature",
            "onto_branch": "main",
            "from_hash": "feature-hash",
            "old_hash": "main-hash",
        }

    client.merge.side_effect = assert_authorized
    client.delete_branch.side_effect = lambda **kwargs: auth.assert_any_call(
        "branch.delete", cli_branch.get_nessie_cli_adapter, resource_id="feature"
    )
    cli_branch.merge.callback("feature", "main", dry_run, keep)
    assert client.merge.call_count == int(not dry_run)
    assert client.delete_branch.call_count == int(not dry_run and not keep)
    assert auth.call_count == (0 if dry_run else 1 if keep else 2)


@pytest.mark.parametrize("denied_command", ["branch.merge", "branch.delete"])
def test_merge_authorization_denial_precedes_client_access(
    merge_client, monkeypatch, denied_command
):
    _, auth, _ = merge_client
    get_client = Mock()
    monkeypatch.setattr(cli_branch, "get_nessie_client", get_client)

    def authorize(command, _adapter, **kwargs):
        if command == denied_command:
            raise click.ClickException("denied")

    auth.side_effect = authorize
    with pytest.raises(click.ClickException, match="denied"):
        cli_branch.merge.callback("feature", "main", False, False)
    get_client.assert_not_called()


@pytest.mark.parametrize(
    "refs,message",
    [
        ([], "source branch not found"),
        ([SimpleNamespace(name="feature", hash_="hash")], "target branch not found"),
        (
            [SimpleNamespace(name="feature"), SimpleNamespace(name="main", hash_="hash")],
            "branch hash unavailable",
        ),
    ],
)
def test_merge_missing_references_do_not_mutate(merge_client, refs, message):
    client, _, _ = merge_client
    client.list_references.return_value = refs
    with pytest.raises(click.ClickException, match=message):
        cli_branch.merge.callback("feature", "main", False, False)
    client.merge.assert_not_called()
    client.delete_branch.assert_not_called()


@pytest.mark.parametrize(
    "error,level,event,message",
    [
        (RuntimeError("CONFLICT"), "warning", "nessie_branch_merge_conflict", "merge conflict"),
        (RuntimeError("offline"), "error", "nessie_branch_merge_failed", "could not merge"),
    ],
)
def test_merge_errors_are_classified(merge_client, error, level, event, message):
    client, _, logger = merge_client
    client.merge.side_effect = error
    with pytest.raises(click.ClickException, match=message):
        cli_branch.merge.callback("feature", "main", False, False)
    assert getattr(logger, level).call_args.args == (event,)
    client.delete_branch.assert_not_called()


def test_merge_delete_failure_is_only_warning(merge_client):
    client, _, logger = merge_client
    client.delete_branch.side_effect = RuntimeError("offline")
    cli_branch.merge.callback("feature", "main", False, False)
    assert logger.warning.call_args.args == ("nessie_branch_merge_source_delete_failed",)
    logger.error.assert_not_called()


def test_merge_reference_failure_is_termination(merge_client):
    client, _, logger = merge_client
    client.list_references.side_effect = RuntimeError("offline")
    with pytest.raises(click.ClickException, match="could not merge"):
        cli_branch.merge.callback("feature", "main", False, False)
    assert logger.error.call_args.args == ("nessie_branch_merge_terminated",)


def test_merge_click_error_is_preserved(merge_client):
    client, _, logger = merge_client
    error = click.ClickException("specific failure")
    client.merge.side_effect = error
    with pytest.raises(click.ClickException) as caught:
        cli_branch.merge.callback("feature", "main", False, False)
    assert caught.value is error
    logger.error.assert_not_called()
