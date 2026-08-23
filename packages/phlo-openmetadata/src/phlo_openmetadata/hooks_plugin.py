"""Hook plugin for OpenMetadata integration.

Registers hook handlers that sync lineage, quality results, and publish
events into OpenMetadata automatically during pipeline execution.

Supported hooks:
    - lineage.edges: Sync lineage edges to OpenMetadata
    - quality.result: Sync quality check results as test cases
    - publish.end: Sync published table metadata

Example:
    The plugin is auto-discovered by phlo's hook system and begins syncing
    automatically when configured.

"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from phlo.hooks import LineageEvent, PublishEvent, QualityResultEvent
from phlo.logging import get_logger
from phlo.plugins.base import PluginMetadata
from phlo.plugins.hooks import HookFilter, HookPlugin, HookRegistration

from phlo_openmetadata.openmetadata import OpenMetadataClient, OpenMetadataTable
from phlo_openmetadata.quality_sync import QualityCheckMapper
from phlo_openmetadata.settings import get_settings as get_openmetadata_settings

logger = get_logger(__name__)


class OpenMetadataHookPlugin(HookPlugin):
    """Hook plugin that syncs lineage, quality, and publish events.

    Automatically captures pipeline events and syncs them to OpenMetadata:
    - Lineage edges between assets
    - Quality check results as test cases
    - Published table metadata

    The plugin initializes the OpenMetadata client lazily and only syncs
    when openmetadata_sync_enabled is True in settings.

    Example:
        >>> plugin = OpenMetadataHookPlugin()
        >>> hooks = plugin.get_hooks()
        >>> len(hooks)
        3

    """

    def __init__(self) -> None:
        """Initialize the plugin with lazy client setup.

        The OpenMetadata client is not created until first use to avoid
        connection overhead if sync is disabled.
        """
        self._client: OpenMetadataClient | None = None

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin name, version, and description."""
        return PluginMetadata(
            name="openmetadata",
            version="0.1.0",
            description="Hook handlers for OpenMetadata metadata and quality sync",
        )

    def get_hooks(self) -> list[HookRegistration]:
        """Register lineage, quality, and publish hook handlers."""
        return [
            HookRegistration(
                hook_name="openmetadata_lineage",
                handler=self._handle_lineage,
                filters=HookFilter(event_types={"lineage.edges"}),
            ),
            HookRegistration(
                hook_name="openmetadata_quality",
                handler=self._handle_quality_result,
                filters=HookFilter(event_types={"quality.result"}),
            ),
            HookRegistration(
                hook_name="openmetadata_publish",
                handler=self._handle_publish,
                filters=HookFilter(event_types={"publish.end"}),
            ),
        ]

    def cleanup(self) -> None:
        """Close the OpenMetadata client if initialized; call during plugin shutdown."""
        if self._client:
            self._client.close()
            self._client = None

    def _handle_lineage(self, event: Any) -> None:
        """Sync lineage edges from a LineageEvent into OpenMetadata."""
        if not isinstance(event, LineageEvent):
            return
        logger.info("openmetadata_lineage_sync_started", edge_count=len(event.edges))
        client = self._get_client()
        if client is None:
            logger.info(
                "openmetadata_lineage_sync_result",
                edge_count=len(event.edges),
                synced_count=0,
                failed_count=0,
                skipped=True,
            )
            return
        synced_count = 0
        failed_count = 0
        for from_fqn, to_fqn in event.edges:
            try:
                client.create_lineage(from_fqn, to_fqn)
                synced_count += 1
            except Exception as exc:
                failed_count += 1
                logger.warning(
                    "openmetadata_lineage_sync_failed",
                    from_fqn=from_fqn,
                    to_fqn=to_fqn,
                    error=str(exc),
                )
        logger.info(
            "openmetadata_lineage_sync_result",
            edge_count=len(event.edges),
            synced_count=synced_count,
            failed_count=failed_count,
            skipped=False,
        )

    def _handle_quality_result(self, event: Any) -> None:
        """Sync quality results into OpenMetadata test metadata.

        Creates or updates test definitions and test cases, then publishes
        the check result.
        """
        if not isinstance(event, QualityResultEvent):
            return
        logger.info(
            "openmetadata_quality_sync_started",
            check_name=event.check_name,
            check_type=event.check_type,
            passed=event.passed,
        )
        client = self._get_client()
        if client is None:
            logger.info(
                "openmetadata_quality_sync_result",
                check_name=event.check_name,
                table_fqn=None,
                definition_synced=False,
                test_case_synced=False,
                test_result_published=False,
                failed_count=0,
                skipped=True,
            )
            return

        table_fqn = _resolve_table_fqn(event)
        if not table_fqn:
            logger.warning("openmetadata_quality_sync_skipped", reason="missing_table_fqn")
            logger.info(
                "openmetadata_quality_sync_result",
                check_name=event.check_name,
                table_fqn=None,
                definition_synced=False,
                test_case_synced=False,
                test_result_published=False,
                failed_count=0,
                skipped=True,
            )
            return

        test_name = event.check_name
        test_type = _resolve_test_type(event)
        entity_type = _resolve_entity_type(event)
        definition_synced = False
        test_case_synced = False
        test_result_published = False
        failed_count = 0
        try:
            client.create_test_definition(
                test_name=test_name, test_type=test_type, entity_type=entity_type
            )
            definition_synced = True
        except Exception as exc:
            failed_count += 1
            logger.warning(
                "openmetadata_quality_definition_sync_failed",
                check_name=test_name,
                table_fqn=table_fqn,
                error=str(exc),
            )

        test_case_name = f"{table_fqn}_{test_name}"
        test_case_fqn = test_case_name
        try:
            test_case = client.create_test_case(
                test_case_name=test_case_name,
                table_fqn=table_fqn,
                test_definition_name=test_name,
            )
            test_case_fqn = (
                test_case.get("fullyQualifiedName") or test_case.get("name") or test_case_name
            )
            test_case_synced = True
        except Exception as exc:
            failed_count += 1
            logger.warning(
                "openmetadata_quality_test_case_sync_failed",
                check_name=test_name,
                table_fqn=table_fqn,
                error=str(exc),
            )

        result_value = event.metadata.get("metric_value")
        try:
            client.publish_test_result(
                test_case_fqn=test_case_fqn,
                result="Success" if event.passed else "Failed",
                test_execution_date=datetime.now(timezone.utc),
                result_value=str(result_value) if result_value is not None else None,
            )
            test_result_published = True
        except Exception as exc:
            failed_count += 1
            logger.warning(
                "openmetadata_quality_test_result_publish_failed",
                check_name=test_name,
                table_fqn=table_fqn,
                error=str(exc),
            )

        logger.info(
            "openmetadata_quality_sync_result",
            check_name=event.check_name,
            table_fqn=table_fqn,
            definition_synced=definition_synced,
            test_case_synced=test_case_synced,
            test_result_published=test_result_published,
            failed_count=failed_count,
            skipped=False,
        )

    def _handle_publish(self, event: Any) -> None:
        """Sync published tables into OpenMetadata; non-success events are skipped."""
        if not isinstance(event, PublishEvent):
            return
        if event.status != "success":
            return
        client = self._get_client()
        if client is None:
            return

        for target_table, target_fqn in event.tables.items():
            schema_name, table_name = _split_table_fqn(
                target_fqn,
                default_schema=os.getenv("PHLO_POSTGRES_MART_SCHEMA", "marts"),
            )
            try:
                table = OpenMetadataTable(name=table_name)
                client.create_or_update_table(schema_name=schema_name, table=table)
            except Exception as exc:
                logger.warning(
                    "OpenMetadata publish sync failed for %s: %s",
                    target_table,
                    exc,
                )

    def _get_client(self) -> OpenMetadataClient | None:
        """Return the OpenMetadata client if sync is enabled.

        Lazily initializes the client on first call. Returns None if
        sync is disabled in settings.
        """
        settings = get_openmetadata_settings()
        if not settings.openmetadata_sync_enabled:
            return None
        if self._client is None:
            try:
                self._client = OpenMetadataClient(
                    base_url=settings.openmetadata_uri(),
                    username=settings.openmetadata_username,
                    password=settings.openmetadata_password,
                    verify_ssl=settings.openmetadata_verify_ssl,
                    service_name=settings.openmetadata_service_name,
                    service_type=settings.openmetadata_database_service_type(),
                    database_name=settings.openmetadata_database(),
                )
            except RuntimeError as exc:
                logger.warning("openmetadata_client_configuration_unavailable", error=str(exc))
                return None
        return self._client


