"""
Phlo Testing Infrastructure

Comprehensive testing module for validating Phlo workflows without Docker.
Provides mock implementations of Iceberg, Trino, and DLT for fast, isolated tests.

## Phase 1: Core Mocks (✅ Implemented)

### MockIcebergCatalog (Task 1.1)
In-memory Iceberg catalog backed by DuckDB for fast table operations.

```python
from phlo_testing import MockIcebergCatalog

catalog = MockIcebergCatalog()
schema = pa.schema([("id", pa.int32()), ("name", pa.string())])
table = catalog.create_table("raw.users", schema=schema)
```

### mock_dlt_source (Task 1.2)
Mock DLT sources that return predefined data without API calls.

```python
from phlo_testing import mock_dlt_source

data = [{"id": 1, "name": "Alice"}]
source = mock_dlt_source(data, resource_name="users")
```

## Phase 2: Execution & Resources (✅ Implemented)

### test_asset_execution (Task 1.3)
Execute assets with mocked dependencies and capture results.

```python
from phlo_testing import test_asset_execution

result = test_asset_execution(
    my_asset,
    partition="2024-01-01",
    mock_data=[{"id": 1, "name": "Alice"}],
)
assert result.success
assert len(result.data) == 1
```

### MockTrinoResource (Task 1.4)
Mock Trino resource backed by DuckDB for SQL testing.

```python
from phlo_testing import MockTrinoResource

trino = MockTrinoResource()
cursor = trino.cursor()
cursor.execute("SELECT * FROM users")
```

### pytest Fixtures (Task 1.5)
Reusable fixtures for common test scenarios.

```python
def test_my_asset(mock_iceberg_catalog, mock_trino, sample_partition_date):
    # Fixtures automatically provided
    pass
```

### Local Test Mode (Task 1.6)
Enable `phlo test --local` without Docker.

```python
from phlo_testing import local_test_mode

with local_test_mode():
    # All resources are mocked automatically
    pass
```

## Quick Start

### 1. Basic Asset Test
```python
from phlo_testing import test_asset_execution, mock_dlt_source

def test_ingestion_asset():
    data = [{"id": 1, "value": 42}]
    result = test_asset_execution(
        my_ingestion_asset,
        partition="2024-01-01",
        mock_data=data,
    )
    assert result.success
    assert len(result.data) == 1
```

### 2. Using Fixtures
```python
def test_with_fixtures(mock_iceberg_catalog, sample_partition_date):
    schema = create_schema()
    table = mock_iceberg_catalog.create_table(
        "raw.test",
        schema=schema,
    )
    # Use in your test
```

### 3. Fixture Recording
```python
from phlo_testing import FixtureRecorder

recorder = FixtureRecorder()
data = recorder.record_dlt_source(
    "users",
    my_api_fetch_function,
)
# Data is saved for replay in tests
```

## Performance

- MockIcebergCatalog: < 100ms per operation
- MockTrinoResource: < 10ms per query
- test_asset_execution: < 1 second typical
- Full test suite: < 5 seconds

## Features

✅ Drop-in replacements for production resources
✅ DuckDB-backed for compatibility with Trino SQL
✅ Automatic resource cleanup
✅ Fixture recording and playback
✅ Context manager and fixture support
✅ Error injection for testing failure paths
✅ Session and function-scoped fixtures

## Modules

- `mock_iceberg.py` - MockIcebergCatalog and table operations
- `mock_dlt.py` - Mock DLT sources and pipelines
- `mock_trino.py` - MockTrinoResource and SQL execution
- `execution.py` - Asset execution with mocked dependencies
- `fixtures.py` - pytest fixtures for common scenarios
- `local_mode.py` - Local test mode with fixture recording

## Testing Guide

For comprehensive testing patterns and best practices, see:
`docs/TESTING_GUIDE.md`
"""

import importlib
from phlo.logging import get_logger

# Phase 1: Core Mocks
from phlo_testing.conftest_template import (
    CONFTEST_TEMPLATE,
    get_conftest_template,
)
from phlo_testing.execution import (
    AssetTestResult,
    MockAssetContext,
    TestAssetExecutor,
    test_asset_execution,
    test_asset_with_catalog,
    test_asset_with_trino,
)

from phlo_testing.local_mode import (
    FixtureRecorder,
    LocalTestMode,
    disable_local_test_mode,
    enable_local_test_mode,
    get_fixture_dir,
    is_local_test_mode,
    local_test,
    local_test_mode,
    set_fixture_dir,
)
from phlo_testing.mock_dlt import (
    MockDLTError,
    MockDLTResource,
    MockDLTSource,
    create_mock_dlt_dataframe,
    mock_dlt_pipeline,
    mock_dlt_source,
    mock_dlt_source_multi,
    mock_dlt_source_with_error,
)
from phlo_testing.mock_iceberg import (
    MockIcebergCatalog,
    MockTable,
    MockTableScan,
)

