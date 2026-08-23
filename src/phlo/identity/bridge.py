"""Identity bridge for canonical principal resolution.

Shared authn-to-authz canonicalization bridge converting AuthPrincipal (from
authentication) to Principal (for authorization), so all regulated surfaces
derive principals consistently from the same upstream IdP or trusted proxy
chain using canonical RBAC subject assignments. Inputs are the AuthPrincipal,
request/session metadata, and canonical RBAC subject assignments; the output is
a Principal with canonical roles and attributes.

Regulated mode: all regulated surfaces must use this bridge for principal
resolution; per-surface bespoke group-to-role mapping is forbidden and local
role mapping behavior is unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phlo.logging import get_logger

if TYPE_CHECKING:
    from phlo.capabilities.interfaces import AuthPrincipal, Principal
    from phlo.rbac.models import CanonicalRBAC

logger = get_logger(__name__)

# Default group-to-role mapping for canonical RBAC
# Only known group names are mapped to canonical roles.
# Unknown groups are discarded to prevent privilege escalation.
DEFAULT_GROUP_ROLE_MAPPING: dict[str, str] = {
    "admin": "admin",
    "operators": "operator",
    "developers": "developer",
    "analysts": "analyst",
    "viewers": "viewer",
}

# Principal types approved for regulated mode
APPROVED_PRINCIPAL_TYPES: frozenset[str] = frozenset(
    {
        "user",  # Human user
        "service",  # Service principal
        "platform",  # Automated platform principal
    }
)


@dataclass(frozen=True)
class IdentityBridgeConfig:
    """Configuration for the identity bridge.

    Fields mirror identity policy knobs: ``group_role_mapping`` maps IdP group
    names to canonical role names (defaults to DEFAULT_GROUP_ROLE_MAPPING);
    ``enforce_approved_principal_types`` rejects principals with non-approved
    types when True (required in regulated mode); ``propagate_idp_groups``
    copies original IdP groups into principal attributes for audit (default
    True); ``authentication_source_claim`` names the AuthPrincipal attribute
    holding the authentication source (default "authentication_source").
    """

    group_role_mapping: dict[str, str] = field(
        default_factory=lambda: DEFAULT_GROUP_ROLE_MAPPING.copy()
    )
    enforce_approved_principal_types: bool = False
    propagate_idp_groups: bool = True
    authentication_source_claim: str = "authentication_source"
    canonical_rbac: CanonicalRBAC | None = None


class IdentityBridge:
    """Shared authn-to-authz canonicalization bridge.

    This bridge converts AuthPrincipal (authenticated caller identity) to
    Principal (authorization subject) using canonical RBAC subject assignments.

    In regulated mode, all approved surfaces must use this bridge to ensure
    consistent principal resolution across the platform.

    """

    def __init__(self, config: IdentityBridgeConfig | None = None) -> None:
        """Initialize the identity bridge with the given (or default) config."""
        self.config = config or IdentityBridgeConfig()

    def canonicalize(
        self,
        auth_principal: AuthPrincipal,
        context: dict[str, str] | None = None,
    ) -> Principal:
        """Convert AuthPrincipal to canonical Principal.

        Core bridge function: validates the principal type is approved (if
        enforcement is enabled), maps IdP groups to canonical roles, applies
        principal-type default roles, propagates relevant attributes, and
        returns the canonical Principal for authorization decisions. ``context``
        carries optional request/session metadata for audit. Raises ValueError
        when the principal type is not approved and enforcement is enabled.
        """
        from phlo.capabilities.interfaces import Principal

        # Validate principal type if enforcement is enabled
        if (
            self.config.enforce_approved_principal_types
            and auth_principal.principal_type not in APPROVED_PRINCIPAL_TYPES
        ):
            logger.warning(
                "unapproved_principal_type_rejected",
                subject=auth_principal.subject,
                principal_type=auth_principal.principal_type,
                approved_types=list(APPROVED_PRINCIPAL_TYPES),
            )
            raise ValueError(
                f"Principal type '{auth_principal.principal_type}' is not approved "
                f"for regulated mode. Approved types: {APPROVED_PRINCIPAL_TYPES}"
            )

        # Map groups to canonical roles
        roles = set(self._map_groups_to_roles(auth_principal.groups))
        if self.config.canonical_rbac is not None:
            roles.update(
                self.config.canonical_rbac.effective_roles_for_subject(
                    auth_principal.subject,
                    auth_principal.principal_type,
                )
            )

        # Apply principal-type default roles
        roles = self._apply_principal_type_roles(
            auth_principal.principal_type, tuple(sorted(roles))
        )

        # Build attributes (use getattr for duck-typed principals)
        principal_attributes: dict[str, str] = dict(getattr(auth_principal, "attributes", {}))
        if self.config.propagate_idp_groups:
            # Store original groups for audit
            principal_attributes["idp_groups"] = ",".join(auth_principal.groups)

        # Extract authentication source (use getattr for duck-typed principals)
        issuer = getattr(auth_principal, "issuer", None)
        authentication_source = (
            principal_attributes.get(self.config.authentication_source_claim) or issuer or "unknown"
        )
        principal_attributes["authentication_source"] = authentication_source

        principal = Principal(
            subject=auth_principal.subject,
            principal_type=auth_principal.principal_type,
            roles=roles,
            attributes=principal_attributes,
        )

        logger.debug(
            "principal_canonicalized",
            subject=principal.subject,
            principal_type=principal.principal_type,
            roles=list(principal.roles),
            authentication_source=authentication_source,
        )

        return principal

    def _map_groups_to_roles(self, groups: tuple[str, ...]) -> tuple[str, ...]:
        """Map authentication groups to canonical roles.

        Only known group names are mapped to canonical roles; unknown groups are
        discarded to prevent privilege escalation based on IdP-native group
        names. Returns the tuple of mapped canonical role names.
        """
        roles: list[str] = []
        for group in groups:
            if group in self.config.group_role_mapping:
                canonical_role = self.config.group_role_mapping[group]
                if canonical_role not in roles:
                    roles.append(canonical_role)
        return tuple(roles)

    def _apply_principal_type_roles(
        self,
        principal_type: str,
        existing_roles: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Apply default roles based on principal type.

        Service principals automatically get the 'service' role when absent;
        otherwise existing roles are returned unchanged.
        """
        if principal_type == "service" and "service" not in existing_roles:
            return (*existing_roles, "service") if existing_roles else ("service",)
        return existing_roles


