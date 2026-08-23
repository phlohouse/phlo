"""CLI regulated surface adapter.

This module provides the regulated surface adapter for the Phlo CLI,
declaring mutation commands that require authorization and enforcing
through core enforcement.

Principal resolution strategy:
- Interactive human: PHLO_AUTH_SUBJECT, PHLO_AUTH_TYPE, PHLO_AUTH_GROUPS env vars
- CI/automation: PHLO_SERVICE_ACCOUNT env var (service principal)
- Open-mode local dev fallback: PHLO_DEV_MODE grants local:root with warning

Mutation commands (require authorization):
- services init/start/stop/add/remove/reset/exec/restart
- plugin create/install/update
- authz sync/revert
- migrate run/write and schema migration write/apply commands
- init

Read commands (no authorization):
- services status/list/logs/ports
- plugin list/info/search/check
- authz validate/plan/verify
- config/env/metrics
- workflow (read-only operations)
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

from phlo.logging import get_logger
from phlo.security.adapters import (
    EnforcementResult,
    SurfaceOperation,
)
from phlo.security.enforcement import enforce

if TYPE_CHECKING:
    from phlo.capabilities.interfaces import AuthPrincipal

logger = get_logger(__name__)

SURFACE_NAME = "phlo-cli"
FRAMEWORK_TYPE = "cli"

MUTATION_COMMANDS: frozenset[str] = frozenset(
    {
        "services.start",
        "services.stop",
        "services.add",
        "services.init",
        "services.remove",
        "services.reset",
        "services.exec",
        "services.restart",
        "plugin.create",
        "plugin.install",
        "plugin.update",
        "authz.sync",
        "authz.revert",
        "migrate.decorators_2026_05",
        "migrate.run",
        "schema_migrate.apply",
        "schema_migrate.export_contract",
        "schema_migrate.scaffold_yaml",
        "schema_migrate.scaffold_yaml_recent",
        "init",
    }
)

READ_COMMANDS: frozenset[str] = frozenset(
    {
        "services.status",
        "services.list",
        "services.logs",
        "services.ports",
        "plugin.list",
        "plugin.info",
        "plugin.search",
        "plugin.check",
        "authz.validate",
        "authz.plan",
        "authz.verify",
        "migrate.validate",
        "migrate.list",
        "migrate.status",
        "schema_migrate.diff",
        "schema_migrate.plan",
        "schema_migrate.history",
        "config",
        "env",
        "metrics",
        "workflow",
    }
)

COMMAND_RESOURCE_MAP: dict[str, str] = {
    "services.start": "infrastructure",
    "services.stop": "infrastructure",
    "services.add": "infrastructure",
    "services.init": "infrastructure",
    "services.remove": "infrastructure",
    "services.reset": "infrastructure",
    "services.exec": "infrastructure",
    "services.restart": "infrastructure",
    "plugin.create": "plugin",
    "plugin.install": "plugin",
    "plugin.update": "plugin",
    "authz.sync": "rbac_policy",
    "authz.revert": "rbac_policy",
    "migrate.decorators_2026_05": "source_code",
    "migrate.run": "migration",
    "schema_migrate.apply": "schema",
    "schema_migrate.export_contract": "schema_contract",
    "schema_migrate.scaffold_yaml": "schema_migration",
    "schema_migrate.scaffold_yaml_recent": "schema_migration",
    "init": "project",
}

COMMAND_ACTION_MAP: dict[str, str] = {
    "services.start": "infrastructure.start",
    "services.stop": "infrastructure.stop",
    "services.add": "infrastructure.configure",
    "services.init": "infrastructure.configure",
    "services.remove": "infrastructure.remove",
    "services.reset": "infrastructure.reset",
    "services.exec": "infrastructure.exec",
    "services.restart": "infrastructure.restart",
    "plugin.create": "plugin.create",
    "plugin.install": "plugin.install",
    "plugin.update": "plugin.update",
    "authz.sync": "rbac.sync",
    "authz.revert": "rbac.revert",
    "migrate.decorators_2026_05": "source_code.migrate",
    "migrate.run": "migration.run",
    "schema_migrate.apply": "schema.migrate",
    "schema_migrate.export_contract": "schema_contract.export",
    "schema_migrate.scaffold_yaml": "schema_migration.scaffold",
    "schema_migrate.scaffold_yaml_recent": "schema_migration.scaffold",
    "init": "project.create",
}


class CliPrincipalResolver:
    """Resolves CLI principal from execution environment.

    Strategy:
    - PHLO_SERVICE_ACCOUNT set -> service principal (CI/automation)
    - PHLO_AUTH_SUBJECT + PHLO_AUTH_TYPE -> human principal
    - PHLO_AUTH_SUBJECT=local:root -> local dev fallback outside regulated mode
    """

    @staticmethod
    def resolve() -> AuthPrincipal:
        """Resolve AuthPrincipal from environment."""
        from phlo.capabilities.interfaces import AuthPrincipal

        service_account = os.environ.get("PHLO_SERVICE_ACCOUNT")
        if service_account:
            return AuthPrincipal(
                subject=service_account,
                principal_type="service",
                issuer="env:PHLO_SERVICE_ACCOUNT",
                groups=("operators",),
                attributes={"authentication_source": "service_account"},
            )

        subject = os.environ.get("PHLO_AUTH_SUBJECT")
        auth_type = os.environ.get("PHLO_AUTH_TYPE", "user")
        groups_raw = os.environ.get("PHLO_AUTH_GROUPS", "")

        if subject:
            groups = (
                tuple(g.strip() for g in groups_raw.split(",") if g.strip()) if groups_raw else ()
            )
            return AuthPrincipal(
                subject=subject,
                principal_type=auth_type,
                issuer="env:PHLO_AUTH_*",
                groups=groups,
                attributes={"authentication_source": "env"},
            )

        local_dev_fallback = os.environ.get("PHLO_DEV_MODE")
        if local_dev_fallback:
            if _is_regulated_for_principal_resolution():
                logger.warning(
                    "cli_authorization_dev_fallback_disabled",
                    message="PHLO_DEV_MODE cannot grant CLI admin identity in regulated mode.",
                )
                return AuthPrincipal(
                    subject="anonymous",
                    principal_type="user",
                    issuer="cli:default",
                    groups=(),
                    attributes={"authentication_source": "default"},
                )

            logger.warning(
                "cli_authorization_dev_fallback",
                message="Using open-mode dev fallback principal. Set PHLO_AUTH_SUBJECT for regulated mode.",
            )
            return AuthPrincipal(
                subject="local:root",
                principal_type="user",
                issuer="dev:PHLO_DEV_MODE",
                groups=("admin",),
                attributes={"authentication_source": "dev_fallback"},
            )

        return AuthPrincipal(
            subject="anonymous",
            principal_type="user",
            issuer="cli:default",
            groups=(),
            attributes={"authentication_source": "default"},
        )


def _is_regulated_for_principal_resolution() -> bool:
    """Return regulated mode for identity fallback decisions, failing closed."""
    try:
        from phlo.security.mode import is_regulated

        return is_regulated()
    except Exception:
        logger.warning("cli_regulated_mode_detection_failed", exc_info=True)
        return True


class CliSurfaceAdapter:
    """Regulated surface adapter for Phlo CLI.

    Declares mutation commands and enforces authorization through core
    enforcement for privileged operations.
    """

    _instance: CliSurfaceAdapter | None = None
    _lock = threading.Lock()

    surface_name_value = SURFACE_NAME
    framework_type_value = FRAMEWORK_TYPE
    mutation_commands = MUTATION_COMMANDS
    read_commands = READ_COMMANDS
    command_resource_map = COMMAND_RESOURCE_MAP
    command_action_map = COMMAND_ACTION_MAP
    default_resource_type = "cli_command"
    default_action_prefix = "cli"
    default_resource_prefix = "cli"

    def __init__(self) -> None:
        self._resolver = CliPrincipalResolver()

    @classmethod
    def get_instance(cls) -> CliSurfaceAdapter:
        """Return the process-wide singleton adapter, creating it on first use."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def surface_name(self) -> str:
        """Return the regulated surface identifier for this adapter."""
        return self.surface_name_value

    @property
    def framework_type(self) -> str:
        """Return the framework type this adapter registers under."""
        return self.framework_type_value

    def list_operations(self) -> list[SurfaceOperation]:
        """Describe every mutation command as a regulatable surface operation."""
        operations: list[SurfaceOperation] = []
        for command in self.mutation_commands:
            resource_type = self.command_resource_map.get(command, self.default_resource_type)
            action = self.command_action_map.get(command, f"{self.default_action_prefix}.{command}")
            operations.append(
                SurfaceOperation(
                    action=action,
                    resource_type=resource_type,
                    operation_name=command,
                    resource_id_strategy=None,
                    framework_metadata={"command": command},
                )
            )
        return operations

    def is_active(self, runtime: Any) -> bool:
        """Return True; the CLI surface is always active."""
        return True

    def install(self, runtime: Any) -> None:
        """No-op; the CLI needs no runtime installation."""

    def enforce_mutation(self, command: str, resource_id: str | None = None) -> EnforcementResult:
        """Enforce authorization for a mutation command.

        Non-mutation commands are allowed without enforcement; the command is
        mapped to its action/resource and checked against the resolved
        principal. Returns an allow, deny, or error EnforcementResult.
        """
        if command not in self.mutation_commands:
            return EnforcementResult.allow()

        action = self.command_action_map.get(command, f"{self.default_action_prefix}.{command}")
        resource_type = self.command_resource_map.get(command, self.default_resource_type)
        resource_id_final = resource_id or f"{self.default_resource_prefix}:{command}"

        principal = self._resolver.resolve()

        from phlo.capabilities.interfaces import ResourceRef

        resource = ResourceRef(
            resource_type=resource_type,
            resource_id=resource_id_final,
        )

        request_id = os.environ.get("PHLO_REQUEST_ID")

        result = self._enforce(
            principal=principal,
            action=action,
            resource=resource,
            context=None,
            request_id=request_id,
            surface=self.surface_name,
        )

        logger.debug(
            "cli_mutation_enforcement_result",
            surface=self.surface_name,
            command=command,
            action=action,
            result=result.variant,
            subject=principal.subject,
        )

        return result

    def _enforce(self, **kwargs: Any) -> EnforcementResult:
        return enforce(**kwargs)

    def check_command_authorization(self, command_path: str) -> EnforcementResult:
        """Check if a command is authorized to run.

        Read commands pass without enforcement and mutation commands go through
        full enforcement. Commands missing from both tables are denied, so a
        newly added command cannot skip authorization by not being registered.
        """
        if command_path in self.read_commands:
            return EnforcementResult.allow()

        if command_path in self.mutation_commands:
            return self.enforce_mutation(command_path)

        logger.warning(
            "cli_unknown_command_classification",
            surface=self.surface_name,
            command=command_path,
        )
        return EnforcementResult.deny(
            reason_code="unknown_command",
            explanation=f"Command '{command_path}' is not classified as read or mutation",
        )


