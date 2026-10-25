"""Tests for Nessie startup hooks.

Covers URL resolution from project env, idempotent bootstrap commits
(no-op when the ref log is non-empty), namespace cleanup around the
bootstrap marker, and dev branch creation ordering.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from phlo_nessie import hooks


@pytest.mark.parametrize(
    ("project_env", "expected"),
    [
        ({"NESSIE_PORT": "29120"}, "http://localhost:29120"),
        ({"NESSIE_URL": "http://custom/"}, "http://custom"),
    ],
    ids=["port-env", "explicit-url"],
)
def test_resolve_nessie_url(monkeypatch, project_env, expected) -> None:
    monkeypatch.setattr(hooks, "load_project_env", lambda: project_env)

    assert hooks._resolve_nessie_url() == expected


def test_ensure_bootstrap_commit_noops_when_log_exists() -> None:
    with (
        patch.object(hooks, "_get_ref_log", return_value=[{"commitMeta": {"hash": "abc"}}]),
        patch.object(hooks, "_get_iceberg_prefix") as get_prefix,
        patch.object(hooks, "_post_json") as post_json,
        patch.object(hooks, "_delete") as delete,
    ):
        hooks._ensure_bootstrap_commit("http://nessie", "main")

    get_prefix.assert_not_called()
    post_json.assert_not_called()
    delete.assert_not_called()


def test_ensure_bootstrap_commit_creates_and_deletes_namespace() -> None:
    with (
        patch.object(hooks, "_get_ref_log", return_value=[]),
        patch.object(hooks, "_get_iceberg_prefix", return_value="main%7Cs3"),
        patch.object(hooks, "_delete_namespace_if_present") as delete_namespace,
        patch.object(hooks, "_post_json", return_value={}) as post_json,
        patch.object(hooks, "_delete", return_value=204) as delete,
    ):
        recorder = MagicMock()
        recorder.attach_mock(delete_namespace, "delete_namespace")
        recorder.attach_mock(post_json, "post_json")
        recorder.attach_mock(delete, "delete")
        hooks._ensure_bootstrap_commit("http://nessie", "main")

    # The marker namespace must exist before it is removed, so the bootstrap
    # commit exists; cleanup-before-create would silently leave no history.
    assert [name for name, _args, _kwargs in recorder.mock_calls] == [
        "delete_namespace",
        "post_json",
        "delete",
    ]
    delete_namespace.assert_called_once_with(
        "http://nessie", "main%7Cs3", "__phlo_bootstrap_main__"
    )
    post_json.assert_called_once_with(
        "http://nessie/iceberg/v1/main%7Cs3/namespaces",
        {"namespace": ["__phlo_bootstrap_main__"]},
    )
    delete.assert_called_once_with(
        "http://nessie/iceberg/v1/main%7Cs3/namespaces/__phlo_bootstrap_main__"
    )


def test_init_branches_bootstraps_main_before_creating_dev(caplog) -> None:
    with caplog.at_level("INFO", logger="phlo_nessie.hooks"):
        with (
            patch.object(hooks, "_resolve_nessie_url", return_value="http://nessie"),
            patch.object(hooks, "_post_json", return_value={"name": "dev"}),
            patch.object(hooks, "_ensure_bootstrap_commit") as ensure_bootstrap,
            patch.object(
                hooks,
                "_get_json",
                side_effect=[
                    {"references": [{"name": "main", "type": "BRANCH"}]},
                    {"references": [{"name": "main", "type": "BRANCH"}]},
                    {"hash": "main-hash"},
                ],
            ),
        ):
            exit_code = hooks.init_branches()

    assert exit_code == 0
    assert ensure_bootstrap.call_args_list == [
        call("http://nessie", "main"),
        call("http://nessie", "dev"),
    ]
    assert "nessie_hooks_dev_branch_created" in caplog.text


def test_init_branches_bootstraps_existing_dev(caplog) -> None:
    with caplog.at_level("INFO", logger="phlo_nessie.hooks"):
        with (
            patch.object(hooks, "_resolve_nessie_url", return_value="http://nessie"),
            patch.object(hooks, "_ensure_bootstrap_commit") as ensure_bootstrap,
            patch.object(hooks, "_post_json") as post_json,
            patch.object(
                hooks,
                "_get_json",
                side_effect=[
                    {
                        "references": [
                            {"name": "main", "type": "BRANCH"},
                            {"name": "dev", "type": "BRANCH"},
                        ]
                    },
                    {
                        "references": [
                            {"name": "main", "type": "BRANCH"},
                            {"name": "dev", "type": "BRANCH"},
                        ]
                    },
                ],
            ),
        ):
            exit_code = hooks.init_branches()

    assert exit_code == 0
    assert ensure_bootstrap.call_args_list == [
        call("http://nessie", "main"),
        call("http://nessie", "dev"),
    ]
    post_json.assert_not_called()
    assert "nessie_hooks_dev_branch_exists" in caplog.text
