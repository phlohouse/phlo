"""Phlo Trino package - Distributed SQL query engine integration.

This package provides Trino integration for the Phlo data platform,
including resource management, governance, and query capabilities.

Exports:
    TrinoGovernanceBackend: Access control via SQL grants.
    TrinoResourceProvider: Plugin providing Trino resources.
    TrinoServicePlugin: Service plugin for Trino orchestration.
    TrinoResource: Core resource for Trino connections and queries.
    TrinoSettings: Configuration settings for Trino connections.
    get_settings: Cached settings factory function.

Example:
    >>> from phlo_trino import TrinoResource, get_settings
    >>> settings = get_settings()
    >>> trino = TrinoResource()
    >>> results = trino.execute("SELECT * FROM my_table")

"""

from phlo_trino.governance import TrinoGovernanceBackend
from phlo_trino.plugin import TrinoResourceProvider, TrinoServicePlugin
from phlo_trino.resource import TrinoResource
from phlo_trino.settings import TrinoSettings, get_settings

__all__ = [
    "TrinoGovernanceBackend",
    "TrinoResourceProvider",
    "TrinoServicePlugin",
    "TrinoResource",
    "TrinoSettings",
    "get_settings",
]
from importlib.metadata import version

__version__ = version("phlo-trino")
