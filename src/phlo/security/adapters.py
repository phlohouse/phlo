"""Regulated surface adapter types and core interfaces.

This module defines the types used by regulated surface adapters and core
enforcement to communicate: SurfaceOperation declarations, EnforcementResult
response types, and the RegulatedSurfaceAdapter protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, Required, TypedDict, runtime_checkable

if TYPE_CHECKING:
    pass


class SurfaceOperation(TypedDict, total=False):
    """TypedDict for surface operation declarations.

    Only `action`, `resource_type`, and `operation_name` are required.
    `resource_id_strategy` and `framework_metadata` are optional.

    Keys:
        action: Canonical action name (e.g., "dataset.read").
        resource_type: Canonical resource type (e.g., "dataset").
        operation_name: Human-readable operation name.
        resource_id_strategy: Optional strategy for extracting resource IDs
            from requests (e.g., "path_param", "query_param").
        framework_metadata: Optional framework-specific metadata (e.g., FastAPI
            route patterns, GraphQL operation names).
    """

    action: Required[str]
    resource_type: Required[str]
    operation_name: Required[str]
    resource_id_strategy: str | None
    framework_metadata: dict[str, Any]


@runtime_checkable
class RegulatedSurfaceAdapter(Protocol):
    """Protocol for regulated surface adapters.

    Adapters declare which operations their surface exposes, report whether
    they are active on the actual framework runtime, and install the adapter
    on startup. Core owns enforcement; adapters only declare and translate.

    The runtime parameter lets validation verify the adapter is actually
    wired to its framework, not just that regulated mode is enabled.
    """

    @property
    def surface_name(self) -> str:
        """Unique name of this surface (e.g., "phlo-api", "dagster-webserver")."""

    @property
    def framework_type(self) -> str:
        """Framework type (e.g., "fastapi", "dagster-graphql", "cli")."""

    def list_operations(self) -> list[SurfaceOperation]:
        """Declare all regulated operations exposed by this surface."""

    def is_active(self, runtime: Any) -> bool:
        """Return True if the surface is active on the given runtime."""

    def install(self, runtime: Any) -> None:
        """Wire the adapter into the framework runtime and register with capability registry."""


class EnforcementResult:
    """Result of a core enforcement decision.

    Returned by enforce() to the calling adapter, which translates into
    framework-native behavior (HTTP 403, GraphQL error, exit code, etc.).

    Variants:
        Allow: Action is permitted.
        Deny: Action is denied. Contains reason_code, policy_id, explanation.
        Error: Enforcement encountered an error. Contains reason_code, explanation.
    """

    def __init__(
        self,
        variant: Literal["allow", "deny", "error"],
        reason_code: str | None = None,
        policy_id: str | None = None,
        explanation: str | None = None,
    ) -> None:
        self.variant = variant
        self.reason_code = reason_code
        self.policy_id = policy_id
        self.explanation = explanation

    @classmethod
    def allow(cls) -> EnforcementResult:
        """Build a permit result with no diagnostic detail."""
        return cls(variant="allow")

    @classmethod
    def deny(
        cls,
        reason_code: str,
        policy_id: str | None = None,
        explanation: str | None = None,
    ) -> EnforcementResult:
        """Build a denial carrying reason_code, optional policy_id, and explanation."""
        return cls(
            variant="deny",
            reason_code=reason_code,
            policy_id=policy_id,
            explanation=explanation,
        )

    @classmethod
    def error(
        cls,
        reason_code: str,
        explanation: str | None = None,
    ) -> EnforcementResult:
        """Build a result for enforcement failures, where the action was never decided."""
        return cls(variant="error", reason_code=reason_code, explanation=explanation)

    @property
    def allowed(self) -> bool:
        """Return True only for the allow variant."""
        return self.variant == "allow"

    def __repr__(self) -> str:
        if self.variant == "allow":
            return "EnforcementResult(allow)"
        if self.variant == "deny":
            return (
                f"EnforcementResult(deny, reason_code={self.reason_code!r}, "
                f"policy_id={self.policy_id!r}, explanation={self.explanation!r})"
            )
        return (
            f"EnforcementResult(error, reason_code={self.reason_code!r}, "
            f"explanation={self.explanation!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EnforcementResult):
            return NotImplemented
        return (
            self.variant == other.variant
            and self.reason_code == other.reason_code
            and self.policy_id == other.policy_id
            and self.explanation == other.explanation
        )


class SurfaceActivationStatus:
    """Activation status of a regulated surface."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    NOT_REGISTERED = "not_registered"
