"""Tests for async operation contracts and compatibility adapters.

Covers bidirectional sync/async adapters for ingesters and transformers
and that sync-facing async adapters reject calls from a running event loop
instead of blocking it.
"""

from __future__ import annotations

import pytest

from phlo.operations import (
    AsyncIngester,
    AsyncToSyncIngesterAdapter,
    AsyncToSyncTransformerAdapter,
    AsyncTransformer,
    BaseIngester,
    BaseTransformer,
    IngestionResult,
    SyncToAsyncIngesterAdapter,
    SyncToAsyncTransformerAdapter,
    TransformationResult,
)

pytestmark = [pytest.mark.core_regression, pytest.mark.filterwarnings("ignore::DeprecationWarning")]


class _TestLogger:
    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        return None

    def error(self, msg: str, *args: object, **kwargs: object) -> None:
        return None


class _SyncIngester(BaseIngester):
    def run_ingestion(
        self,
        partition_key: str | None,
        parameters: dict[str, object],
    ) -> IngestionResult:
        return IngestionResult(
            status="success",
            rows_inserted=1,
            rows_deleted=0,
            metadata={"partition_key": partition_key, **parameters},
        )


class _AsyncIngester(AsyncIngester):
    async def run_ingestion(
        self,
        partition_key: str | None,
        parameters: dict[str, object],
    ) -> IngestionResult:
        return IngestionResult(
            status="success",
            rows_inserted=2,
            rows_deleted=0,
            metadata={"partition_key": partition_key, **parameters},
        )


class _SyncTransformer(BaseTransformer[object]):
    def run_transform(
        self,
        partition_key: str | None = None,
        parameters: dict[str, object] | None = None,
    ) -> TransformationResult:
        return TransformationResult(
            status="success",
            models_built=1,
            models_failed=0,
            tests_passed=1,
            tests_failed=0,
            metadata={"partition_key": partition_key, **(parameters or {})},
        )


class _AsyncTransformer(AsyncTransformer[object]):
    async def run_transform(
        self,
        partition_key: str | None = None,
        parameters: dict[str, object] | None = None,
    ) -> TransformationResult:
        return TransformationResult(
            status="success",
            models_built=2,
            models_failed=0,
            tests_passed=2,
            tests_failed=0,
            metadata={"partition_key": partition_key, **(parameters or {})},
        )


@pytest.mark.anyio
async def test_sync_to_async_ingester_adapter() -> None:
    ingester = _SyncIngester(context=object(), logger=object())
    adapter = SyncToAsyncIngesterAdapter(ingester)
    result = await adapter.run_ingestion("2026-03-01", {"mode": "full"})
    assert result.rows_inserted == 1
    assert result.metadata["mode"] == "full"


def test_async_to_sync_ingester_adapter() -> None:
    ingester = _AsyncIngester(context=object(), logger=object())
    adapter = AsyncToSyncIngesterAdapter(ingester)
    result = adapter.run_ingestion("2026-03-01", {"mode": "full"})
    assert result.rows_inserted == 2
    assert result.metadata["mode"] == "full"


@pytest.mark.anyio
async def test_sync_to_async_transformer_adapter() -> None:
    transformer = _SyncTransformer(context=object(), logger=_TestLogger())
    adapter = SyncToAsyncTransformerAdapter(transformer)
    result = await adapter.run_transform("2026-03-01", {"target": "dev"})
    assert result.models_built == 1
    assert result.metadata["target"] == "dev"


def test_async_to_sync_transformer_adapter() -> None:
    transformer = _AsyncTransformer(context=object(), logger=_TestLogger())
    adapter = AsyncToSyncTransformerAdapter(transformer)
    result = adapter.run_transform("2026-03-01", {"target": "prod"})
    assert result.models_built == 2
    assert result.metadata["target"] == "prod"


@pytest.mark.anyio
async def test_async_to_sync_ingester_adapter_rejects_running_loop() -> None:
    ingester = _AsyncIngester(context=object(), logger=object())
    adapter = AsyncToSyncIngesterAdapter(ingester)
    with pytest.raises(RuntimeError, match="event loop is running"):
        adapter.run_ingestion("2026-03-01", {})


@pytest.mark.anyio
async def test_async_to_sync_transformer_adapter_rejects_running_loop() -> None:
    transformer = _AsyncTransformer(context=object(), logger=_TestLogger())
    adapter = AsyncToSyncTransformerAdapter(transformer)
    with pytest.raises(RuntimeError, match="event loop is running"):
        adapter.run_transform("2026-03-01", {})


def test_adapter_quartet_emits_deprecation_warning() -> None:
    """Instantiating any deprecated adapter emits a warning."""
    ingester = _SyncIngester(context=object(), logger=_TestLogger())
    async_ingester = _AsyncIngester(context=object(), logger=object())
    transformer = _SyncTransformer(context=object(), logger=_TestLogger())
    async_transformer = _AsyncTransformer(context=object(), logger=_TestLogger())

    for adapter_cls, wrapped in [
        (SyncToAsyncIngesterAdapter, ingester),
        (AsyncToSyncIngesterAdapter, async_ingester),
        (SyncToAsyncTransformerAdapter, transformer),
        (AsyncToSyncTransformerAdapter, async_transformer),
    ]:
        with pytest.warns(DeprecationWarning, match=f"{adapter_cls.__name__} is deprecated"):
            adapter_cls(wrapped)  # type: ignore[arg-type]
