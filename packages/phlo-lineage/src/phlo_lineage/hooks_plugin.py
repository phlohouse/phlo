"""Hook plugin for updating the lineage graph from events.

This module implements the LineageHookPlugin class, which integrates with the
Phlo hooks system to receive lineage events and update both the in-memory graph
and persistent store. It enables real-time lineage tracking as assets are
materialized in the pipeline.

    The plugin subscribes to the lineage_updates hook filtered to
    lineage.edges events. Each received event updates the in-memory
    LineageGraph immediately (O(1) per edge) and then persists edges to
    PostgreSQL via LineageStore; persistence failures are logged and never
    affect the in-memory update, and missing database configuration only
    logs a skip. Auto-discovered via entry points; events are emitted by
    the orchestration layer during asset materialization.

"""

from __future__ import annotations

from typing import Any

from phlo.hooks import LineageEvent
from phlo.logging import get_logger
from phlo.plugins.base import PluginMetadata
from phlo.plugins.hooks import HookFilter, HookPlugin, HookRegistration

from phlo_lineage.graph import get_lineage_graph
from phlo_lineage.store import LineageStore, resolve_lineage_db_url_with_postgres_fallback

logger = get_logger(__name__)


class LineageHookPlugin(HookPlugin):
    """Hook plugin that synchronizes lineage events to graph and database.

    Subscribes to lineage events and propagates them to both the in-memory
    graph (immediate visibility) and the persistent store (durability).
    Non-LineageEvent payloads are silently ignored and database failures
    are logged without crashing processing.

    Example:
        The plugin operates automatically once registered. Events are
        typically emitted by the orchestration layer:

        >>> from phlo.hooks import LineageEvent
        >>> event = LineageEvent(
        ...     edges=[("bronze.orders", "silver.stg_orders")],
        ...     asset_keys=["bronze.orders", "silver.stg_orders"],
        ... )
        >>> # Event is automatically processed by LineageHookPlugin

    See Also:
        phlo.plugins.hooks.HookPlugin for the base interface.

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for discovery and identification.

        Example:
            >>> plugin = LineageHookPlugin()
            >>> meta = plugin.metadata
            >>> print(meta.name)
            'lineage'
        """
        return PluginMetadata(
            name="lineage",
            version="0.1.0",
            description="Hook handlers for lineage updates",
        )

    def get_hooks(self) -> list[HookRegistration]:
        """Register hook handlers with the Phlo event system.

        Returns one HookRegistration for the "lineage_updates" hook bound to
        ``_handle_lineage`` and filtered to lineage.edges event types so
        other lineage-related events are ignored.
        """
        return [
            HookRegistration(
                hook_name="lineage_updates",
                handler=self._handle_lineage,
                filters=HookFilter(event_types={"lineage.edges"}),
            )
        ]

    def _handle_lineage(self, event: Any) -> None:
        """Process a lineage event: update the in-memory graph immediately,
        then persist edges when a database is configured.

        Non-LineageEvent payloads are ignored. Persistence failures are
        logged with edge context and never propagate to the caller. Called
        automatically by the hooks system; manual invocation is mainly for
        testing.
        """
        if not isinstance(event, LineageEvent):
            return
        edge_count = len(event.edges)
        asset_key_count = len(event.asset_keys or [])
        graph = get_lineage_graph()
        for source, target in event.edges:
            graph.add_edge(source, target)

        connection_string = resolve_lineage_db_url_with_postgres_fallback()
        if not connection_string:
            logger.info(
                "lineage_sync_skipped_missing_db_url",
                event_type=event.event_type,
                edge_count=edge_count,
                asset_key_count=asset_key_count,
            )
            return

        logger.info(
            "lineage_sync_started",
            event_type=event.event_type,
            edge_count=edge_count,
            asset_key_count=asset_key_count,
        )
        try:
            store = LineageStore(connection_string)
            persisted_edges = store.record_asset_edges(
                event.edges,
                asset_keys=event.asset_keys,
                metadata=event.metadata,
                tags=event.tags,
            )
            logger.info(
                "lineage_sync_succeeded",
                event_type=event.event_type,
                edge_count=edge_count,
                asset_key_count=asset_key_count,
                persisted_edge_count=persisted_edges,
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist asset lineage edges: %s",
                exc,
                event_type=event.event_type,
                edge_count=edge_count,
                asset_key_count=asset_key_count,
            )
