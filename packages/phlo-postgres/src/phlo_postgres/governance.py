"""PostgreSQL governance backend for access control via SQL grants.

Implements the GovernanceBackend protocol over ``PostgresResource``:
apply/revoke emit GRANT/REVOKE/ALTER DEFAULT PRIVILEGES, and
``list_policies`` returns managed grants as typed rows the core
``PostgresCompiler`` diffs for plan and verify.
"""

from __future__ import annotations

import re
from typing import Any

from phlo.capabilities.interfaces import AccessPolicy
from phlo.logging import get_logger
from phlo_postgres.resource import PostgresResource

logger = get_logger(__name__)

_ALLOWED_PRIVILEGES = frozenset(
    {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "USAGE", "MAINTAIN"}
)
_PREDEFINED_ROLES = frozenset({"pg_read_all_data", "pg_write_all_data"})
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_RESOURCE_RE = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*$"
    r"|^[a-zA-Z_][a-zA-Z0-9_]*\.\*$"
    r"|^\*$"
)
_MANAGED_ROLE_RE = "phlo\\_%"


def _quote_identifier(identifier: str) -> str:
    """Quote one validated SQL identifier."""
    if not _IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Invalid identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _split_resource(pattern: str) -> tuple[str, str | None]:
    """Split a resource pattern into (schema, table-or-None-for-wildcards)."""
    if not _RESOURCE_RE.match(pattern):
        raise ValueError(f"Invalid resource pattern: {pattern!r}")
    if pattern == "*":
        return "*", None
    if pattern.endswith(".*"):
        return pattern[:-2], None
    schema, _, table = pattern.partition(".")
    return schema, table


