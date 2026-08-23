"""Phlo Sling package for data replication.

Provides Sling-based data replication capabilities for the Phlo platform,
enabling declarative and programmatic definitions of replication pipelines
from various sources to target data stores. Exposes decorators for
registering Sling-backed assets and functions for retrieving registered
assets at runtime; ``SlingReplication`` is the data class for Python-first
replication definitions.

Example:
    Basic usage with the @phlo_sling_replication decorator::

        from phlo_sling import phlo_sling_replication

        @phlo_sling_replication(
            stream_name="public.users",
            table_name="users",
            source_conn="PHLO_POSTGRES",
            group="ingestion",
            mode="incremental",
            update_key="updated_at",
        )
        def replicate_users(context):
            pass
"""

from collections.abc import Callable
from typing import Any

from phlo_sling.authorization import SlingSurfaceAdapter, get_adapter as get_sling_adapter
from phlo_sling.helpers import (
    ConnectionSummary,
    ReplicationPlan,
    build_partition_where,
    build_replication_plan,
    summarize_connections,
    table_name_from_stream,
)
from phlo_sling.registry import SlingReplication


def phlo_sling_assets(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """Lazily resolve and forward to the Sling asset discovery decorator."""
    from phlo_sling.decorator import phlo_sling_assets as _phlo_sling_assets

    return _phlo_sling_assets(*args, **kwargs)


def phlo_sling_replication(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """Lazily resolve and forward to the Sling replication decorator factory."""
    from phlo_sling.decorator import phlo_sling_replication as _phlo_sling_replication

    return _phlo_sling_replication(*args, **kwargs)


def get_sling_assets() -> list[Any]:
    """Lazily resolve and return registered sling replication assets."""
    from phlo_sling.decorator import get_sling_assets as _get_sling_assets

    return _get_sling_assets()


__all__ = [
    "SlingReplication",
    "SlingSurfaceAdapter",
    "ConnectionSummary",
    "ReplicationPlan",
    "build_partition_where",
    "build_replication_plan",
    "get_sling_adapter",
    "get_sling_assets",
    "phlo_sling_assets",
    "phlo_sling_replication",
    "summarize_connections",
    "table_name_from_stream",
]
