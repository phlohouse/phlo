"""Compatibility adapters for sync and async operation contracts.

Adapt ingesters and transformers in both directions: sync implementations run
on worker threads behind the async contract, async implementations run on a
private event loop behind the sync contract. The sync wrappers refuse to run
inside an active event loop instead of failing opaquely.

Deprecated: the adapter quartet (SyncToAsyncIngesterAdapter, AsyncToSyncIngesterAdapter,
SyncToAsyncTransformerAdapter, AsyncToSyncTransformerAdapter) has zero
production callers. Instantiating any of them emits a DeprecationWarning and
they will be removed in an upcoming release; implement the target contract
directly instead. No shim is provided.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

from phlo.operations.ingestion import AsyncIngester, BaseIngester, IngestionResult
from phlo.operations.transformation import AsyncTransformer, BaseTransformer, TransformationResult


def _warn_adapter_deprecated(class_name: str) -> None:
    """Emit the deprecation warning for one adapter use."""
    warnings.warn(
        f"{class_name} is deprecated and will be removed in an upcoming "
        "release: implement the target ingestion/transform "
        "contract directly instead. No shim is provided.",
        DeprecationWarning,
        stacklevel=3,
    )


def _ensure_no_running_event_loop() -> None:
    """Raise a clear error when sync wrappers are used from an active event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "Cannot run async operation from sync adapter while an event loop is running. "
        "Use the async operation directly."
    )


class SyncToAsyncIngesterAdapter(AsyncIngester):
    """Expose a sync ingester behind the async ingestion contract."""

    def __init__(self, ingester: BaseIngester):
        super().__init__(context=ingester.context, logger=ingester.logger)
        _warn_adapter_deprecated(type(self).__name__)
        self._ingester = ingester

    async def run_ingestion(
        self,
        partition_key: str | None,
        parameters: dict[str, Any],
    ) -> IngestionResult:
        """Run the wrapped sync ingester on a worker thread.

        The blocking call must not occupy the caller's event loop thread, so it
        is offloaded via ``asyncio.to_thread``.
        """
        return await asyncio.to_thread(self._ingester.run_ingestion, partition_key, parameters)


class AsyncToSyncIngesterAdapter(BaseIngester):
    """Expose an async ingester behind the sync ingestion contract."""

    def __init__(self, ingester: AsyncIngester):
        super().__init__(context=ingester.context, logger=ingester.logger)
        _warn_adapter_deprecated(type(self).__name__)
        self._ingester = ingester

    def run_ingestion(
        self,
        partition_key: str | None,
        parameters: dict[str, Any],
    ) -> IngestionResult:
        """Run the wrapped async ingester on a private event loop.

        ``asyncio.run`` creates and closes a loop per call; a caller already
        inside an event loop must use the async contract instead.
        """
        _ensure_no_running_event_loop()
        return asyncio.run(self._ingester.run_ingestion(partition_key, parameters))


class SyncToAsyncTransformerAdapter(AsyncTransformer[Any]):
    """Expose a sync transformer behind the async transform contract."""

    def __init__(self, transformer: BaseTransformer[Any]):
        super().__init__(context=transformer.context, logger=transformer.logger)
        _warn_adapter_deprecated(type(self).__name__)
        self._transformer = transformer

    async def run_transform(
        self,
        partition_key: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> TransformationResult:
        """Run the wrapped sync transformer on a worker thread.

        The blocking call must not occupy the caller's event loop thread, so it
        is offloaded via ``asyncio.to_thread``.
        """
        return await asyncio.to_thread(self._transformer.run_transform, partition_key, parameters)


class AsyncToSyncTransformerAdapter(BaseTransformer[Any]):
    """Expose an async transformer behind the sync transform contract."""

    def __init__(self, transformer: AsyncTransformer[Any]):
        super().__init__(context=transformer.context, logger=transformer.logger)
        _warn_adapter_deprecated(type(self).__name__)
        self._transformer = transformer

    def run_transform(
        self,
        partition_key: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> TransformationResult:
        """Run the wrapped async transformer on a private event loop.

        ``asyncio.run`` creates and closes a loop per call; a caller already
        inside an event loop must use the async contract instead.
        """
        _ensure_no_running_event_loop()
        return asyncio.run(self._transformer.run_transform(partition_key, parameters))
