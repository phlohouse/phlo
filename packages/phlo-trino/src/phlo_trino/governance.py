"""Trino governance backend for access control via SQL grants.

This module implements the GovernanceBackend interface using Trino's
native SQL GRANT/DENY/REVOKE commands for access control management.

Classes:
    TrinoGovernanceBackend: Governance backend for Trino access control.

Functions:
    _validate_identifier: Validate SQL identifiers to prevent injection.

Constants:
    _ALLOWED_ACTIONS: Set of supported SQL privilege actions.

Example:
    >>> from phlo_trino.governance import TrinoGovernanceBackend
    >>> from phlo.capabilities.interfaces import AccessPolicy
    >>> backend = TrinoGovernanceBackend()
    >>> policy = AccessPolicy(
    ...     table_pattern="my_schema.my_table",
    ...     principal="analyst_role",
    ...     action="SELECT",
    ...     effect="GRANT"
    ... )
    >>> backend.apply_policy(policy=policy)

"""

from __future__ import annotations

import re
from typing import Any

from phlo.capabilities.interfaces import AccessPolicy
from phlo.logging import get_logger
from phlo_trino.resource import TrinoResource

logger = get_logger(__name__)

_ALLOWED_ACTIONS = frozenset(
    {
        "SELECT",
        "INSERT",
        "DELETE",
        "UPDATE",
        "ALL PRIVILEGES",
        "GRANT",
    }
)
_IDENTIFIER_RE = re.compile(r"^[\w][\w.]*$")


def _validate_identifier(value: str, label: str) -> str:
    """Validate a SQL identifier against the allowed-character pattern to
    prevent injection, raising ValueError (with label in the message) on
    invalid characters.
    """
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def _quote_identifier(identifier: str) -> str:
    """Quote a single validated Trino identifier."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _quote_qualified_identifier(identifier: str) -> str:
    """Quote each segment of a validated dotted Trino identifier."""
    return ".".join(_quote_identifier(part) for part in identifier.split("."))


class TrinoGovernanceBackend:
    """GovernanceBackend implementation using Trino SQL grants."""

    def __init__(self, trino: TrinoResource | None = None) -> None:
        """Initialize the governance backend with optional Trino resource."""
        self._trino = trino or TrinoResource()

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
        """List grants, optionally filtered by table."""
        if table_name is not None:
            _validate_identifier(table_name, "table_name")
            sql = f"SHOW GRANTS ON TABLE {_quote_qualified_identifier(table_name)}"
        else:
            sql = "SHOW GRANTS"
        try:
            rows = self._trino.execute(sql)
        except Exception:
            logger.warning(
                "trino_governance_list_policies_failed",
                table_name=table_name,
                exc_info=True,
            )
            return []
        # Trino SHOW GRANTS columns:
        # 0=grantor 1=grantor_type 2=grantee 3=grantee_type
        # 4=catalog 5=schema 6=table 7=privilege 8=is_grantable 9=with_hierarchy
        policies: list[dict[str, Any]] = []
        for row in rows:
            policies.append(
                {
                    "grantor": row[0] if len(row) > 0 else None,
                    "grantor_type": row[1] if len(row) > 1 else None,
                    "grantee": row[2] if len(row) > 2 else None,
                    "grantee_type": row[3] if len(row) > 3 else None,
                    "catalog": row[4] if len(row) > 4 else None,
                    "schema": row[5] if len(row) > 5 else None,
                    "table": row[6] if len(row) > 6 else None,
                    "privilege": row[7] if len(row) > 7 else None,
                    "grantable": row[8] if len(row) > 8 else None,
                    "with_hierarchy": row[9] if len(row) > 9 else None,
                }
            )
        return policies

    def apply_policy(self, *, policy: AccessPolicy) -> None:
        """Apply a GRANT or DENY via Trino SQL."""
        action = policy.action.upper()
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported action: {action!r}")
        _validate_identifier(policy.table_pattern, "table_pattern")
        _validate_identifier(policy.principal, "principal")
        if policy.columns:
            logger.warning("trino_governance_column_grants_unsupported", columns=policy.columns)

        table_ref = _quote_qualified_identifier(policy.table_pattern)
        principal_ref = _quote_identifier(policy.principal)
        if policy.effect == "DENY":
            sql = f"DENY {action} ON TABLE {table_ref} TO {principal_ref}"
        else:
            sql = f"GRANT {action} ON TABLE {table_ref} TO {principal_ref}"

        logger.info(
            "trino_governance_apply_policy",
            sql=sql,
            principal=policy.principal,
            table=policy.table_pattern,
            action=action,
        )
        self._trino.execute(sql)

    def revoke_policy(self, *, policy_id: str) -> None:
        """Revoke a grant. policy_id format: ``ACTION:TABLE:PRINCIPAL``."""
        parts = policy_id.split(":", 2)
        if len(parts) != 3:
            msg = f"policy_id must be 'ACTION:TABLE:PRINCIPAL', got: {policy_id}"
            raise ValueError(msg)
        action, table, principal = parts
        if action.upper() not in _ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported action: {action!r}")
        _validate_identifier(table, "table")
        _validate_identifier(principal, "principal")
        sql = (
            f"REVOKE {action.upper()} ON TABLE {_quote_qualified_identifier(table)} "
            f"FROM {_quote_identifier(principal)}"
        )
        logger.info("trino_governance_revoke_policy", sql=sql, policy_id=policy_id)
        self._trino.execute(sql)

    def check_access(self, *, principal: str, table_name: str, action: str) -> bool:
        """Check if principal has the specified privilege on a table.

        Only positive grants are inspected: a DENY applied via apply_policy
        does not make this return False.
        """
        policies = self.list_policies(table_name=table_name)
        action_upper = action.upper()
        for policy in policies:
            if policy.get("grantee") == principal and policy.get("privilege") == action_upper:
                return True
        return False
