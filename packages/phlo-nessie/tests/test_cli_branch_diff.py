"""Tests `phlo branch diff`: classifying Nessie diff entries into added,
modified and deleted tables and rendering them as JSON or a table."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
import requests
from click.testing import CliRunner
from rich.console import Console

from phlo_nessie import cli_branch

DIFF_ENTRIES = [
    {"key": {"elements": ["raw", "orders"]}, "to": {}},
    {"key": {"elements": ["raw", "customers"]}, "from": {}, "to": {}},
    {"key": {"elements": ["staging", "legacy"]}, "from": {}},
    {"key": {"elements": ["raw", "ignored"]}},
]


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise requests.HTTPError(f"{self._status} error")

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def nessie(monkeypatch):
    """Serve references and diffs from an in-memory Nessie and capture console output."""
    state = SimpleNamespace(
        refs=["feature", "main"],
        diffs=DIFF_ENTRIES,
        status=200,
        requested=[],
        wrap_refs=False,
        list_error=None,
    )

    def list_references():
        if state.list_error:
            raise state.list_error
        refs = [SimpleNamespace(name=name) for name in state.refs]
        return SimpleNamespace(references=refs) if state.wrap_refs else refs

    def get(url: str, timeout: int):
        state.requested.append(url)
        return _Response({"diffs": state.diffs}, state.status)

    output = io.StringIO()
    monkeypatch.setattr(
        cli_branch, "get_nessie_client", lambda: SimpleNamespace(list_references=list_references)
    )
    monkeypatch.setattr(
        cli_branch,
        "get_nessie_settings",
        lambda: SimpleNamespace(nessie_host="nessie", nessie_port=19120),
    )
    monkeypatch.setattr(cli_branch.requests, "get", get)
    monkeypatch.setattr(cli_branch, "console", Console(file=output, width=200, color_system=None))
    state.console = output
    return state


def _invoke(*args: str):
    return CliRunner().invoke(cli_branch.branch, ["diff", *args])


def test_json_output_classifies_added_modified_and_deleted_tables(nessie) -> None:
    result = _invoke("feature", "main", "--format", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "added_tables": ["raw.orders"],
        "modified_tables": ["raw.customers"],
        "deleted_tables": ["staging.legacy"],
    }
    assert nessie.requested == ["http://nessie:19120/api/v1/diffs/feature...main"]
    assert "Differences: feature -> main" in nessie.console.getvalue()


def test_table_output_lists_each_difference_by_type(nessie) -> None:
    result = _invoke("feature", "main")

    assert result.exit_code == 0, result.output
    assert "Branch Differences" in nessie.console.getvalue()
    rows = [
        line.strip("│ ").split("│")
        for line in nessie.console.getvalue().splitlines()
        if line.startswith("│")
    ]
    assert [[cell.strip() for cell in row] for row in rows] == [
        ["Added Tables", "raw.orders"],
        ["Modified Tables", "raw.customers"],
        ["Deleted Tables", "staging.legacy"],
    ]


def test_target_branch_defaults_to_main(nessie) -> None:
    nessie.wrap_refs = True

    result = _invoke("feature", "--format", "json")

    assert result.exit_code == 0, result.output
    assert nessie.requested == ["http://nessie:19120/api/v1/diffs/feature...main"]


def test_reports_no_differences_for_identical_branches(nessie) -> None:
    nessie.diffs = []

    result = _invoke("feature", "main")

    assert result.exit_code == 0
    assert "No differences found" in nessie.console.getvalue()
    assert "Branch Differences" not in nessie.console.getvalue()


def test_json_output_for_identical_branches_is_empty_lists(nessie) -> None:
    nessie.diffs = []

    result = _invoke("feature", "main", "--format", "json")

    assert json.loads(result.output) == {
        "added_tables": [],
        "modified_tables": [],
        "deleted_tables": [],
    }


@pytest.mark.parametrize("output_format", ["table", "json"])
def test_unsupported_diff_api_warns_without_rendering_results(nessie, output_format) -> None:
    nessie.status = 404

    result = _invoke("feature", "main", "--format", output_format)

    assert result.exit_code == 0
    assert result.output == ""
    rendered = nessie.console.getvalue()
    assert "Diff not supported by this Nessie version" in rendered
    assert "No differences found" not in rendered
    assert "Branch Differences" not in rendered


@pytest.mark.parametrize("refs", [["main"], ["feature"], []])
def test_missing_branch_is_a_user_error(nessie, refs) -> None:
    nessie.refs = refs

    result = _invoke("feature", "main")

    assert result.exit_code != 0
    assert "one or both branches were not found" in result.output
    assert nessie.requested == []


def test_client_failure_is_reported_as_a_comparison_error(nessie) -> None:
    nessie.list_error = ConnectionError("nessie down")

    result = _invoke("feature", "main")

    assert result.exit_code != 0
    assert "could not compare branches" in result.output
    assert nessie.requested == []