def create_regulated_bridge(
    group_role_mapping: dict[str, str] | None = None,
    canonical_rbac: CanonicalRBAC | None = None,
) -> IdentityBridge:
    """Create an identity bridge configured for regulated mode.

    Convenience factory returning a bridge with regulated-mode enforcement
    enabled (empty group mapping, approved-principal-type enforcement on).
    ``group_role_mapping`` is retained only for API compatibility: regulated
    bridges never translate identity-provider groups into authorization roles.
    """
    config = IdentityBridgeConfig(
        group_role_mapping={},
        enforce_approved_principal_types=True,
        propagate_idp_groups=True,
        canonical_rbac=canonical_rbac,
    )
    return IdentityBridge(config)


def create_regulated_mode_bridge(
    group_role_mapping: dict[str, str] | None = None,
    canonical_rbac: CanonicalRBAC | None = None,
) -> IdentityBridge:
    """Deprecated: use create_regulated_bridge() instead."""
    import warnings

    warnings.warn(
        "create_regulated_mode_bridge() is deprecated, use create_regulated_bridge() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_regulated_bridge(group_role_mapping, canonical_rbac)


def canonicalize_principal(
    auth_principal: AuthPrincipal,
    regulated: bool = False,
    context: dict[str, str] | None = None,
) -> Principal:
    """Canonicalize an AuthPrincipal to a Principal.

    Convenience wrapper for simple use cases; enables regulated-mode enforcement
    when ``regulated`` is True. ``context`` carries optional request/session
    metadata. For more control, use the IdentityBridge class directly.
    """
    bridge = create_regulated_bridge() if regulated else IdentityBridge()
    return bridge.canonicalize(auth_principal, context)
