"""Integration tests for phlo-dagster.

Covers plugin/daemon registration, partition setup, Iceberg S3
credential propagation into service definitions, and runtime context
handling: run tags and run ids are read from the run when the context
lacks them, invalid retry attempts are rejected rather than aliased to
attempt 1, and failed materializations report failure status.
"""

from types import SimpleNamespace
from typing import cast

import pytest
from dagster import AssetExecutionContext, Definitions, asset, materialize

pytestmark = pytest.mark.integration


def test_dagster_definitions_load():
    """Test that Dagster Definitions can be instantiated."""
    # Basic Definitions object creation
    defs = Definitions(assets=[], resources={})
    assert isinstance(defs, Definitions)


def test_dagster_asset_materialization():
    """Test that a simple asset can be materialized without external services."""

    @asset
    def test_asset():
        """Return a deterministic payload for materialization verification."""
        return {"value": 42}

    # Materialize the asset
    result = materialize([test_asset])

    assert result.success
    # Verify the asset output
    output = result.output_for_node("test_asset")
    assert output == {"value": 42}


def test_phlo_dagster_partitions():
    """Test that phlo_dagster partitions are properly configured."""
    from phlo_dagster.partitions import daily_partition

    assert daily_partition is not None
    assert daily_partition.timezone == "Europe/London"

    # Verify partitions are generated correctly
    partitions = list(daily_partition.get_partition_keys())
    assert len(partitions) > 0
    # First partition should be 2025-01-01
    assert partitions[0] == "2025-01-01"


def test_phlo_dagster_plugin_metadata():
    """Test that phlo-dagster plugin provides correct metadata."""
    from phlo_dagster.plugin import DagsterServicePlugin, DagsterDaemonServicePlugin

    # Test main plugin
    plugin = DagsterServicePlugin()
    assert plugin.metadata.name == "dagster"
    assert "orchestration" in plugin.metadata.tags

    # Test daemon plugin
    daemon_plugin = DagsterDaemonServicePlugin()
    assert daemon_plugin.metadata.name == "dagster-daemon"


def test_phlo_dagster_service_definition():
    """Test that service definitions can be loaded."""
    from phlo_dagster.plugin import DagsterServicePlugin

    plugin = DagsterServicePlugin()
    service_def = plugin.service_definition

    assert isinstance(service_def, dict)
    assert "services" in service_def or "service" in service_def or "name" in service_def


def test_dagster_services_propagate_iceberg_s3_credentials():
    """Dagster containers must pass generated MinIO credentials to Iceberg I/O."""
    from phlo_dagster.plugin import DagsterDaemonServicePlugin, DagsterServicePlugin

    plugins = [DagsterServicePlugin(), DagsterDaemonServicePlugin()]

    for plugin in plugins:
        service_def = plugin.service_definition
        environment = service_def["compose"]["environment"]

        assert (
            environment["ICEBERG_S3_ACCESS_KEY"]
            == "${DAGSTER_MINIO_ACCESS_KEY:-${MINIO_ROOT_USER:-minio}}"
        )
        assert (
            environment["ICEBERG_S3_SECRET_KEY"]
            == "${DAGSTER_MINIO_SECRET_KEY:-${MINIO_ROOT_PASSWORD:-minio123}}"
        )
        assert environment["ICEBERG_S3_ENDPOINT"] == "http://minio:9000"
        assert environment["ICEBERG_S3_REGION"] == "us-east-1"


def test_dagster_with_phlo_iceberg_resource():
    """Test Dagster integration with IcebergResource."""
    try:
        from phlo_iceberg.resource import IcebergResource
    except ImportError:
        pytest.skip("phlo-iceberg not installed")

    # Just verify the resource can be instantiated
    # We don't actually connect to a catalog here
    iceberg = IcebergResource()
    assert iceberg is not None


def test_phlo_dagster_version():
    """Test that phlo-dagster has proper version."""
    from importlib.metadata import version

    import phlo_dagster

    assert hasattr(phlo_dagster, "__version__")
    assert phlo_dagster.__version__ == version("phlo-dagster")


