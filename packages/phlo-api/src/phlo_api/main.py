"""
Phlo API - Backend service exposing phlo internals to Observatory.

This FastAPI service provides endpoints for Observatory to:
- List installed plugins and services
- Get service status and configuration
- Read phlo.yaml config
- Query data contracts and lineage
- Access maintenance and observability data

The API is organized into multiple router modules under `api/` and
`observatory_api/` directories, auto-discovered and registered at startup.

Environment Variables:
    HOST: API server bind address (default: "0.0.0.0").
    PORT: API server port (default: 4000).
    PHLO_PROJECT_PATH: Path to the phlo project directory (default: "/app/project").

    API server entrypoint: launched via uvicorn as phlo_api.main:app rather than imported directly.
    Wires phlo.capabilities, plugin discovery, registry client, and run evidence into one FastAPI app.
"""

from __future__ import annotations

import importlib
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from phlo.logging import bind_context, clear_context, get_logger
from phlo.capabilities.discovery import discover_capabilities
from phlo_api.regulated_surface_adapter import get_adapter
from phlo_api.security_manifest import install_manifest_enforcement
from phlo.security.validation import require_regulated_validation

logger = get_logger(__name__, service="phlo-api")


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """Own the durable evidence resource for the API process lifetime."""
    from phlo.run_evidence.store import default_run_evidence_store

    store = default_run_evidence_store()
    store.initialize()
    application.state.run_evidence_store = store
    try:
        yield
    finally:
        store.close()
        del application.state.run_evidence_store


app = FastAPI(
    title="Phlo API",
    description="Backend API for Phlo Observatory",
    version="0.1.0",
    lifespan=_lifespan,
)

# Allow CORS for Observatory
_cors_origins_raw = os.environ.get(
    "PHLO_API_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001,"
    "http://localhost:3005,http://127.0.0.1:3005,"
    "http://localhost:4000,http://127.0.0.1:4000",
)
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=_cors_origins != ["*"],  # Browsers reject credentials with a "*" origin.
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auto-discover and register API routers
_ROUTERS = [
    ("phlo_api.api.authoring", "/api/authoring"),
    ("phlo_api.api.continuity", "/api/continuity"),
    ("phlo_api.api.maintenance", "/api/maintenance"),
    ("phlo_api.api.observability", "/api/observability"),
    ("phlo_api.observatory_api.loki", "/api/loki"),
    ("phlo_api.observatory_api.observatory", "/api/observatory"),
    ("phlo_api.observatory_api.package_install", "/api/observatory"),
    ("phlo_api.observatory_api.run_report", "/api/observatory"),
]

_OBSERVATORY_ROUTERS_NO_PREFIX: list[str] = []


def _register_observatory_routers() -> None:
    """Register Observatory API routers if available.

    Auto-discovers and registers routers from the _ROUTERS and
    _OBSERVATORY_ROUTERS_NO_PREFIX lists. Each module is expected to
    expose a FastAPI `router` object.

    Routers with a prefix are mounted at that path; routers without
    a prefix are mounted at the root.

    Import errors are logged as debug messages to allow graceful
    degradation when optional dependencies are not installed.

    """
    all_routers = [
        *_ROUTERS,
        *((f"phlo_api.observatory_api.{name}", None) for name in _OBSERVATORY_ROUTERS_NO_PREFIX),
    ]

    for module_name, prefix in all_routers:
        try:
            module = importlib.import_module(module_name)
            router = getattr(module, "router", None)
            if router:
                if prefix is not None:
                    app.include_router(router, prefix=prefix)
                else:
                    app.include_router(router)
        except ImportError as e:
            logger.debug("Failed to import API router %s: %s", module_name, e)


_register_observatory_routers()


@app.middleware("http")
async def bind_request_logging_context(request: Request, call_next: Any) -> Any:
    """Bind per-request correlation fields for structured logging.

    Reuses an inbound x-request-id (generating one when absent) and honors
    traceparent/x-trace-id so logs correlate across the request lifecycle.
    """
    request_id = request.headers.get("x-request-id") or str(uuid4())
    trace_id = request.headers.get("traceparent") or request.headers.get("x-trace-id")
    request.state.request_id = request_id
    bind_context(
        request_id=request_id, trace_id=trace_id, path=request.url.path, method=request.method
    )
    try:
        response = await call_next(request)
        response.headers.setdefault("x-request-id", request_id)
        return response
    finally:
        with suppress(Exception):
            clear_context()