def get_cli_adapter() -> CliSurfaceAdapter:
    """Get the singleton CLI surface adapter."""
    return CliSurfaceAdapter.get_instance()


def cli_surface_adapter_class(
    class_name: str,
    *,
    surface_name: str,
    mutation_commands: frozenset[str] = frozenset(),
    read_commands: frozenset[str] = frozenset(),
    command_resource_map: dict[str, str] | None = None,
    command_action_map: dict[str, str] | None = None,
    default_resource_type: str = "dataset",
    default_action_prefix: str = "cli",
    default_resource_prefix: str | None = None,
) -> type[CliSurfaceAdapter]:
    """Create a package-specific CLI surface adapter from command tables."""
    resource_map = command_resource_map or {}
    action_map = command_action_map or {}
    resource_prefix = default_resource_prefix or surface_name.removeprefix("phlo-")

    return type(
        class_name,
        (CliSurfaceAdapter,),
        {
            "surface_name_value": surface_name,
            "mutation_commands": mutation_commands,
            "read_commands": read_commands,
            "command_resource_map": resource_map,
            "command_action_map": action_map,
            "default_resource_type": default_resource_type,
            "default_action_prefix": default_action_prefix,
            "default_resource_prefix": resource_prefix,
            "__module__": __name__,
        },
    )