def _resolve_table_fqn(event: QualityResultEvent) -> str | None:
    """Return the table FQN from event metadata, falling back to the asset key."""
    for key in ("table_fqn", "table_name", "table"):
        value = event.metadata.get(key)
        if isinstance(value, str) and value:
            return value
    if event.asset_key:
        return event.asset_key
    return None


def _resolve_test_type(event: QualityResultEvent) -> str:
    """Map a quality check type to its OpenMetadata test type."""
    check_type = event.check_type or ""
    if check_type.lower() == "pandera":
        return "schemaCheck"
    return QualityCheckMapper.CHECK_TYPE_MAP.get(check_type, "customCheck")


def _resolve_entity_type(event: QualityResultEvent) -> str:
    """Return 'COLUMN' for column-level check types, otherwise 'TABLE'."""
    check_type = (event.check_type or "").lower()
    if check_type in {"nullcheck", "rangecheck", "uniquecheck", "freshnesscheck"}:
        return "COLUMN"
    return "TABLE"


def _split_table_fqn(table_fqn: str, default_schema: str) -> tuple[str, str]:
    """Split 'schema.table' into schema and table, using default_schema when unqualified."""
    if "." not in table_fqn:
        return default_schema, table_fqn
    schema_name, table_name = table_fqn.split(".", 1)
    return schema_name, table_name
