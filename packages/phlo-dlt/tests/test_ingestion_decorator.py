"""Tests for Ingestion Decorator Module.

This module contains unit tests for the phlo-dlt decorator module.
Tests cover decorator application, schema auto-generation, asset registration,
configuration parameters, and error handling.
"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pandera.pandas import DataFrameModel, Field

pytest.importorskip("pyiceberg")

from phlo.contracts import Consumer, SLA
from phlo.logging import get_logger
from phlo.run_evidence import SQLiteRunEvidenceStore
from phlo.run_evidence.hooks import CoreRunEvidenceHookProvider
from phlo_dlt.decorator import clear_ingestion_assets, get_ingestion_assets, phlo_ingestion
from phlo_dlt.dlt_helpers import get_branch_from_context, get_write_branch_from_context
from phlo_dlt.executor import DltIngester
from phlo_dlt.registry import TableConfig
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType


@pytest.fixture(autouse=True)
def _clear_ingestion_registry() -> None:
    """Clear the ingestion asset registry before each test."""
    clear_ingestion_assets()


def test_phlo_ingestion_export_is_available() -> None:
    """Verify the ingestion decorator export is callable."""
    assert callable(phlo_ingestion)


def test_blessed_decorator_persists_runtime_correlation(monkeypatch, tmp_path) -> None:
    """The normal decorated asset path persists project and attempt into DLT evidence."""

    class _Schema:
        __annotations__ = {"id": int}

    captured: list[dict[str, Any]] = []
    states = iter(
        [
            {"state": "absent", "snapshot_id": None, "schema_hash": None, "metadata": {}},
            {"state": "present", "snapshot_id": "after", "schema_hash": "schema", "metadata": {}},
        ]
    )
    monkeypatch.setattr(
        "phlo_dlt.decorator._resolve_table_store_capability",
        lambda _runtime: (SimpleNamespace(), "test-store"),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline",
        lambda **_kwargs: (SimpleNamespace(), tmp_path),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.stage_to_parquet",
        lambda **_kwargs: ([tmp_path / "staged.parquet"], 0.01),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.staged_object_inventory",
        lambda _paths: [{"identity": "staged.parquet", "checksum": "abc", "byte_count": 1}],
    )
    monkeypatch.setattr("phlo_dlt.executor.dlt_execution_identity", lambda *args: ("exec-1", True))
    monkeypatch.setattr("phlo_dlt.executor.dlt_observed_metrics", lambda _pipeline: {})
    monkeypatch.setattr("phlo_dlt.executor.table_state", lambda *_args: next(states))
    monkeypatch.setattr(
        "phlo_dlt.executor.merge_to_table_store",
        lambda **_kwargs: {"rows_inserted": 1, "rows_deleted": 0},
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.emit_observation",
        lambda **kwargs: captured.append(kwargs),
    )

    @phlo_ingestion(
        table_name="events",
        unique_key="id",
        group="raw",
        validation_schema=_Schema,
        validate=False,
        add_metadata_columns=False,
    )
    def events(partition_date: str):
        return []

    runtime = SimpleNamespace(
        run_id="run-decorated",
        partition_key="2026-07-13",
        tags={"phlo/project_id": "project-decorated", "phlo/attempt": "2"},
        resources={},
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    list(get_ingestion_assets()[0].run.fn(runtime))

    assert captured[0]["run_id"] == "run-decorated"
    assert captured[0]["project_id"] == "project-decorated"
    assert captured[0]["attempt"] == 2
    assert captured[0]["resources"]
    assert all(
        resource["resource_identity"]["tenant"] == "project-decorated"
        for resource in captured[0]["resources"]
    )
    assert captured[0]["resources"][-1]["resource_identity"] == {
        "resource_type": "iceberg_table",
        "resource_id": "raw.events",
        "tenant": "project-decorated",
        "attributes": {"catalog_ref": "main"},
    }


def test_blessed_decorator_persists_runtime_correlation_through_sqlite(
    monkeypatch, tmp_path
) -> None:
    """The real Dagster runtime uses configured project identity without a tag."""

    class _Schema:
        __annotations__ = {"id": int}

    states = iter(
        [
            {"state": "absent", "snapshot_id": None, "schema_hash": None, "metadata": {}},
            {"state": "present", "snapshot_id": "after", "schema_hash": "schema", "metadata": {}},
        ]
    )
    monkeypatch.setattr(
        "phlo_dlt.decorator._resolve_table_store_capability",
        lambda _runtime: (SimpleNamespace(), "test-store"),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline",
        lambda **_kwargs: (SimpleNamespace(), tmp_path),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.stage_to_parquet",
        lambda **_kwargs: ([tmp_path / "staged.parquet"], 0.01),
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.staged_object_inventory",
        lambda _paths: [{"identity": "staged.parquet", "checksum": "abc", "byte_count": 1}],
    )
    monkeypatch.setattr("phlo_dlt.executor.dlt_execution_identity", lambda *args: ("exec-1", True))
    monkeypatch.setattr("phlo_dlt.executor.dlt_observed_metrics", lambda _pipeline: {})
    monkeypatch.setattr("phlo_dlt.executor.table_state", lambda *_args: next(states))
    monkeypatch.setattr(
        "phlo_dlt.executor.merge_to_table_store",
        lambda **_kwargs: {"rows_inserted": 1, "rows_deleted": 0},
    )
    monkeypatch.setattr(
        "phlo_dagster.adapter.get_settings",
        lambda: SimpleNamespace(phlo_project="project-durable"),
    )

    @phlo_ingestion(
        table_name="events",
        unique_key="id",
        group="raw",
        validation_schema=_Schema,
        validate=False,
        add_metadata_columns=False,
    )
    def events(partition_date: str):
        return []

    tags = {"phlo/attempt": "2"}
    from phlo_dagster.adapter import DagsterRuntime

    runtime_context = SimpleNamespace(
        tags=tags,
        run=SimpleNamespace(run_id="run-durable", tags=tags),
        has_partition_key=True,
        partition_key="2026-07-14",
        log=get_logger("test_durable_decorator"),
        resources=SimpleNamespace(),
    )
    runtime = DagsterRuntime(context=runtime_context)
    store = SQLiteRunEvidenceStore(":memory:")
    from phlo.hooks import get_hook_bus

    global_bus = get_hook_bus()
    global_bus.clear()
    global_bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    try:
        list(get_ingestion_assets()[0].run.fn(runtime))
    finally:
        global_bus.clear()

    run = store.get_run("project-durable", "run-durable")
    assert run is not None and run["attempt"] == 2
    assert store.list_resources("project-durable", "run-durable", attempt=2)
    assert store.list_resources("project-durable", "run-durable", attempt=1) == []
    assert store.count_events("project-durable", "run-durable") > 0


@pytest.mark.parametrize(
    ("tags", "configured", "expected_project", "expected_error"),
    [
        ({"phlo/project_id": "project"}, "project", "project", None),
        ({"phlo/project_id": "tag"}, "project", None, "project_conflict"),
        ({}, None, None, "project_missing"),
    ],
)
def test_dagster_runtime_project_identity_is_explicit(
    monkeypatch, tags, configured, expected_project, expected_error
):
    from phlo_dagster.adapter import DagsterRuntime

    monkeypatch.setattr(
        "phlo_dagster.adapter.get_settings",
        lambda: SimpleNamespace(phlo_project=configured),
    )
    runtime_tags = {**tags, "phlo/attempt": "1"}
    runtime = DagsterRuntime(
        context=SimpleNamespace(
            tags=runtime_tags,
            run=SimpleNamespace(run_id="run-project", tags=runtime_tags),
            has_partition_key=False,
            log=get_logger("test_project_identity"),
            resources=SimpleNamespace(),
        )
    )

    routing = runtime.routing

    assert routing.project_id == expected_project
    assert routing.project_error == expected_error


@pytest.mark.parametrize(
    ("tags", "configured", "expected_error"),
    [
        ({"phlo/project_id": "tag"}, "configured", "project_conflict"),
        ({}, None, "project_missing"),
    ],
)
def test_dlt_emits_explicit_project_correlation_gap(monkeypatch, tags, configured, expected_error):
    """DLT keeps the run visible while marking missing or conflicting project identity."""
    from phlo_dagster.adapter import DagsterRuntime

    monkeypatch.setattr(
        "phlo_dagster.adapter.get_settings",
        lambda: SimpleNamespace(phlo_project=configured),
    )
    runtime_tags = {**tags, "phlo/attempt": "1"}
    runtime = DagsterRuntime(
        context=SimpleNamespace(
            tags=runtime_tags,
            run=SimpleNamespace(run_id="run-gap", tags=runtime_tags),
            has_partition_key=False,
            log=get_logger("test_dlt_project_gap"),
            resources=SimpleNamespace(),
        )
    )
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "phlo_dlt.executor.emit_observation",
        lambda **kwargs: captured.append(kwargs),
    )
    ingester = DltIngester(
        context=runtime,
        logger=get_logger("test_dlt_project_gap"),
        table_config=TableConfig(
            table_name="entries",
            table_schema=None,
            validation_schema=None,
            unique_key="name",
            group_name="raw",
        ),
        table_store_resource=SimpleNamespace(),
        dlt_source_func=lambda partition_date: None,
        validate=False,
    )

    result = ingester.run_ingestion(partition_key="2026-03-05")

    assert result.status == "no_data"
    assert captured[0]["project_id"] is None
    assert captured[0]["correlation_error"] == expected_error


@pytest.mark.parametrize("raw_attempt", ["0", "garbage"])
def test_blessed_decorator_does_not_alias_invalid_attempt_to_one(raw_attempt: str, monkeypatch):
    """Malformed retry tags produce a gap and no attempt-one evidence."""

    class _Schema:
        __annotations__ = {"id": int}

    monkeypatch.setattr(
        "phlo_dlt.decorator._resolve_table_store_capability",
        lambda _runtime: (SimpleNamespace(), "test-store"),
    )

    @phlo_ingestion(
        table_name="events",
        unique_key="id",
        group="raw",
        validation_schema=_Schema,
        validate=False,
        add_metadata_columns=False,
    )
    def events(partition_date: str):
        return None

    from phlo.hooks import get_hook_bus
    from phlo_dagster.adapter import DagsterRuntime

    tags = {"phlo/project_id": "project-invalid", "phlo/attempt": raw_attempt}
    runtime = DagsterRuntime(
        context=SimpleNamespace(
            tags=tags,
            run=SimpleNamespace(run_id="run-invalid", tags=tags),
            has_partition_key=True,
            partition_key="2026-07-14",
            log=get_logger("test_invalid_decorator"),
            resources=SimpleNamespace(),
        )
    )
    store = SQLiteRunEvidenceStore(":memory:")
    global_bus = get_hook_bus()
    global_bus.clear()
    global_bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    try:
        list(get_ingestion_assets()[0].run.fn(runtime))
    finally:
        global_bus.clear()

    assert store.get_run("project-invalid", "run-invalid") is None
    assert store.list_resources("project-invalid", "run-invalid", attempt=1) == []


def test_dlt_ingestion_asset_has_provider_neutral_metadata() -> None:
    """DLT assets should expose provider-neutral metadata for core surfaces."""

    class _Schema:
        __annotations__ = {"id": int}

    @phlo_ingestion(
        table_name="events",
        unique_key="id",
        group="raw",
        validation_schema=_Schema,
    )
    def events(partition_date: str):
        return []

    asset = get_ingestion_assets()[0]

    assert asset.tags["provider"] == "dlt"
    assert asset.tags["asset_type"] == "ingestion"
    assert asset.metadata["provider"] == "dlt"
    assert asset.metadata["asset_type"] == "ingestion"
    assert asset.metadata["table_name"] == "events"
    assert asset.metadata["primary_key"] == ["id"]
    assert asset.metadata["quality_provider"] == "pandera"


def test_dlt_quality_provider_metadata_is_none_when_validation_disabled() -> None:
    """DLT asset metadata should only name Pandera when validation is active."""

    class _Schema:
        __annotations__ = {"id": int}

    @phlo_ingestion(
        table_name="events",
        unique_key="id",
        group="raw",
        validation_schema=_Schema,
        validate=False,
    )
    def events(partition_date: str):
        return []

    asset = get_ingestion_assets()[0]

    assert asset.metadata["schema_ref"] == "_Schema"
    assert asset.metadata["quality_provider"] is None


def get_asset_spec(asset_key: str) -> Any:
    """Helper to get AssetSpec by key."""
    for spec in get_ingestion_assets():
        if spec.key == asset_key:
            return spec
    raise AssertionError(f"AssetSpec {asset_key} not found")


def test_get_branch_from_context_prefers_canonical_ref() -> None:
    """Canonical runtime routing should drive ref selection."""

    class RuntimeStub:
        run_id = "run-1"
        partition_key = "2025-01-01"
        tags = {"branch": "legacy-branch", "phlo/ref": "canonical-ref"}
        resources = {}

        @property
        def logger(self) -> Any:
            return object()

        def get_resource(self, name: str) -> Any:
            raise KeyError(name)

    assert get_branch_from_context(RuntimeStub()) == "canonical-ref"


def test_get_branch_from_context_defaults_to_main() -> None:
    """Missing routing metadata should fall back to main."""

    class RuntimeStub:
        run_id = None
        partition_key = None
        tags: dict[str, str] = {}
        resources: dict[str, Any] = {}

        @property
        def logger(self) -> Any:
            return object()

        def get_resource(self, name: str) -> Any:
            raise KeyError(name)

    assert get_branch_from_context(RuntimeStub()) == "main"


def test_get_write_branch_from_context_prefers_wap_branch_for_strict_runs() -> None:
    """Strict validation should use the isolated WAP branch when present."""

    class RuntimeStub:
        run_id = "run-1"
        partition_key = "2025-01-01"
        tags = {
            "phlo/ref": "main",
            "phlo/wap_branch": "pipeline-run-run-1",
        }
        resources = {}

        @property
        def logger(self) -> Any:
            return object()

        def get_resource(self, name: str) -> Any:
            raise KeyError(name)

    assert (
        get_write_branch_from_context(RuntimeStub(), strict_validation=True) == "pipeline-run-run-1"
    )


def test_get_write_branch_from_context_uses_target_branch_without_wap() -> None:
    """Without WAP routing, writes should fall back to the target branch."""

    class RuntimeStub:
        run_id = "run-1"
        partition_key = "2025-01-01"
        tags = {"phlo/ref": "feature/orders"}
        resources = {}

        @property
        def logger(self) -> Any:
            return object()

        def get_resource(self, name: str) -> Any:
            raise KeyError(name)

    assert get_write_branch_from_context(RuntimeStub(), strict_validation=True) == "feature/orders"


def test_get_write_branch_from_context_ignores_blank_wap_like_dbt() -> None:
    """A blank WAP tag should fall through to the same canonical ref used by dbt."""

    class RuntimeStub:
        run_id = "run-1"
        partition_key = "2025-01-01"
        tags = {"phlo/wap_branch": "  ", "phlo/ref": "feature_orders"}
        resources = {}

        @property
        def logger(self) -> Any:
            return object()

        def get_resource(self, name: str) -> Any:
            raise KeyError(name)

    assert get_write_branch_from_context(RuntimeStub(), strict_validation=True) == "feature_orders"


class TestSchemaAutoGeneration:
    """Test schema parameter handling."""

    def test_validation_schema_only_registers_asset(self):
        """Test asset registration succeeds with validation_schema and no explicit table_schema."""

        class TestPanderaSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str = Field(nullable=False)
            value: int

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestPanderaSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Verify asset was created
        assert test_asset is not None
        spec = get_asset_spec("dlt_test_table")
        assert spec.key == "dlt_test_table"

    def test_explicit_table_schema_used(self):
        """Test explicit table-store schema is used when provided."""

        class TestPanderaSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        explicit_schema = Schema(
            NestedField(field_id=1, name="custom_field", field_type=StringType(), required=True)
        )

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestPanderaSchema,
            table_schema=explicit_schema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Asset should be created successfully
        assert test_asset is not None
        spec = get_asset_spec("dlt_test_table")
        assert spec.key == "dlt_test_table"

    def test_error_when_no_schema_provided(self):
        """Test error raised when neither validation_schema nor table_schema provided."""
        from phlo.exceptions import PhloConfigError

        with pytest.raises(PhloConfigError, match="Missing required schema parameter"):

            @phlo_ingestion(
                table_name="test_table",
                unique_key="id",
                group="test",
            )
            def test_asset(partition_date: str):
                """Placeholder asset function used for decorator registration tests."""
                pass


class TestDecoratorConfiguration:
    """Test decorator parameter configuration."""

    def test_table_name_configuration(self):
        """Test table_name parameter is properly configured."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="custom_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check asset name includes table name
        spec = get_asset_spec("dlt_custom_table")
        assert spec.key == "dlt_custom_table"

    def test_unique_key_configuration(self):
        """Test unique_key parameter is stored."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            custom_id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="custom_id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Asset should be created
        assert test_asset is not None

    def test_group_name_configuration(self):
        """Test group_name parameter is applied to asset."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="custom_group",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check asset has correct group
        spec = get_asset_spec("dlt_test_table")
        assert spec.group == "custom_group"

    def test_contract_metadata_configuration(self):
        """Test owner/consumers/sla metadata is attached to ingestion asset."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="custom_group",
            owner="platform-team",
            consumers=["analytics", Consumer("ml-pipeline", contact="#ml")],
            sla=SLA(freshness_hours=6, quality_threshold=0.99),
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""

            pass

        spec = get_asset_spec("dlt_test_table")
        assert spec.metadata["owner"] == "platform-team"
        assert spec.metadata["consumers"] == [
            {"name": "analytics", "contact": None, "usage": None},
            {"name": "ml-pipeline", "contact": "#ml", "usage": None},
        ]
        assert spec.metadata["sla"] == {
            "freshness_hours": 6,
            "quality_threshold": 0.99,
            "max_failures": None,
            "notify": None,
        }


class TestAutomationConfiguration:
    """Test automation condition and scheduling configuration."""

    def test_cron_schedule_applied(self):
        """Test cron schedule is applied when provided."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
            cron="0 */1 * * *",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check automation condition is set
        spec = get_asset_spec("dlt_test_table")
        assert spec.run is not None
        assert spec.run.cron == "0 */1 * * *"

    def test_no_cron_means_no_automation(self):
        """Test no automation condition when cron not provided."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check no automation condition
        spec = get_asset_spec("dlt_test_table")
        assert spec.run is not None
        assert spec.run.cron is None


class TestFreshnessConfiguration:
    """Test freshness policy configuration."""

    def test_freshness_policy_applied(self):
        """Test freshness policy is created from hours tuple."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
            freshness_hours=(1, 24),
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check freshness policy is set
        spec = get_asset_spec("dlt_test_table")
        assert spec.run is not None
        assert spec.run.freshness_hours == (1, 24)

    def test_no_freshness_when_not_provided(self):
        """Test no freshness policy when freshness_hours not provided."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check no freshness policy
        spec = get_asset_spec("dlt_test_table")
        assert spec.run is not None
        assert spec.run.freshness_hours is None


class TestRetryConfiguration:
    """Test retry policy configuration."""

    def test_default_retry_policy(self):
        """Test default retry policy configuration."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check retry policy exists
        spec = get_asset_spec("dlt_test_table")
        assert spec.run is not None
        assert spec.run.max_retries == 3  # Default

    def test_custom_retry_configuration(self):
        """Test custom max_retries and retry_delay configuration."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
            max_retries=5,
            retry_delay_seconds=60,
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check retry policy exists
        spec = get_asset_spec("dlt_test_table")
        assert spec.run is not None
        assert spec.run.max_retries == 5
        assert spec.run.retry_delay_seconds == 60


class TestAssetRegistration:
    """Test asset registration and discovery."""

    def test_decorated_asset_registered(self):
        """Test decorated asset is added to _INGESTION_ASSETS."""

        # Clear registry before test
        initial_count = len(get_ingestion_assets())

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="registration_test",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check asset was registered
        assets = get_ingestion_assets()
        assert len(assets) == initial_count + 1
        assert any(spec.key == "dlt_registration_test" for spec in assets)

    def test_get_ingestion_assets_returns_copy(self):
        """Test get_ingestion_assets() returns a copy of registered assets."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="copy_test",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        assets = get_ingestion_assets()

        # Should return a list
        assert isinstance(assets, list)

        # Modifying returned list should not affect internal registry
        original_length = len(assets)
        assets.append(assets[0])
        assert len(get_ingestion_assets()) == original_length


