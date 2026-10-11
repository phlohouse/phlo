"""Phlo RBAC module.

This module provides the canonical RBAC model and governance backend compilers
as defined in Spec 0017: RBAC Core Services Enforcement And Policy Sync.
"""

from phlo.rbac.compiler import GovernanceCompiler
from phlo.rbac.config import RBACConfigLoader
from phlo.rbac.models import (
    BackendArtifact,
    CanonicalAction,
    CanonicalRBAC,
    PoliciesConfig,
    PolicyEffect,
    PolicyRule,
    RolesConfig,
    SyncPlan,
    SyncResult,
    VerifyResult,
)
from phlo.rbac.registry import COMPILER_REGISTRY, get_compiler
from phlo.rbac.sync import SyncController

__all__ = [
    "BackendArtifact",
    "CanonicalAction",
    "CanonicalRBAC",
    "COMPILER_REGISTRY",
    "GovernanceCompiler",
    "get_compiler",
    "PoliciesConfig",
    "PolicyEffect",
    "PolicyRule",
    "RBACConfigLoader",
    "RolesConfig",
    "SyncController",
    "SyncPlan",
    "SyncResult",
    "VerifyResult",
]
