"""Deployment context for Mission Control.

``GET /api/observatory/mission/context`` answers the one configured project,
environment and data mode this deployment is bound to, plus sanitized
dependency statuses and per-family action availability. This is the
diagnostic that must still answer honestly when the project path is bad or a
provider is down, so it computes everything itself instead of relying on the
read surfaces it describes.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
import socket
from urllib.parse import urlsplit

from fastapi import APIRouter
from phlo.logging import get_logger
from phlo.plugins.observatory_settings import (
    ObservatorySettingsStorageConfig,
    StorageUnavailableError,
    get_settings_service,
)
from phlo.security.mode import is_regulated, requires_http_authorization

from phlo_api.observatory_api.observatory_mission_control_mode import (
    data_mode,
    demo_envelope,
    environment_id,
    live_envelope,
    project_id,
    project_root,
    validate_project,
)
from phlo_api.observatory_api.observatory_mission_control_models import (
    MissionActionAvailability,
    MissionContext,
    MissionDependencyStatus,
    ReadEnvelope,
)

logger = get_logger(__name__)

router = APIRouter(tags=["observatory-mission"])

_PROBE_TIMEOUT_SECONDS = 1.5


def _probe_http(url: str) -> str | None:
    """TCP-connect to a resolved provider URL's host:port.

    Returns a sanitized error detail on failure, None when the endpoint
    accepts a connection. Bounded by a short timeout so a dead provider
    cannot stall the context read.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if not host:
            return "provider URL has no host"
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return "provider URL is malformed"
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_SECONDS):
            return None
    except OSError as exc:
        return f"endpoint unreachable ({type(exc).__name__})"


