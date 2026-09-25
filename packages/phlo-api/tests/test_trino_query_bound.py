"""Pin Trino page polling and result bounds against a scripted HTTP backend."""

from __future__ import annotations

import httpx
import pytest

from phlo_api.observatory_api import http_client, trino


@pytest.mark.anyio
async def test_query_stops_after_max_rows_plus_one(monkeypatch) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/statement":
            return httpx.Response(
                200,
                json={
                    "columns": [{"name": "id", "type": "bigint"}],
                    "data": [[1], [2]],
                    "nextUri": "http://internal:8080/v1/next/1",
                },
            )
        if request.url.path == "/v1/next/1":
            return httpx.Response(
                200,
                json={"data": [[3], [4]], "nextUri": "http://internal:8080/v1/next/2"},
            )
        raise AssertionError("Trino must not poll after row four")

    monkeypatch.setattr(trino, "resolve_trino_url", lambda: "http://trino:8080")
    monkeypatch.setattr(trino, "resolve_default_catalog", lambda: "warehouse")
    monkeypatch.setattr(trino, "resolve_default_ref", lambda: "main")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(trino.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        result = await trino.execute_trino_query("SELECT id FROM events", max_rows=3)

    assert result == {
        "columns": ["id"],
        "column_types": ["bigint"],
        "rows": [{"id": 1}, {"id": 2}, {"id": 3}],
        "has_more": True,
    }
    assert calls == ["/v1/statement", "/v1/next/1"]


@pytest.mark.anyio
async def test_query_reads_initial_page_and_rejects_invalid_bounds(monkeypatch) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"columns": [{"name": "value", "type": "varchar"}], "data": [["first"]]},
        )

    monkeypatch.setattr(trino, "resolve_trino_url", lambda: "http://trino:8080")
    monkeypatch.setattr(trino, "resolve_default_catalog", lambda: "warehouse")
    monkeypatch.setattr(trino, "resolve_default_ref", lambda: "main")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        result = await trino.execute_trino_query("SELECT 'first'", max_rows=1)
        invalid = await trino.execute_trino_query("SELECT 1", max_rows=0)

    assert result == {
        "columns": ["value"],
        "column_types": ["varchar"],
        "rows": [{"value": "first"}],
        "has_more": False,
    }
    assert isinstance(invalid, trino.QueryExecutionError)
    assert invalid.kind == "validation"
    assert calls == ["/v1/statement"]


@pytest.mark.anyio
async def test_lifespan_reuses_one_http_client() -> None:
    async with http_client.lifespan_client():
        async with http_client.backend_client() as first:
            async with http_client.backend_client() as second:
                assert first is second
                assert not first.is_closed
    assert first.is_closed


@pytest.mark.anyio
async def test_standalone_backend_client_closes_after_use() -> None:
    async with http_client.backend_client() as client:
        assert not client.is_closed
    assert client.is_closed
