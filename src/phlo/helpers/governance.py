"""Governance, policy, and audit helper utilities.

Policy checks delegate to the resolved governance backend and fail open when
no backend is available; column classification and masking are name-based
heuristics that produce policy mappings, not enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phlo.capabilities import resolve_capability


@dataclass(frozen=True, slots=True)
class ApprovalRequirement:
    """Descriptor for operations requiring approval."""

    operation: str
    resource: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


def require_approval(operation: str, resource: str, *, reason: str) -> ApprovalRequirement:
    """Create an approval requirement descriptor."""
    return ApprovalRequirement(operation=operation, resource=resource, reason=reason)


def classify_columns(columns: list[str]) -> dict[str, list[str]]:
    """Classify likely sensitive columns by name heuristics."""
    pii_tokens = ("email", "phone", "address", "name", "ssn", "dob")
    secret_tokens = ("password", "token", "secret", "key")
    return {
        "pii": [col for col in columns if any(token in col.lower() for token in pii_tokens)],
        "secrets": [col for col in columns if any(token in col.lower() for token in secret_tokens)],
    }


def mask_columns(columns: list[str], *, strategy: str = "redact") -> dict[str, str]:
    """Return a simple masking policy mapping."""
    return dict.fromkeys(columns, strategy)


def policy_check(
    principal: str,
    table_name: str,
    action: str,
    *,
    backend: Any = None,
) -> bool:
    """Check an access policy when a governance backend is available."""
    provider = backend
    if provider is None:
        resolution = resolve_capability("governance_backend")
        provider = resolution.provider if resolution else None
    if provider is None or not hasattr(provider, "check_access"):
        return True
    return bool(provider.check_access(principal=principal, table_name=table_name, action=action))


def audit_event(
    action: str, *, resource: str, actor: str | None = None, **metadata: Any
) -> dict[str, Any]:
    """Build a structured audit event payload."""
    return {"action": action, "resource": resource, "actor": actor, "metadata": metadata}
