"""MinIO service and resource provider plugin for Phlo.

This module provides the complete MinIO integration for Phlo, including:
- Service plugin for MinIO server deployment
- Bucket initialization (setup) service
- Object storage capability provider
- Resource provider for S3-compatible storage

The module implements Phlo's plugin interfaces to provide S3-compatible
object storage capabilities for data lake operations.

Examples:
    Service plugin usage:
        >>> from phlo_minio.plugin import MinioServicePlugin
        >>> plugin = MinioServicePlugin()
        >>> defn = plugin.service_definition
        >>> print(defn['services'].keys())
        dict_keys(['minio'])

    Object store provider:
        >>> from phlo_minio.plugin import MinioObjectStoreProvider
        >>> provider = MinioObjectStoreProvider()
        >>> conn = provider.to_sling_connection()
        >>> print(conn['type'])
        's3'

    Resource provider integration:
        >>> from phlo_minio.plugin import MinioResourceProvider
        >>> rp = MinioResourceProvider()
        >>> stores = rp.get_object_stores()
        >>> print(stores[0].name)
        'minio'

See Also:
    phlo_minio.settings: Configuration management for MinIO connections.
    phlo.plugins: Phlo plugin framework interfaces.


    MinIO plugin module; its resource and service plugins register via phlo plugin entry points.
    Builds on phlo.capabilities, the phlo.plugins interfaces, and phlo_minio.settings.
"""

from __future__ import annotations

from typing import Any

from phlo.capabilities import BackendReadinessSpec, ObjectStoreSpec, ResourceSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin, service_plugin_class


MinioServicePlugin = service_plugin_class(
    "MinioServicePlugin",
    name="minio",
    version="0.1.0",
    description="S3-compatible object storage for data lake",
    author="Phlo Team",
    tags=["core", "storage", "s3"],
)


MinioSetupServicePlugin = service_plugin_class(
    "MinioSetupServicePlugin",
    name="minio-setup",
    version="0.1.0",
    description="Initialize MinIO buckets for data lake",
    author="Phlo Team",
    tags=["core", "storage", "bootstrap"],
    service_definition_file="minio-setup.yaml",
)


class MinioObjectStoreProvider:
    """Capability provider for MinIO-backed S3-compatible object storage.

    This class provides object storage capabilities compatible with
    S3 SDKs and tools. It handles configuration translation between
    Phlo settings and various S3 client formats.

    The provider supports:
    - S3 API operations (boto3, aws-sdk, etc.)
    - Sling replication tool integration
    - Standard S3 endpoint configuration

    Examples:
        Basic provider usage:
            >>> provider = MinioObjectStoreProvider()
            >>> conn = provider.to_sling_connection()
            >>> print(conn['endpoint'])
            'http://minio:10001'

        Integration with boto3:
            >>> import boto3
            >>> provider = MinioObjectStoreProvider()
            >>> conn = provider.to_sling_connection()
            >>> s3 = boto3.client(
            ...     's3',
            ...     endpoint_url=conn['endpoint'],
            ...     aws_access_key_id=conn['access_key_id'],
            ...     aws_secret_access_key=conn['secret_access_key'],
            ...     region_name=conn['region']
            ... )
            >>> # Now use s3.list_buckets(), s3.put_object(), etc.

        List buckets:
            >>> import boto3
            >>> provider = MinioObjectStoreProvider()
            >>> conn = provider.to_sling_connection()
            >>> s3 = boto3.client('s3', **conn)
            >>> response = s3.list_buckets()
            >>> print([b['Name'] for b in response['Buckets']])
            ['bronze', 'silver', 'gold']

    Note:
        Uses phlo_minio.settings.get_settings() for configuration.
        Settings are cached for performance.

    """

    def to_sling_connection(self) -> dict[str, Any]:
        """Return a Sling-compatible S3 connection definition (type, endpoint,
        root credentials, region) built from MinIO settings.

        Examples:
            Sling connection:
                >>> provider = MinioObjectStoreProvider()
                >>> conn = provider.to_sling_connection()
                >>> print(conn)
                {
                    'type': 's3',
                    'endpoint': 'http://minio:10001',
                    'access_key_id': 'minio',
                    'secret_access_key': 'minio123',
                    'region': 'us-east-1'
                }

            Sling replication config:
                >>> provider = MinioObjectStoreProvider()
                >>> conn = provider.to_sling_connection()
                >>> sling_config = {
                ...     'source': conn,
                ...     'target': {'type': 'postgres', ...},
                ...     'streams': {'s3://bucket/key': {'mode': 'full_refresh'}}
                ... }

            Direct S3 operations:
                >>> import boto3
                >>> provider = MinioObjectStoreProvider()
                >>> conn = provider.to_sling_connection()
                >>> s3 = boto3.client(
                ...     's3',
                ...     endpoint_url=conn['endpoint'],
                ...     aws_access_key_id=conn['access_key_id'],
                ...     aws_secret_access_key=conn['secret_access_key'],
                ...     region_name=conn['region']
                ... )
                >>> # Upload a file
                >>> s3.upload_file('data.csv', 'my-bucket', 'data.csv')
                >>> # Download a file
                >>> s3.download_file('my-bucket', 'data.csv', 'downloaded.csv')
                >>> # List objects
                >>> response = s3.list_objects_v2(Bucket='my-bucket', Prefix='data/')
                >>> print([obj['Key'] for obj in response.get('Contents', [])])

        """
        from phlo_minio.settings import get_settings

        settings = get_settings()
        return {
            "type": "s3",
            "endpoint": f"http://{settings.minio_endpoint()}",
            "access_key_id": settings.minio_root_user,
            "secret_access_key": settings.minio_root_password,
            "region": settings.s3_region,
        }


