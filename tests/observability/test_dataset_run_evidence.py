"""Dataset readiness consumes complete, explicitly bound producer evidence."""

import pytest

from phlo.dataset.models import candidate_dataset_id
from phlo.dataset.policy import DatasetPolicy, EvidenceCondition, PolicyRule
from phlo.dataset_projection import RunReportQualityEvidenceSource, build_dataset_authority
from phlo.governance.surface import GovernanceSurface
from phlo.run_evidence.models import PipelineRun, RunEvent, RunQualityResult
from phlo.run_evidence.store import SQLiteRunEvidenceStore


@pytest.mark.parametrize(
    "project,attempt,asset,check,passed,ready",
    [
        ("project", 1, "orders", "range", True, True),
        ("project", 1, "orders", "range", False, False),
        ("other-project", 1, "orders", "range", True, False),
        ("project", 2, "orders", "range", True, False),
        ("project", 1, "invoices", "range", True, False),
        ("project", 1, "orders", "different-check", True, False),
    ],
)
def test_readiness_requires_the_bound_check(
    tmp_path, project, attempt, asset, check, passed, ready
):
    store = SQLiteRunEvidenceStore(tmp_path / "evidence.sqlite")
    try:
        store.append_event(
            RunEvent(
                project_id=project,
                run_id="run",
                attempt=attempt,
                event_id="quality",
                event_type="quality.result",
                producer="quality",
                payload={"passed": passed},
            ),
            run=PipelineRun(project_id=project, run_id="run", attempt=attempt),
            quality_result=RunQualityResult(
                project_id=project,
                run_id="run",
                attempt=attempt,
                quality_result_id="quality",
                asset=asset,
                check_id=check,
                passed=passed,
                blocking=True,
            ),
        )
        authority = build_dataset_authority(
            str(tmp_path),
            store_mode="memory",
            surface=GovernanceSurface(tables={}),
            policy=DatasetPolicy(
                policy_version="test",
                rules=(
                    PolicyRule(
                        control="quality",
                        evidence_kind="quality_checks",
                        condition=EvidenceCondition("passed", "true"),
                    ),
                ),
            ),
            evidence_source=RunReportQualityEvidenceSource(
                store,
                project_id="project",
                run_id="run",
                attempt=1,
                checks_by_table={"gold.orders": (("orders", "range"),)},
            ),
        )
        assert authority.readiness(candidate_dataset_id("gold.orders"), "promote").ready is ready
        # A partial run cannot approve a table requiring two checks.
        authority = build_dataset_authority(
            str(tmp_path),
            store_mode="memory",
            surface=GovernanceSurface(tables={}),
            policy=authority.service.policy_for("gold.orders"),
            evidence_source=RunReportQualityEvidenceSource(
                store,
                project_id="project",
                run_id="run",
                attempt=1,
                checks_by_table={"gold.orders": (("orders", "range"), ("orders", "not_null"))},
            ),
        )
        assert not authority.readiness(candidate_dataset_id("gold.orders"), "promote").ready
    finally:
        store.close()
