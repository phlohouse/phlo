"""Tests Observatory capability inventory construction and secret redaction.

Fake registries expose metadata containing URLs, DSNs, and connection strings;
these tests pin that such fields never leak into the published inventory while
safe values and nested entries survive.
"""

from types import SimpleNamespace

from phlo_api.observatory_api.observatory_capabilities import build_capability_inventory
from phlo_api.observatory_api.observatory import (
    _filter_capabilities_to_project_services,
    _pages_from_inventory,
)
from phlo_api.observatory_api.observatory_models import (
    ObservatoryCapabilityInventory,
    ObservatoryCapabilityProvider,
    ObservatoryCapabilitySupport,
    ObservatoryRouteRequirement,
)


class FakeRegistry:
    def list(self, family: str):
        if family == "query_engine":
            return self.list_query_engines()
        return []

    def list_query_engines(self):
        return [
            SimpleNamespace(
                name="trino",
                metadata={
                    "service_type": "Trino",
                    "url": "http://secret-host",
                    "endpoint": "http://secret-endpoint",
                    "dsn": "postgres://secret",
                    "connection_string": "postgres://secret",
                    "harmless_name": "http://internal",
                    "nested": {
                        "safe": "ok",
                        "secret": "hidden",
                        "nested_url": "http://internal",
                    },
                    "items": [
                        {"name": "safe-item", "endpoint": "http://nested-endpoint"},
                        {"connection": "hidden", "enabled": True},
                        {"name": "unsafe-value", "location": "http://internal"},
                    ],
                    "native_links": [
                        {
                            "label": "Open",
                            "url": "http://internal.example/ui?token=secret",
                            "kind": "app",
                        }
                    ],
                },
                support={"supports_metrics": True},
            )
        ]

    def list_table_stores(self):
        return []

    def list_catalogs(self):
        return []

    def list_catalog_scanners(self):
        return []

    def list_object_stores(self):
        return []

    def list_quality_backends(self):
        return []

    def list_maintenance_read_models(self):
        return []

    def list_metadata_catalogs(self):
        return []

    def list_lineage_sinks(self):
        return []

    def list_governance_backends(self):
        return []

    def list_authorization_policy_backends(self):
        return []

    def list_authentication_providers(self):
        return []

    def list_publish_targets(self):
        return []

    def list_alert_sinks(self):
        return []

    def list_api_backends(self):
        return []

    def list_observability_backends(self):
        return []

    def list_regulated_surfaces(self):
        return []


def test_observatory_capability_inventory_serializes_support_and_requirements() -> None:
    provider = ObservatoryCapabilityProvider(
        capability_type="query_engine",
        name="trino",
        display_name="Trino",
        package="phlo-trino",
        metadata={"service_type": "Trino"},
        support=ObservatoryCapabilitySupport(
            supports_metrics=True,
            supports_permissions=True,
        ),
        health={"state": "ok", "message": "registered"},
        native_links=[{"label": "Open", "url": "http://localhost:8080/ui", "kind": "app"}],
    )
    inventory = ObservatoryCapabilityInventory(
        version=2,
        providers={"query_engine": [provider]},
        requirements=[
            ObservatoryRouteRequirement(
                route_id="tables",
                label="Tables",
                path="/tables",
                required_any=["query_engine", "table_store"],
            )
        ],
    )

    payload = inventory.model_dump()

    assert payload["providers"]["query_engine"][0]["name"] == "trino"
    assert payload["providers"]["query_engine"][0]["support"]["supports_metrics"] is True
    assert payload["requirements"][0]["required_any"] == ["query_engine", "table_store"]


def test_build_capability_inventory_serializes_registry_without_private_urls() -> None:
    inventory = build_capability_inventory(FakeRegistry())
    metadata = inventory.providers["query_engine"][0].metadata

    assert inventory.providers["query_engine"][0].name == "trino"
    assert inventory.providers["query_engine"][0].support.supports_metrics is True
    assert "url" not in metadata
    assert "endpoint" not in metadata
    assert "dsn" not in metadata
    assert "connection_string" not in metadata
    assert "harmless_name" not in metadata
    assert inventory.providers["query_engine"][0].native_links == []
    assert metadata["nested"] == {"safe": "ok"}
    assert metadata["items"] == [{"name": "safe-item"}, {"enabled": True}, {"name": "unsafe-value"}]


def test_inventory_includes_ui_contributions() -> None:
    def list_capabilities(family: str):
        if family != "ui_contribution":
            return []
        return [
            SimpleNamespace(
                name="trino-tables",
                capability_type="query_engine",
                capability_name="trino",
                surfaces=["tables"],
                read_models={"tables": "/api/observatory/tables"},
                actions=["query.run"],
                native_links=[],
                metadata={"safe": "yes", "url": "http://internal"},
            )
        ]

    registry = SimpleNamespace(list=list_capabilities)

    inventory = build_capability_inventory(registry)

    assert inventory.ui_contributions[0].name == "trino-tables"
    assert inventory.ui_contributions[0].surfaces == ["tables"]
    assert "url" not in inventory.ui_contributions[0].metadata


