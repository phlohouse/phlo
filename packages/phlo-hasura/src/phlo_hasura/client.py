"""Hasura Metadata API client for table tracking and permission management.

This module provides the HasuraClient class for interacting with Hasura's
Metadata API v1. It handles table tracking, permission management, relationship
creation, and metadata import/export operations.

The client automatically resolves URLs and handles authentication via the
admin secret. All API calls include proper error handling and logging.

Example:
    >>> from phlo_hasura.client import HasuraClient
    >>> client = HasuraClient()
    >>> client.track_table("api", "orders")
    >>> client.create_select_permission("api", "orders", "anon")

Environment Variables:
    HASURA_ADMIN_SECRET: Hasura admin secret. Defaults to the generated
        development Compose secret when not provided.
    HASURA_PORT: Port override for Hasura URL resolution.

"""

import json
import os
from pathlib import Path
from typing import Any

import requests
from pydantic import Field
from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_url
from phlo.logging import get_logger

logger = get_logger(__name__)


class HasuraClientSettings(BaseConfig):
    """Configuration for Hasura client connectivity and authentication."""

    hasura_admin_secret: str | None = Field(
        default="phlo-hasura-admin-secret",
        description="Hasura admin secret used for Metadata API requests",
    )


@project_root_cached
def get_settings(project_root: Path) -> HasuraClientSettings:
    """Return cached Hasura client settings loaded from env and `.phlo` env files."""
    return HasuraClientSettings()


def _resolve_hasura_url(url: str) -> str:
    """Resolve a raw Hasura URL to a host-reachable endpoint, applying the
    HASURA_PORT override for Docker-internal hostnames such as 'hasura'.

    Example:
        >>> _resolve_hasura_url("http://hasura:8080")
        'http://localhost:8080'  # When running outside Docker

    """
    return resolve_url(url, port_env_var="HASURA_PORT")