def get_project_path() -> Path:
    """Get the phlo project path from environment or default.

    The project path locates configuration files like phlo.yaml and
    contract artifacts.

    Environment Variables:
        PHLO_PROJECT_PATH: Overrides the default path.
    """
    project_path = os.environ.get("PHLO_PROJECT_PATH", "/app/project")
    return Path(project_path)


def load_phlo_config() -> dict[str, Any]:
    """Load phlo.yaml configuration from the project directory.

    Returns a fallback configuration when the file is missing; raises a 500
    when the file exists but cannot be read or is not a mapping.
    """
    config_path = get_project_path() / "phlo.yaml"
    logger.info("phlo_config_load_started", config_path=str(config_path))
    if not config_path.exists():
        fallback_config = {"name": "unknown", "description": ""}
        logger.warning(
            "phlo_config_load_fallback",
            config_path=str(config_path),
            reason="missing_file",
            fallback_name=fallback_config["name"],
        )
        return fallback_config

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("phlo_config_load_failed", config_path=str(config_path), error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to read phlo.yaml") from exc
    if not isinstance(config, dict):
        logger.error("phlo_config_load_failed", config_path=str(config_path), error="not_mapping")
        raise HTTPException(status_code=500, detail="phlo.yaml must contain a mapping")

    logger.info(
        "phlo_config_load_succeeded",
        config_path=str(config_path),
        key_count=len(config),
        has_name=bool(config.get("name")),
    )
    return config


def _default_table_store_name() -> str | None:
    """Resolve the default table store capability name (None when unavailable)."""
    try:
        from phlo.capabilities import resolve_capability

        resolution = resolve_capability("table_store")
        return resolution.name if resolution is not None else None
    except Exception:
        return None


def _default_schema_migrator_name() -> str | None:
    """Resolve the default schema migrator capability name (None when unavailable)."""
    try:
        from phlo.capabilities import resolve_capability

        resolution = resolve_capability("schema_migrator")
        return resolution.name if resolution is not None else None
    except Exception:
        return None


def _list_api_backends() -> list[dict[str, Any]]:
    """List capability-backed API backends with their current health status.

    A backend whose describe() or health_check() raises is reported unhealthy
    rather than failing the whole listing.
    """
    try:
        from phlo.capabilities import get_capability_registry
        from phlo.capabilities.discovery import discover_capabilities

        discover_capabilities()
        registry = get_capability_registry()
        specs = registry.list("api_backend")
    except Exception:
        return []

    backends: list[dict[str, Any]] = []
    for spec in specs:
        try:
            description = spec.provider.describe()
            healthy = spec.provider.health_check()
        except Exception:
            description = None
            healthy = False

        backends.append(
            {
                "name": spec.name,
                "healthy": healthy,
                "metadata": spec.metadata,
                "description": description,
            }
        )
    return backends


def _get_api_backend(name: str) -> dict[str, Any] | None:
    """Get one capability-backed API backend by name."""
    for backend in _list_api_backends():
        if backend["name"] == name:
            return backend
    return None


def _parse_quality_contract_tags(tags: dict[str, str]) -> dict[str, Any]:
    """Parse owner, consumers, and SLA from contract_* asset tags.

    An SLA payload that does not parse as a JSON object is treated as absent.
    """
    owner = tags.get("contract_owner")
    consumers_raw = tags.get("contract_consumers", "")
    consumers = [c for c in consumers_raw.split(",") if c]
    sla: dict[str, Any] | None = None

    raw_sla = tags.get("contract_sla")
    if raw_sla:
        try:
            parsed = json.loads(raw_sla)
            if isinstance(parsed, dict):
                sla = parsed
        except json.JSONDecodeError:
            sla = None

    return {"owner": owner, "consumers": consumers, "sla": sla}


def _load_contract_artifacts() -> dict[str, dict[str, Any]]:
    """Load contract artifacts from .phlo/contracts, keyed by table name.

    Malformed files are skipped silently; the API degrades to registry-only
    contract data.
    """
    contracts_dir = get_project_path() / ".phlo" / "contracts"
    if not contracts_dir.exists():
        return {}

    artifacts: dict[str, dict[str, Any]] = {}
    for contract_file in contracts_dir.glob("*.json"):
        try:
            payload = json.loads(contract_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        table_name = payload.get("table_name")
        if not isinstance(table_name, str) or not table_name:
            continue
        artifacts[table_name] = payload
    return artifacts


def _list_contracts() -> list[dict[str, Any]]:
    """Merge registry-derived table contracts with generated artifacts.

    Registry assets form the base contracts; artifact JSONs enrich matching
    tables with generated schemas and migration plans and contribute tables
    the registry no longer exposes. Output is sorted by table name.
    """
    contracts: dict[str, dict[str, Any]] = {}

    try:
        from phlo.capabilities import get_capability_registry
        from phlo.capabilities.discovery import discover_capabilities

        discover_capabilities()
        registry = get_capability_registry()
        assets = registry.list("asset")
        checks = registry.list("check")
    except Exception:
        assets = []
        checks = []

    table_store = _default_table_store_name()
    schema_migrator = _default_schema_migrator_name()

    quality_checks_by_asset: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        tags = dict(check.tags)
        quality_checks_by_asset.setdefault(check.asset_key, []).append(
            {
                "name": check.name,
                "severity": check.severity,
                "blocking": check.blocking,
                "description": check.description,
                "tags": tags,
                "owner": _parse_quality_contract_tags(tags).get("owner"),
                "consumers": _parse_quality_contract_tags(tags).get("consumers"),
                "sla": _parse_quality_contract_tags(tags).get("sla"),
            }
        )

    dlt_assets: dict[str, Any] = {}
    dbt_assets: list[Any] = []
    for asset in assets:
        kinds = asset.kinds or set()
        if "dlt" in kinds:
            dlt_assets[asset.key] = asset
        if "dbt" in kinds:
            dbt_assets.append(asset)

    for asset_key, asset in dlt_assets.items():
        table_name = asset.metadata.get("table_name")
        if not isinstance(table_name, str) or not table_name:
            continue

        # dlt assets without a schema qualifier are assumed to land in the raw schema.
        qualified_table = table_name if "." in table_name else f"raw.{table_name}"
        transform_refs: list[str] = []
        for dbt_asset in dbt_assets:
            deps = dbt_asset.deps or []
            if asset_key in deps or table_name in deps:
                transform_refs.append(dbt_asset.key)

        contracts[qualified_table] = {
            "table_name": qualified_table,
            "asset_key": asset_key,
            "table_store": table_store,
            "schema_migrator": schema_migrator,
            "contract_metadata": {
                "owner": asset.metadata.get("owner"),
                "consumers": asset.metadata.get("consumers", []),
                "sla": asset.metadata.get("sla"),
            },
            "quality_checks": quality_checks_by_asset.get(asset_key, []),
            "transform_refs": sorted(set(transform_refs)),
            "generated_at": None,
            "source": "registry",
        }

    artifacts = _load_contract_artifacts()
    for table_name, artifact in artifacts.items():
        if table_name in contracts:
            contracts[table_name]["generated_at"] = artifact.get("generated_at")
            contracts[table_name]["contract_version"] = artifact.get("contract_version")
            contracts[table_name]["normalized_schema"] = artifact.get("normalized_schema")
            contracts[table_name]["migration_plan"] = artifact.get("migration_plan")
            contracts[table_name]["source"] = "registry+artifact"
            continue

        contracts[table_name] = {
            "table_name": table_name,
            "asset_key": f"dlt_{table_name.split('.')[-1]}",
            "table_store": artifact.get("table_store"),
            "schema_migrator": artifact.get("schema_migrator"),
            "contract_metadata": artifact.get(
                "contract_metadata", {"owner": None, "consumers": [], "sla": None}
            ),
            "quality_checks": artifact.get("quality_checks", []),
            "transform_refs": artifact.get("transform_refs", []),
            "generated_at": artifact.get("generated_at"),
            "contract_version": artifact.get("contract_version"),
            "normalized_schema": artifact.get("normalized_schema"),
            "migration_plan": artifact.get("migration_plan"),
            "source": "artifact",
        }

    return [contracts[key] for key in sorted(contracts.keys())]


def _get_contract_by_table(table_name: str) -> dict[str, Any] | None:
    normalized = table_name.replace("__", ".")
    for contract in _list_contracts():
        candidate = contract.get("table_name")
        if not isinstance(candidate, str):
            continue
        if candidate == normalized or candidate == table_name:
            return contract
    return None


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    """Get the parsed phlo.yaml configuration."""
    logger.info("api_config_get_started")
    return load_phlo_config()


@app.get("/api/plugins")
def get_plugins() -> dict[str, list[str]]:
    """List installed plugins by type.

    Falls back to an empty per-type mapping when the plugin system cannot be
    imported.
    """
    logger.info("api_plugins_list_started")
    try:
        from phlo.plugins.discovery import list_plugins

        plugins = list_plugins()
        logger.info(
            "api_plugins_list_succeeded",
            plugin_type_count=len(plugins),
            plugin_count=sum(len(items) for items in plugins.values()),
        )
        return plugins
    except ImportError as exc:
        logger.warning("api_plugins_list_failed", error=str(exc))
        fallback_plugins = {
            "source_connector": [],
            "quality_check": [],
            "transformation": [],
            "service": [],
        }
        logger.warning(
            "api_plugins_list_fallback",
            plugin_type_count=len(fallback_plugins),
            plugin_count=0,
        )
        return fallback_plugins


@app.get("/api/plugins/{plugin_type}")
def get_plugins_by_type(plugin_type: str) -> list[str]:
    """List plugins of a specific type."""
    logger.info("api_plugins_type_list_started", plugin_type=plugin_type)
    try:
        from phlo.plugins.discovery import list_plugins

        all_plugins = list_plugins()
        if plugin_type not in all_plugins:
            logger.warning(
                "api_plugins_type_list_failed",
                plugin_type=plugin_type,
                reason="unknown_plugin_type",
            )
            raise HTTPException(status_code=404, detail=f"Unknown plugin type: {plugin_type}")
        plugins = all_plugins[plugin_type]
        logger.info(
            "api_plugins_type_list_succeeded",
            plugin_type=plugin_type,
            plugin_count=len(plugins),
        )
        return plugins
    except ImportError as exc:
        logger.warning("api_plugins_type_list_failed", plugin_type=plugin_type, error=str(exc))
        logger.warning(
            "api_plugins_type_list_fallback",
            plugin_type=plugin_type,
            plugin_count=0,
        )
        return []


@app.get("/api/plugins/{plugin_type}/{name}")
def get_plugin_info(plugin_type: str, name: str) -> dict[str, Any]:
    """Get detailed information about a specific plugin."""
    logger.info("api_plugin_get_started", plugin_type=plugin_type, plugin_name=name)
    try:
        from phlo.plugins.discovery import get_plugin_info as _get_plugin_info

        info = _get_plugin_info(plugin_type, name)
        if not info:
            logger.warning(
                "api_plugin_get_failed",
                plugin_type=plugin_type,
                plugin_name=name,
                reason="not_found",
            )
            raise HTTPException(status_code=404, detail=f"Plugin not found: {name}")
        logger.info(
            "api_plugin_get_succeeded",
            plugin_type=plugin_type,
            plugin_name=name,
            key_count=len(info),
        )
        return info
    except ImportError as e:
        logger.error(
            "api_plugin_get_failed", plugin_type=plugin_type, plugin_name=name, error=str(e)
        )
        raise HTTPException(status_code=500, detail="Plugin system not available") from e
    except ValueError as e:
        logger.warning(
            "api_plugin_get_failed", plugin_type=plugin_type, plugin_name=name, error=str(e)
        )
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/services")
def get_services() -> list[dict[str, Any]]:
    """List discovered services.

    Returns an empty list when service discovery is unavailable.
    """
    logger.info("api_services_list_started")
    try:
        from phlo.plugins.discovery import ServiceDiscovery

        discovery = ServiceDiscovery()
        services = discovery.discover()
        service_list = [
            {
                "name": s.name,
                "description": s.description,
                "category": s.category,
                "default": s.default,
                "profile": s.profile,
                "core": getattr(s, "core", False),
            }
            for s in services.values()
        ]
        logger.info("api_services_list_succeeded", service_count=len(service_list))
        return service_list
    except ImportError as exc:
        logger.warning("api_services_list_failed", error=str(exc))
        logger.warning("api_services_list_fallback", service_count=0)
        return []


@app.get("/api/services/{name}")
def get_service_info(name: str) -> dict[str, Any]:
    """Get detailed information about a specific service."""
    logger.info("api_service_get_started", service_name=name)
    try:
        from phlo.plugins.discovery import ServiceDiscovery

        discovery = ServiceDiscovery()
        service = discovery.get_service(name)
        if not service:
            logger.warning("api_service_get_failed", service_name=name, reason="not_found")
            raise HTTPException(status_code=404, detail=f"Service not found: {name}")
        service_payload = {
            "name": service.name,
            "description": service.description,
            "category": service.category,
            "default": service.default,
            "profile": service.profile,
            "depends_on": service.depends_on,
            "env_vars": service.env_vars,
            "core": getattr(service, "core", False),
        }
        depends_on = service_payload["depends_on"]
        env_vars = service_payload["env_vars"]
        logger.info(
            "api_service_get_succeeded",
            service_name=name,
            depends_on_count=len(depends_on) if isinstance(depends_on, list) else 0,
            env_var_count=len(env_vars) if isinstance(env_vars, dict) else 0,
        )
        return service_payload
    except ImportError as e:
        logger.error("api_service_get_failed", service_name=name, error=str(e))
        raise HTTPException(status_code=500, detail="Service discovery not available") from e


@app.get("/api/registry")
def get_registry() -> dict[str, Any]:
    """Get the plugin registry (available plugins for installation)."""
    try:
        from phlo.plugins.registry_client import get_registry_data

        return get_registry_data()
    except ImportError:
        return {"plugins": {}}


@app.get("/api/backends")
def get_api_backends() -> list[dict[str, Any]]:
    """List capability-backed API and graph backends."""
    logger.info("api_backends_list_started")
    backends = _list_api_backends()
    logger.info("api_backends_list_succeeded", backend_count=len(backends))
    return backends


@app.get("/api/backends/{name}")
def get_api_backend_info(name: str) -> dict[str, Any]:
    """Get capability-backed API backend details by name."""
    logger.info("api_backend_get_started", backend_name=name)
    backend = _get_api_backend(name)
    if backend is None:
        logger.warning("api_backend_get_failed", backend_name=name, reason="not_found")
        raise HTTPException(status_code=404, detail=f"API backend not found: {name}")
    logger.info("api_backend_get_succeeded", backend_name=name)
    return backend


@app.get("/api/contracts")
def get_contracts() -> list[dict[str, Any]]:
    """List resolved table contracts from registry and generated artifacts."""
    logger.info("api_contracts_list_started")
    contracts = _list_contracts()
    logger.info("api_contracts_list_succeeded", contract_count=len(contracts))
    return contracts


@app.get("/api/contracts/{table_name:path}")
def get_contract(table_name: str) -> dict[str, Any]:
    """Get the resolved contract payload for a single table.

    Raises:
        HTTPException: 404 if the contract is not found.
    """
    logger.info("api_contract_get_started", table_name=table_name)
    contract = _get_contract_by_table(table_name)
    if contract is None:
        logger.warning("api_contract_get_failed", table_name=table_name, reason="not_found")
        raise HTTPException(status_code=404, detail=f"Contract not found: {table_name}")
    logger.info("api_contract_get_succeeded", table_name=table_name)
    return contract


# Register the selected authentication and authorization providers before the
# manifest is installed and regulated validation resolves their exact names.
discover_capabilities()
install_manifest_enforcement(app)
get_adapter().install(app)
require_regulated_validation(runtime=app)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "4000"))
    uvicorn.run(app, host=host, port=port)
