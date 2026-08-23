"""External OPA-backed authorization policy provider.

This is an example external provider that uses Open Policy Agent (OPA) as the
policy decision point. It implements the same AuthorizationPolicyBackend contract
as the built-in RBAC provider, enabling parity and easy migration.

Usage:
    1. Start OPA: docker run -p 8181:8181 openpolicyagent/opa run --server
    2. Configure PHLO_OPA_URL=http://localhost:8181 in .phlo/.env
    3. Register this provider via plugin or direct registration

    OPA-backed authorization policy backend built on phlo.capabilities.interfaces and support.
    Currently imported only by tests/security/test_authorization_opa.py.
"""

from __future__ import annotations

from typing import Any

import httpx

from phlo.capabilities.interfaces import (
    AuthorizationDecision,
    DecisionContext,
    Principal,
    ResourceRef,
)
from phlo.capabilities.support import CapabilitySupport
from phlo.logging import get_logger

logger = get_logger(__name__)


class OPAAuthorizationPolicyBackend:
    """Authorization policy backend backed by Open Policy Agent.

    This provider sends authorization requests to an OPA server and translates
    the response into the standard AuthorizationDecision format.

    Expected OPA input format:
    {
        "principal": {"subject": "...", "type": "...", "roles": [...]},
        "action": "dataset.read",
        "resource": {"type": "...", "id": "..."},
        "context": {"environment": "...", ...}
    }

    Expected OPA policy (Rego):
    package phlo.authz

    default allow = false

    allow {
        input.principal.roles[_] == "analyst"
        input.action == "dataset.read"
        input.resource.type == "dataset"
    }
    """

    def __init__(
        self,
        opa_url: str | None = None,
        opa_policy_package: str = "phlo.authz",
        timeout_seconds: float = 5.0,
    ):
        resolved_url = opa_url or "http://localhost:8181"
        if not resolved_url.startswith(("http://", "https://")):
            raise ValueError(f"OPA URL must use http:// or https:// scheme, got: {resolved_url}")
        self._opa_url = resolved_url
        self._opa_policy_package = opa_policy_package
        self._timeout = timeout_seconds

    def is_allowed(
        self,
        principal: Principal,
        action: str,
        resource: ResourceRef,
        context: DecisionContext | None = None,
    ) -> bool:
        """Check if an action is allowed via OPA."""
        decision = self.explain_decision(principal, action, resource, context)
        return decision.allowed

    def explain_decision(
        self,
        principal: Principal,
        action: str,
        resource: ResourceRef,
        context: DecisionContext | None = None,
    ) -> AuthorizationDecision:
        """Explain an authorization decision via OPA."""
        try:
            input_data = self._build_input(principal, action, resource, context)

            response = self._evaluate(input_data)

            if response is None:
                return AuthorizationDecision(
                    allowed=False,
                    reason_code="backend_unavailable",
                    explanation="OPA server returned no response",
                )

            result = response.get("result", {})

            if isinstance(result, bool):
                if result:
                    return AuthorizationDecision(
                        allowed=True,
                        reason_code="opa_allow",
                        explanation="OPA granted access",
                    )
                return AuthorizationDecision(
                    allowed=False,
                    reason_code="opa_deny",
                    explanation="OPA denied access",
                )

            allow = result.get("allow", False)
            if allow:
                return AuthorizationDecision(
                    allowed=True,
                    reason_code="opa_allow",
                    explanation=result.get("reason", "OPA granted access"),
                )

            return AuthorizationDecision(
                allowed=False,
                reason_code=result.get("reason_code", "opa_deny"),
                explanation=result.get("reason", "OPA denied access"),
            )

        except httpx.ConnectError:
            logger.warning("opa_connect_error", opa_url=self._opa_url)
            return AuthorizationDecision(
                allowed=False,
                reason_code="backend_unavailable",
                policy_id=None,
                explanation="Cannot connect to OPA server",
            )
        except httpx.TimeoutException:
            logger.warning("opa_timeout", opa_url=self._opa_url)
            return AuthorizationDecision(
                allowed=False,
                reason_code="backend_unavailable",
                policy_id=None,
                explanation="OPA request timed out",
            )
        except Exception as e:
            logger.exception("opa_evaluation_failed")
            return AuthorizationDecision(
                allowed=False,
                reason_code="backend_unavailable",
                policy_id=None,
                explanation=f"OPA evaluation failed: {e}",
            )

    def filter_resources(
        self,
        principal: Principal,
        resources: list[ResourceRef],
        action: str,
        context: DecisionContext | None = None,
    ) -> list[ResourceRef]:
        """Filter resources via OPA batch evaluation."""
        allowed: list[ResourceRef] = []

        for resource in resources:
            if self.is_allowed(principal, action, resource, context):
                allowed.append(resource)

        return allowed

    def health_check(self) -> bool:
        """Check if OPA server is reachable."""
        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.get(f"{self._opa_url}/health")
                return response.status_code == 200
        except Exception:
            return False

    def _build_input(
        self,
        principal: Principal,
        action: str,
        resource: ResourceRef,
        context: DecisionContext | None,
    ) -> dict[str, Any]:
        """Build the OPA input document."""
        return {
            "principal": {
                "subject": principal.subject,
                "type": principal.principal_type,
                "roles": list(principal.roles),
                "attributes": dict(principal.attributes),
            },
            "action": action,
            "resource": {
                "type": resource.resource_type,
                "id": resource.resource_id,
                "tenant": resource.tenant,
                "attributes": dict(resource.attributes),
            },
            "context": {
                "environment": context.environment if context else None,
                "request_id": context.request_id if context else None,
                "ip_address": context.ip_address if context else None,
                "attributes": dict(context.attributes) if context else {},
            },
        }

    def _evaluate(self, input_data: dict[str, Any]) -> dict[str, Any] | None:
        """Evaluate the input against OPA."""
        url = f"{self._opa_url}/v1/data/{self._opa_policy_package.replace('.', '/')}"
        payload = {"input": input_data}

        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(url, json=payload)

            if response.status_code != 200:
                return None

            return response.json()


def create_opa_provider(
    opa_url: str | None = None,
    opa_policy_package: str = "phlo.authz",
) -> tuple[OPAAuthorizationPolicyBackend, CapabilitySupport]:
    """Create an OPA provider with support metadata.

    ``opa_url`` defaults to http://localhost:8181 and ``opa_policy_package`` to
    phlo.authz. Returns a ``(provider, support_metadata)`` tuple.
    """
    provider = OPAAuthorizationPolicyBackend(
        opa_url=opa_url,
        opa_policy_package=opa_policy_package,
    )
    support = CapabilitySupport(
        supports_permissions=True,
        supports_attributes=True,
    )
    return provider, support
