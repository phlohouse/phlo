"""Metadata catalog provider for OpenMetadata.

Provides a capability-based interface for publishing metadata into OpenMetadata,
including tables, quality results, and lineage edges.

This module implements the MetadataCatalogSpec interface for OpenMetadata,
allowing it to be discovered and used by the phlo capability system.

Example:
    >>> from phlo_openmetadata.metadata_catalog import OpenMetadataCatalogProvider
    >>> provider = OpenMetadataCatalogProvider()
    >>> provider.health_check()
    True
    >>> provider.upsert_table(namespace="bronze", table=table_obj)

"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from phlo.hooks import QualityResultEvent

from phlo_openmetadata.hooks_plugin import (
    _resolve_entity_type,
    _resolve_table_fqn,
    _resolve_test_type,
)
from phlo_openmetadata.openmetadata import OpenMetadataClient
from phlo_openmetadata.settings import get_settings as get_openmetadata_settings


class OpenMetadataCatalogProvider:
    """Capability provider for publishing metadata into OpenMetadata.

    Wraps the OpenMetadataClient to provide a standardized interface
    for the phlo capability system. Handles lazy client initialization
    and configuration resolution.

    Example:
        >>> provider = OpenMetadataCatalogProvider()
        >>> provider.publish_quality_result(event=quality_event)
    """

    def __init__(self) -> None:
        """Initialize with lazy client construction.

        The OpenMetadata client is created on first use to avoid
        unnecessary connections.
        """
        self._client: OpenMetadataClient | None = None

    def health_check(self) -> bool:
        """Check OpenMetadata connectivity."""
        return self._get_client().health_check()

    def upsert_table(self, *, namespace: str, table: Any) -> Any:
        """Create or update a table entity in OpenMetadata."""
        return self._get_client().create_or_update_table(schema_name=namespace, table=table)

    def publish_quality_result(self, *, event: Any) -> None:
        """Publish a quality result into OpenMetadata test metadata.

        Creates test definitions, test cases, and publishes results
        for quality checks.
        """
        if not isinstance(event, QualityResultEvent):
            return

        table_fqn = _resolve_table_fqn(event)
        if not table_fqn:
            return

        client = self._get_client()
        test_name = event.check_name
        client.create_test_definition(
            test_name=test_name,
            test_type=_resolve_test_type(event),
            entity_type=_resolve_entity_type(event),
        )
        test_case = client.create_test_case(
            test_case_name=f"{table_fqn}_{test_name}",
            table_fqn=table_fqn,
            test_definition_name=test_name,
        )
        test_case_fqn = (
            test_case.get("fullyQualifiedName")
            or test_case.get("name")
            or (f"{table_fqn}_{test_name}")
        )
        result_value = event.metadata.get("metric_value")
        client.publish_test_result(
            test_case_fqn=test_case_fqn,
            result="Success" if event.passed else "Failed",
            test_execution_date=datetime.now(timezone.utc),
            result_value=str(result_value) if result_value is not None else None,
        )

    def publish_lineage_edges(self, *, edges: list[tuple[str, str]]) -> None:
        """Publish lineage edges into OpenMetadata."""
        client = self._get_client()
        for from_fqn, to_fqn in edges:
            client.create_lineage(from_fqn, to_fqn)

    def _get_client(self) -> OpenMetadataClient:
        """Return the lazily initialized OpenMetadata client.

        Creates the client on first call using configured settings.
        """
        if self._client is None:
            settings = get_openmetadata_settings()
            self._client = OpenMetadataClient(
                base_url=settings.openmetadata_uri(),
                username=settings.openmetadata_username,
                password=settings.openmetadata_password,
                verify_ssl=settings.openmetadata_verify_ssl,
                service_name=settings.openmetadata_service_name,
                service_type=settings.openmetadata_database_service_type(),
                database_name=settings.openmetadata_database(),
            )
        return self._client
