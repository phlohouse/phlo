"""Nessie service plugin package.

This package provides integration with Apache Nessie, a Git-like catalog for
Iceberg tables. It includes service management, resource providers, branch
management, and CLI commands for the Nessie catalog backend.

Example:
    >>> from phlo_nessie import NessieServicePlugin, NessieResource
    >>> plugin = NessieServicePlugin()
    >>> resource = NessieResource()

    Exposes NessieServicePlugin, NessieResourceProvider, NessieResource,
    BranchManagerResource, and NessieSettings.
"""

from phlo_nessie.plugin import NessieServicePlugin
from phlo_nessie.resource_provider import NessieResourceProvider
from phlo_nessie.resource import BranchManagerResource, NessieResource
from phlo_nessie.settings import NessieSettings, get_settings

__all__ = [
    "NessieServicePlugin",
    "NessieResourceProvider",
    "NessieResource",
    "BranchManagerResource",
    "NessieSettings",
    "get_settings",
]
__version__ = "0.14.0"
