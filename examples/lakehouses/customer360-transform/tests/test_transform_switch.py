"""In-situ provider switching keeps the project's asset contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from phlo.config.transformation import (
    active_transformation_provider,
    set_active_transformation_provider,
)
from phlo_transform.assets import has_dbt_project, provider_is_active

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Asset keys selected by workflows/schedules/customer360.py. Both providers
# must emit these names, or switching engines would break the schedules.
SCHEDULE_ASSET_KEYS = {
    "sling_c360_customers",
    "sling_c360_orders",
    "dlt_support_tickets",
    "dlt_marketing_contacts",
    "dlt_consent_events",
    "stg_commerce_customers",
    "stg_commerce_orders",
    "stg_support_tickets",
    "stg_marketing_contacts",
    "stg_consent_events",
    "identity_resolution",
    "customer_dimension",
    "consent_current",
    "consent_safe_product",
}


@pytest.fixture(autouse=True)
def _project_marker(monkeypatch: pytest.MonkeyPatch):
    """Run every test against the example root and restore any local marker."""

    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.delenv("PHLO_TRANSFORMATION_PROVIDER", raising=False)
    marker = PROJECT_ROOT / ".phlo" / "transformation-provider"
    previous = marker.read_text() if marker.exists() else None
    marker.unlink(missing_ok=True)
    yield
    marker.unlink(missing_ok=True)
    if previous is not None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(previous)


def test_dbt_is_the_default_when_both_providers_are_installed() -> None:
    assert has_dbt_project(PROJECT_ROOT) is True
    assert active_transformation_provider(PROJECT_ROOT) is None
    assert provider_is_active(PROJECT_ROOT) is False


def test_marker_switches_ownership_and_reverts() -> None:
    path = set_active_transformation_provider("phlo-transform", PROJECT_ROOT)

    assert path is not None
    assert path.read_text().strip() == "transform"
    assert active_transformation_provider(PROJECT_ROOT) == "transform"
    assert provider_is_active(PROJECT_ROOT) is True

    set_active_transformation_provider("dbt", PROJECT_ROOT)
    assert provider_is_active(PROJECT_ROOT) is False

    set_active_transformation_provider(None, PROJECT_ROOT)
    assert provider_is_active(PROJECT_ROOT) is False


def test_environment_override_does_not_touch_the_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHLO_TRANSFORMATION_PROVIDER", "phlo-transform")

    assert provider_is_active(PROJECT_ROOT) is True
    assert not (PROJECT_ROOT / ".phlo" / "transformation-provider").exists()


INGESTION_ASSET_KEYS = {
    "sling_c360_customers",
    "sling_c360_orders",
    "dlt_support_tickets",
    "dlt_marketing_contacts",
    "dlt_consent_events",
}


def test_schedule_keys_are_the_dbt_model_names() -> None:
    """The schedule contract is literal model names, not engine-specific ids."""

    dbt_models = {
        path.stem
        for path in (PROJECT_ROOT / "workflows").glob("*/models/*.sql")
        if path.parent.name == "models"
    }
    scheduled_models = SCHEDULE_ASSET_KEYS - INGESTION_ASSET_KEYS
    assert scheduled_models <= dbt_models
    assert dbt_models - scheduled_models == {"support_engagement"}