class TestAssetAttributes:
    """Test Dagster asset attributes."""

    def test_asset_name_format(self):
        """Test asset name follows dlt_{table_name} format."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="github_events",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check asset name
        spec = get_asset_spec("dlt_github_events")
        assert spec.key == "dlt_github_events"

    def test_asset_has_description(self):
        """Test asset preserves function docstring as description."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Custom docstring for this asset."""
            pass

        # Check description
        spec = get_asset_spec("dlt_test_table")
        assert "Custom docstring" in spec.description

    def test_asset_compute_kind(self):
        """Test asset has correct compute kind."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        spec = get_asset_spec("dlt_test_table")
        assert "dlt" in spec.kinds
        assert "table_store" in spec.kinds

    def test_asset_has_partitions_def(self):
        """Test asset has partitions_def configured."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Check partitions def
        spec = get_asset_spec("dlt_test_table")
        assert spec.partitions is not None
        assert spec.partitions.kind == "daily"

    def test_asset_can_override_capability_selection(self):
        """Test asset stores explicit capability overrides."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
            capabilities={"table_store": "delta"},
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        spec = get_asset_spec("dlt_test_table")
        assert spec.capability_overrides == {"table_store": "delta"}
        assert spec.resources == set()


class TestComplexSchemas:
    """Test decorator with complex real-world schemas."""

    def test_github_events_like_schema(self):
        """Test decorator with GitHub events-like schema."""

        class GitHubEvents(DataFrameModel):
            """Pandera schema representing GitHub events test data."""

            id: str = Field(nullable=False, unique=True, description="Event ID")
            type: str = Field(nullable=False)
            actor: str = Field(nullable=False)
            repo: str = Field(nullable=False)
            created_at: datetime = Field(nullable=False)
            public: bool = Field(nullable=False)

        @phlo_ingestion(
            table_name="github_user_events",
            unique_key="id",
            validation_schema=GitHubEvents,
            group="github",
            cron="0 */1 * * *",
            freshness_hours=(1, 24),
        )
        def github_events(partition_date: str):
            """Ingest GitHub user events."""
            pass

        # Check asset configured correctly
        spec = get_asset_spec("dlt_github_user_events")
        assert spec.key == "dlt_github_user_events"
        assert spec.group == "github"
        assert spec.run is not None
        assert spec.run.cron == "0 */1 * * *"
        assert spec.run.freshness_hours == (1, 24)

    def test_glucose_entries_like_schema(self):
        """Test decorator with Nightscout glucose-like schema."""

        class GlucoseEntries(DataFrameModel):
            """Pandera schema representing glucose entries test data."""

            _id: str = Field(nullable=False, unique=True)
            sgv: int = Field(ge=1, le=1000, nullable=False)
            date: int = Field(nullable=False)
            date_string: datetime = Field(nullable=False)
            direction: str | None = Field(nullable=True)

        @phlo_ingestion(
            table_name="glucose_entries",
            unique_key="_id",
            validation_schema=GlucoseEntries,
            group="nightscout",
            cron="0 */1 * * *",
            freshness_hours=(1, 24),
            max_runtime_seconds=600,
        )
        def glucose_entries(partition_date: str):
            """Ingest Nightscout glucose entries."""
            pass

        # Check asset configured correctly
        spec = get_asset_spec("dlt_glucose_entries")
        assert spec.key == "dlt_glucose_entries"
        assert spec.group == "nightscout"
        assert spec.run is not None
        assert spec.run.max_runtime_seconds == 600


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_partition_spec_optional(self):
        """Test partition_spec is optional."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
            partition_spec=None,
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Should create asset successfully
        assert test_asset is not None

    def test_validate_flag_optional(self):
        """Test validate flag is optional."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
            validate=False,
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        # Should create asset successfully
        assert test_asset is not None

    def test_max_runtime_configuration(self):
        """Test max_runtime_seconds is applied to op_tags."""

        class TestSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        @phlo_ingestion(
            table_name="test_table",
            unique_key="id",
            validation_schema=TestSchema,
            group="test",
            max_runtime_seconds=900,
        )
        def test_asset(partition_date: str):
            """Placeholder asset function used for decorator registration tests."""
            pass

        spec = get_asset_spec("dlt_test_table")
        assert spec.run is not None
        assert spec.run.max_runtime_seconds == 900
