"""Tests for publish_table and governance publish readiness against a
fake publish target, including config error paths."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import phlo
from phlo.exceptions import PhloConfigError
from phlo.helpers import governance_publish_readiness, publish_table

pytestmark = pytest.mark.core_regression


class _FakePublishTarget:
    def __init__(self) -> None:
        self.published: list[dict[str, object]] = []

    def publish_table(
        self,
        *,
        table_name: str,
        target_table: str,
        mode: str,
        **options: object,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "table_name": table_name,
            "target_table": target_table,
            "mode": mode,
            "options": options,
        }
        self.published.append(result)
        return result


@pytest.fixture(autouse=True)
def _clear_flow_declarations() -> Iterator[None]:
    phlo.clear_flow_declarations()
    yield
    phlo.clear_flow_declarations()


def test_governance_publish_readiness_reports_missing_declarations() -> None:
    report = governance_publish_readiness("gold.customer_health")

    assert report["ready"] is False
    assert report["governance"] is None
    assert report["warnings"] == [
        {
            "table": "gold.customer_health",
            "code": "missing_governance_declaration",
            "message": (
                "gold.customer_health has no Phlo governance declaration. Add @phlo.contract, "
                "@phlo.publish, and @phlo.access declarations before requiring governance."
            ),
            "severity": "error",
        }
    ]


def test_publish_table_can_block_when_governance_is_incomplete() -> None:
    @phlo.publish(table="gold.customer_health", audience=["sales"])
    def publish_customer_health() -> None:
        return None

    target = _FakePublishTarget()

    with pytest.raises(PhloConfigError) as exc:
        publish_table("gold.customer_health", target=target, require_governance=True)

    assert "missing_owner" in str(exc.value)
    assert "missing_access_policy" in str(exc.value)
    assert target.published == []


def test_publish_table_can_require_complete_governance() -> None:
    @phlo.contract(
        table="gold.customer_health",
        owner="data-platform",
        consumers=["sales"],
        pii=True,
        freshness_hours=6,
        lifecycle="production",
    )
    def customer_health_contract() -> None:
        return None

    @phlo.publish(table="gold.customer_health", owner="data-platform", audience=["sales"])
    def publish_customer_health() -> None:
        return None

    @phlo.access(
        table="gold.customer_health",
        roles=["sales_read"],
        pii_columns=["email"],
    )
    def customer_health_access() -> None:
        return None

    target = _FakePublishTarget()

    result = publish_table("gold.customer_health", target=target, require_governance=True)

    assert result["table_name"] == "gold.customer_health"
    assert governance_publish_readiness("gold.customer_health")["ready"] is True


def test_publish_table_keeps_governance_gate_opt_in() -> None:
    target = _FakePublishTarget()

    result = publish_table("gold.customer_health", target=target)

    assert result["target_table"] == "gold.customer_health"
