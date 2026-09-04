# phlo-testing

Testing utilities for Phlo packages.

## Overview

`phlo-testing` provides shared testing utilities, fixtures, and mocks for developing and testing Phlo packages. This is a development utility library, not a runtime plugin.

## Installation

```bash
pip install phlo-testing
```

## Features

This package is a **utility library** with no auto-configuration:

| Feature       | Status                 |
| ------------- | ---------------------- |
| Entry Points  | None - utility library |
| Runtime Hooks | None - testing only    |

## Usage

### Fixtures

```python
import pytest
from phlo_testing.fixtures import (
    mock_iceberg_catalog,
    mock_nessie_client,
    mock_trino_connection,
    test_dataframe
)

def test_my_ingestion(mock_iceberg_catalog):
    # Test with mocked catalog
    catalog = mock_iceberg_catalog
    # ... test code
    pass

def test_with_nessie(mock_nessie_client):
    # Test with mocked Nessie client
    client = mock_nessie_client
    client.create_branch("test-branch")
    # ... test code
    pass
```

### Test Utilities

```python
from phlo_testing.utils import (
    create_test_dataframe,
    assert_table_exists,
    assert_schema_matches,
    wait_for_service
)

# Create test data
df = create_test_dataframe(
    columns=["id", "name", "value"],
    rows=10
)

# Assert table exists
assert_table_exists("bronze.test_table")

# Assert schema matches expected
assert_schema_matches(
    table="bronze.users",
    expected_columns=["id", "name", "email"]
)

# Wait for service to be ready
wait_for_service("http://localhost:8080/health", timeout=30)
```

### Mock Factories

```python
from phlo_testing.mocks import (
    MockDagsterContext,
    MockIcebergTable,
    MockTrinoResult
)

# Create mock Dagster context
context = MockDagsterContext(
    partition_key="2024-01-15"
)

# Create mock Iceberg table
table = MockIcebergTable(
    name="bronze.users",
    schema={"id": "string", "name": "string"}
)

# Create mock Trino result
result = MockTrinoResult(
    columns=["id", "name"],
    rows=[("1", "Alice"), ("2", "Bob")]
)
```

### Test Marker Rules

```python
import pytest

@pytest.mark.unit
def test_pure_transform_logic():
    """No network, Docker, or filesystem side effects."""
    pass

@pytest.mark.integration
def test_full_pipeline():
    """Requires Docker services to be running."""
    pass

@pytest.mark.slow
def test_large_dataset():
    """Takes a long time to run."""
    pass

# Run specific markers
# pytest -m unit
# pytest -m integration
# pytest -m "not slow"
```

Use `unit` for import-only tests, pure functions, and tests that run without external services.
Use `integration` only for tests that require service discovery, Docker containers, dbt artifacts,
or other cross-package runtime wiring.

## Common Test Patterns

### Testing Ingestion

```python
from phlo_testing.fixtures import mock_iceberg_catalog, test_dataframe

def test_ingestion_creates_table(mock_iceberg_catalog, test_dataframe):
    # Arrange
    catalog = mock_iceberg_catalog

    # Act
    # ... run ingestion

    # Assert
    assert catalog.table_exists("bronze.my_table")
```

### Contract harnesses

`phlo-testing` includes profile-level harnesses for cross-package contract tests.

- `BundledStackHarness`
  Boots a real generated project against the bundled service stack using local
  workspace source.
- `NonVersionedProfileHarness`
  Boots a local DuckDB + dbt profile for fast non-versioned verification.

These harnesses are useful when unit tests are not enough and you need to prove
that the supported capability combination actually wires together correctly.

### Support status

`phlo-testing` is supported as an **internal test contract**: first-party Phlo
packages and this repository's test suites consume it, but it is not a runtime
v1 guarantee for end users. Its harness helpers (`phlo_testing.harness_utils`,
`phlo_testing.profile_harness`) are bundled inside the package, so the
installed distribution is self-contained and does not depend on repository
scripts. The repo's golden-path E2E runner remains a standalone repo-only
debugging tool.

### Testing Quality Checks

```python
from phlo_testing.utils import create_test_dataframe
from phlo.quality import NullCheck

def test_null_check_fails_on_nulls():
    # Create data with nulls
    df = create_test_dataframe(
        columns=["id", "name"],
        rows=10,
        null_columns=["name"]
    )

    # Run check
    check = NullCheck(columns=["name"])
    result = check.execute(df, context=None)

    # Assert
    assert not result.passed
```

## Note

This package does not register any entry points as it is intended for development and testing only.

## Related Packages

- [phlo-pandera](phlo-pandera.md) - Quality checks
- [phlo-dlt](phlo-dlt.md) - Data ingestion

## Next Steps

- [Testing Strategy Guide](../guides/testing-strategy.md) - Testing approaches
- [Operations Testing](../operations/testing.md) - Integration tests
