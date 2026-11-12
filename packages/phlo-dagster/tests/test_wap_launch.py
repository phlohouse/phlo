"""Tests for WAP launch coordination before Dagster starts a run.

Verifies deterministic WAP branch and tag creation with manifest emission,
and that launch refuses to reuse an existing branch or to run without a
configured project.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from phlo.config_schema import WapConfig
from phlo.exceptions import PhloConfigError
from phlo.wap_evidence import WapEvidence
from phlo_dagster.wap_launch import (
    WAP_ATTEMPT_TAG,
    WAP_BRANCH_TAG,
    WAP_PROJECT_ID_TAG,
    WAP_REF_TAG,
    WAP_RUN_ID_TAG,
    WapLaunchError,
    prepare_wap_launch,
    submit_wap_launch,
)

pytestmark = pytest.mark.usefixtures("synthetic_wap_context")


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
        self.branches[name] = self.branches[from_ref]
        return self.branches[name]

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
        "environment": "test",
        "phlo/wap_binding": launch.binding_checksum,
    }
    assert catalog.created == [("pipeline-run-request-42", "main")]
    report = json.loads(
        (tmp_path / ".phlo" / "wap-reports" / "request-42.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "branch_created"
    assert report["branch"] == "pipeline-run-request-42"
    assert report["launch_tags"] == launch.tags
    assert report["launch_source_hash"] == "main-hash"
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

    with pytest.raises(PhloConfigError, match="refusing to reuse"):
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

    with pytest.raises(PhloConfigError, match="requires PHLO_PROJECT"):
        prepare_wap_launch(logical_run_id="request-42")


@pytest.mark.parametrize(
    "outcome,report_status",
    [
        ("accepted", "launched"),
        ("rejected", "launch_rejected"),
        ("transport_failure", "launch_ambiguous"),
        ("server_failure", "launch_ambiguous"),
        ("missing_run_id", "launch_ambiguous"),
        ("manifest_failure", "branch_created"),
    ],
)
def test_submit_wap_launch_binds_only_acknowledged_runs(
    monkeypatch, tmp_path, outcome, report_status
) -> None:
    catalog = _Catalog()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_OPERATIONS_JOURNAL_DIR", str(tmp_path / "operations"))
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.get_settings", lambda: SimpleNamespace(phlo_project="warehouse")
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.resolve_capability",
        lambda _: SimpleNamespace(
            provider=catalog, support=SimpleNamespace(supports_refs=True, supports_promote=True)
        ),
    )
    launch = prepare_wap_launch(logical_run_id="request-42")
    if outcome == "manifest_failure":
        manifests = tmp_path / ".phlo" / "wap-reports" / "launches"
        manifests.rename(manifests.with_name("retained-launches"))
        manifests.write_text("blocked storage", encoding="utf-8")

    async def post(self, url, *, json, headers):
        assert headers["Authorization"] == "Bearer caller-token"
        if "ExistingMaterializationRun" in json["query"]:
            data = {"runsOrError": {"__typename": "Runs", "results": []}}
        else:
            if outcome == "transport_failure":
                raise httpx.ReadTimeout("response lost")
            execution = json["variables"]["executionParams"]
            assert execution["selector"]["repositoryLocationName"] == "phlo_dagster"
            assert execution["selector"]["pipelineName"] == "custom-job"
            assert execution["selector"]["repositoryName"] == "custom-repository"
            tags = {tag["key"]: tag["value"] for tag in execution["executionMetadata"]["tags"]}
            assert tags["phlo/idempotency_key"] == "request-42"
            assert tags["dagster/partition"] == "2025-06-19"
            assert tags["phlo/wap_binding"] == launch.binding_checksum
            payload = (
                {"__typename": "RunConfigValidationInvalid", "message": "launch rejected"}
                if outcome == "rejected"
                else {"__typename": "PythonError", "message": "server failed"}
                if outcome == "server_failure"
                else {
                    "__typename": "LaunchRunSuccess",
                    "run": {
                        "runId": None if outcome == "missing_run_id" else "physical-17",
                        "status": "STARTING",
                    },
                }
            )
            data = {"launchPipelineExecution": payload}
        return httpx.Response(200, request=httpx.Request("POST", url), json={"data": data})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    # Submission uses the prepared routing proof, not a subsequently changed location.
    submission = submit_wap_launch(
        launch,
        config=WapConfig(
            job_name="custom-job",
            repository_name="custom-repository",
            repository_location_name="changed-location",
        ),
        dagster_url="http://dagster.test/graphql",
        asset_key_path="silver/orders",
        access_token="caller-token",
        partition_key="2025-06-19",
    )
    if outcome == "accepted":
        assert asyncio.run(submission) == "physical-17"
    else:
        with pytest.raises(WapLaunchError) as failure:
            asyncio.run(submission)
        assert failure.value.run_id == ("physical-17" if outcome == "manifest_failure" else None)
    evidence = WapEvidence(tmp_path)
    report = evidence.read_report("request-42")
    assert report["status"] == report_status
    assert catalog.get_branch_hash(launch.branch) == "main-hash"
    assert catalog.deleted == []
    if outcome == "accepted":
        manifest = evidence.read_binding("request-42", report["launch_manifest_checksum"])
        assert manifest["dagster_run_id"] == report["dagster_run_id"] == "physical-17"
        assert manifest["tags"] == launch.tags
    else:
        assert "dagster_run_id" not in report
