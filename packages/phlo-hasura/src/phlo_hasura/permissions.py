"""Hasura permission management and synchronization.

This module provides classes and functions for managing Hasura permissions
from configuration files. It supports YAML and JSON formats, role hierarchies,
and bulk permission operations.

Classes:
    HasuraPermissionManager: Manages permissions from config files.
    RoleHierarchy: Manages role inheritance for permission expansion.

Example:
    >>> from phlo_hasura.permissions import HasuraPermissionManager
    >>> manager = HasuraPermissionManager()
    >>> config = manager.load_config("permissions.yaml")
    >>> manager.sync_permissions(config)

"""

import json
from pathlib import Path
from typing import Any, Optional

from phlo_hasura.client import HasuraClient
from phlo.logging import get_logger

logger = get_logger(__name__)


class HasuraPermissionManager:
    """Manages Hasura permissions from YAML/JSON config files.

    Provides methods for loading permission configurations, synchronizing
    them with Hasura, and exporting current permissions back to config format.

    Example:
        >>> manager = HasuraPermissionManager()
        >>> config = manager.load_config("permissions.yaml")
        >>> manager.sync_permissions(config, verbose=True)
        >>> current = manager.export_permissions()
        >>> manager.save_permissions(current, "backup.json")
    """

    def __init__(self, client: Optional[HasuraClient] = None):
        """Initialize permission manager.

        Example:
            >>> manager = HasuraPermissionManager()
            >>> custom_manager = HasuraPermissionManager(HasuraClient())
        """
        self.client = client or HasuraClient()

    def load_config(self, config_path: str | Path) -> dict[str, Any]:
        """Load permission config from YAML or JSON file.

        Reads a permission configuration file and returns it as a dictionary.
        Supports both .json and .yaml/.yml file extensions.

        Raises: ImportError if PyYAML is required but not installed (YAML files only).
        Raises: ValueError if the file format is not supported.
        Raises: FileNotFoundError if the config file does not exist.
        Example:
            >>> config = manager.load_config("permissions.yaml")
            >>> config = manager.load_config("/path/to/config.json")
        """
        config_path = Path(config_path)

        if config_path.suffix == ".json":
            with open(config_path) as f:
                return json.load(f)

        elif config_path.suffix in [".yaml", ".yml"]:
            try:
                import yaml
            except ImportError:
                raise ImportError("PyYAML required for YAML config files")

            with open(config_path) as f:
                return yaml.safe_load(f) or {}

        else:
            raise ValueError(f"Unsupported config format: {config_path.suffix}")

    def sync_permissions(
        self,
        config: dict[str, Any],
        verbose: bool = True,
    ) -> dict[str, Any]:
        """Apply permissions from config to Hasura.

        Synchronizes permissions defined in the config dictionary with
        the actual Hasura instance. Creates or updates SELECT, INSERT,
        UPDATE, and DELETE permissions for all tables and roles specified.

        Example:
            >>> config = {
            ...     "tables": {
            ...         "api.orders": {
            ...             "select": {"anon": {"filter": {}, "columns": ["*"]}}
            ...         }
            ...     }
            ... }
            >>> results = manager.sync_permissions(config)
        """
        if verbose:
            logger.info("=" * 60)
            logger.info("Hasura Permission Sync")
            logger.info("=" * 60)

        results = {
            "select": {},
            "insert": {},
            "update": {},
            "delete": {},
        }

        tables = config.get("tables", {})

        for table_path, permissions in tables.items():
            schema, table = table_path.rsplit(".", 1)

            if verbose:
                logger.info("Syncing %s...", table_path)

            permission_syncers = {
                "select": lambda role, perm_config: self.client.create_select_permission(
                    schema,
                    table,
                    role,
                    filter=perm_config.get("filter", {}),
                    columns=perm_config.get("columns"),
                ),
                "insert": lambda role, perm_config: self.client.create_insert_permission(
                    schema,
                    table,
                    role,
                    check=perm_config.get("check", {}),
                    columns=perm_config.get("columns"),
                    set=perm_config.get("set"),
                ),
                "update": lambda role, perm_config: self.client.create_update_permission(
                    schema,
                    table,
                    role,
                    filter=perm_config.get("filter", {}),
                    check=perm_config.get("check", {}),
                    columns=perm_config.get("columns"),
                    set=perm_config.get("set"),
                ),
                "delete": lambda role, perm_config: self.client.create_delete_permission(
                    schema,
                    table,
                    role,
                    filter=perm_config.get("filter", {}),
                ),
            }

            for perm_type, sync_permission in permission_syncers.items():
                for role, perm_config in permissions.get(perm_type, {}).items():
                    if perm_config is False:
                        continue

                    try:
                        if verbose:
                            logger.info("  %s for %s...", perm_type.upper(), role)

                        sync_permission(role, perm_config)

                        results[perm_type][(table_path, role)] = True
                        if verbose:
                            logger.info("  %s for %s ✓", perm_type.upper(), role)
                    except Exception as e:
                        results[perm_type][(table_path, role)] = False
                        if verbose:
                            logger.warning(
                                "  %s for %s ✗ (%s)",
                                perm_type.upper(),
                                role,
                                str(e)[:200],
                            )

        if verbose:
            logger.info("=" * 60)
            flat_results = [ok for group in results.values() for ok in group.values()]
            success_count = sum(1 for ok in flat_results if ok)
            total_count = len(flat_results)
            logger.info("✓ Permission sync completed (%s/%s)", success_count, total_count)
            logger.info("=" * 60)

        return results

    def export_permissions(self) -> dict[str, Any]:
        """Export current Hasura permissions to config format.

        Retrieves the current permission configuration from Hasura and
        formats it as a config dictionary suitable for saving to a file.

        Example:
            >>> config = manager.export_permissions()
            >>> for table, perms in config["tables"].items():
            ...     print(f"{table}: {list(perms.keys())}")
        """
        metadata = self.client.export_metadata()

        config = {"tables": {}}

        for source in metadata.get("sources", []):
            if source.get("name") != "default":
                continue

            for table in source.get("tables", []):
                schema = table.get("table", {}).get("schema", "public")
                table_name = table["table"]["name"]
                table_path = f"{schema}.{table_name}"

                config["tables"][table_path] = {}

                # Extract permissions
                for perm_type in ["select", "insert", "update", "delete"]:
                    perm_key = f"{perm_type}_permissions"
                    perms = table.get(perm_key, [])

                    if not perms:
                        continue

                    config["tables"][table_path][perm_type] = {}

                    for perm in perms:
                        role = perm.get("role")
                        permission = perm.get("permission", {})

                        exported_permission: dict[str, Any] = {}

                        if "filter" in permission or perm_type in {"select", "update", "delete"}:
                            exported_permission["filter"] = permission.get("filter", {})
                        if "columns" in permission or perm_type in {"select", "insert", "update"}:
                            exported_permission["columns"] = permission.get("columns", ["*"])
                        if "check" in permission or perm_type in {"insert", "update"}:
                            exported_permission["check"] = permission.get("check", {})
                        if "set" in permission:
                            exported_permission["set"] = permission["set"]

                        config["tables"][table_path][perm_type][role] = exported_permission

        return config

    def save_permissions(
        self, config: dict[str, Any], output_path: str | Path, format: str = "json"
    ) -> None:
        """Save permissions to file.

        Writes a permission configuration dictionary to a file in either
        JSON or YAML format.

        Raises: ImportError if PyYAML is required but not installed (YAML format only).
        Raises: ValueError if an unsupported format is specified.
        Example:
            >>> config = manager.export_permissions()
            >>> manager.save_permissions(config, "perms.json")
            >>> manager.save_permissions(config, "perms.yaml", format="yaml")
        """
        output_path = Path(output_path)

        if format == "json":
            with open(output_path, "w") as f:
                json.dump(config, f, indent=2)

        elif format == "yaml":
            try:
                import yaml
            except ImportError:
                raise ImportError("PyYAML required for YAML format")

            with open(output_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        else:
            raise ValueError(f"Unsupported format: {format}")


class RoleHierarchy:
    """Manages role hierarchy for permission inheritance.

    Implements role-based permission inheritance where roles can inherit
    permissions from other roles. For example, an "admin" role might
    inherit all permissions from "analyst" and "anon" roles.

    Example:
        >>> hierarchy = RoleHierarchy()
        >>> inherited = hierarchy.get_inherited_roles("admin")
        >>> print(inherited)  # ['admin', 'analyst', 'anon']
        >>> expanded = hierarchy.expand_permissions(config)
    """

    def __init__(self, hierarchy: Optional[dict[str, list[str]]] = None):
        """Initialize role hierarchy.

        Example:
            >>> default = RoleHierarchy()
            >>> custom = RoleHierarchy({
            ...     "superuser": ["admin", "user"],
            ...     "admin": ["user"],
            ...     "user": []
            ... })
        """
        self.hierarchy = hierarchy or {
            "admin": ["analyst", "anon"],
            "analyst": ["anon"],
            "anon": [],
        }

    def get_inherited_roles(self, role: str) -> list[str]:
        """Get all roles inherited by a role.

        Performs a depth-first traversal of the role hierarchy to find
        all roles that the specified role inherits from, including itself.

        Example:
            >>> hierarchy = RoleHierarchy()
            >>> hierarchy.get_inherited_roles("admin")
            ['admin', 'analyst', 'anon']
            >>> hierarchy.get_inherited_roles("anon")
            ['anon']
        """
        inherited = [role]

        def visit(r: str) -> None:
            """Depth-first traversal that accumulates inherited roles."""
            for inherited_role in self.hierarchy.get(r, []):
                if inherited_role not in inherited:
                    inherited.append(inherited_role)
                    visit(inherited_role)

        visit(role)
        return inherited

    def expand_permissions(self, config: dict[str, Any]) -> dict[str, Any]:
        """Expand permissions based on role hierarchy.

        Takes a permission configuration and expands it to include
        all inherited permissions. Higher-level roles receive the
        permissions of their inherited roles.

        Example:
            >>> config = {
            ...     "tables": {
            ...         "api.users": {
            ...             "select": {"admin": {"filter": {}, "columns": ["*"]}}
            ...         }
            ...     }
            ... }
            >>> expanded = hierarchy.expand_permissions(config)
            >>> # Now includes select permissions for analyst and anon too
        """
        expanded = {"tables": {}}

        for table_path, permissions in config.get("tables", {}).items():
            expanded["tables"][table_path] = {}

            for perm_type in ["select", "insert", "update", "delete"]:
                if perm_type not in permissions:
                    continue

                expanded["tables"][table_path][perm_type] = {}

                for role, perm_config in permissions[perm_type].items():
                    inherited_roles = self.get_inherited_roles(role)

                    for inherited_role in inherited_roles:
                        if inherited_role not in expanded["tables"][table_path][perm_type]:
                            expanded["tables"][table_path][perm_type][inherited_role] = (
                                perm_config.copy()
                            )

        return expanded
