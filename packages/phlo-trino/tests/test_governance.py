"""Tests for Trino governance SQL rendering.

Every statement must quote each qualified table name segment individually so
GRANT, REVOKE, and SHOW GRANTS apply to exactly the parsed
catalog.schema.table and nothing else.
"""

from __future__ import annotations

from phlo.capabilities.interfaces import AccessPolicy
from phlo_trino.governance import TrinoGovernanceBackend


class _FakeTrino:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str):
        self.queries.append(query)
        return []


def test_list_policies_quotes_each_table_name_segment() -> None:
    trino = _FakeTrino()
    backend = TrinoGovernanceBackend(trino=trino)

    backend.list_policies(table_name="marts.orders")

    assert trino.queries == ['SHOW GRANTS ON TABLE "marts"."orders"']


def test_apply_policy_preserves_qualified_table_names() -> None:
    trino = _FakeTrino()
    backend = TrinoGovernanceBackend(trino=trino)

    backend.apply_policy(
        policy=AccessPolicy(
            table_pattern="marts.orders",
            principal="analyst_role",
            action="SELECT",
            effect="GRANT",
        )
    )

    assert trino.queries == ['GRANT SELECT ON TABLE "marts"."orders" TO "analyst_role"']


def test_revoke_policy_preserves_qualified_table_names() -> None:
    trino = _FakeTrino()
    backend = TrinoGovernanceBackend(trino=trino)

    backend.revoke_policy(policy_id="SELECT:marts.orders:analyst_role")

    assert trino.queries == ['REVOKE SELECT ON TABLE "marts"."orders" FROM "analyst_role"']