def _endpoint_detail(url: str) -> str:
    """Sanitized endpoint identity: host and port only, never credentials."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "endpoint configured"
    host = parts.hostname or "unknown"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"endpoint {host}:{port}"


def _settings_dependency() -> MissionDependencyStatus:
    """Durable state readiness: every mission collection flows through here."""
    config = ObservatorySettingsStorageConfig()
    if config.observatory_settings_backend == "memory":
        return MissionDependencyStatus(
            name="durable_state",
            kind="storage",
            status="unsupported",
            detail="In-memory settings backend: control state does not survive restart",
        )
    try:
        get_settings_service()
    except StorageUnavailableError:
        return MissionDependencyStatus(
            name="durable_state",
            kind="storage",
            status="unavailable",
            detail="Durable settings store capability is not registered",
        )
    except Exception:
        logger.warning("mission_context_settings_probe_failed", exc_info=True)
        return MissionDependencyStatus(
            name="durable_state",
            kind="storage",
            status="unavailable",
            detail="Durable settings store failed to resolve",
        )
    return MissionDependencyStatus(
        name="durable_state",
        kind="storage",
        status="ready",
        detail="Durable settings store resolved",
    )


def _security_dependencies() -> list[MissionDependencyStatus]:
    deps: list[MissionDependencyStatus] = []
    try:
        from phlo_api.api.authentication import get_authentication_provider

        provider = get_authentication_provider()
    except Exception:
        logger.warning("mission_context_auth_probe_failed", exc_info=True)
        provider = None
        deps.append(
            MissionDependencyStatus(
                name="authentication",
                kind="security",
                status="unavailable",
                detail="Authentication provider failed to resolve",
            )
        )
    else:
        deps.append(
            MissionDependencyStatus(
                name="authentication",
                kind="security",
                status="ready" if provider is not None else "unconfigured",
                detail=None if provider is not None else "No authentication provider configured",
            )
        )

    from phlo_api.security_manifest import get_authorization_backend

    try:
        backend = get_authorization_backend()
    except Exception:
        logger.warning("mission_context_authz_probe_failed", exc_info=True)
        backend = None
    required = is_regulated() or requires_http_authorization()
    deps.append(
        MissionDependencyStatus(
            name="authorization",
            kind="security",
            status=(
                "ready" if backend is not None else "unavailable" if required else "unconfigured"
            ),
            detail=None
            if backend is not None
            else "Authorization backend required but not available"
            if required
            else "No authorization backend configured; development mode",
        )
    )

    deps.append(
        MissionDependencyStatus(
            name="audit",
            kind="security",
            status="unconfigured",
            detail="No durable audit sink configured; durable-required mutations fail closed",
        )
    )
    return deps


def _provider_dependencies() -> list[MissionDependencyStatus]:
    deps: list[MissionDependencyStatus] = []

    from phlo_api.observatory_api.dagster import resolve_dagster_url
    from phlo_api.observatory_api.nessie import resolve_nessie_url
    from phlo_api.observatory_api.observatory import _catalog_branch_provider
    from phlo_api.observatory_api.observatory_services import docker_reachable

    try:
        dagster_url = resolve_dagster_url()
        dagster_error = _probe_http(dagster_url)
    except Exception:
        logger.warning("mission_context_orchestrator_probe_failed", exc_info=True)
        dagster_url, dagster_error = "", "orchestrator endpoint could not be resolved"
    deps.append(
        MissionDependencyStatus(
            name="orchestrator",
            kind="provider",
            status="ready" if dagster_error is None else "unavailable",
            detail=dagster_error or _endpoint_detail(dagster_url),
        )
    )

    try:
        catalog_provider = _catalog_branch_provider()
    except Exception:
        logger.warning("mission_context_catalog_probe_failed", exc_info=True)
        catalog_provider = None
    if catalog_provider is None:
        deps.append(
            MissionDependencyStatus(
                name="catalog",
                kind="provider",
                status="unconfigured",
                detail="No catalog provider with branch support is registered",
            )
        )
    else:
        try:
            nessie_url = resolve_nessie_url()
            nessie_error = _probe_http(nessie_url)
        except Exception:
            logger.warning("mission_context_nessie_probe_failed", exc_info=True)
            nessie_url, nessie_error = "", "catalog endpoint could not be resolved"
        deps.append(
            MissionDependencyStatus(
                name="catalog",
                kind="provider",
                status="ready" if nessie_error is None else "unavailable",
                detail=nessie_error or _endpoint_detail(nessie_url),
            )
        )

    try:
        runtime_ready = docker_reachable()
    except Exception:
        logger.warning("mission_context_runtime_probe_failed", exc_info=True)
        runtime_ready = False
    deps.append(
        MissionDependencyStatus(
            name="container_runtime",
            kind="runtime",
            status="ready" if runtime_ready else "unavailable",
            detail=None if runtime_ready else "Container runtime is not reachable",
        )
    )
    return deps


def _run_evidence_dependency() -> MissionDependencyStatus:
    """Run-evidence durability: postgres when configured, sqlite in dev."""
    if os.environ.get("PHLO_RUN_EVIDENCE_DB_URL"):
        return MissionDependencyStatus(
            name="run_evidence",
            kind="storage",
            status="ready",
            detail="PostgreSQL run evidence store configured",
        )
    if environment_id() in {"prod", "production", "staging", "regulated"}:
        return MissionDependencyStatus(
            name="run_evidence",
            kind="storage",
            status="unavailable",
            detail="PHLO_RUN_EVIDENCE_DB_URL is required in this environment",
        )
    return MissionDependencyStatus(
        name="run_evidence",
        kind="storage",
        status="ready",
        detail="Local SQLite run evidence store (development)",
    )


def _action_availability(
    *,
    demo: bool,
    control_ready: bool,
    deps: list[MissionDependencyStatus],
) -> list[MissionActionAvailability]:
    dep_status = {dep.name: dep.status for dep in deps}

    def gate(family: str, dependency: str | None) -> MissionActionAvailability:
        if demo:
            return MissionActionAvailability(
                action=family, available=False, reason="Demo mode: mutations are rejected"
            )
        if not control_ready:
            return MissionActionAvailability(
                action=family, available=False, reason="Control plane is not ready"
            )
        if dependency is not None and dep_status.get(dependency) != "ready":
            return MissionActionAvailability(
                action=family,
                available=False,
                reason=f"{dependency} is {dep_status.get(dependency, 'unknown')}",
            )
        return MissionActionAvailability(action=family, available=True)

    return [
        gate("asset:materialize", "orchestrator"),
        gate("asset:backfill", "orchestrator"),
        gate("run:retry", "orchestrator"),
        gate("run:cancel", "orchestrator"),
        gate("service:restart", "container_runtime"),
        gate("service:probe", "container_runtime"),
        gate("branch:create", "catalog"),
        gate("branch:merge", "catalog"),
        gate("branch:delete", "catalog"),
        gate("branch:checkout", "catalog"),
        gate("dataset:publish", "durable_state"),
        gate("dataset:retire", "durable_state"),
        gate("candidate:claim", "catalog"),
        gate("candidate:promote", "catalog"),
        gate("candidate:reject", "catalog"),
        gate("workflow:dispatch", "durable_state"),
    ]


@router.get("/mission/context", response_model=ReadEnvelope[MissionContext])
def get_mission_context() -> ReadEnvelope[MissionContext]:
    """Report the configured project, environment and control readiness."""
    valid, detail = validate_project()
    demo = data_mode() == "demo"

    dependencies = [
        _settings_dependency(),
        _run_evidence_dependency(),
        *_security_dependencies(),
        *_provider_dependencies(),
    ]
    dep_status = {dep.name: dep.status for dep in dependencies}

    blockers: list[str] = []
    if not valid:
        blockers.append(f"Project configuration invalid: {detail}")
    if demo:
        blockers.append("Demo mode: mutations are rejected and reads serve fixture data")
    if dep_status.get("durable_state") != "ready":
        blockers.append("Durable settings state is not ready")
    if is_regulated() and dep_status.get("audit") != "ready":
        blockers.append("Regulated mode requires durable audit persistence")
    if (is_regulated() or requires_http_authorization()) and dep_status.get(
        "authorization"
    ) != "ready":
        blockers.append("Authorization is required but not available")

    control_ready = not blockers
    context = MissionContext(
        project_id=project_id() if valid else None,
        project_root=str(project_root()),
        project_valid=valid,
        project_detail=detail,
        environment_id=environment_id(),
        data_mode=data_mode(),
        read_ready=valid,
        control_ready=control_ready,
        blockers=blockers,
        dependencies=dependencies,
        actions=_action_availability(demo=demo, control_ready=control_ready, deps=dependencies),
        observed_at=datetime.now(UTC).isoformat(),
    )
    if demo:
        return demo_envelope(context, source="context")
    return live_envelope(context, source="context")
