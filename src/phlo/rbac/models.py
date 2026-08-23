"""Canonical RBAC configuration models.

This module provides data classes for the canonical RBAC model defined in
Spec 0017: RBAC Core Services Enforcement And Policy Sync.

The canonical RBAC model consists of:
- roles.yaml: role hierarchy and subject assignments
- policies.yaml: canonical product-level permissions
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PolicyEffect(StrEnum):
    """Policy effect types."""

    ALLOW = "allow"
    DENY = "deny"


class CanonicalAction(StrEnum):
    """Canonical actions defined in the RBAC model."""

    DATASET_READ = "dataset.read"
    DATASET_QUERY = "dataset.query"
    DATASET_WRITE = "dataset.write"
    DATASET_PUBLISH = "dataset.publish"
    ASSET_READ = "asset.read"
    ASSET_EXECUTE = "asset.execute"
    ASSET_MANAGE = "asset.manage"
    ASSET_APPROVE = "asset.approve"
    SERVICE_READ = "service.read"
    SERVICE_MANAGE = "service.manage"
    ADMIN_READ = "admin.read"
    ADMIN_MANAGE = "admin.manage"
    SETTINGS_READ = "settings.read"
    SETTINGS_MANAGE = "settings.manage"
    OBJECT_READ = "object.read"
    OBJECT_WRITE = "object.write"
    CATALOG_READ = "catalog.read"
    CATALOG_MANAGE = "catalog.manage"
    PLATFORM_METADATA_READ = "platform_metadata.read"
    OBSERVABILITY_READ = "observability.read"
    MAINTENANCE_READ = "maintenance.read"
    RUN_READ = "run.read"
    RUN_EXECUTE = "run.execute"
    RUN_MANAGE = "run.manage"
    AUDIT_READ = "audit.read"


class ResourceType(StrEnum):
    """Canonical resource types."""

    PROJECT = "project"
    PACKAGE = "package"
    DATASET = "dataset"
    ASSET = "asset"
    SERVICE = "service"
    ADMIN = "admin"
    SETTINGS = "settings"
    OBJECT = "object"
    CATALOG = "catalog"
    PLATFORM_METADATA = "platform_metadata"
    OBSERVABILITY = "observability"
    MAINTENANCE = "maintenance"
    RUN = "run"
    AUDIT = "audit"


CANONICAL_ACTIONS: frozenset[str] = frozenset(a.value for a in CanonicalAction)


@dataclass(frozen=True)
class Role:
    """A role in the canonical RBAC model."""

    name: str
    inherits: tuple[str, ...] = ()
    description: str | None = None


@dataclass(frozen=True)
class SubjectAssignment:
    """Assignment of subjects to roles."""

    services: dict[str, list[str]] = field(default_factory=dict)
    users: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RolesConfig:
    """Canonical roles configuration (roles.yaml)."""

    version: int
    roles: Mapping[str, Role]
    subjects: SubjectAssignment = field(default_factory=SubjectAssignment)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RolesConfig:
        """Parse roles configuration from a dictionary."""
        version = data.get("version", 1)
        roles_data = data.get("roles", {})
        subjects_data = data.get("subjects", {})

        roles: dict[str, Role] = {}
        for name, config in roles_data.items():
            inherits = tuple(config.get("inherits", []))
            description = config.get("description")
            roles[name] = Role(name=name, inherits=inherits, description=description)

        services = subjects_data.get("services", {})
        users = subjects_data.get("users", {})
        subjects = SubjectAssignment(
            services=dict(services),
            users=dict(users),
        )

        return cls(version=version, roles=roles, subjects=subjects)

    def expand_role_hierarchy(self, role_name: str) -> tuple[str, ...]:
        """Expand a role to include all inherited roles.

        Returns the role name plus every ancestor. Raise ValueError when the role
        does not exist or the hierarchy contains a cycle.
        """
        if role_name not in self.roles:
            raise ValueError(f"Unknown role: {role_name}")

        # `path` is the current DFS stack and detects inheritance cycles;
        # `result_set` memoizes fully expanded roles so diamond hierarchies
        # contribute each role once and expansion terminates.
        path: list[str] = []
        result_set: set[str] = set()

        def _expand(name: str) -> list[str]:
            if name in path:
                raise ValueError(f"Cycle detected in role hierarchy: {' -> '.join(path)} -> {name}")

            if name in result_set:
                return []

            if name not in self.roles:
                raise ValueError(f"Role '{name}' referenced in hierarchy does not exist")

            path.append(name)
            result_set.add(name)

            role = self.roles[name]
            result = [name]
            for parent in role.inherits:
                result.extend(_expand(parent))

            path.pop()
            return result

        return tuple(_expand(role_name))

    def get_effective_roles(self, role_name: str) -> frozenset[str]:
        """Collect all effective roles for a role, including inherited ones."""
        return frozenset(self.expand_role_hierarchy(role_name))


@dataclass(frozen=True)
class PolicyRule:
    """A single policy rule."""

    policy_id: str
    effect: PolicyEffect
    principal_roles: tuple[str, ...]
    principal_attributes: Mapping[str, str]
    action: str
    resource_type: str
    resource_id_pattern: str
    resource_attributes: Mapping[str, str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyRule:
        """Parse a policy rule from a dictionary."""
        effect = PolicyEffect(data.get("effect", "deny"))
        principal = data.get("principal", {})
        resource = data.get("resource", {})

        return cls(
            policy_id=data["policy_id"],
            effect=effect,
            principal_roles=tuple(principal.get("roles", [])),
            principal_attributes=principal.get("attributes", {}),
            action=data.get("action", "*"),
            resource_type=resource.get("type", "*"),
            resource_id_pattern=resource.get("id_pattern", "*"),
            resource_attributes=resource.get("attributes", {}),
        )


@dataclass(frozen=True)
class PoliciesConfig:
    """Canonical policies configuration (policies.yaml)."""

    version: int
    policies: tuple[PolicyRule, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PoliciesConfig:
        """Parse policies configuration from a dictionary."""
        version = data.get("version", 1)
        policies_data = data.get("policies", [])

        policies = tuple(PolicyRule.from_dict(p) for p in policies_data)
        return cls(version=version, policies=policies)

    def _action_matches(self, pattern: str, action: str) -> bool:
        """Check if action matches pattern (supports wildcards)."""
        import fnmatch

        if pattern == "*":
            return True
        return fnmatch.fnmatch(action, pattern)


@dataclass(frozen=True)
class CanonicalRBAC:
    """Combined canonical RBAC model."""

    roles: RolesConfig
    policies: PoliciesConfig
    version_hash: str | None = None

    @classmethod
    def from_configs(
        cls,
        roles: RolesConfig,
        policies: PoliciesConfig,
    ) -> CanonicalRBAC:
        """Create a canonical RBAC model from configs."""
        import hashlib
        import json

        content = json.dumps(
            {
                "roles_version": roles.version,
                "policies_version": policies.version,
                "roles": {k: {"inherits": v.inherits} for k, v in roles.roles.items()},
                "subjects": {
                    "services": dict(roles.subjects.services),
                    "users": dict(roles.subjects.users),
                },
                "policies": [
                    {
                        "policy_id": p.policy_id,
                        "effect": p.effect.value,
                        "principal_roles": p.principal_roles,
                        "principal_attributes": dict(p.principal_attributes),
                        "action": p.action,
                        "resource_type": p.resource_type,
                        "resource_id_pattern": p.resource_id_pattern,
                        "resource_attributes": dict(p.resource_attributes),
                    }
                    for p in policies.policies
                ],
            },
            sort_keys=True,
        )
        version_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        return cls(roles=roles, policies=policies, version_hash=version_hash)

    def validate(self) -> list[str]:
        """Validate the canonical RBAC model.

        Returns a list of validation errors, empty when the model is valid.
        """
        errors: list[str] = []

        unsupported_effects = {
            policy.effect.value
            for policy in self.policies.policies
            if policy.effect != PolicyEffect.ALLOW
        }
        for effect in sorted(unsupported_effects):
            errors.append(
                "Canonical RBAC does not support "
                f"{effect!r} policies yet. Remove those rules until backend compilation "
                "semantics are implemented."
            )

        # Check that all principal roles exist
        for policy in self.policies.policies:
            for role in policy.principal_roles:
                if role not in self.roles.roles:
                    errors.append(f"Policy {policy.policy_id} references unknown role: {role}")

        # Check for cycles in role hierarchy
        for role_name in self.roles.roles:
            try:
                self.roles.expand_role_hierarchy(role_name)
            except ValueError as e:
                errors.append(str(e))

        return errors

    def effective_roles_for_subject(self, subject: str, principal_type: str) -> frozenset[str]:
        """Return assigned roles plus their inherited roles for one principal."""
        # Platform principals share the service assignment table.
        assignments = (
            self.roles.subjects.services
            if principal_type in {"service", "platform"}
            else self.roles.subjects.users
        )
        effective: set[str] = set()
        for role_name in assignments.get(subject, []):
            effective.update(self.roles.get_effective_roles(role_name))
        return frozenset(effective)


@dataclass(frozen=True)
class BackendArtifact:
    """A compiled backend artifact (role, grant, policy, etc.)."""

    backend: str
    artifact_type: str  # e.g., "role", "grant", "policy"
    name: str
    statement: str  # SQL or JSON statement
    managed: bool = True  # Whether Phlo manages this object
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyChange:
    """A planned policy change."""

    change_type: str  # "create", "update", "delete"
    backend: str
    artifact: BackendArtifact
    revert_id: str | None = None


@dataclass(frozen=True)
class SyncPlan:
    """A planned synchronization between canonical RBAC and backend."""

    version_hash: str
    backend: str
    changes: tuple[PolicyChange, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    """Result of a sync operation."""

    success: bool
    backend: str
    version_hash: str
    applied_count: int
    failed_count: int
    errors: tuple[str, ...]
    revert_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifyResult:
    """Result of a verification operation."""

    backend: str
    in_sync: bool
    missing: tuple[BackendArtifact, ...] = ()
    extra: tuple[BackendArtifact, ...] = ()
    mismatched: tuple[BackendArtifact, ...] = ()
