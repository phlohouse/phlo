"""Ingestion and transformation operation contracts for Phlo.

Exposes sync and async ingester/transformer base classes plus adapters that
bridge sync implementations into async pipelines and vice versa.
"""

from phlo.operations.adapters import (
    AsyncToSyncIngesterAdapter,
    AsyncToSyncTransformerAdapter,
    SyncToAsyncIngesterAdapter,
    SyncToAsyncTransformerAdapter,
)
from phlo.operations.ingestion import AsyncIngester, BaseIngester, IngestionResult
from phlo.operations.transformation import AsyncTransformer, BaseTransformer, TransformationResult

__all__ = [
    "AsyncToSyncIngesterAdapter",
    "AsyncToSyncTransformerAdapter",
    "AsyncIngester",
    "AsyncTransformer",
    "BaseIngester",
    "BaseTransformer",
    "IngestionResult",
    "SyncToAsyncIngesterAdapter",
    "SyncToAsyncTransformerAdapter",
    "TransformationResult",
]
