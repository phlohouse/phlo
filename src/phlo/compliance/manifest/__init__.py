"""System manifest module for regulated deployments.

Public surface re-exports the frozen manifest types and capture_manifest()
from phlo.compliance.manifest.types; no behaviour is defined here.
"""

from phlo.compliance.manifest.types import (
    COMPLIANCE_MODE_TO_REGULATED,
    ComplianceMode,
    ComponentVersion,
    DeploymentEnvironment,
    SecurityConfiguration,
    SystemManifest,
    capture_manifest,
)

__all__ = [
    "COMPLIANCE_MODE_TO_REGULATED",
    "capture_manifest",
    "ComponentVersion",
    "ComplianceMode",
    "DeploymentEnvironment",
    "SecurityConfiguration",
    "SystemManifest",
]
