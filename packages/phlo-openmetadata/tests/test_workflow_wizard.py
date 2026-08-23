"""Tests the OpenMetadata catalog contribution to the workflow wizard.

Pins the publish-stage wizard payload: id, package, APPLY mode, and field set.
"""

from phlo.capabilities import WorkflowContributionMode
from phlo_openmetadata.plugin import get_workflow_wizard_contributions


def test_openmetadata_contributes_catalog_wizard() -> None:
    contributions = get_workflow_wizard_contributions()

    contribution = contributions[0]
    payload = contribution.to_browser_dict()

    assert payload["id"] == "openmetadata.catalog"
    assert payload["stage"] == "publish"
    assert payload["package"] == "phlo-openmetadata"
    assert WorkflowContributionMode.APPLY in contribution.modes
    assert [field["name"] for field in payload["fields"]] == [
        "service_name",
        "database",
        "schema",
        "owner",
        "tags",
        "description",
    ]
