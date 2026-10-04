"""Bounds and ref selection for the v1 Trino preview."""

from __future__ import annotations

import asyncio
import base64
import json
from importlib.resources import files

import httpx
import pytest

from phlo_api.observatory_api import http_client
from phlo_api.observatory_api import v1_preview as preview


@pytest.fixture(autouse=True)
def preview_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "prod": {"catalog": "iceberg_prod", "nessie_ref": "main"},
                "staging": {"catalog": "iceberg_staging", "nessie_ref": "dev"},
            }
        ),
    )
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD", "secret")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING", "staging-secret")


def test_catalog_requires_exact_environment_ref_mapping(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", raising=False)
    with pytest.raises(preview.PreviewUnavailable):
        preview.preview_catalog("prod", "release_a")

    monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "prod": {"catalog": "warehouse_blue", "nessie_ref": "release_a"},
                "staging": {"catalog": "warehouse_green", "nessie_ref": "review_b"},
            }
        ),
    )

    assert preview.preview_catalog("prod", "release_a") == "warehouse_blue"
    assert preview.preview_catalog("staging", "review_b") == "warehouse_green"
    with pytest.raises(preview.PreviewUnavailable):
        preview.preview_catalog("prod", "review_b")

    access = json.loads(
        (files("phlo_trino") / "preview" / "access-control.json").read_text(encoding="utf-8")
    )
    groups = json.loads(
        (files("phlo_trino") / "preview" / "resource-groups.json").read_text(encoding="utf-8")
    )
    assert access["catalogs"][0] == {
        "user": "phlo_api_preview_prod",
        "catalog": "iceberg_preview_prod",
        "allow": "read-only",
    }
    assert access["catalogs"][1] == {
        "user": "phlo_api_preview_staging",
        "catalog": "iceberg_preview_staging",
        "allow": "read-only",
    }
    assert access["catalogs"][-1]["allow"] == "all"
    assert groups["selectors"][0]["user"] == "phlo_api_preview_(prod|staging)"
    assert groups["selectors"][-1]["group"] == "global.default"

    monkeypatch.delenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD")
    with pytest.raises(preview.PreviewUnavailable):
        preview.preview_catalog("prod", "release_a")


def test_quote_table_quotes_every_asset_key_segment() -> None:
    assert preview.quote_table("iceberg_prod", 'warehouse/order"s') == (
        '"iceberg_prod"."warehouse"."order""s"'
    )
    assert preview.quote_table("iceberg_prod", 'warehouse/orders"; DROP TABLE x') == (
        '"iceberg_prod"."warehouse"."orders""; DROP TABLE x"'
    )


def test_preview_uses_an_environment_specific_trino_identity() -> None:
    prod = preview._preview_auth_headers("iceberg_prod")
    staging = preview._preview_auth_headers("iceberg_staging")

    assert prod["X-Trino-User"] == "phlo_api_preview_prod"
    assert prod["Authorization"] == "Basic " + base64.b64encode(
        b"phlo_api_preview_prod:secret"
    ).decode("ascii")
    assert staging["X-Trino-User"] == "phlo_api_preview_staging"
    assert staging["Authorization"] == "Basic " + base64.b64encode(
        b"phlo_api_preview_staging:staging-secret"
    ).decode("ascii")
    with pytest.raises(preview.PreviewUnavailable, match="no environment-scoped server identity"):
        preview._preview_auth_headers("iceberg_other")


@pytest.mark.anyio
async def test_preview_cancels_latest_continuation_after_row_limit(monkeypatch) -> None:
    requests: list[tuple[str, str, str, str]] = []
    statement = 'SELECT * FROM "iceberg_prod"."warehouse"."orders" LIMIT 3'

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
            assert request.content == statement.encode("utf-8")
            return httpx.Response(
                200,
                json={
                    "columns": [
                        {
                            "name": "id",
                            "type": "bigint",
                            "typeSignature": {"rawType": "bigint", "arguments": []},
                        }
                    ],
                    "data": [[1], [2], [3]],
                    "nextUri": "https://trino:8443/v1/next/1",
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError("preview must not fetch another result page after limit + 1")

    monkeypatch.setattr(preview, "resolve_trino_url", lambda: "https://trino:8443")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        result = await preview.execute_preview(
            statement,
            catalog="iceberg_prod",
            disconnected=lambda: _not_disconnected(),
            limit=2,
        )

    assert result["columns"] == [{"name": "id", "type": "bigint"}]
    assert result["rows"] == [{"id": 1}, {"id": 2}]
    assert result["has_more"] is True
    assert [item[:2] for item in requests] == [
        ("POST", "/v1/statement"),
        ("DELETE", "/v1/next/1"),
    ]
    assert requests[0][2] == ""
    expected_auth = "Basic " + base64.b64encode(b"phlo_api_preview_prod:secret").decode("ascii")
    assert all(item[3] == expected_auth for item in requests)


@pytest.mark.anyio
async def test_query_rejection_reports_a_safe_reason_not_engine_unavailability(monkeypatch) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": {
                    "errorName": "NOT_SUPPORTED",
                    "message": "private SQL and connector details must not be returned",
                }
            },
        )

    monkeypatch.setattr(preview, "resolve_trino_url", lambda: "https://trino:8443")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        with pytest.raises(
            preview.PreviewQueryRejected,
            match="This table or operation is not supported by the query engine.",
        ) as rejected:
            await preview.execute_preview(
                "SELECT * FROM information_schema.applicable_roles",
                catalog="iceberg_prod",
                disconnected=lambda: _not_disconnected(),
                limit=10,
            )
        assert rejected.value.error_name == "NOT_SUPPORTED"


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
async def test_preview_cancels_when_workspace_requests_cancellation(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    cancel_requested = False

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"id": "trino-1", "columns": [], "nextUri": "https://trino:8443/v1/next/5"},
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError("cancelled query must not fetch another page")

    def on_progress(query_id: str | None, next_uri: str | None) -> None:
        nonlocal cancel_requested
        assert query_id == "trino-1"
        assert next_uri == "https://trino:8443/v1/next/5"
        cancel_requested = True

    monkeypatch.setattr(preview, "resolve_trino_url", lambda: "https://trino:8443")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(http_client, "_client", client)
        with pytest.raises(asyncio.CancelledError):
            await preview.execute_preview(
                "SELECT 1",
                catalog="iceberg_prod",
                disconnected=lambda: _not_disconnected(),
                limit=1,
                on_progress=on_progress,
                should_cancel=lambda: cancel_requested,
            )

    assert calls == [("POST", "/v1/statement"), ("DELETE", "/v1/next/5")]


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
