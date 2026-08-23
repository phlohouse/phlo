"""Tests for the Sling replication config registry.

Checks ReplicationConfig defaults, qualified table-name derivation, and
custom-field handling.
"""

from phlo_sling.registry import ReplicationConfig


def test_replication_config_defaults():
    """Validate default config values."""
    config = ReplicationConfig(
        stream_name="public.users",
        table_name="users",
        source_conn="MY_PG",
    )

    assert config.mode == "incremental"
    assert config.primary_key == []
    assert config.update_key is None
    assert config.group_name == "sling"
    assert config.asset_key == "sling_users"


def test_replication_config_full_table_name():
    """Validate namespace-prefixed table name."""
    config = ReplicationConfig(
        stream_name="public.users",
        table_name="users",
        source_conn="MY_PG",
    )

    assert config.full_table_name == "raw.users"


def test_replication_config_custom():
    """Validate custom config values."""
    config = ReplicationConfig(
        stream_name="sales.orders",
        table_name="orders",
        source_conn="MY_PG",
        target_conn="MY_S3",
        mode="full-refresh",
        primary_key=["id"],
        update_key="updated_at",
        group_name="sales",
        select=["id", "total", "created_at"],
        where="created_at > '2024-01-01'",
    )

    assert config.asset_key == "sling_orders"
    assert config.primary_key == ["id"]
    assert config.select == ["id", "total", "created_at"]
