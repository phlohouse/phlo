"""Tests for the Sling executor.

Target resolution defaults to the configured table name, fails with
PhloConfigError before invoking Sling when no destination resolves, and
accepts runtime file/object overrides without a connection. Executor
runs inject auto-discovered Sling connection env before execution.
"""

from types import SimpleNamespace

import pytest

from phlo.exceptions import PhloConfigError
from phlo_sling.executor import SlingIngester
from phlo_sling.registry import ReplicationConfig


def test_replication_config_asset_key():
    """Validate asset_key format."""
    config = ReplicationConfig(
        stream_name="public.users",
        table_name="users",
        source_conn="MY_PG",
    )
    assert config.asset_key == "sling_users"


def test_build_sling_kwargs_derives_default_target_object() -> None:
    """Executor should default the target object from the configured table name."""
    config = ReplicationConfig(
        stream_name="public.users",
        table_name="users",
        source_conn="MY_PG",
        target_conn="WAREHOUSE",
    )
    ingester = SlingIngester(
        context=SimpleNamespace(job_name="test_job"),
        logger=SimpleNamespace(
            info=lambda *_args, **_kwargs: None, error=lambda *_args, **_kwargs: None
        ),
        replication_config=config,
        source_func=lambda _context: None,
    )

    kwargs = ingester._build_sling_kwargs(partition_key="2026-03-09")

    assert kwargs["tgt_conn"] == "WAREHOUSE"
    assert kwargs["tgt_object"] == "raw.users"


def test_build_sling_kwargs_requires_destination() -> None:
    """Executor should fail before calling Sling when no target can be resolved."""
    config = ReplicationConfig(
        stream_name="public.users",
        table_name="users",
        source_conn="MY_PG",
    )
    ingester = SlingIngester(
        context=SimpleNamespace(job_name="test_job"),
        logger=SimpleNamespace(
            info=lambda *_args, **_kwargs: None, error=lambda *_args, **_kwargs: None
        ),
        replication_config=config,
        source_func=lambda _context: None,
    )

    with pytest.raises(PhloConfigError, match="destination object"):
        ingester._build_sling_kwargs(partition_key="2026-03-09")


def test_build_sling_kwargs_allows_runtime_target_override() -> None:
    """Runtime overrides may provide a file/object target without a connection."""
    config = ReplicationConfig(
        stream_name="public.users",
        table_name="users",
        source_conn="MY_PG",
    )
    ingester = SlingIngester(
        context=SimpleNamespace(job_name="test_job"),
        logger=SimpleNamespace(),
        replication_config=config,
        source_func=lambda _context: None,
        overrides={"tgt_object": "file:///tmp/users.parquet"},
    )

    kwargs = ingester._build_sling_kwargs(partition_key="2026-03-09")

    assert kwargs["tgt_object"] == "file:///tmp/users.parquet"
    assert "tgt_conn" not in kwargs


def test_run_ingestion_injects_auto_connections(monkeypatch) -> None:
    """Executor-backed runs should inject auto-discovered Sling env before execution."""
    config = ReplicationConfig(
        stream_name="public.users",
        table_name="users",
        source_conn="PHLO_POSTGRES",
        target_conn="WAREHOUSE",
        mode="full-refresh",
    )
    ingester = SlingIngester(
        context=SimpleNamespace(job_name="test_job"),
        logger=SimpleNamespace(
            info=lambda *_args, **_kwargs: None, error=lambda *_args, **_kwargs: None
        ),
        replication_config=config,
        source_func=lambda _context: None,
    )
    calls: list[str] = []

    class _FakeSling:
        rows_count = 7

        def __init__(self, **_kwargs):
            pass

        def run(self) -> None:
            calls.append("run")

    monkeypatch.setattr(
        "phlo_sling.connections.apply_sling_connection_env", lambda: calls.append("env")
    )
    monkeypatch.setattr("sling.Sling", _FakeSling)

    result = ingester.run_ingestion(partition_key="2026-03-09", parameters={"run_id": "r1"})

    assert calls[:2] == ["env", "run"]
    assert result.rows_inserted == 7