def test_build_capability_inventory_route_requirements_use_emitted_provider_keys() -> None:
    inventory = build_capability_inventory(FakeRegistry())
    requirements = {requirement.route_id: requirement for requirement in inventory.requirements}

    assert list(requirements) == [
        "overview",
        "tables",
        "workflows",
        "lineage",
        "quality",
        "logs",
        "branches",
        "operations",
        "runs",
        "storage",
        "observability",
        "governance",
        "datasets",
        "publishing",
        "pipelines",
        "apis",
        "bi",
        "extensions",
        "services",
        "settings",
    ]
    assert requirements["tables"].required_any == ["query_engine", "table_store"]
    assert requirements["tables"].optional == []
    assert requirements["lineage"].required_any == [
        "query_engine",
        "table_store",
        "lineage_sink",
    ]
    assert requirements["lineage"].optional == [
        "quality_backend",
        "maintenance_read_model",
    ]
    assert requirements["quality"].required_any == ["quality_backend"]
    assert requirements["quality"].nav is True
    assert requirements["logs"].required_any == []
    assert requirements["logs"].optional == [
        "observability_backend",
        "maintenance_read_model",
    ]
    assert requirements["branches"].required_any == ["catalog"]
    assert requirements["branches"].optional == ["table_store"]
    assert requirements["operations"].required_any == ["maintenance_read_model"]
    assert requirements["runs"].required_any == ["orchestrator"]
    assert requirements["runs"].optional == ["maintenance_read_model"]
    assert requirements["storage"].required_any == ["table_store", "object_store"]
    assert requirements["storage"].optional == []
    assert requirements["observability"].required_any == ["observability_backend"]
    assert requirements["observability"].optional == ["alert_sink"]
    assert requirements["governance"].required_any == [
        "governance_backend",
        "authorization_policy_backend",
        "authentication_provider",
        "regulated_surface",
    ]
    assert requirements["governance"].optional == []
    assert requirements["datasets"].required_any == [
        "metadata_catalog",
        "catalog_scanner",
    ]
    assert requirements["datasets"].optional == []
    assert requirements["publishing"].required_any == [
        "publish_target",
        "metadata_catalog",
        "catalog_scanner",
    ]
    assert requirements["publishing"].optional == ["authorization_policy_backend"]
    assert requirements["pipelines"].required_any == [
        "orchestrator",
        "orchestrator_operations",
        "maintenance_read_model",
    ]
    assert requirements["pipelines"].optional == ["quality_backend"]
    assert requirements["apis"].required_any == ["api_backend"]
    assert requirements["apis"].optional == []
    assert requirements["bi"].required_any == ["publish_target"]
    assert requirements["bi"].optional == ["query_engine"]
    assert requirements["extensions"].required_any == []
    assert requirements["extensions"].nav is False
    assert requirements["overview"].required_any == []
    assert requirements["services"].required_any == []
    assert requirements["settings"].required_any == []
    assert "asset_provider" not in set().union(
        *(
            set(requirement.required_any) | set(requirement.optional)
            for requirement in requirements.values()
        )
    )
    assert "orchestrator" in set().union(
        *(
            set(requirement.required_any) | set(requirement.optional)
            for requirement in requirements.values()
        )
    )


def test_pages_from_inventory_enables_routes_by_required_capabilities() -> None:
    inventory = ObservatoryCapabilityInventory(
        providers={
            "query_engine": [
                ObservatoryCapabilityProvider(
                    capability_type="query_engine",
                    name="trino",
                    display_name="Trino",
                )
            ]
        },
        requirements=[
            ObservatoryRouteRequirement(
                route_id="tables",
                label="Tables",
                path="/tables",
                required_any=["query_engine", "table_store"],
                reason="Install data provider.",
            ),
            ObservatoryRouteRequirement(
                route_id="storage",
                label="Storage",
                path="/storage",
                required_any=["object_store"],
                reason="Install storage provider.",
            ),
        ],
    )

    pages = _pages_from_inventory(inventory)

    assert {page.id: page.available for page in pages} == {
        "tables": True,
        "storage": False,
    }
    assert pages[0].providers == ["trino"]


def test_project_service_filter_removes_service_backed_providers_without_project_stack() -> None:
    inventory = ObservatoryCapabilityInventory(
        providers={
            "query_engine": [
                ObservatoryCapabilityProvider(
                    capability_type="query_engine",
                    name="trino",
                    display_name="Trino",
                )
            ],
            "observability_backend": [
                ObservatoryCapabilityProvider(
                    capability_type="observability_backend",
                    name="loki",
                    display_name="Loki",
                )
            ],
            "lineage_sink": [
                ObservatoryCapabilityProvider(
                    capability_type="lineage_sink",
                    name="phlo-lineage",
                    display_name="Phlo Lineage",
                )
            ],
        },
    )

    _filter_capabilities_to_project_services(inventory, [])

    assert inventory.providers["query_engine"] == []
    assert inventory.providers["observability_backend"] == []
    assert inventory.providers["lineage_sink"] == []


def test_project_service_filter_keeps_configured_service_providers() -> None:
    inventory = ObservatoryCapabilityInventory(
        providers={
            "query_engine": [
                ObservatoryCapabilityProvider(
                    capability_type="query_engine",
                    name="trino",
                    display_name="Trino",
                )
            ],
            "observability_backend": [
                ObservatoryCapabilityProvider(
                    capability_type="observability_backend",
                    name="loki",
                    display_name="Loki",
                )
            ],
            "lineage_sink": [
                ObservatoryCapabilityProvider(
                    capability_type="lineage_sink",
                    name="phlo-lineage",
                    display_name="Phlo Lineage",
                )
            ],
        },
    )
    services = [
        SimpleNamespace(id="trino", in_stack=False, definition_state="configured"),
    ]

    _filter_capabilities_to_project_services(inventory, services)

    assert [provider.name for provider in inventory.providers["query_engine"]] == ["trino"]
    assert [provider.name for provider in inventory.providers["lineage_sink"]] == ["phlo-lineage"]
    assert inventory.providers["observability_backend"] == []