class HasuraClient:
    """Client for Hasura Metadata API v1.

    Manages table tracking, permissions, relationships, and metadata
    import/export, resolving URLs and authenticating automatically.
    Exposes hasura_url (the resolved GraphQL endpoint), admin_secret, and
    the derived metadata_url.

    Example:
        >>> client = HasuraClient()
        >>> client.track_table("api", "users")
        >>> client.create_select_permission("api", "users", "anon")
        >>> metadata = client.export_metadata()

    Environment Variables:
        HASURA_ADMIN_SECRET: Hasura admin secret. Defaults to the generated
            development Compose secret when not provided.
        HASURA_PORT: Override the port in the URL.

    """

    hasura_url: str
    admin_secret: str
    metadata_url: str

    def __init__(self, hasura_url: str | None = None, admin_secret: str | None = None) -> None:
        """Initialize the client, resolving hasura_url for Docker hostnames and
        taking the admin secret from the argument, HASURA_ADMIN_SECRET, or
        project config; the generated development default logs a warning.

        Example:
            >>> client = HasuraClient()
            >>> client = HasuraClient(
            ...     hasura_url="http://custom:8080",
            ...     admin_secret="my-secret"
            ... )

        """
        raw_url = hasura_url or "http://hasura:8080"
        self.hasura_url = _resolve_hasura_url(raw_url)
        resolved_admin_secret = admin_secret or os.environ.get("HASURA_ADMIN_SECRET")
        if not resolved_admin_secret:
            resolved_admin_secret = get_settings().hasura_admin_secret
        if not resolved_admin_secret:
            raise ValueError(
                "Hasura admin secret must be provided via the 'admin_secret' argument "
                "or the HASURA_ADMIN_SECRET environment/.phlo config."
            )
        self.admin_secret = resolved_admin_secret
        if self.admin_secret == "phlo-hasura-admin-secret":
            logger.warning(
                "hasura_using_generated_default_admin_secret",
                message=(
                    "Using the generated default Hasura admin secret. "
                    "Set HASURA_ADMIN_SECRET for non-local deployments."
                ),
            )
        self.metadata_url = f"{self.hasura_url}/v1/metadata"

    def _request(
        self,
        method: str,
        data: dict[str, Any],
        query_type: str | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated metadata API request and return the response
        JSON. Raises requests.RequestException on transport failure or a
        non-2xx status, with the response body appended to the error.

        Example:
            >>> data = {
            ...     "type": "export_metadata",
            ...     "args": {}
            ... }
            >>> response = client._request("POST", data, "export_metadata")

        """
        headers = {
            "X-Hasura-Admin-Secret": self.admin_secret,
            "Content-Type": "application/json",
        }

        try:
            response = requests.request(
                method, self.metadata_url, json=data, headers=headers, timeout=30
            )
        except requests.RequestException:
            logger.exception(
                "hasura_metadata_request_transport_failed",
                method=method,
                query_type=query_type or "unknown",
                metadata_url=self.metadata_url,
            )
            raise

        if response.status_code >= 400:
            error_msg = f"Hasura API error ({query_type}): {response.status_code}"
            logger.error(
                "hasura_metadata_request_failed",
                method=method,
                query_type=query_type or "unknown",
                metadata_url=self.metadata_url,
                status_code=response.status_code,
            )
            try:
                error_data = response.json()
                error_msg += f"\n{json.dumps(error_data, indent=2)}"
            except Exception:
                error_msg += f"\n{response.text}"
            raise requests.RequestException(error_msg)

        return response.json()

    def track_table(self, schema: str, table: str, alias: str | None = None) -> dict[str, Any]:
        """Track a PostgreSQL table so it becomes available through the GraphQL
        API; alias customizes the GraphQL root field names.

        Example:
            >>> client.track_table("api", "orders")
            >>> client.track_table("api", "order_items", alias="line_items")

        """
        config_dict: dict[str, Any] = {}
        if alias:
            config_dict = {
                "custom_root_fields": {},
                "custom_column_names": {},
            }

        data: dict[str, Any] = {
            "type": "pg_track_table",
            "args": {
                "schema": schema,
                "name": table,
                "configuration": config_dict,
            },
        }

        if alias and isinstance(data["args"], dict):
            config = data["args"].get("configuration")
            if isinstance(config, dict):
                config["custom_root_fields"] = {
                    "select": alias,
                    "select_by_pk": f"{alias}_by_pk",
                    "select_aggregate": f"{alias}_aggregate",
                }

        return self._request("POST", data, f"track_table({schema}.{table})")

    def untrack_table(self, schema: str, table: str) -> dict[str, Any]:
        """Remove a previously tracked table from Hasura metadata so it is no
        longer exposed through the GraphQL API.

        Example:
            >>> client.untrack_table("api", "old_table")

        """
        data = {
            "type": "pg_untrack_table",
            "args": {
                "schema": schema,
                "table": table,
            },
        }

        return self._request("POST", data, f"untrack_table({schema}.{table})")

    def create_select_permission(
        self,
        schema: str,
        table: str,
        role: str,
        filter: dict[str, Any] | None = None,
        columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Grant SELECT on a tracked table to a role, with an optional row-level
        filter (default: all rows) and column allow-list (default: all columns).

        Example:
            >>> # Allow anon to read all rows
            >>> client.create_select_permission("api", "orders", "anon")
            >>> # Allow users to read only their own orders
            >>> client.create_select_permission(
            ...     "api", "orders", "user",
            ...     filter={"user_id": {"_eq": "X-Hasura-User-Id"}},
            ...     columns=["id", "total", "status"]
            ... )

        """
        if filter is None:
            filter = {}

        permission = {
            "columns": columns or ["*"],
            "filter": filter,
            "allow_aggregations": True,
        }

        data = {
            "type": "pg_create_select_permission",
            "args": {
                "schema": schema,
                "table": table,
                "role": role,
                "permission": permission,
            },
        }

        return self._request("POST", data, f"create_select_permission({schema}.{table}.{role})")

    def create_insert_permission(
        self,
        schema: str,
        table: str,
        role: str,
        check: dict[str, Any] | None = None,
        columns: list[str] | None = None,
        set: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Grant INSERT on a tracked table to a role, with an optional check
        expression for validation (default: none), column allow-list, and
        preset values applied to every insert.

        Example:
            >>> client.create_insert_permission("api", "orders", "user")
            >>> client.create_insert_permission(
            ...     "api", "orders", "user",
            ...     check={"status": {"_eq": "pending"}},
            ...     set={"created_by": "x-hasura-user-id"}
            ... )

        """
        if check is None:
            check = {}

        permission = {
            "columns": columns or ["*"],
            "check": check,
        }

        if set:
            permission["set"] = set

        data = {
            "type": "pg_create_insert_permission",
            "args": {
                "schema": schema,
                "table": table,
                "role": role,
                "permission": permission,
            },
        }

        return self._request("POST", data, f"create_insert_permission({schema}.{table}.{role})")

    def create_update_permission(
        self,
        schema: str,
        table: str,
        role: str,
        filter: dict[str, Any] | None = None,
        check: dict[str, Any] | None = None,
        columns: list[str] | None = None,
        set: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Grant UPDATE on a tracked table to a role; filter selects the
        updatable rows, check validates the rows after update, and set
        presets values on update.
        """
        if filter is None:
            filter = {}
        if check is None:
            check = {}

        permission: dict[str, Any] = {
            "columns": columns or ["*"],
            "filter": filter,
            "check": check,
        }

        if set:
            permission["set"] = set

        data = {
            "type": "pg_create_update_permission",
            "args": {
                "schema": schema,
                "table": table,
                "role": role,
                "permission": permission,
            },
        }

        return self._request("POST", data, f"create_update_permission({schema}.{table}.{role})")

    def create_delete_permission(
        self,
        schema: str,
        table: str,
        role: str,
        filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Grant DELETE on a tracked table to a role, restricted by an optional
        row-level filter.
        """
        if filter is None:
            filter = {}

        data = {
            "type": "pg_create_delete_permission",
            "args": {
                "schema": schema,
                "table": table,
                "role": role,
                "permission": {
                    "filter": filter,
                },
            },
        }

        return self._request("POST", data, f"create_delete_permission({schema}.{table}.{role})")

    def drop_permission(
        self, schema: str, table: str, role: str, permission_type: str = "select"
    ) -> dict[str, Any]:
        """Remove a previously granted permission from a role on a table.
        permission_type is one of select, insert, update, or delete (default
        select); unknown values raise KeyError.

        Example:
            >>> client.drop_permission("api", "orders", "anon", "select")
            >>> client.drop_permission("api", "orders", "temp_role", "insert")

        """
        type_map = {
            "select": "pg_drop_select_permission",
            "insert": "pg_drop_insert_permission",
            "update": "pg_drop_update_permission",
            "delete": "pg_drop_delete_permission",
        }

        data = {
            "type": type_map[permission_type],
            "args": {
                "schema": schema,
                "table": table,
                "role": role,
            },
        }

        return self._request(
            "POST",
            data,
            f"drop_{permission_type}_permission({schema}.{table}.{role})",
        )

    def create_object_relationship(
        self,
        schema: str,
        table: str,
        name: str,
        manual_configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a many-to-one relationship where a row of the source table
        relates to a single row of another table (e.g., order -> customer);
        manual_configuration typically carries 'foreign_key_constraint_on'.

        Example:
            >>> client.create_object_relationship(
            ...     "api", "orders", "customer",
            ...     manual_configuration={"foreign_key_constraint_on": "customer_id"}
            ... )

        """
        data = {
            "type": "pg_create_object_relationship",
            "args": {
                "schema": schema,
                "table": table,
                "name": name,
                "using": manual_configuration or {},
            },
        }

        return self._request(
            "POST",
            data,
            f"create_object_relationship({schema}.{table}.{name})",
        )

    def create_array_relationship(
        self,
        schema: str,
        table: str,
        name: str,
        manual_configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a one-to-many relationship where a row of the source table
        relates to multiple rows of another table (e.g., customer -> orders);
        manual_configuration typically carries 'foreign_key_constraint_on'
        with table and column.

        Example:
            >>> client.create_array_relationship(
            ...     "api", "customers", "orders",
            ...     manual_configuration={
            ...         "foreign_key_constraint_on": {
            ...             "table": "orders",
            ...             "column": "customer_id"
            ...         }
            ...     }
            ... )

        """
        data = {
            "type": "pg_create_array_relationship",
            "args": {
                "schema": schema,
                "table": table,
                "name": name,
                "using": manual_configuration or {},
            },
        }

        return self._request(
            "POST",
            data,
            f"create_array_relationship({schema}.{table}.{name})",
        )

    def export_metadata(self) -> dict[str, Any]:
        """Export the complete Hasura metadata: tracked tables, relationships,
        permissions, event triggers, remote schemas, actions, and more.

        Example:
            >>> metadata = client.export_metadata()
            >>> len(metadata.get("sources", []))
            1

        """
        data = {"type": "export_metadata", "args": {}}
        return self._request("POST", data, "export_metadata")

    def apply_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Replace the current Hasura metadata with the given dictionary,
        removing any existing metadata not present in the input.

        Example:
            >>> metadata = client.export_metadata()
            >>> # Modify metadata...
            >>> response = client.apply_metadata(metadata)

        """
        data = {"type": "replace_metadata", "args": {"metadata": metadata}}
        return self._request("POST", data, "apply_metadata")

    def reload_metadata(self) -> dict[str, Any]:
        """Force Hasura to reload its metadata from the database, e.g. after
        schema changes made outside Hasura.

        Example:
            >>> client.reload_metadata()  # After manual DB schema changes

        """
        data = {"type": "reload_metadata", "args": {}}
        return self._request("POST", data, "reload_metadata")

    def get_tables(self, schema: str) -> list[str]:
        """Return the names of all tables tracked in a schema, read from the
        current metadata.

        Example:
            >>> tables = client.get_tables("api")
            >>> print(tables)
            ['orders', 'customers', 'products']

        """
        metadata = self.export_metadata()

        tables = []
        for source in metadata.get("sources", []):
            if source.get("name") == "default":
                for table in source.get("tables", []):
                    if table.get("table", {}).get("schema") == schema:
                        tables.append(table["table"]["name"])

        return tables

    def get_tracked_tables(self) -> dict[str, list[str]]:
        """Return tracked table names grouped by schema across all data
        sources, e.g. {"api": ["orders", "customers"], "public": ["users"]}.

        Example:
            >>> tracked = client.get_tracked_tables()
            >>> for schema, tables in tracked.items():
            ...     print(f"{schema}: {len(tables)} tables")

        """
        metadata = self.export_metadata()
        tracked = {}

        for source in metadata.get("sources", []):
            if source.get("name") == "default":
                for table in source.get("tables", []):
                    schema = table.get("table", {}).get("schema", "public")
                    table_name = table["table"]["name"]

                    if schema not in tracked:
                        tracked[schema] = []

                    tracked[schema].append(table_name)

        return tracked
