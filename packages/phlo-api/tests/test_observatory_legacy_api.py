"""Tests the Observatory legacy API proxy routes against stubbed HTTPX clients.

A scripted _AsyncClient stands in for outbound requests so loki, nessie,
trino, iceberg, search, and settings endpoints can be exercised without live
services; authentication comes from security_test_support.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from phlo_api.observatory_api import (
    extension_settings,
    extensions,
    iceberg,
    loki,
    nessie,
    search,
    settings,
)
from phlo_api.observatory_api.trino import QueryExecutionError
from security_test_support import authenticated_client


class _Response:
    def __init__(self, status_code: int, payload: object, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.reason_phrase = "OK" if status_code < 400 else "Error"

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _AsyncClient:
    calls: list[tuple[str, str, object | None]] = []
    responses: list[_Response] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_AsyncClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, params: object | None = None) -> _Response:
        self.calls.append(("GET", url, params))
        return self.responses.pop(0)

    async def post(
        self, url: str, params: object | None = None, json: object | None = None
    ) -> _Response:
        self.calls.append(("POST", url, json if json is not None else params))
        return self.responses.pop(0)

    async def delete(self, url: str, params: object | None = None) -> _Response:
        self.calls.append(("DELETE", url, params))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def clear_state() -> None:
    _AsyncClient.calls = []
    _AsyncClient.responses = []
    iceberg._cache.clear()


@pytest.mark.anyio
async def test_nessie_proxy_handlers_translate_rest_payloads(monkeypatch) -> None:
    monkeypatch.setattr(nessie.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(nessie, "resolve_nessie_url", lambda override=None: "http://nessie/api/v2")

    _AsyncClient.responses = [
        _Response(200, {"defaultBranch": "main"}),
        _Response(200, {"references": [{"type": "BRANCH", "name": "main", "hash": "h1"}]}),
        _Response(200, {"type": "BRANCH", "name": "feature", "hash": "h2"}),
        _Response(
            200,
            {
                "logEntries": [
                    {
                        "commitMeta": {
                            "hash": "c1",
                            "message": "commit",
                            "authors": ["dev"],
                        }
                    }
                ]
            },
        ),
        _Response(200, {"entries": [{"name": {"elements": ["silver", "orders"]}}]}),
        _Response(200, {"diffs": [{"key": "orders"}]}),
        _Response(200, {"type": "BRANCH", "name": "main", "hash": "h1"}),
        _Response(201, {"name": "feature", "hash": "h2"}),
        _Response(200, {}),
        _Response(200, {"type": "BRANCH", "name": "feature", "hash": "h2"}),
        _Response(200, {"type": "BRANCH", "name": "main", "hash": "h1"}),
        _Response(200, {"resultantTargetHash": "h3"}),
    ]

    assert (await nessie.check_connection()).default_branch == "main"
    assert (await nessie.get_branches())[0].name == "main"
    assert (await nessie.get_branch("feature")).hash == "h2"
    assert (await nessie.get_commits("feature"))[0].commit_meta.hash == "c1"
    assert (await nessie.get_contents("feature"))[0]["name"]["elements"] == ["silver", "orders"]
    assert (await nessie.compare_branches("feature", "main"))["diffs"][0]["key"] == "orders"
    assert (await nessie.create_branch("feature", "main")).hash == "h2"
    assert await nessie.delete_branch("feature", "h2") == {"success": True}
    assert (await nessie.merge_branch("feature", "main")).hash == "h3"


@pytest.mark.anyio
async def test_iceberg_table_handlers_return_schema_counts_and_metadata(monkeypatch) -> None:
    monkeypatch.setattr(iceberg, "resolve_default_catalog", lambda: "warehouse")
    monkeypatch.setattr(iceberg, "resolve_default_ref", lambda: "silver")
    monkeypatch.setattr(iceberg, "resolve_table_discovery_schemas", lambda *_args: ["silver"])

    async def fake_fetch_tables(*_args: object) -> list[iceberg.IcebergTable]:
        return [
            iceberg.IcebergTable(
                catalog="warehouse",
                schema_name="silver",
                name="orders",
                full_name="warehouse.silver.orders",
                layer="silver",
            )
        ]

    async def fake_fetch_schema(*_args: object) -> list[iceberg.TableColumn]:
        return [iceberg.TableColumn(name="id", type="varchar", nullable=True)]

    async def fake_execute(*_args: object) -> dict[str, object] | QueryExecutionError:
        return {"columns": [{"name": "cnt", "type": "bigint"}], "rows": [{"cnt": 3}]}

    monkeypatch.setattr(iceberg, "fetch_tables", fake_fetch_tables)
    monkeypatch.setattr(iceberg, "fetch_table_schema", fake_fetch_schema)
    monkeypatch.setattr(iceberg, "execute_trino_query", fake_execute)

    assert (await iceberg.get_tables())[0].name == "orders"
    assert (await iceberg.get_table_schema("orders"))[0].name == "id"
    assert await iceberg.get_table_row_count("orders") == 3
    metadata = await iceberg.get_table_metadata("orders")
    assert metadata.row_count == 3
    assert metadata.columns[0].name == "id"


@pytest.mark.anyio
async def test_search_index_aggregates_assets_tables_and_columns(monkeypatch) -> None:
    monkeypatch.setattr(search, "resolve_default_catalog", lambda: "warehouse")

    async def fake_assets(*_args: object) -> list[object]:
        return [
            SimpleNamespace(
                id="asset-1",
                key_path="silver/orders",
                group_name="silver",
                compute_kind="python",
            )
        ]

    async def fake_tables(*_args: object) -> list[object]:
        return [
            SimpleNamespace(
                catalog="warehouse",
                schema_name="silver",
                name="orders",
                full_name="warehouse.silver.orders",
                layer="silver",
            )
        ]

    async def fake_schema(*_args: object) -> list[object]:
        return [SimpleNamespace(name="id", type="varchar")]

    monkeypatch.setattr(search, "get_assets", fake_assets)
    monkeypatch.setattr(search, "get_tables", fake_tables)
    monkeypatch.setattr(search, "get_table_schema", fake_schema)

    result = await search.get_search_index(include_columns=True)
    assert result.assets[0].id == "asset-1"
    assert result.tables[0].name == "orders"
    assert result.columns[0].name == "id"


def test_extension_handlers_list_details_and_reject_missing_assets(monkeypatch) -> None:
    manifest = SimpleNamespace(
        compat=SimpleNamespace(observatory_min="0.0.0"),
        model_dump=lambda: {"name": "demo"},
    )
    plugin = SimpleNamespace(
        metadata=SimpleNamespace(name="demo"),
        get_manifest=lambda: manifest,
        asset_root=SimpleNamespace(joinpath=lambda *_parts: SimpleNamespace(is_file=lambda: False)),
    )
    monkeypatch.setattr(extensions, "discover_observatory_extensions", lambda: [plugin])
    monkeypatch.setattr(extensions, "_get_observatory_version", lambda: "1.0.0")
    extensions._cached_extensions = None

    assert extensions.list_extensions()["extensions"][0]["manifest"] == {"name": "demo"}
    assert extensions.get_extension("demo")["assets_base_path"].endswith("/demo/assets")
    with pytest.raises(HTTPException) as exc:
        extensions.get_extension_asset(
            "demo", "../secret", SimpleNamespace(add_task=lambda *_: None)
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_observatory_settings_handlers_use_settings_service(monkeypatch) -> None:
    record = SimpleNamespace(settings={"version": 1}, updated_at="2026-06-26T00:00:00")
    service = SimpleNamespace(
        get=lambda *_args: record,
        put=lambda *_args, **_kwargs: record,
    )
    monkeypatch.setattr(settings, "get_settings_service", lambda: service)
    monkeypatch.setattr(settings, "check_admin_read", lambda *_args: None)
    monkeypatch.setattr(settings, "check_admin_manage", lambda *_args: None)
    request = SimpleNamespace()

    assert (await settings.get_observatory_settings(request)).settings == {"version": 1}
    payload = settings.ObservatorySettingsPayload(settings={"version": 1})
    assert (
        await settings.put_observatory_settings(request, payload)
    ).updated_at == record.updated_at


@pytest.mark.anyio
async def test_extension_settings_handlers_use_manifest_scope_and_defaults(monkeypatch) -> None:
    manifest_settings = SimpleNamespace(
        scope="extension",
        settings_schema={"type": "object"},
        defaults={"theme": "dark"},
    )
    extension = SimpleNamespace(
        get_manifest=lambda: SimpleNamespace(settings=manifest_settings),
    )
    record = SimpleNamespace(settings={"theme": "light"}, updated_at="2026-06-26T00:00:00")
    service = SimpleNamespace(
        get=lambda *_args: None,
        put=lambda *_args, **_kwargs: record,
    )
    monkeypatch.setattr(extension_settings, "get_observatory_extension", lambda _name: extension)
    monkeypatch.setattr(extension_settings, "get_settings_service", lambda: service)

    assert (await extension_settings.get_extension_settings("demo")).settings == {"theme": "dark"}
    payload = extension_settings.ExtensionSettingsPayload(settings={"theme": "light"})
    assert (await extension_settings.put_extension_settings("demo", payload)).settings == {
        "theme": "light"
    }


_SSRF_LOKI_OVERRIDE = "http://169.254.169.254/latest/meta-data/#"

_LOKI_OVERRIDE_ROUTES: tuple[tuple[str, dict[str, str]], ...] = (
    ("/api/loki/connection", {}),
    (
        "/api/loki/query",
        {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T01:00:00Z"},
    ),
    ("/api/loki/runs/run-1", {}),
    ("/api/loki/runs/run-1/stream", {"timeout_seconds": "1", "interval_seconds": "0.25"}),
    ("/api/loki/assets/demo/orders", {}),
    ("/api/loki/labels", {}),
)


def test_loki_routes_reject_url_override_before_transport(monkeypatch) -> None:
    """Caller-supplied loki_url must 422 before DNS or outbound HTTP."""
    monkeypatch.setattr(loki.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(loki, "resolve_loki_url", lambda: "http://configured-loki:3100")

    def fail_dns(_host: str) -> str:
        raise AssertionError("DNS resolution must not run for request-controlled loki_url")

    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", fail_dns)

    client = authenticated_client("viewer")
    for path, params in _LOKI_OVERRIDE_ROUTES:
        _AsyncClient.calls = []
        response = client.get(path, params={**params, "loki_url": _SSRF_LOKI_OVERRIDE})
        assert response.status_code == 422, path
        detail = response.json()["detail"]
        assert detail["error"] == "loki_url_override_not_allowed"
        assert _AsyncClient.calls == [], path


def test_loki_labels_uses_configured_url_without_override(monkeypatch) -> None:
    """Normal path still talks to the operator-configured Loki endpoint."""
    monkeypatch.setattr(loki.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(loki, "resolve_loki_url", lambda: "http://configured-loki:3100")
    _AsyncClient.responses = [_Response(200, {"data": ["level", "job"]})]

    client = authenticated_client("viewer")
    response = client.get("/api/loki/labels")

    assert response.status_code == 200
    assert response.json() == {"labels": ["level", "job"]}
    assert _AsyncClient.calls == [("GET", "http://configured-loki:3100/loki/api/v1/labels", None)]


@pytest.mark.anyio
async def test_loki_internal_helpers_ignore_request_override_parameter() -> None:
    """resolve_loki_url no longer accepts a caller override argument."""
    with pytest.raises(TypeError):
        loki.resolve_loki_url("http://attacker.example")  # type: ignore[call-arg]

    with pytest.raises(HTTPException) as exc:
        loki.reject_request_loki_url(_SSRF_LOKI_OVERRIDE)
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "loki_url_override_not_allowed"


# ---------------------------------------------------------------------------
# Observatory settings storage unavailable → 503 (issue #626)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_observatory_settings_get_returns_503_when_storage_unavailable(monkeypatch) -> None:
    """GET /api/observatory/settings must return 503 when durable storage is unavailable."""
    from phlo.plugins.observatory_settings import StorageUnavailableError

    def _unavailable() -> None:
        raise StorageUnavailableError("Settings storage is unavailable")

    monkeypatch.setattr(settings, "get_settings_service", _unavailable)
    monkeypatch.setattr(settings, "check_admin_read", lambda *_args: None)
    request = SimpleNamespace()

    with pytest.raises(HTTPException) as exc:
        await settings.get_observatory_settings(request)
    assert exc.value.status_code == 503
    assert "unavailable" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_observatory_settings_put_returns_503_when_storage_unavailable(monkeypatch) -> None:
    """PUT /api/observatory/settings must return 503 when durable storage is unavailable."""
    from phlo.plugins.observatory_settings import StorageUnavailableError

    def _unavailable() -> None:
        raise StorageUnavailableError("Settings storage is unavailable")

    monkeypatch.setattr(settings, "get_settings_service", _unavailable)
    monkeypatch.setattr(settings, "check_admin_manage", lambda *_args: None)
    payload = settings.ObservatorySettingsPayload(settings={"version": 1})
    request = SimpleNamespace()

    with pytest.raises(HTTPException) as exc:
        await settings.put_observatory_settings(request, payload)
    assert exc.value.status_code == 503
    assert "unavailable" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_extension_settings_get_returns_503_when_storage_unavailable(monkeypatch) -> None:
    """GET extension settings must return 503 when durable storage is unavailable."""
    from phlo.plugins.observatory_settings import StorageUnavailableError

    def _unavailable() -> None:
        raise StorageUnavailableError("Settings storage is unavailable")

    monkeypatch.setattr(extension_settings, "get_settings_service", _unavailable)
    monkeypatch.setattr(extension_settings, "get_observatory_extension", lambda _name: None)
    # The extension lookup will return None → 404 before storage is called,
    # so mock a valid extension to reach the storage call.
    manifest_settings = SimpleNamespace(scope="extension", settings_schema=None, defaults=None)
    extension = SimpleNamespace(get_manifest=lambda: SimpleNamespace(settings=manifest_settings))
    monkeypatch.setattr(extension_settings, "get_observatory_extension", lambda _name: extension)

    with pytest.raises(HTTPException) as exc:
        await extension_settings.get_extension_settings("demo")
    assert exc.value.status_code == 503
    assert "unavailable" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_extension_settings_put_returns_503_when_storage_unavailable(monkeypatch) -> None:
    """PUT extension settings must return 503 when durable storage is unavailable."""
    from phlo.plugins.observatory_settings import StorageUnavailableError

    def _unavailable() -> None:
        raise StorageUnavailableError("Settings storage is unavailable")

    monkeypatch.setattr(extension_settings, "get_settings_service", _unavailable)
    manifest_settings = SimpleNamespace(
        scope="extension",
        settings_schema={"type": "object"},
        defaults=None,
    )
    extension = SimpleNamespace(get_manifest=lambda: SimpleNamespace(settings=manifest_settings))
    monkeypatch.setattr(extension_settings, "get_observatory_extension", lambda _name: extension)
    payload = extension_settings.ExtensionSettingsPayload(settings={"theme": "dark"})

    with pytest.raises(HTTPException) as exc:
        await extension_settings.put_extension_settings("demo", payload)
    assert exc.value.status_code == 503
    assert "unavailable" in exc.value.detail.lower()
