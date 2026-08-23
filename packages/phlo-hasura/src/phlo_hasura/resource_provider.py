"""Resource provider plugin for phlo-hasura capabilities.

This module provides the HasuraResourceProvider class that exposes Hasura
as an API backend capability through the Phlo resource provider system.

The provider allows Hasura to be discovered and used as a swappable
GraphQL API backend by other components in the Phlo ecosystem.

Example:
    >>> from phlo_hasura.resource_provider import HasuraResourceProvider
    >>> provider = HasuraResourceProvider()
    >>> backends = provider.get_api_backends()
    >>> print(backends[0].name)
    'hasura'


    Hasura resource provider, loaded via the phlo.plugins.resources entry point at startup.
    Builds on phlo.capabilities and the phlo.plugins resource-provider interfaces.
"""

from __future__ import annotations

from phlo.capabilities import ApiBackendSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin

from phlo_hasura.api_backend import HasuraApiBackend


class HasuraResourceProvider(ResourceProviderPlugin):
    """Expose Hasura as a swappable API backend capability.

    This provider integrates Hasura with the Phlo capability system,
    allowing it to be discovered and used as a GraphQL API backend.

    Example:
        >>> provider = HasuraResourceProvider()
        >>> provider.metadata.name
        'hasura'
        >>> backends = provider.get_api_backends()
        >>> backends[0].metadata['backend_kind']
        'graphql'

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for capability discovery.

        Example:
            >>> provider = HasuraResourceProvider()
            >>> meta = provider.metadata
            >>> print(meta.name, meta.tags)
            hasura ['api', 'graphql', 'bi']
        """
        return PluginMetadata(
            name="hasura",
            version="0.1.0",
            description="Hasura API backend capability provider",
            tags=["api", "graphql", "bi"],
        )

    def get_resources(self) -> list:
        """Return an empty list: this provider exposes no raw resources directly;
        resources are accessed through the API backend interface.
        """
        return []

    def get_api_backends(self) -> list[ApiBackendSpec]:
        """Expose Hasura as an API backend capability consumable by components that
        need a GraphQL API backend.

        Example:
            >>> provider = HasuraResourceProvider()
            >>> backends = provider.get_api_backends()
            >>> backends[0].name
            'hasura'
            >>> backends[0].metadata['backend_kind']
            'graphql'
        """
        return [
            ApiBackendSpec(
                name="hasura",
                provider=HasuraApiBackend(),
                metadata={
                    "backend_kind": "graphql",
                    "service_name": "hasura",
                    "category": "api",
                },
            )
        ]
