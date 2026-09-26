"""Fail-closed, budgeted Trino execution for the environment-scoped v1 preview."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

import httpx

from phlo_api.observatory_api.http_client import backend_client
from phlo_api.observatory_api.trino import resolve_trino_url

_MAX_ROWS = 100
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_TRINO_PAGE_BYTES = 8_388_608
_ADMISSION = asyncio.Semaphore(2)


class PreviewUnavailable(RuntimeError):
    """Preview is not configured or Trino did not provide bounded evidence."""


class PreviewLimitExceeded(RuntimeError):
    """The preview exceeded its response or execution budget."""


def preview_catalog(env: str, nessie_ref: str) -> str:
    """Resolve a catalog only from an operator map bound to the current target ref."""
    try:
        if os.environ.get("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED") != "1":
            raise ValueError
        user = os.environ.get("PHLO_V1_PREVIEW_TRINO_USER")
        password = os.environ.get("PHLO_V1_PREVIEW_TRINO_PASSWORD")
        if not user or not password:
            raise ValueError
        mapping = json.loads(os.environ["PHLO_V1_PREVIEW_CATALOGS"])
        if not isinstance(mapping, dict) or set(mapping) != {"prod", "staging"}:
            raise ValueError
        catalogs: set[str] = set()
        for target in mapping.values():
            if not isinstance(target, dict) or set(target) != {"catalog", "nessie_ref"}:
                raise ValueError
            name = target["catalog"]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
                raise ValueError
            catalogs.add(name)
        if len(catalogs) != 2:
            raise ValueError
        item = mapping[env]
        catalog = item["catalog"]
        mapped_ref = item["nessie_ref"]
        if mapped_ref != nessie_ref or not isinstance(catalog, str):
            raise ValueError
        return catalog
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PreviewUnavailable("Preview catalog and server limits are not configured.") from exc


def quote_table(catalog: str, asset_id: str) -> str:
    """Render a two-part asset key as a quoted table identifier."""
    parts = asset_id.split("/")
    if len(parts) != 2 or any(not part or "\x00" in part for part in parts):
        raise ValueError("Asset key must contain exactly namespace and table.")
    return ".".join(f'"{part.replace(chr(34), chr(34) * 2)}"' for part in (catalog, *parts))


def _assert_next_uri(uri: str, base_url: str) -> str:
    parsed, base = urlsplit(uri), urlsplit(base_url)
    if (
        parsed.scheme != base.scheme
        or parsed.netloc != base.netloc
        or not parsed.path.startswith("/v1/")
    ):
        raise PreviewUnavailable("Trino returned an untrusted query continuation URL.")
    return uri


async def _read_result(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    timeout: httpx.Timeout,
) -> dict[str, Any]:
    content = bytearray()
    async with client.stream(method, url, headers=headers, timeout=timeout) as response:
        if response.status_code != 200:
            raise PreviewUnavailable("Trino preview query failed.")
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > _MAX_TRINO_PAGE_BYTES:
                raise PreviewLimitExceeded("Trino preview response exceeded the byte budget.")
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PreviewUnavailable("Trino returned an invalid preview response.") from exc
    if not isinstance(result, dict):
        raise PreviewUnavailable("Trino returned an invalid preview response.")
    return result


async def _cancel(client: httpx.AsyncClient, next_uri: str, headers: dict[str, str]) -> None:
    try:
        await client.delete(next_uri, headers=headers, timeout=2)
    except httpx.HTTPError:
        return


async def _read_or_disconnect(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    disconnected: Callable[[], Awaitable[bool]],
) -> dict[str, Any]:
    query = asyncio.create_task(_read_result(client, method, url, headers, timeout))
    try:
        while not query.done():
            if await disconnected():
                query.cancel()
                with suppress(asyncio.CancelledError):
                    await query
                raise asyncio.CancelledError
            await asyncio.wait({query}, timeout=0.1)
        return await query
    finally:
        if not query.done():
            query.cancel()
            with suppress(asyncio.CancelledError):
                await query


def _preview_headers(catalog: str) -> dict[str, str]:
    return {
        "Content-Type": "text/plain",
        **_preview_auth_headers(),
        "X-Trino-Catalog": catalog,
    }


def _preview_auth_headers() -> dict[str, str]:
    credentials = (
        f"{os.environ['PHLO_V1_PREVIEW_TRINO_USER']}:{os.environ['PHLO_V1_PREVIEW_TRINO_PASSWORD']}"
    ).encode("utf-8")
    return {
        "X-Trino-User": os.environ["PHLO_V1_PREVIEW_TRINO_USER"],
        "Authorization": f"Basic {base64.b64encode(credentials).decode('ascii')}",
    }


async def _collect_pages(
    client: httpx.AsyncClient,
    sql: str,
    base_url: str,
    headers: dict[str, str],
    disconnected: Callable[[], Awaitable[bool]],
    limit: int,
) -> tuple[list[dict[str, Any]], list[list[Any]], str | None]:
    timeout = httpx.Timeout(22)
    active_uri: str | None = None
    try:
        result = await _read_or_disconnect(
            client, "POST", f"{base_url}/v1/statement", headers, timeout, disconnected
        )
        columns: list[dict[str, Any]] = []
        rows: list[list[Any]] = []
        while True:
            if result.get("error"):
                raise PreviewUnavailable("Trino could not complete the preview query.")
            if not columns and isinstance(result.get("columns"), list):
                columns = result["columns"]
            data = result.get("data") or []
            if not isinstance(data, list):
                raise PreviewUnavailable("Trino returned invalid preview rows.")
            rows.extend(data)
            next_uri = result.get("nextUri")
            active_uri = _assert_next_uri(next_uri, base_url) if isinstance(next_uri, str) else None
            if await disconnected():
                raise asyncio.CancelledError
            if len(rows) > limit or active_uri is None:
                return columns, rows, active_uri
            result = await _read_or_disconnect(
                client, "GET", active_uri, _preview_auth_headers(), timeout, disconnected
            )
    except BaseException:
        if active_uri is not None:
            await asyncio.shield(_cancel(client, active_uri, _preview_auth_headers()))
        raise


def _preview_payload(
    columns: list[dict[str, Any]], rows: list[list[Any]], limit: int
) -> dict[str, Any]:
    labels = [column.get("name") for column in columns]
    if any(not isinstance(label, str) for label in labels):
        raise PreviewUnavailable("Trino returned invalid preview columns.")
    items = [dict(zip(labels, row, strict=True)) for row in rows[:limit]]
    payload = {"columns": columns, "rows": items, "has_more": len(rows) > limit}
    encoded = json.dumps(payload, separators=(",", ":"), default=str).encode()
    if len(encoded) > _MAX_RESPONSE_BYTES:
        raise PreviewLimitExceeded("Preview output exceeded the byte budget.")
    return payload


async def _run_query(
    client: httpx.AsyncClient,
    sql: str,
    catalog: str,
    disconnected: Callable[[], Awaitable[bool]],
    limit: int,
) -> dict[str, Any]:
    base_url = resolve_trino_url().rstrip("/")
    if urlsplit(base_url).scheme != "https":
        raise PreviewUnavailable("Preview requires an HTTPS Trino connection.")
    columns, rows, active_uri = await _collect_pages(
        client, sql, base_url, _preview_headers(catalog), disconnected, limit
    )
    try:
        payload = _preview_payload(columns, rows, limit)
        if active_uri is not None:
            await _cancel(client, active_uri, _preview_auth_headers())
        return payload
    except BaseException:
        if active_uri is not None:
            await asyncio.shield(_cancel(client, active_uri, _preview_auth_headers()))
        raise


async def execute_preview(
    sql: str,
    *,
    catalog: str,
    disconnected: Callable[[], Awaitable[bool]],
    limit: int,
) -> dict[str, Any]:
    """Run a mapped read-only preview under cluster, process, time and output budgets."""
    if not 1 <= limit <= _MAX_ROWS:
        raise ValueError("Preview limit is outside the supported range.")
    try:
        await asyncio.wait_for(_ADMISSION.acquire(), timeout=0.25)
    except TimeoutError as exc:
        raise PreviewUnavailable("Preview capacity is busy; retry later.") from exc
    try:
        async with backend_client() as client, asyncio.timeout(22):
            result = await _run_query(client, sql, catalog, disconnected, limit)
            return result
    except httpx.TimeoutException as exc:
        raise PreviewLimitExceeded("Preview exceeded its execution time budget.") from exc
    except TimeoutError as exc:
        raise PreviewLimitExceeded("Preview exceeded its execution time budget.") from exc
    except httpx.HTTPError as exc:
        raise PreviewUnavailable("Trino preview service is unavailable.") from exc
    finally:
        _ADMISSION.release()