class PostgresGovernanceBackend:
    """GovernanceBackend implementation using PostgreSQL GRANT/REVOKE."""

    def __init__(self, postgres: PostgresResource | None = None) -> None:
        """Initialize with an optional Postgres resource."""
        self._pg = postgres or PostgresResource()

    def probe(self) -> bool:
        """Return whether postgres answers a trivial read (readiness evidence)."""
        try:
            row = self._pg.query_one("SELECT 1")
        except Exception:
            return False
        return bool(row)

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
        """List managed grants as typed rows.

        Row kinds: ``table_grant`` (one row per concrete table grant),
        ``schema_usage`` (USAGE on a schema), ``all_tables`` (one row per
        managed role/schema/privilege carrying ``complete`` — whether the
        role holds the privilege on every current table in the schema),
        ``default_privileges`` (default ACLs for future tables), and
        ``role_membership`` (predefined ``pg_*_all_data`` memberships).
        """
        rows: list[dict[str, Any]] = []
        table_filter = ""
        params: tuple[Any, ...] = ()
        if table_name is not None:
            _split_resource(table_name)
            table_filter = "AND (g.table_schema || '.' || g.table_name) = %s"
            params = (table_name,)
        grant_rows = self._pg.query(
            f"""
            SELECT g.grantee, g.table_schema, g.table_name, g.privilege_type
            FROM information_schema.role_table_grants g
            WHERE g.grantee LIKE %s ESCAPE '\\' {table_filter}
            ORDER BY g.grantee, g.table_schema, g.table_name, g.privilege_type
            """,
            (_MANAGED_ROLE_RE, *params),
        )
        for grantee, schema, table, privilege in grant_rows:
            rows.append(
                {
                    "kind": "table_grant",
                    "grantee": grantee,
                    "schema": schema,
                    "table": table,
                    "privilege": privilege,
                }
            )

        coverage_rows = self._pg.query(
            """
            SELECT g.grantee, g.table_schema, g.privilege_type,
                   bool_and(
                       has_table_privilege(
                           g.grantee,
                           quote_ident(t.table_schema) || '.' || quote_ident(t.table_name),
                           g.privilege_type
                       )
                   ) AS complete
            FROM (
                SELECT DISTINCT grantee, table_schema, privilege_type
                FROM information_schema.role_table_grants
                WHERE grantee LIKE %s ESCAPE '\\'
            ) g
            JOIN information_schema.tables t ON t.table_schema = g.table_schema
            GROUP BY g.grantee, g.table_schema, g.privilege_type
            """,
            (_MANAGED_ROLE_RE,),
        )
        for grantee, schema, privilege, complete in coverage_rows:
            rows.append(
                {
                    "kind": "all_tables",
                    "grantee": grantee,
                    "schema": schema,
                    "table": "",
                    "privilege": privilege,
                    "complete": bool(complete),
                }
            )

        usage_rows = self._pg.query(
            """
            SELECT grantee.rolname, n.nspname, acl.privilege_type
            FROM pg_namespace n
            CROSS JOIN LATERAL aclexplode(n.nspacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE acl.privilege_type = 'USAGE' AND grantee.rolname LIKE %s ESCAPE '\\'
            """,
            (_MANAGED_ROLE_RE,),
        )
        for grantee, schema, privilege in usage_rows:
            rows.append(
                {
                    "kind": "schema_usage",
                    "grantee": grantee,
                    "schema": schema,
                    "table": "",
                    "privilege": privilege,
                }
            )

        default_rows = self._pg.query(
            """
            SELECT grantee.rolname, n.nspname, acl.privilege_type
            FROM pg_default_acl d
            JOIN pg_namespace n ON n.oid = d.defaclnamespace
            CROSS JOIN LATERAL aclexplode(d.defaclacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE d.defaclobjtype = 'r' AND grantee.rolname LIKE %s ESCAPE '\\'
            """,
            (_MANAGED_ROLE_RE,),
        )
        for grantee, schema, privilege in default_rows:
            rows.append(
                {
                    "kind": "default_privileges",
                    "grantee": grantee,
                    "schema": schema,
                    "table": "",
                    "privilege": privilege,
                }
            )

        membership_rows = self._pg.query(
            """
            SELECT member.rolname, parent.rolname
            FROM pg_auth_members m
            JOIN pg_roles member ON member.oid = m.member
            JOIN pg_roles parent ON parent.oid = m.roleid
            WHERE member.rolname LIKE %s ESCAPE '\\'
              AND parent.rolname IN ('pg_read_all_data', 'pg_write_all_data')
            """,
            (_MANAGED_ROLE_RE,),
        )
        for grantee, parent_role in membership_rows:
            rows.append(
                {
                    "kind": "role_membership",
                    "grantee": grantee,
                    "schema": "",
                    "table": "",
                    "privilege": parent_role,
                }
            )
        return rows

    def apply_policy(self, *, policy: AccessPolicy) -> None:
        """Apply one canonical grant as PostgreSQL statements.

        The policy id encodes the artifact scope (the core compiler names
        artifacts ``<role>__<resource>__<priv>`` with ``__DEFAULT`` for
        default privileges); the resource pattern selects the statement
        shape: ``*`` grants a predefined role, ``schema.*`` grants USAGE or
        ALL TABLES plus default privileges, ``schema.table`` grants on one
        table.
        """
        role = policy.principal
        _quote_identifier(role)
        scope_hint = str(policy.policy_id or "")
        for privilege in [p.strip() for p in policy.action.split(",") if p.strip()]:
            self._apply_privilege(privilege, policy.table_pattern, role, scope_hint)
        logger.info(
            "postgres_governance_apply_policy",
            principal=role,
            resource=policy.table_pattern,
            action=policy.action,
        )

    def _apply_privilege(self, privilege: str, pattern: str, role: str, scope_hint: str) -> None:
        schema, table = _split_resource(pattern)
        role_ref = _quote_identifier(role)
        if pattern == "*":
            if privilege not in _PREDEFINED_ROLES:
                raise ValueError(f"Unsupported predefined role: {privilege!r}")
            self._pg.execute(f"GRANT {privilege} TO {role_ref}")
            return
        if privilege not in _ALLOWED_PRIVILEGES:
            raise ValueError(f"Unsupported privilege: {privilege!r}")
        schema_ref = _quote_identifier(schema)
        if table is None:
            if privilege == "USAGE" or scope_hint.endswith("__USAGE"):
                self._pg.execute(f"GRANT USAGE ON SCHEMA {schema_ref} TO {role_ref}")
                return
            if scope_hint.endswith("__DEFAULT"):
                self._pg.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_ref} "
                    f"GRANT {privilege} ON TABLES TO {role_ref}"
                )
                return
            self._pg.execute(
                f"GRANT {privilege} ON ALL TABLES IN SCHEMA {schema_ref} TO {role_ref}"
            )
            return
        table_ref = f"{schema_ref}.{_quote_identifier(table)}"
        self._pg.execute(f"GRANT {privilege} ON TABLE {table_ref} TO {role_ref}")

    def revoke_policy(self, *, policy_id: str) -> None:
        """Revoke a grant. policy_id: ``SCOPE:PRIVILEGE:RESOURCE:ROLE``."""
        parts = policy_id.split(":")
        if len(parts) != 4:
            raise ValueError(f"policy_id must be 'SCOPE:PRIVILEGE:RESOURCE:ROLE', got: {policy_id}")
        scope, privilege, pattern, role = parts
        role_ref = _quote_identifier(role)
        schema, table = _split_resource(pattern)
        if pattern == "*":
            if privilege not in _PREDEFINED_ROLES:
                raise ValueError(f"Unsupported predefined role: {privilege!r}")
            self._pg.execute(f"REVOKE {privilege} FROM {role_ref}")
            return
        if privilege not in _ALLOWED_PRIVILEGES:
            raise ValueError(f"Unsupported privilege: {privilege!r}")
        schema_ref = _quote_identifier(schema)
        if scope == "schema_usage" or privilege == "USAGE":
            self._pg.execute(f"REVOKE USAGE ON SCHEMA {schema_ref} FROM {role_ref}")
            return
        if scope == "default_privileges":
            self._pg.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_ref} "
                f"REVOKE {privilege} ON TABLES FROM {role_ref}"
            )
            return
        if table is None:
            self._pg.execute(
                f"REVOKE {privilege} ON ALL TABLES IN SCHEMA {schema_ref} FROM {role_ref}"
            )
            return
        self._pg.execute(
            f"REVOKE {privilege} ON TABLE {schema_ref}.{_quote_identifier(table)} FROM {role_ref}"
        )
        logger.info("postgres_governance_revoke_policy", policy_id=policy_id)

    def check_access(self, *, principal: str, table_name: str, action: str) -> bool:
        """Return whether principal holds the privilege on the table."""
        _quote_identifier(principal)
        _split_resource(table_name)
        privilege = action.split(",")[0].strip().upper()
        if privilege not in _ALLOWED_PRIVILEGES:
            raise ValueError(f"Unsupported privilege: {privilege!r}")
        row = self._pg.query_one(
            "SELECT has_table_privilege(%s, %s, %s)",
            (principal, table_name, privilege),
        )
        return bool(row and row[0])
