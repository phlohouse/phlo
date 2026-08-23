"""Surface gating for regulated mode.

Categorizes services by regulatory boundary status:

- APPROVED_SERVICES: fully integrated regulated surfaces
- APPROVED_CLI_PACKAGES: approved CLI plugin packages with authorization adapters
- INGRESS_OPTIONAL_SERVICES: optional surfaces protected by ingress + upstream APIs
  (hasura, postgrest, superset: use their own permission models, require ingress protection)
- WRITE_RESTRICTED_SERVICES: ingress-optional surfaces that must be read-only in
  regulated mode unless explicitly opted in via phlo.yaml surfaces.{name}.allow_writes
- UNSUPPORTED_SERVICES: blocked surfaces not suitable for regulated deployments
- PENDING_ADAPTER_SERVICES: surfaces with adapters awaiting approval (currently empty)

CLI Plugin Packages (APPROVED_CLI_PACKAGES):
- phlo-alerting: alerting CLI (all read commands, no adapter needed)
- phlo-lineage: lineage CLI (has mutation: lineage.column.import-dbt)
- phlo-dlt: DLT CLI (has mutation: workflow.create)
- phlo-dbt: dbt CLI (has mutations: dbt.run, dbt.publishing.scaffold)
- phlo-pandera: Pandera CLI (all read commands, no adapter needed)

UI Surface Classifications (Phase 3):

- observatory: approved, inside regulated boundary via ingress + phlo-api
- superset: ingress_optional, requires ingress/IdP integration for regulated deployments
- pgweb: unsupported, blocked due to direct Postgres access without Phlo auth mediation
"""

from __future__ import annotations

from typing import Any

from phlo.logging import get_logger
from phlo.security.mode import is_regulated

logger = get_logger(__name__)

UNSUPPORTED_SERVICES: frozenset[str] = frozenset(
    {
        "pgweb",
        "openmetadata",
        "openmetadata-server",
        "openmetadata-ingestion",
    }
)

APPROVED_CLI_PACKAGES: frozenset[str] = frozenset(
    {
        "phlo-alerting",
        "phlo-lineage",
        "phlo-dlt",
        "phlo-dbt",
        "phlo-pandera",
        "phlo-clickhouse",
        "phlo-clickstack",
        "phlo-sling",
    }
)

PENDING_ADAPTER_SERVICES: frozenset[str] = frozenset()

INGRESS_OPTIONAL_SERVICES: frozenset[str] = frozenset(
    {
        "superset",
        "hasura",
        "postgrest",
    }
)

# Ingress-optional services that must be read-only in regulated mode.
# Writes are blocked unless the operator explicitly opts in via:
#   phlo.yaml: surfaces.<service>.allow_writes: true
WRITE_RESTRICTED_SERVICES: frozenset[str] = frozenset(
    {
        "hasura",
        "postgrest",
    }
)

APPROVED_SERVICES: frozenset[str] = frozenset(
    {
        "phlo-api",
        "cli",
        "dagster-webserver",
        "dagster-daemon",
        "postgres",
        "minio",
        "minio-setup",
        "nessie",
        "trino",
        "observatory",
        "prometheus",
        "grafana",
        "loki",
        "alloy",
        "clickhouse",
        "clickhouse-setup",
        "phlo-trino-cli",
        "phlo-nessie-cli",
        "phlo-minio-cli",
        "phlo-postgres-cli",
    }
)


class UnsupportedSurfaceError(Exception):
    """Raised when an unsupported surface is accessed in regulated mode."""

    def __init__(self, surface: str, reason: str) -> None:
        self.surface = surface
        self.reason = reason
        super().__init__(f"Unsupported surface '{surface}' in regulated mode: {reason}")


def check_service_allowed(service_name: str, regulated: bool | None = None) -> None:
    """Check if a service is allowed in regulated mode."""
    if regulated is None:
        regulated = is_regulated()

    if not regulated:
        return

    normalized = service_name.lower().replace("_", "-").replace(" ", "-")

    if normalized in UNSUPPORTED_SERVICES:
        raise UnsupportedSurfaceError(
            surface=service_name,
            reason=f"'{service_name}' is not an approved regulated entry point. "
            "Use phlo-api or approved CLI paths instead.",
        )

    if normalized in PENDING_ADAPTER_SERVICES:
        raise UnsupportedSurfaceError(
            surface=service_name,
            reason=f"'{service_name}' adapter is pending approval. "
            "Direct access is forbidden until the adapter is approved.",
        )

    if normalized in INGRESS_OPTIONAL_SERVICES:
        logger.warning(
            "ingress_optional_service_in_regulated_mode",
            service=service_name,
            note="ingress protection and upstream auth required",
        )
        if normalized in WRITE_RESTRICTED_SERVICES and is_write_restricted(
            service_name, regulated=True
        ):
            logger.warning(
                "write_restricted_service",
                service=service_name,
                note="Read-only in regulated mode. "
                "Set surfaces.<service>.allow_writes: true in phlo.yaml to opt in.",
            )
        return

    if normalized in APPROVED_CLI_PACKAGES:
        return

    if normalized not in APPROVED_SERVICES:
        logger.warning(
            "unknown_service_in_regulated_mode",
            service=service_name,
            approved=sorted(APPROVED_SERVICES),
        )
        raise UnsupportedSurfaceError(
            surface=service_name,
            reason=f"'{service_name}' is not a known approved regulated entry point.",
        )


