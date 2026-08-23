"""Regulated surface adapter for phlo-api.

This adapter registers phlo-api as a regulated surface with the capability registry.
"""

from __future__ import annotations

from typing import Any

from phlo.capabilities import (
    RegulatedSurfaceSpec,
    register_capability,
)
from phlo.security.adapters import SurfaceOperation

SURFACE_NAME = "phlo-api"
SURFACE_FRAMEWORK = "fastapi"


class PhloAPIRegulatedSurfaceAdapter:
    """Regulated surface adapter for phlo-api.

    Declares all regulated operations and registers with the capability registry.
    """

    surface_name: str = SURFACE_NAME
    framework_type: str = SURFACE_FRAMEWORK
    _installed_runtime: Any | None = None

    def list_operations(self) -> list[SurfaceOperation]:
        """Return all regulated operations exposed by phlo-api."""
        from phlo_api.security_manifest import HTTP_ROUTE_DECLARATIONS, validate_manifest

        # Before install() there is no runtime to validate against, so fall
        # back to the static declarations; validated entries additionally carry
        # the concrete method/path pairs observed on the live app.
        if self._installed_runtime is not None:
            specs = validate_manifest(self._installed_runtime)
        else:
            specs = HTTP_ROUTE_DECLARATIONS
        return [
            SurfaceOperation(
                action=spec.action,
                resource_type=spec.resource_type,
                operation_name=f"http.{spec.operation_name}",
                resource_id_strategy="path_query_body",
                framework_metadata={
                    "surface": SURFACE_NAME,
                    "methods": spec.methods,
                    "path": spec.path,
                    "resource_keys": spec.resource_keys,
                    "public": spec.public,
                },
            )
            for spec in specs
        ]

    def is_active(self, runtime: Any) -> bool:
        """Return True if runtime matches the FastAPI app this adapter is installed on."""
        if self._installed_runtime is None:
            return False
        return runtime is self._installed_runtime

    def install(self, runtime: Any) -> None:
        """Register phlo-api as a regulated surface and track the runtime."""
        if runtime is None:
            raise ValueError("phlo-api adapter requires a non-None FastAPI app as runtime")
        from phlo_api.security_manifest import validate_manifest

        validate_manifest(runtime)
        self._installed_runtime = runtime
        spec = RegulatedSurfaceSpec(
            name=SURFACE_NAME,
            provider=self,
            metadata={"framework_runtime": "fastapi"},
        )
        register_capability("regulated_surface", spec)


_adapter: PhloAPIRegulatedSurfaceAdapter | None = None


def get_adapter() -> PhloAPIRegulatedSurfaceAdapter:
    """Return the singleton adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = PhloAPIRegulatedSurfaceAdapter()
    return _adapter
