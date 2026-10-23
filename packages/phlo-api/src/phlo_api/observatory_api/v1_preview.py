"""Fail-closed, budgeted Trino execution for the environment-scoped v1 preview."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import ssl
from pathlib import Path
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any
from urllib.parse import urlsplit

import httpx

from phlo.config.env import resolve_project_root
from phlo.config.project import get_environment_targets
from phlo_api.settings import get_deployment_settings, get_process_settings

_MAX_ROWS = 100
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_TRINO_PAGE_BYTES = 8_388_608
_ADMISSION = asyncio.Semaphore(2)


class PreviewUnavailable(RuntimeError):
    """Preview is not configured or Trino did not provide bounded evidence."""


class PreviewQueryRejected(PreviewUnavailable):
    """Trino rejected SQL; the message excludes raw SQL and engine details."""

    def __init__(self, message: str, *, error_name: str | None = None) -> None:
        super().__init__(message)
        self.error_name = error_name


class PreviewLimitExceeded(RuntimeError):
    """The preview exceeded its response or execution budget."""


def resolve_trino_url() -> str:
    """Use only the explicitly selected preview server, never ingestion discovery."""
    value = get_process_settings().phlo_v1_preview_trino_url or ""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PreviewUnavailable("Preview requires an HTTPS Trino connection.")
    return value.rstrip("/")


def _preview_tls() -> ssl.SSLContext:
    try:
        return ssl.create_default_context(
            cafile=get_process_settings().phlo_v1_preview_trino_ca_file
        )
    except (OSError, ValueError) as exc:
        raise PreviewUnavailable("Preview trusted CA configuration is invalid.") from exc


@asynccontextmanager
async def backend_client() -> AsyncIterator[httpx.AsyncClient]:
    # A separate client prevents preview CA/auth changes from altering ingestion reads.
    # Recreate trust for each query so a mounted CA bundle can be renewed without restart.
    async with httpx.AsyncClient(verify=_preview_tls(), follow_redirects=False) as client:
        yield client


def _preview_password(env: str) -> str:
    settings = get_process_settings()
    try:
        if settings.phlo_v1_preview_trino_password_files is not None:
            mapping = json.loads(settings.phlo_v1_preview_trino_password_files)
            targets = get_environment_targets()
            opted = {key for key, target in targets.items() if target.preview is not None}
            if not isinstance(mapping, dict) or set(mapping) != (opted or set(targets)):
                raise ValueError
            path = Path(mapping[env])
            if path.stat().st_mode & 0o077:
                raise ValueError
            password = path.read_text(encoding="utf-8").rstrip("\n")
        elif (
            target := get_environment_targets().get(env)
        ) is not None and target.preview is not None:
            path = resolve_project_root() / target.preview.password_file
            if path.stat().st_mode & 0o077:
                raise ValueError
            password = path.read_text(encoding="utf-8").rstrip("\n")
        else:
            # Preserve installed prod/staging secrets, but new IDs use the explicit file map.
            password = {
                "prod": settings.phlo_v1_preview_trino_password_prod,
                "staging": settings.phlo_v1_preview_trino_password_staging,
            }.get(env)
        if not password or any(char in password for char in "\r\n\x00"):
            raise ValueError
        return password
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise PreviewUnavailable(
            "Environment-scoped private preview credential is unavailable."
        ) from exc


def _preview_configuration() -> dict[str, dict[str, str]]:
    try:
        if not get_deployment_settings().preview_server_limits_configured:
            raise ValueError
        targets = get_environment_targets()
        opted = {env: target for env, target in targets.items() if target.preview is not None}
        override = get_process_settings().phlo_v1_preview_catalogs
        mapping = (
            json.loads(override)
            if override
            else {
                env: {"catalog": target.preview.catalog, "nessie_ref": target.nessie_ref}
                for env, target in opted.items()
                if target.preview is not None
            }
        )
        if (
            not targets
            or not mapping
            or not isinstance(mapping, dict)
            or set(mapping) != set(opted or targets)
        ):
            raise ValueError
        catalogs: set[str] = set()
        validated: dict[str, dict[str, str]] = {}
        for env, target in mapping.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", env):
                raise ValueError
            if not isinstance(target, dict) or set(target) != {"catalog", "nessie_ref"}:
                raise ValueError
            name = target["catalog"]
            ref = target["nessie_ref"]
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name)
                or not isinstance(ref, str)
                or not ref
                or ref != targets[env].nessie_ref
            ):
                raise ValueError
            _preview_password(env)
            catalogs.add(name)
            validated[env] = {"catalog": name, "nessie_ref": ref}
        if len(catalogs) != len(mapping):
            raise ValueError
        return validated
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PreviewUnavailable("Preview catalog and server limits are not configured.") from exc


def preview_preflight(env: str | None = None) -> list[dict[str, str]]:
    """Collect local prerequisites, never assert deployed enforcement or reachability.

    Synchronous and read-only. Safe for readiness and CLI callers. An optional
    environment must be in the explicit configuration, never a discovered ref.
    """
    settings = get_process_settings()
    checks: list[dict[str, str]] = []

    def add(state: str, code: str, message: str, remediation: str) -> None:
        checks.append(
            {"state": state, "reason_code": code, "message": message, "remediation": remediation}
        )

    try:
        targets = get_environment_targets()
    except ValueError:
        add(
            "unavailable",
            "configuration_invalid",
            "Environment configuration is invalid.",
            "Review phlo.yaml environments and default_environment; never infer a target from a branch.",
        )
        return checks
    opted = {key for key, target in targets.items() if target.preview is not None}
    if env is not None and env not in targets:
        add(
            "unavailable",
            "configuration_invalid",
            "Environment is not configured.",
            "Select an authorised environment from the configured target list.",
        )
        return checks
    if (
        not settings.phlo_v1_preview_catalogs
        and not settings.phlo_v1_preview_trino_url
        and not opted
    ) or (env is not None and env in targets and opted and env not in opted):
        add(
            "disabled",
            "intentionally_disabled",
            "Query preview is not configured.",
            "Provision a dedicated preview coordinator with phlo trino preview provision --config INPUT --output NEW_DIRECTORY; review the preview deployment guide.",
        )
        return checks
    for name, present in (
        ("preview catalog configuration", settings.phlo_v1_preview_catalogs or opted),
        ("PHLO_V1_PREVIEW_TRINO_URL", settings.phlo_v1_preview_trino_url),
        ("phlo.yaml environments", targets),
    ):
        if not present:
            add(
                "unavailable",
                "configuration_missing",
                f"{name} is missing.",
                f"Configure {name} for the dedicated preview deployment, not the ingestion coordinator.",
            )
    try:
        deployment = get_deployment_settings()
    except ValueError:
        add(
            "unavailable",
            "configuration_invalid",
            "Deployment process settings are invalid.",
            "Review process-only assertions and require WEB_CONCURRENCY=1.",
        )
        return checks
    for name, asserted in (
        ("single API replica", deployment.query_single_replica),
        ("single API process", deployment.query_single_process and deployment.web_concurrency == 1),
        ("preview server limits", deployment.preview_server_limits_configured),
    ):
        if not asserted:
            add(
                "unavailable",
                "enforcement_unverified",
                f"Deployment assertion is absent or contradicted: {name}.",
                f"Verify {name} in the actual runtime before asserting it. Active sessions are process-local and do not survive restart.",
            )
    for validate, label in (
        (resolve_trino_url, "HTTPS endpoint"),
        (_preview_tls, "trusted CA"),
        (_preview_configuration, "catalog/ref mapping and private credentials"),
    ):
        try:
            value = validate()
            if isinstance(value, dict) and env is not None and env not in value:
                raise PreviewUnavailable("Unconfigured environment")
        except (PreviewUnavailable, ValueError, TypeError):
            add(
                "unavailable",
                "configuration_invalid",
                f"Preview {label} is invalid or unavailable.",
                f"Review {label}; mappings must match configured environments and exact refs. Preserve certificate verification and private file permissions.",
            )
    add(
        "configured" if not checks else "unavailable",
        "enforcement_unverified",
        "Local configuration is not proof of deployed preview enforcement.",
        "Verify authenticated TLS, catalog reads, write/session denial, bounds, evidence, restart and secret/certificate renewal on the deployed coordinator.",
    )
    return checks


def preview_catalog(env: str, nessie_ref: str) -> str:
    """Resolve a catalog only from an operator map bound to the current target ref."""
    try:
        item = _preview_configuration()[env]
        catalog = item["catalog"]
        mapped_ref = item["nessie_ref"]
        if mapped_ref != nessie_ref:
            raise ValueError
        return catalog
    except (KeyError, ValueError) as exc:
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
    statement: str | None,
) -> dict[str, Any]:
    content = bytearray()
    async with client.stream(
        method, url, headers=headers, timeout=timeout, content=statement
    ) as response:
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
    content: str | None = None,
) -> dict[str, Any]:
    query = asyncio.create_task(_read_result(client, method, url, headers, timeout, content))
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
        **_preview_auth_headers(catalog),
        "X-Trino-Catalog": catalog,
    }


def _preview_auth_headers(catalog: str) -> dict[str, str]:
    identity = next(
        (
            (env, f"phlo_api_preview_{env}")
            for env, item in _preview_configuration().items()
            if item["catalog"] == catalog
        ),
        None,
    )
    if identity is None:
        raise PreviewUnavailable("Preview catalog has no environment-scoped server identity.")
    env, user = identity
    password = _preview_password(env)
    credentials = f"{user}:{password}".encode("utf-8")
    return {
        "X-Trino-User": user,
        "Authorization": f"Basic {base64.b64encode(credentials).decode('ascii')}",
    }


async def _collect_pages(
    client: httpx.AsyncClient,
    sql: str,
    base_url: str,
    headers: dict[str, str],
    disconnected: Callable[[], Awaitable[bool]],
    limit: int,
    on_progress: Callable[[str | None, str | None], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[list[dict[str, Any]], list[list[Any]], str | None]:
    timeout = httpx.Timeout(22)
    active_uri: str | None = None
    try:
        result = await _read_or_disconnect(
            client, "POST", f"{base_url}/v1/statement", headers, timeout, disconnected, sql
        )
        columns: list[dict[str, Any]] = []
        rows: list[list[Any]] = []
        while True:
            if result.get("error"):
                error = result["error"]
                error_name = error.get("errorName") if isinstance(error, dict) else None
                reasons = {
                    "NOT_SUPPORTED": "This table or operation is not supported by the query engine.",
                    "SYNTAX_ERROR": "The query contains invalid SQL syntax.",
                    "COLUMN_NOT_FOUND": "A query column does not exist or is not accessible.",
                    "TABLE_NOT_FOUND": "A query table does not exist or is not accessible.",
                    "PERMISSION_DENIED": "The query engine denied access to this table or operation.",
                }
                raise PreviewQueryRejected(
                    reasons.get(
                        error_name if isinstance(error_name, str) else "",
                        "The query engine rejected the query.",
                    ),
                    error_name=error_name if isinstance(error_name, str) else None,
                )
            if not columns and isinstance(result.get("columns"), list):
                columns = result["columns"]
            data = result.get("data") or []
            if not isinstance(data, list):
                raise PreviewUnavailable("Trino returned invalid preview rows.")
            rows.extend(data)
            next_uri = result.get("nextUri")
            active_uri = _assert_next_uri(next_uri, base_url) if isinstance(next_uri, str) else None
            query_id = result.get("id")
            if on_progress is not None:
                on_progress(query_id if isinstance(query_id, str) else None, active_uri)
            if should_cancel is not None and should_cancel():
                raise asyncio.CancelledError
            if await disconnected():
                raise asyncio.CancelledError
            if len(rows) > limit or active_uri is None:
                return columns, rows, active_uri
            result = await _read_or_disconnect(
                client,
                "GET",
                active_uri,
                _preview_auth_headers(headers["X-Trino-Catalog"]),
                timeout,
                disconnected,
            )
    except BaseException:
        if active_uri is not None:
            await asyncio.shield(
                _cancel(client, active_uri, _preview_auth_headers(headers["X-Trino-Catalog"]))
            )
        raise


def _preview_payload(
    columns: list[dict[str, Any]], rows: list[list[Any]], limit: int
) -> dict[str, Any]:
    labels = [column.get("name") for column in columns]
    if any(not isinstance(label, str) for label in labels):
        raise PreviewUnavailable("Trino returned invalid preview columns.")
    if len(set(labels)) != len(labels):
        raise PreviewQueryRejected("Query results require unique column aliases.")
    items = [dict(zip(labels, row, strict=True)) for row in rows[:limit]]
    payload = {
        "columns": [{"name": column["name"], "type": column.get("type")} for column in columns],
        "rows": items,
        "has_more": len(rows) > limit,
    }
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
    on_progress: Callable[[str | None, str | None], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    base_url = resolve_trino_url().rstrip("/")
    if urlsplit(base_url).scheme != "https":
        raise PreviewUnavailable("Preview requires an HTTPS Trino connection.")
    columns, rows, active_uri = await _collect_pages(
        client,
        sql,
        base_url,
        _preview_headers(catalog),
        disconnected,
        limit,
        on_progress,
        should_cancel,
    )
    try:
        payload = _preview_payload(columns, rows, limit)
        if active_uri is not None:
            await _cancel(client, active_uri, _preview_auth_headers(catalog))
        return payload
    except BaseException:
        if active_uri is not None:
            await asyncio.shield(_cancel(client, active_uri, _preview_auth_headers(catalog)))
        raise


async def execute_preview(
    sql: str,
    *,
    catalog: str,
    disconnected: Callable[[], Awaitable[bool]],
    limit: int,
    on_progress: Callable[[str | None, str | None], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
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
            result = await _run_query(
                client, sql, catalog, disconnected, limit, on_progress, should_cancel
            )
            return result
    except httpx.TimeoutException as exc:
        raise PreviewLimitExceeded("Preview exceeded its execution time budget.") from exc
    except TimeoutError as exc:
        raise PreviewLimitExceeded("Preview exceeded its execution time budget.") from exc
    except httpx.HTTPError as exc:
        raise PreviewUnavailable("Trino preview service is unavailable.") from exc
    finally:
        _ADMISSION.release()