def test_dagster_runtime_reads_run_tags_when_context_has_no_tags():
    """DagsterRuntime should support contexts exposing run-level tags only."""
    from phlo_dagster.adapter import DagsterRuntime

    context = SimpleNamespace(
        run=SimpleNamespace(tags={"dbt_target": "ci"}),
        run_id="abc123",
        has_partition_key=False,
        log=SimpleNamespace(),
        resources=SimpleNamespace(),
    )

    runtime = DagsterRuntime(context=cast(AssetExecutionContext, context))
    assert runtime.tags == {"dbt_target": "ci"}


def test_dagster_runtime_reads_run_id_from_run_object():
    """DagsterRuntime should use Dagster's canonical run object for the run id."""
    from phlo_dagster.adapter import DagsterRuntime

    class _Context:
        run = SimpleNamespace(run_id="run-from-object", tags={})
        has_partition_key = False
        log = SimpleNamespace()
        resources = SimpleNamespace()

        @property
        def run_id(self) -> str:
            raise AssertionError("DagsterRuntime should use context.run.run_id")

    runtime = DagsterRuntime(context=cast(AssetExecutionContext, _Context()))
    assert runtime.run_id == "run-from-object"


def test_dagster_runtime_builds_routing_without_recursion():
    """DagsterRuntime.routing should return concrete routing metadata."""
    from phlo_dagster.adapter import DagsterRuntime

    context = SimpleNamespace(
        tags={
            "environment": "dev",
            "phlo/wap_branch": "pipeline-run-abc123",
            "phlo/ref": "feature/orders",
            "feature/wap": "true",
        },
        run=SimpleNamespace(run_id="abc123"),
        has_partition_key=True,
        partition_key="2025-01-01",
        log=SimpleNamespace(),
        resources=SimpleNamespace(table_store="iceberg"),
    )

    runtime = DagsterRuntime(context=cast(AssetExecutionContext, context))

    assert runtime.routing.environment == "dev"
    assert runtime.routing.ref == "pipeline-run-abc123"
    assert runtime.routing.partition_key == "2025-01-01"
    assert runtime.routing.run_id == "abc123"
    assert runtime.routing.feature_flags == {"wap": "true"}
    assert runtime.routing.resources["table_store"] == "iceberg"


@pytest.mark.parametrize("raw_attempt", ["0", "garbage"])
def test_dagster_runtime_rejects_invalid_attempt_without_aliasing_to_one(raw_attempt: str):
    """Malformed retry tags fail closed instead of becoming attempt one."""
    from phlo_dagster.adapter import DagsterRuntime

    context = SimpleNamespace(
        tags={"phlo/project_id": "project", "phlo/attempt": raw_attempt},
        run=SimpleNamespace(
            run_id="run", tags={"phlo/project_id": "project", "phlo/attempt": raw_attempt}
        ),
        has_partition_key=False,
        log=SimpleNamespace(),
        resources=SimpleNamespace(),
    )

    routing = DagsterRuntime(context=cast(AssetExecutionContext, context)).routing

    assert routing.attempt is None
    assert routing.attempt_error == "invalid_attempt"


def test_dagster_runtime_preserves_valid_retry_attempt():
    """A valid retry tag stays bound to its authoritative attempt."""
    from phlo_dagster.adapter import DagsterRuntime

    context = SimpleNamespace(
        tags={"phlo/project_id": "project", "phlo/attempt": "2"},
        run=SimpleNamespace(run_id="run", tags={"phlo/project_id": "project", "phlo/attempt": "2"}),
        has_partition_key=False,
        log=SimpleNamespace(),
        resources=SimpleNamespace(),
    )

    assert DagsterRuntime(context=cast(AssetExecutionContext, context)).routing.attempt == 2


def test_materialize_result_failure_status_fails_step():
    """Failure statuses from capability assets must fail Dagster runs."""
    from phlo.capabilities import AssetSpec, MaterializeResult, RunSpec
    from phlo_dagster.adapter import DagsterOrchestratorAdapter

    def _run(_runtime):
        """Emit a failure materialize result for adapter failure-path testing."""
        return [MaterializeResult(status="failure", metadata={"reason": "boom"})]

    adapter = DagsterOrchestratorAdapter()
    asset_def = adapter._build_asset(
        AssetSpec(key="failure_asset", group=None, description=None, run=RunSpec(fn=_run))
    )
    result = materialize([asset_def], raise_on_error=False)

    assert not result.success