class MinioResourceProvider(ResourceProviderPlugin):
    def get_backend_readiness(self) -> list[BackendReadinessSpec]:
        """Expose the minio security readiness inspector (read-only)."""
        from phlo_minio.security_readiness import MinioReadinessProvider

        return [BackendReadinessSpec(name="minio", provider=MinioReadinessProvider())]

    """Resource provider plugin exposing MinIO object storage capabilities.

    This plugin implements the ResourceProviderPlugin interface to
    expose MinIO as an object storage capability within Phlo's
    capability framework. It enables other Phlo components to
    discover and use MinIO for S3-compatible storage operations.

    The provider exposes:
    - ObjectStoreSpec for S3 operations
    - Metadata for capability discovery
    - Connection parameters for client configuration

    Examples:
        Plugin metadata:
            >>> provider = MinioResourceProvider()
            >>> print(provider.metadata.name)
            'minio'
            >>> print(provider.metadata.tags)
            ['core', 'storage', 's3']

        Get object stores:
            >>> provider = MinioResourceProvider()
            >>> stores = provider.get_object_stores()
            >>> store = stores[0]
            >>> print(store.name)
            'minio'
            >>> print(store.metadata['storage_system'])
            's3'

        Access store provider:
            >>> provider = MinioResourceProvider()
            >>> store = provider.get_object_stores()[0]
            >>> conn = store.provider.to_sling_connection()
            >>> print(conn['endpoint'])
            'http://minio:10001'

    The provider exposes an ObjectStoreSpec for S3 operations plus metadata
    for capability discovery. See phlo.capabilities.ObjectStoreSpec and
    MinioObjectStoreProvider for the underlying implementation.

    See Also:
        phlo.capabilities.ObjectStoreSpec: Object storage capability spec.
        MinioObjectStoreProvider: The underlying capability implementation.

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the MinIO resource provider."""
        return PluginMetadata(
            name="minio",
            version="0.1.0",
            description="MinIO object-store capability for Phlo",
            tags=["core", "storage", "s3"],
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Return an empty list — MinIO exposes no traditional resources;
        object storage is provided via get_object_stores().

        Examples:
            Check resources:
                >>> provider = MinioResourceProvider()
                >>> resources = provider.get_resources()
                >>> len(resources)
                0

        """
        return []

    def get_object_stores(self) -> list[ObjectStoreSpec]:
        """Return the MinIO ObjectStoreSpec with its provider instance and
        storage metadata (type, storage_system, endpoint).

        Examples:
            Get object stores:
                >>> provider = MinioResourceProvider()
                >>> stores = provider.get_object_stores()
                >>> len(stores)
                1
                >>> store = stores[0]
                >>> store.name
                'minio'

            Access store metadata:
                >>> provider = MinioResourceProvider()
                >>> store = provider.get_object_stores()[0]
                >>> print(store.metadata['type'])
                's3'
                >>> print(store.metadata['storage_system'])
                's3'
                >>> print(store.metadata['endpoint'])
                'http://minio:10001'

            Use with S3 client:
                >>> provider = MinioResourceProvider()
                >>> store = provider.get_object_stores()[0]
                >>> conn = store.provider.to_sling_connection()
                >>>
                >>> # Use with boto3
                >>> import boto3
                >>> s3 = boto3.client('s3', **conn)
                >>> buckets = s3.list_buckets()
                >>> print([b['Name'] for b in buckets['Buckets']])
                ['bronze', 'silver', 'gold']

            Use with Sling:
                >>> provider = MinioResourceProvider()
                >>> store = provider.get_object_stores()[0]
                >>> conn = store.provider.to_sling_connection()
                >>>
                >>> # Configure Sling replication
                >>> sling_replication = {
                ...     'source': conn,
                ...     'target': {'type': 'postgres', 'url': '...'},
                ...     'streams': {
                ...         's3://bronze/raw-data/*.csv': {
                ...             'mode': 'incremental',
                ...             'primary_key': ['id']
                ...         }
                ...     }
                ... }

        """
        provider = MinioObjectStoreProvider()
        return [
            ObjectStoreSpec(
                name="minio",
                provider=provider,
                metadata={
                    "storage_system": "s3",
                    "type": "s3",
                    "endpoint": provider.to_sling_connection()["endpoint"],
                },
            )
        ]
