"""ClickHouse service and resource plugin package.

This package provides ClickHouse integration for the Phlo data platform,
including service plugins, resource providers, CLI commands, and configuration
tools for managing ClickHouse databases.

Example:
    Basic usage of the ClickHouse package:

    >>> from phlo_clickhouse import ClickHouseResource, get_settings
    >>> settings = get_settings()
    >>> resource = ClickHouseResource()
    >>> resource.execute("SELECT 1")
    [[1]]
"""

from phlo_clickhouse.authorization import (
    ClickHouseSurfaceAdapter,
    get_adapter as get_clickhouse_adapter,
)
from phlo_clickhouse.plugin import (
    ClickHouseResourceProvider,
    ClickHouseServicePlugin,
    ClickHouseSetupServicePlugin,
)
from phlo_clickhouse.resource import ClickHouseResource
from phlo_clickhouse.settings import ClickHouseSettings, get_settings

__all__ = [
    "ClickHouseResource",
    "ClickHouseResourceProvider",
    "ClickHouseServicePlugin",
    "ClickHouseSetupServicePlugin",
    "ClickHouseSettings",
    "ClickHouseSurfaceAdapter",
    "get_clickhouse_adapter",
    "get_settings",
]
__version__ = "0.14.0"