def is_service_allowed(service_name: str, regulated: bool | None = None) -> bool:
    """Check if a service is allowed without raising."""
    try:
        check_service_allowed(service_name, regulated)
        return True
    except UnsupportedSurfaceError:
        return False


def get_blocked_services() -> list[str]:
    """Return sorted list of blocked service names."""
    return sorted(UNSUPPORTED_SERVICES | PENDING_ADAPTER_SERVICES)


def get_ingress_optional_services() -> list[str]:
    """Return sorted list of ingress-optional service names."""
    return sorted(INGRESS_OPTIONAL_SERVICES)


def get_approved_services() -> list[str]:
    """Return sorted list of approved service names."""
    return sorted(APPROVED_SERVICES)


def get_approved_cli_packages() -> list[str]:
    """Return sorted list of approved CLI plugin packages."""
    return sorted(APPROVED_CLI_PACKAGES)


def get_write_restricted_services() -> list[str]:
    """Return sorted list of write-restricted service names."""
    return sorted(WRITE_RESTRICTED_SERVICES)


def is_write_restricted(service_name: str, regulated: bool | None = None) -> bool:
    """Check if a service is write-restricted in regulated mode.

    Write-restricted services (hasura, postgrest) must operate in read-only
    mode unless the operator explicitly opts in via phlo.yaml
    ``surfaces.<service>.allow_writes``. Defaults to is_regulated() when
    regulated is not supplied.
    """
    if regulated is None:
        regulated = is_regulated()

    if not regulated:
        return False

    normalized = service_name.lower().replace("_", "-").replace(" ", "-")
    if normalized not in WRITE_RESTRICTED_SERVICES:
        return False

    # Check for operator opt-in via phlo.yaml surfaces.<service>.allow_writes
    try:
        from phlo.infrastructure.config import load_project_config

        project_config = load_project_config()
        surfaces_config = project_config.get("surfaces", {})
        service_config = surfaces_config.get(normalized, {})
        if service_config.get("allow_writes") is True:
            logger.info(
                "write_restriction_opted_out",
                service=service_name,
                note="Operator has explicitly enabled writes for this service",
            )
            return False
    except Exception:
        pass

    return True


def check_cli_package_allowed(package_name: str, regulated: bool | None = None) -> None:
    """Check if a CLI plugin package is allowed in regulated mode.

    Raises UnsupportedSurfaceError when package_name is not in the approved
    list; non-regulated mode allows everything.
    """
    if regulated is None:
        regulated = is_regulated()

    if not regulated:
        return

    normalized = package_name.lower().replace("_", "-").replace(" ", "-")

    if normalized not in APPROVED_CLI_PACKAGES:
        raise UnsupportedSurfaceError(
            surface=package_name,
            reason=f"'{package_name}' is not an approved CLI package. "
            "Use only approved CLI plugin packages in regulated mode.",
        )


def is_cli_package_allowed(package_name: str, regulated: bool | None = None) -> bool:
    """Check if a CLI plugin package is allowed without raising."""
    try:
        check_cli_package_allowed(package_name, regulated)
        return True
    except UnsupportedSurfaceError:
        return False


def validate_service_selection(
    services: list[str],
    regulated: bool | None = None,
) -> dict[str, Any]:
    """Validate a selection of services for regulated mode."""
    if regulated is None:
        regulated = is_regulated()

    result: dict[str, Any] = {
        "allowed": [],
        "blocked": [],
        "unknown": [],
        "ingress_optional": [],
        "regulated": regulated,
    }

    for service in services:
        normalized = service.lower().replace("_", "-").replace(" ", "-")

        if normalized in UNSUPPORTED_SERVICES:
            result["blocked"].append(
                {"service": service, "reason": "Not an approved regulated entry point"}
            )
        elif normalized in PENDING_ADAPTER_SERVICES:
            result["blocked"].append({"service": service, "reason": "Adapter pending approval"})
        elif normalized in INGRESS_OPTIONAL_SERVICES:
            result["ingress_optional"].append(
                {"service": service, "reason": "Ingress protection required"}
            )
            # Fail closed: this report cannot verify that ingress enforcement
            # is configured, so ingress-optional services count as blocked too.
            result["blocked"].append(
                {"service": service, "reason": "Required ingress enforcement is not configured"}
            )
        elif normalized in APPROVED_CLI_PACKAGES:
            result["allowed"].append(service)
        elif normalized not in APPROVED_SERVICES:
            result["unknown"].append(service)
            result["blocked"].append(
                {"service": service, "reason": "Not a known approved regulated entry point"}
            )
        else:
            result["allowed"].append(service)

    return result
