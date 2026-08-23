"""Default authorization policy backend capability provider.

Implements a simple RBAC-backed policy engine: declarative rules match
principal roles and attributes, actions, resource types/ids/patterns
(fnmatch wildcards), with deny-by-default when no rule grants access.
explain_decision returns the full reasoning so callers can audit
verdicts; register_default_capability_providers wires the backend from
the authoritative project policy file.

Imported by phlo.capabilities.discovery and exercised by the phlo-api security
and authorization test suites.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from phlo.capabilities.interfaces import (
    AuthorizationDecision,
    DecisionContext,
    Principal,
    ResourceRef,
)
from phlo.capabilities.registry import register_capability
from phlo.capabilities.specs import AuthorizationPolicyBackendSpec
from phlo.capabilities.support import CapabilitySupport
from phlo.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PolicyRule:
    """Single authorization policy rule."""

    policy_id: str
    effect: str
    principal_roles: tuple[str, ...]
    principal_attributes: Mapping[str, str]
    action: str
    resource_type: str
    resource_id_pattern: str
    resource_attributes: Mapping[str, str]


class DefaultAuthorizationPolicyBackend:
    """Simple RBAC-backed authorization policy backend.

    This provider implements basic role-based access control with pattern
    matching for resource identifiers. It follows the decision semantics
    from the spec:
    - Explicit deny overrides explicit allow
    - No matching rule means deny
    - Provider failures fail closed
    """

    def __init__(
        self,
        policies: list[dict[str, Any]] | None = None,
        rbac: Any = None,
    ):
        self.rbac = rbac
        if policies is None and rbac is not None:
            policies = [
                {
                    "policy_id": policy.policy_id,
                    "effect": policy.effect.value,
                    "principal": {
                        "roles": policy.principal_roles,
                        "attributes": dict(policy.principal_attributes),
                    },
                    "action": policy.action,
                    "resource": {
                        "type": policy.resource_type,
                        "id_pattern": policy.resource_id_pattern,
                        "attributes": dict(policy.resource_attributes),
                    },
                }
                for policy in rbac.policies.policies
            ]
        self._policies = self._parse_policies(policies or [])

    def _parse_policies(self, policy_configs: list[dict[str, Any]]) -> list[PolicyRule]:
        """Parse policy configuration into PolicyRule objects."""
        rules: list[PolicyRule] = []
        for config in policy_configs:
            effect = config.get("effect", "deny")
            principal = config.get("principal", {})
            resource = config.get("resource", {})

            rule = PolicyRule(
                policy_id=config.get("policy_id", "unknown"),
                effect=effect,
                principal_roles=tuple(principal.get("roles", [])),
                principal_attributes=principal.get("attributes", {}),
                action=config.get("action", "*"),
                resource_type=resource.get("type", "*"),
                resource_id_pattern=resource.get("id_pattern", "*"),
                resource_attributes=resource.get("attributes", {}),
            )
            rules.append(rule)
        return rules

    def is_allowed(
        self,
        principal: Principal,
        action: str,
        resource: ResourceRef,
        context: DecisionContext | None = None,
    ) -> bool:
        """Check if an action is allowed."""
        decision = self.explain_decision(principal, action, resource, context)
        return decision.allowed

    def explain_decision(
        self,
        principal: Principal,
        action: str,
        resource: ResourceRef,
        context: DecisionContext | None = None,
    ) -> AuthorizationDecision:
        """Explain an authorization decision with full details."""
        try:
            matching_deny: AuthorizationDecision | None = None
            matching_allow: AuthorizationDecision | None = None

            for rule in self._policies:
                if self._rule_matches(rule, principal, action, resource):
                    if rule.effect == "deny":
                        matching_deny = AuthorizationDecision(
                            allowed=False,
                            reason_code="explicit_deny",
                            policy_id=rule.policy_id,
                            explanation=f"Matched deny rule {rule.policy_id}",
                        )
                        break
                    if rule.effect == "allow":
                        matching_allow = AuthorizationDecision(
                            allowed=True,
                            reason_code="explicit_allow",
                            policy_id=rule.policy_id,
                            explanation=f"Matched allow rule {rule.policy_id}",
                        )

            if matching_deny is not None:
                return matching_deny
            if matching_allow is not None:
                return matching_allow

            return AuthorizationDecision(
                allowed=False,
                reason_code="default_deny",
                policy_id=None,
                explanation="No matching policy rule",
            )
        except Exception:
            logger.exception("authorization_backend_failed")
            return AuthorizationDecision(
                allowed=False,
                reason_code="backend_unavailable",
                policy_id=None,
                explanation="Authorization backend failed",
            )

    def filter_resources(
        self,
        principal: Principal,
        resources: list[ResourceRef],
        action: str,
        context: DecisionContext | None = None,
    ) -> list[ResourceRef]:
        """Filter resources to only those the principal can access."""
        return [r for r in resources if self.is_allowed(principal, action, r, context)]

    def _rule_matches(
        self,
        rule: PolicyRule,
        principal: Principal,
        action: str,
        resource: ResourceRef,
    ) -> bool:
        """Check if a rule matches the given request."""
        if not self._action_matches(rule.action, action):
            return False

        if not self._resource_type_matches(rule.resource_type, resource.resource_type):
            return False

        if not self._resource_id_matches(rule.resource_id_pattern, resource.resource_id):
            return False

        if not self._principal_roles_match(rule.principal_roles, principal.roles):
            return False

        if not self._attributes_match(rule.principal_attributes, principal.attributes):
            return False

        return self._attributes_match(rule.resource_attributes, resource.attributes)

    def _action_matches(self, rule_action: str, request_action: str) -> bool:
        """Check if action matches (supports wildcards)."""
        if rule_action == "*":
            return True
        return fnmatch.fnmatch(request_action, rule_action)

    def _resource_type_matches(self, rule_type: str, request_type: str) -> bool:
        """Check if resource type matches (supports wildcards)."""
        if rule_type == "*":
            return True
        return fnmatch.fnmatch(request_type, rule_type)

    def _resource_id_matches(self, pattern: str, resource_id: str) -> bool:
        """Check if resource ID matches pattern (supports wildcards)."""
        if pattern == "*":
            return True
        return fnmatch.fnmatch(resource_id, pattern)

    def _principal_roles_match(
        self,
        rule_roles: tuple[str, ...],
        principal_roles: tuple[str, ...],
    ) -> bool:
        """Check if principal roles match rule roles."""
        if not rule_roles:
            return True
        return any(role in principal_roles for role in rule_roles)

    def _attributes_match(
        self,
        rule_attrs: Mapping[str, str],
        request_attrs: Mapping[str, str],
    ) -> bool:
        """Check if attributes match."""
        for key, value in rule_attrs.items():
            if key not in request_attrs:
                return False
            if not fnmatch.fnmatch(request_attrs[key], value):
                return False
        return True


def register_default_capability_providers(*, rbac: Any = None) -> None:
    """Register the default backend from the authoritative project policy file."""
    from phlo.infrastructure.config import _default_project_root
    from phlo.rbac.config import RBACConfigLoader
    from phlo.security.mode import is_regulated

    loader = RBACConfigLoader(_default_project_root() / ".phlo")
    try:
        if rbac is None:
            rbac = loader.load()
        policies = rbac.policies.policies
    except (FileNotFoundError, ValueError) as exc:
        if is_regulated():
            raise RuntimeError("Regulated authorization policies are unavailable") from exc
        try:
            policies = loader.load_policies().policies
        except (FileNotFoundError, ValueError):
            policies = ()

    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="default",
            provider=DefaultAuthorizationPolicyBackend(
                policies=[
                    {
                        "policy_id": policy.policy_id,
                        "effect": policy.effect.value,
                        "principal": {
                            "roles": policy.principal_roles,
                            "attributes": dict(policy.principal_attributes),
                        },
                        "action": policy.action,
                        "resource": {
                            "type": policy.resource_type,
                            "id_pattern": policy.resource_id_pattern,
                            "attributes": dict(policy.resource_attributes),
                        },
                    }
                    for policy in policies
                ],
                rbac=rbac,
            ),
            metadata={
                "policy_format": "rbac",
                "default_policies": [policy.policy_id for policy in policies],
                "canonical_rbac_version": getattr(rbac, "version_hash", None),
            },
            support=CapabilitySupport(
                supports_permissions=True,
                supports_attributes=False,
            ),
        ),
    )
