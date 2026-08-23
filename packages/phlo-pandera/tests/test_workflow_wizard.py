"""Verify the quality-stage contribution phlo-pandera registers with the
workflow wizard."""

from phlo.capabilities import WorkflowContributionMode
from phlo_pandera.plugin import get_workflow_wizard_contributions


def test_pandera_contributes_quality_wizard() -> None:
    contributions = get_workflow_wizard_contributions()

    contribution = contributions[0]
    payload = contribution.to_browser_dict()

    assert payload["id"] == "pandera.quality-checks"
    assert payload["stage"] == "quality"
    assert payload["package"] == "phlo-pandera"
    assert WorkflowContributionMode.APPLY in contribution.modes
    assert [field["name"] for field in payload["fields"]] == [
        "target_table",
        "check_name",
        "unique_key",
        "not_null_columns",
        "range_checks",
        "freshness_column",
        "freshness_hours",
        "min_rows",
    ]