# Phase 2: Execution & Resources
from phlo_testing.mock_trino import (
    MockConnection,
    MockCursor,
    MockTrinoResource,
)
from phlo_testing.hooks import (
    MockHookBus,
    capture_events,
    sample_ingestion_event,
    sample_lineage_event,
    sample_publish_event,
    sample_quality_event,
    sample_service_event,
    sample_telemetry_event,
    sample_transform_event,
)
from phlo_testing.profile_harness import (
    BUNDLED_STACK_CORE_SERVICES,
    BUNDLED_STACK_DEV_PACKAGES,
    BundledStackHarness,
    BundledStackPorts,
    bootstrap_bundled_stack_harness,
    build_bundled_stack_env_updates,
    bundled_stack_contract_enabled,
    keep_bundled_stack_running,
)
from phlo_testing.non_versioned_profile_harness import (
    NonVersionedProfileHarness,
    bootstrap_non_versioned_profile_harness,
)

logger = get_logger(__name__)

# Fixtures are auto-discovered by pytest from fixtures.py.
# Load lazily so base package import does not require pytest at runtime.
_FIXTURE_EXPORTS = [
    "mock_iceberg_catalog",
    "mock_trino",
    "mock_asset_context",
    "mock_resources",
    "mock_hook_bus",
    "sample_partition_date",
    "sample_partition_range",
    "sample_dlt_data",
    "sample_dataframe",
    "mock_dlt_source_fixture",
    "temp_staging_dir",
    "test_data_dir",
    "setup_test_catalog",
    "setup_test_trino",
    "load_json_fixture",
    "load_csv_fixture",
    "test_config",
    "create_partition_dates",
    "conftest_template",
]
_FIXTURES_AVAILABLE = False

try:
    _fixtures_module = importlib.import_module("phlo_testing.fixtures")
except ModuleNotFoundError as exc:
    if exc.name != "pytest":
        logger.warning(
            "phlo_testing_fixtures_import_failed",
            missing_module=exc.name,
            exc_info=True,
        )
        raise
    logger.debug("phlo_testing_fixtures_pytest_unavailable")
else:
    _FIXTURES_AVAILABLE = True
    _MISSING = object()
    missing_exports: list[str] = []
    for _fixture_name in _FIXTURE_EXPORTS:
        fixture = getattr(_fixtures_module, _fixture_name, _MISSING)
        if fixture is _MISSING:
            missing_exports.append(_fixture_name)
            continue
        globals()[_fixture_name] = fixture
    if missing_exports:
        logger.warning(
            "phlo_testing_fixtures_missing_exports",
            missing_exports=missing_exports,
        )
        missing = ", ".join(missing_exports)
        raise ImportError(
            f"phlo_testing.fixtures is missing exports declared in _FIXTURE_EXPORTS: {missing}"
        )

__all__ = [
    # Phase 1: Core Mocks
    "MockIcebergCatalog",
    "MockTable",
    "MockTableScan",
    "MockDLTResource",
    "MockDLTSource",
    "mock_dlt_source",
    "mock_dlt_source_multi",
    "mock_dlt_source_with_error",
    "mock_dlt_pipeline",
    "create_mock_dlt_dataframe",
    "MockDLTError",
    # Phase 2: Execution & Resources
    "MockTrinoResource",
    "MockConnection",
    "MockCursor",
    "test_asset_execution",
    "test_asset_with_catalog",
    "test_asset_with_trino",
    "AssetTestResult",
    "MockAssetContext",
    "TestAssetExecutor",
    "LocalTestMode",
    "local_test_mode",
    "local_test",
    "FixtureRecorder",
    "is_local_test_mode",
    "enable_local_test_mode",
    "disable_local_test_mode",
    "set_fixture_dir",
    "get_fixture_dir",
    "CONFTEST_TEMPLATE",
    "get_conftest_template",
    # Hooks
    "MockHookBus",
    "capture_events",
    "sample_ingestion_event",
    "sample_quality_event",
    "sample_transform_event",
    "sample_publish_event",
    "sample_lineage_event",
    "sample_telemetry_event",
    "sample_service_event",
    # Profile harnesses
    "BUNDLED_STACK_CORE_SERVICES",
    "BUNDLED_STACK_DEV_PACKAGES",
    "BundledStackHarness",
    "BundledStackPorts",
    "bootstrap_bundled_stack_harness",
    "build_bundled_stack_env_updates",
    "bundled_stack_contract_enabled",
    "keep_bundled_stack_running",
    "NonVersionedProfileHarness",
    "bootstrap_non_versioned_profile_harness",
]

if _FIXTURES_AVAILABLE:
    __all__.extend(_FIXTURE_EXPORTS)

__version__ = "0.14.0"
