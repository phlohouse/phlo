"""ClickHouse publish target for mart publishing.

This module defines the publish target implementation for ClickHouse,
enabling data marts to be published to ClickHouse tables.

Example:
    Creating a ClickHouse publish target:

    >>> from phlo_clickhouse.publish_target import ClickHousePublishTarget
    >>> target = ClickHousePublishTarget()
    >>> target.target_system
    'clickhouse'

"""

from __future__ import annotations

from dataclasses import dataclass, field

from phlo_clickhouse.resource import ClickHouseResource


@dataclass
class ClickHousePublishTarget:
    """Publish target backed by ClickHouse for data marts.

    Example:
        >>> target = ClickHousePublishTarget()
        >>> target.target_system
        'clickhouse'
        >>> target.default_schema
        'marts'

    """

    resource: ClickHouseResource = field(default_factory=ClickHouseResource)
    target_system: str = "clickhouse"
    default_schema: str = "marts"
