"""Tests the dlt REST API source contribution to the workflow wizard.

Verifies that phlo-dlt registers a source-stage wizard contribution with the
expected browser payload fields and apply capability.
"""

from phlo.capabilities import WorkflowContributionMode
from phlo_dlt.plugin import get_workflow_wizard_contributions


def test_dlt_contributes_rest_api_source_wizard() -> None:
    contributions = get_workflow_wizard_contributions()

    contribution = contributions[0]
    payload = contribution.to_browser_dict()

    assert payload["id"] == "dlt.rest-api-source"
    assert payload["stage"] == "source"
    assert payload["package"] == "phlo-dlt"
    assert WorkflowContributionMode.APPLY in contribution.modes
    assert [field["name"] for field in payload["fields"]] == [
        "domain",
        "table_name",
        "unique_key",
        "api_base_url",
        "response_path",
        "pagination",
        "auth",
        "cron",
        "fields",
    ]
