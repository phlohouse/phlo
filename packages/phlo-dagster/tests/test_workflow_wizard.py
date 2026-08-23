"""Verify the orchestration contribution phlo-dagster registers with the
workflow wizard."""

from phlo.capabilities import WorkflowContributionMode
from phlo_dagster.plugin import get_workflow_wizard_contributions


def test_dagster_contributes_orchestration_wizard() -> None:
    contributions = get_workflow_wizard_contributions()

    contribution = contributions[0]
    payload = contribution.to_browser_dict()

    assert payload["id"] == "dagster.orchestration"
    assert payload["stage"] == "publish"
    assert payload["package"] == "phlo-dagster"
    assert WorkflowContributionMode.APPLY in contribution.modes
    assert [field["name"] for field in payload["fields"]] == [
        "job_name",
        "asset_group",
        "schedule",
        "include_sensor",
    ]
