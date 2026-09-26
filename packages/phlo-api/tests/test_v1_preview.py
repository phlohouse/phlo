"""Bounds and ref selection for the v1 Trino preview."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from phlo_api.observatory_api import http_client
from phlo_api.observatory_api import v1_preview as preview


def test_catalog_requires_exact_environment_ref_mapping(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_USER", "phlo_preview")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD", "secret")
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "prod": {"catalog": "iceberg_prod", "nessie_ref": "main"},
                "staging": {"catalog": "iceberg_stage", "nessie_ref": "candidate"},
            }
        ),
    )

    assert preview.preview_catalog("prod", "main") == "iceberg_prod"
    assert preview.preview_catalog("staging", "candidate") == "iceberg_stage"
    with pytest.raises(preview.PreviewUnavailable):
        preview.preview_catalog("prod", "candidate")

    monkeypatch.delenv("PHLO_V1_PREVIEW_TRINO_PASSWORD")
    with pytest.raises(preview.PreviewUnavailable):
        preview.preview_catalog("prod", "main")


def test_quote_table_quotes_every_asset_key_segment() -> None:
    assert preview.quote_table("iceberg_prod", 'warehouse/order"s') == (
        '"iceberg_prod"."warehouse"."order""s"'
    )
    assert preview.quote_table("iceberg_prod", 'warehouse/orders"; DROP TABLE x') == (
        '"iceberg_prod"."warehouse"."orders""; DROP TABLE x"'
    )


@pytest.mark.anyio
async def test_preview_cancels_latest_continuation_after_row_limit(monkeypatch) -> None:
    requests: list[tuple[str, str, str, str]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.method,
                request.url.path,
                request.headers.get("x-trino-session", ""),
                request.headers.get("authorization", ""),
            )
        )
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "columns": [{"name": "id", "type": "bigint"}],
                    "data": [[1], [2], [3]],
                    "nextUri": "https://trino:8443/v1/next/1",
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError("preview must not fetch another result page after limit + 1")

    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_USER", "phlo_preview")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD", "secret")
    monkeypatch.setattr(preview, "resolve_trino_url", lambda: "https://trino:8443")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        result = await preview.execute_preview(
            'SELECT * FROM "iceberg_prod"."warehouse"."orders" LIMIT 3',
            catalog="iceberg_prod",
            disconnected=lambda: _not_disconnected(),
            limit=2,
        )

    assert result["rows"] == [{"id": 1}, {"id": 2}]
    assert result["has_more"] is True
    assert [item[:2] for item in requests] == [
        ("POST", "/v1/statement"),
        ("DELETE", "/v1/next/1"),
    ]
    assert "query_max_run_time=20s" in requests[0][2]
    assert "query_max_scan_physical_bytes=268435456B" in requests[0][2]
    expected_auth = "Basic " + base64.b64encode(b"phlo_preview:secret").decode("ascii")
    assert all(item[3] == expected_auth for item in requests)


@pytest.mark.anyio
async def test_preview_cancels_query_when_result_poll_times_out(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "columns": [{"name": "id", "type": "bigint"}],
                    "nextUri": "https://trino:8443/v1/next/2",
                },
            )
        if request.method == "GET":
            raise httpx.ReadTimeout("simulated stalled poll")
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(f"unexpected request {request.method}")

    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_USER", "phlo_preview")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD", "secret")
    monkeypatch.setattr(preview, "resolve_trino_url", lambda: "https://trino:8443")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        with pytest.raises(preview.PreviewLimitExceeded):
            await preview.execute_preview(
                "SELECT 1",
                catalog="iceberg_prod",
                disconnected=lambda: _not_disconnected(),
                limit=10,
            )

    assert calls == [
        ("POST", "/v1/statement"),
        ("GET", "/v1/next/2"),
        ("DELETE", "/v1/next/2"),
    ]


@pytest.mark.anyio
async def test_preview_cancels_query_when_output_exceeds_byte_budget(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "columns": [{"name": "value", "type": "varchar"}],
                    "data": [["x" * 1_100_000], ["extra"]],
                    "nextUri": "https://trino:8443/v1/next/3",
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError("overflow must cancel without fetching another page")

    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_USER", "phlo_preview")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD", "secret")
    monkeypatch.setattr(preview, "resolve_trino_url", lambda: "https://trino:8443")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        with pytest.raises(preview.PreviewLimitExceeded):
            await preview.execute_preview(
                "SELECT value",
                catalog="iceberg_prod",
                disconnected=lambda: _not_disconnected(),
                limit=1,
            )

    assert calls == [("POST", "/v1/statement"), ("DELETE", "/v1/next/3")]


@pytest.mark.anyio
async def test_preview_cancels_query_when_client_disconnects(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    disconnect_checks = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"columns": [], "nextUri": "https://trino:8443/v1/next/4"},
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError("disconnect must cancel the active query")

    async def disconnected() -> bool:
        nonlocal disconnect_checks
        disconnect_checks += 1
        return disconnect_checks > 1

    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_USER", "phlo_preview")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD", "secret")
    monkeypatch.setattr(preview, "resolve_trino_url", lambda: "https://trino:8443")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        with pytest.raises(asyncio.CancelledError):
            await preview.execute_preview(
                "SELECT 1",
                catalog="iceberg_prod",
                disconnected=disconnected,
                limit=1,
            )

    assert calls == [("POST", "/v1/statement"), ("DELETE", "/v1/next/4")]


@pytest.mark.anyio
async def test_preview_rejects_unencrypted_trino_connection(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_USER", "phlo_preview")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD", "secret")
    monkeypatch.setattr(preview, "resolve_trino_url", lambda: "http://trino:8080")

    with pytest.raises(preview.PreviewUnavailable, match="requires an HTTPS Trino connection"):
        await preview.execute_preview(
            "SELECT 1",
            catalog="iceberg_prod",
            disconnected=lambda: _not_disconnected(),
            limit=1,
        )


async def _not_disconnected() -> bool:
    return False
