"""phlo.security: regulated access control namespace.

Submodules:
    mode: regulated mode detection (PHLO_REGULATED env var).
    adapters: SurfaceOperation, RegulatedSurfaceAdapter, EnforcementResult types.
    enforcement: EnforcementContext singleton and enforce() core function.
    gating: surface-level allowlist/blocklist for regulated mode.
    validation: startup validation for regulated mode.
"""

from __future__ import annotations

from phlo.security.adapters import (
    EnforcementResult,
    RegulatedSurfaceAdapter,
    SurfaceActivationStatus,
    SurfaceOperation,
)
from phlo.security.enforcement import EnforcementContext, enforce
from phlo.security.gating import (
    UnsupportedSurfaceError,
    check_service_allowed,
    get_approved_services,
    get_blocked_services,
    is_service_allowed,
    validate_service_selection,
)
from phlo.security.mode import is_regulated, is_regulated_mode_enabled
from phlo.security.production_preflight import (
    ProductionReadinessCheck,
    ProductionReadinessCheckId,
    ProductionReadinessReport,
    ProductionReadinessState,
    load_effective_environment,
    run_production_readiness,
)
from phlo.security.validation import (
    RegulatedModeError,
    RegulatedModeValidationReport,
    RegulatedValidationError,
    RegulatedValidationReport,
    require_regulated_mode_validation,
    require_regulated_validation,
    run_regulated_mode_validation,
    run_regulated_validation,
)

__all__ = [
    "EnforcementContext",
    "EnforcementResult",
    "ProductionReadinessCheck",
    "ProductionReadinessCheckId",
    "ProductionReadinessReport",
    "ProductionReadinessState",
    "RegulatedModeError",
    "RegulatedModeValidationReport",
    "RegulatedSurfaceAdapter",
    "RegulatedValidationError",
    "RegulatedValidationReport",
    "SurfaceActivationStatus",
    "SurfaceOperation",
    "UnsupportedSurfaceError",
    "check_service_allowed",
    "enforce",
    "get_approved_services",
    "get_blocked_services",
    "is_regulated",
    "is_regulated_mode_enabled",
    "is_service_allowed",
    "load_effective_environment",
    "require_regulated_mode_validation",
    "require_regulated_validation",
    "run_production_readiness",
    "run_regulated_mode_validation",
    "run_regulated_validation",
    "validate_service_selection",
]
