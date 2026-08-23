"""System manifest types for regulated deployments.

Captures the state of a regulated deployment including version info,
compliance features, security configuration, and component inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class DeploymentEnvironment(StrEnum):
    """Deployment environment type."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class ComplianceMode(StrEnum):
    """Compliance mode indicator."""

    OPEN = "open"
    REGULATED = "regulated"


COMPLIANCE_MODE_TO_REGULATED: dict[ComplianceMode, bool] = {
    ComplianceMode.OPEN: False,
    ComplianceMode.REGULATED: True,
}


@dataclass(frozen=True, kw_only=True)
class ComponentVersion:
    """Version information for a deployed component."""

    name: str
    version: str
    build_hash: str | None = None
    deploy_timestamp: str | None = None


@dataclass(frozen=True, kw_only=True)
class SecurityConfiguration:
    """Security configuration snapshot."""

    compliance_mode: ComplianceMode
    regulated: bool
    tamper_evident_audit: bool
    electronic_signatures: bool
    access_governance: bool
    auth_providers: tuple[str, ...] = ()
    require_mfa: bool = False
    session_timeout_seconds: int | None = None


@dataclass(frozen=True, kw_only=True)
class SystemManifest:
    """System manifest capturing deployment state for compliance.

    This manifest provides an immutable snapshot of the system state
    at a point in time, suitable for compliance auditing and evidence.
    """

    manifest_id: str
    captured_at: str
    phlo_version: str
    environment: DeploymentEnvironment
    security: SecurityConfiguration
    components: tuple[ComponentVersion, ...] = field(default_factory=tuple)
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    platform: str | None = None
    region: str | None = None


def capture_manifest(
    phlo_version: str,
    environment: DeploymentEnvironment,
    security: SecurityConfiguration,
    components: list[ComponentVersion] | None = None,
    config_snapshot: dict[str, Any] | None = None,
    manifest_id: str | None = None,
    platform: str | None = None,
    region: str | None = None,
) -> SystemManifest:
    """Capture a system manifest with the current deployment state.

    A manifest_id is generated when not supplied; components default to an
    empty tuple and config_snapshot to an empty mapping.
    """
    from uuid import uuid4

    return SystemManifest(
        manifest_id=manifest_id or str(uuid4()),
        captured_at=datetime.now(UTC).isoformat(),
        phlo_version=phlo_version,
        environment=environment,
        security=security,
        components=tuple(components) if components else (),
        config_snapshot=config_snapshot or {},
        platform=platform,
        region=region,
    )
