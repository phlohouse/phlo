"""Project-scoped Observatory extension manifests, assets, and settings reads.

Extensions are installed Python packages, so these resources describe the
API process's project rather than an environment and reject ``env``.
"""

from __future__ import annotations

from typing import Any, Literal

from anyio.to_thread import run_sync
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse

from phlo.plugins.observatory import ObservatoryExtensionManifest
from phlo_api.api.v1_pages import reject_unknown_query
from phlo_api.errors import (
    BackendUnavailableError,
    BadInputError,
    NotFoundError,
    PhloApiError,
)
from phlo_api.observatory_api import extension_settings, extensions
from phlo_api.v1_contract import WireModel

router = APIRouter(tags=["v1 extensions"])
_NO_QUERY: frozenset[str] = frozenset()


class ExtensionManifestView(WireModel):
    name: str
    manifest: ObservatoryExtensionManifest
    assets_base_path: str


class ExtensionManifestList(WireModel):
    scope: Literal["project"] = "project"
    items: list[ExtensionManifestView]


class ExtensionSettingsView(WireModel):
    name: str
    settings: dict[str, Any] | None
    updated_at: str | None


def _typed_error(exc: HTTPException) -> PhloApiError:
    message = str(exc.detail)
    if exc.status_code == 404:
        return NotFoundError(message)
    if exc.status_code == 400:
        return BadInputError(message)
    return PhloApiError(message)


@router.get("/extensions", response_model=ExtensionManifestList)
def v1_extensions(request: Request) -> ExtensionManifestList:
    """List compatible installed extensions and the asset path each one is served from."""
    reject_unknown_query(request, _NO_QUERY)
    return ExtensionManifestList(
        items=[
            ExtensionManifestView(
                name=plugin.metadata.name,
                manifest=plugin.get_manifest(),
                assets_base_path=extensions.extension_assets_base_path(plugin.metadata.name),
            )
            for plugin in sorted(extensions._load_extensions(), key=lambda p: p.metadata.name)
        ]
    )


@router.get(
    "/extensions/{name}/assets/{asset_path:path}",
    response_class=FileResponse,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def v1_extension_asset(
    request: Request, name: str, asset_path: str, background_tasks: BackgroundTasks
) -> FileResponse:
    """Serve one packaged asset file of a compatible installed extension."""
    reject_unknown_query(request, _NO_QUERY)
    try:
        return extensions.get_extension_asset(name, asset_path, background_tasks)
    except HTTPException as exc:
        raise _typed_error(exc) from exc


@router.get("/extensions/{name}/settings", response_model=ExtensionSettingsView)
async def v1_extension_settings(request: Request, name: str) -> ExtensionSettingsView:
    """Read stored extension settings, or the manifest defaults when none are stored."""
    reject_unknown_query(request, _NO_QUERY)
    try:
        stored = await run_sync(extension_settings._fetch_settings_sync, name)
    except HTTPException as exc:
        raise _typed_error(exc) from exc
    except RuntimeError as exc:
        raise BackendUnavailableError("Extension settings storage is unavailable.") from exc
    return ExtensionSettingsView(name=name, settings=stored.settings, updated_at=stored.updated_at)
