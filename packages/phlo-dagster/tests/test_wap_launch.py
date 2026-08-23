"""Tests for WAP launch coordination before Dagster starts a run.

Verifies deterministic WAP branch and tag creation with manifest emission,
and that launch refuses to reuse an existing branch or to run without a
configured project.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from phlo_dagster.wap_launch import (
    WAP_ATTEMPT_TAG,
    WAP_BRANCH_TAG,
    WAP_PROJECT_ID_TAG,
    WAP_REF_TAG,
    WAP_RUN_ID_TAG,
    prepare_wap_launch,
)


class _Catalog:
    def __init__(self, branches: dict[str, str] | None = None) -> None:
        self.branches = branches or {"main": "main-hash"}
        self.created: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def get_branch_hash(self, name: str) -> str | None:
        return self.branches.get(name)

    def list_branches(self) -> list[object]:
        return []

    def create_branch(self, name: str, from_ref: str = "main") -> str:
        self.created.append((name, from_ref))
        self.branches[name] = "branch-hash"
        return "branch-hash"

    def delete_branch(self, name: str) -> bool:
        self.deleted.append(name)
        return self.branches.pop(name, None) is not None

    def merge_branch(self, source: str, target: str = "main") -> bool:
        return False


def test_prepare_wap_launch_creates_deterministic_branch_tags_and_manifest(
    monkeypatch, tmp_path
) -> None:
    catalog = _Catalog()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.get_settings",
        lambda: SimpleNamespace(phlo_project="warehouse"),
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.resolve_capability",
        lambda _: SimpleNamespace(
            provider=catalog,
            support=SimpleNamespace(supports_refs=True, supports_promote=True),
        ),
    )

    launch = prepare_wap_launch(logical_run_id="request-42")

    assert launch.branch == "pipeline-run-request-42"
    assert launch.created_branch is True
    assert launch.tags == {
        WAP_RUN_ID_TAG: "request-42",
        WAP_BRANCH_TAG: "pipeline-run-request-42",
        WAP_REF_TAG: "pipeline-run-request-42",
        WAP_PROJECT_ID_TAG: "warehouse",
        WAP_ATTEMPT_TAG: "1",
    }
    assert catalog.created == [("pipeline-run-request-42", "main")]
    report = json.loads(
        (tmp_path / ".phlo" / "wap-reports" / "request-42.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "branch_created"
    assert report["branch"] == "pipeline-run-request-42"
    assert report["launch_tags"] == launch.tags
    assert report["launch_source_hash"] == "branch-hash"
    assert report["launch_target_hash_before"] == "main-hash"

    launch.record_launch_result(status="launch_ambiguous", error="response lost")
    updated = json.loads(
        (tmp_path / ".phlo" / "wap-reports" / "request-42.json").read_text(encoding="utf-8")
    )
    assert updated["status"] == "launch_ambiguous"
    assert updated["launch_error"] == "response lost"


def test_prepare_wap_launch_refuses_to_reuse_an_existing_branch(monkeypatch) -> None:
    catalog = _Catalog({"main": "main-hash", "pipeline-run-request-42": "old-hash"})
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.get_settings",
        lambda: SimpleNamespace(phlo_project="warehouse"),
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.resolve_capability",
        lambda _: SimpleNamespace(
            provider=catalog,
            support=SimpleNamespace(supports_refs=True, supports_promote=True),
        ),
    )

    import pytest

    with pytest.raises(Exception, match="refusing to reuse"):
        prepare_wap_launch(logical_run_id="request-42")

    assert catalog.created == []
    assert catalog.deleted == []


def test_prepare_wap_launch_requires_project_before_creating_a_branch(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.get_settings",
        lambda: SimpleNamespace(phlo_project=None),
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.resolve_capability",
        lambda _: (_ for _ in ()).throw(AssertionError("must not resolve the catalog")),
    )

    import pytest

    with pytest.raises(Exception, match="requires PHLO_PROJECT"):
        prepare_wap_launch(logical_run_id="request-42")
