"""Registry for Sling replication table configurations.

This module defines data structures for representing Sling replication
configurations within the Phlo platform. It provides both immutable
configuration objects for runtime execution and Python-first definitions
for programmatic asset discovery.

Classes:
    ReplicationConfig: Immutable configuration for a registered replication stream.
    SlingReplication: Python-first definition for dynamic asset discovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from phlo_sling.settings import get_settings


@dataclass(frozen=True)
class ReplicationConfig:
    """Configuration describing a registered Sling replication stream.

    This immutable dataclass represents the complete configuration for a
    single Sling replication operation, including source and target connections,
    replication mode, filtering, and options.

    Example:
        Create a replication config::

            config = ReplicationConfig(
                stream_name="public.users",
                table_name="users",
                source_conn="PHLO_POSTGRES",
                mode="incremental",
                update_key="updated_at",
            )
    """

    stream_name: str
    table_name: str
    source_conn: str
    target_conn: str | None = None
    mode: str = "incremental"
    primary_key: list[str] = field(default_factory=list)
    update_key: str | None = None
    group_name: str = "sling"
    object: str | None = None
    select: list[str] = field(default_factory=list)
    where: str | None = None
    source_options: dict[str, Any] = field(default_factory=dict)
    target_options: dict[str, Any] = field(default_factory=dict)

    @property
    def full_table_name(self) -> str:
        """Return fully qualified table name with default namespace.

        Combines the configured namespace with the table name to create
        a fully qualified identifier for the target table.
        """
        return f"{get_settings().sling_default_namespace}.{self.table_name}"

    @property
    def asset_key(self) -> str:
        """Return the Phlo asset key for this replication stream.

        Generates a unique asset key for referencing this replication
        within the Phlo orchestration system.
        """
        return f"sling_{self.table_name}"


@dataclass(frozen=True)
class SlingReplication:
    """Python-first replication definition for dynamic Sling asset discovery.

    This dataclass provides a programmatic way to define Sling replication
    configurations when using the @phlo_sling_assets decorator. It supports
    all the same options as the individual @phlo_sling_replication decorator
    but allows for dynamic generation of multiple assets.

    Example:
        Use in a discovery function::

            from phlo_sling import phlo_sling_assets, SlingReplication

            @phlo_sling_assets(group="ingestion")
            def discover_tables():
                return [
                    SlingReplication(
                        stream_name="public.users",
                        table_name="users",
                        source_conn="PHLO_POSTGRES",
                        mode="incremental",
                        update_key="updated_at",
                    ),
                ]
    """

    stream_name: str
    table_name: str
    source_conn: str
    target_conn: str | None = None
    mode: Literal["full-refresh", "incremental", "snapshot", "backfill"] | None = None
    primary_key: list[str] | str | None = None
    update_key: str | None = None
    group_name: str | None = None
    object: str | None = None
    select: list[str] = field(default_factory=list)
    where: str | None = None
    source_options: dict[str, Any] = field(default_factory=dict)
    target_options: dict[str, Any] = field(default_factory=dict)
    description: str | None = None
    owner: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
