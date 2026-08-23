"""Compliance feature flag resolution.

Determines which compliance features are active based on regulated mode
configuration and per-feature overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

from phlo.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class ComplianceFeatures:
    """Compliance features active in the current deployment.

    All features default to False when regulated mode is not active.
    When regulated mode is active, all features default to True unless
    explicitly disabled via the compliance config block.
    """

    tamper_evident_audit: bool = False
    """Whether audit events are sealed with hash chaining."""

    electronic_signatures: bool = False
    """Whether critical actions require explicit electronic signatures."""

    system_manifest: bool = False
    """Whether system manifest is captured at startup and config changes."""

    access_governance: bool = False
    """Whether access governance primitives are active (dormant detection, recertification, SoD)."""

    evidence_export: bool = False
    """Whether evidence pack export is available for audit submission."""


def resolve_compliance_features(
    regulated: bool | None = None,
    compliance_config: dict | None = None,
) -> ComplianceFeatures:
    """Resolve which compliance features are active.

    regulated falls back to is_regulated() when None; compliance_config is an
    optional phlo.yaml block whose boolean keys (tamper_evident_audit,
    electronic_signatures, system_manifest, access_governance) override the
    regulated-mode defaults.
    """
    if regulated is None:
        from phlo.security.mode import is_regulated

        regulated = is_regulated()

    if not regulated:
        return ComplianceFeatures()

    config = compliance_config or {}

    return ComplianceFeatures(
        tamper_evident_audit=config.get("tamper_evident_audit", True),
        electronic_signatures=config.get("electronic_signatures", True),
        system_manifest=config.get("system_manifest", True),
        access_governance=config.get("access_governance", True),
        evidence_export=config.get("evidence_export", True),
    )
