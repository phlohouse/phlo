# phlo-testing

Testing utilities for Phlo packages.

## Description

Shared testing utilities, fixtures, and mocks for developing and testing Phlo packages. This is a development utility library, not a runtime plugin.

## Installation

```bash
uv add phlo-testing
```

## Usage

### Fixtures

```python
import pytest
from phlo_testing.fixtures import mock_iceberg_catalog, mock_nessie_client

def test_my_ingestion(mock_iceberg_catalog):
    # Test with mocked catalog
    pass
```

### Test Utilities

```python
from phlo_testing.utils import create_test_dataframe, assert_table_exists

# Create test data
df = create_test_dataframe(columns=["id", "name"], rows=10)

# Assert table exists
assert_table_exists("bronze.test_table")
```

## Auto-Configuration

This package is a **utility library** with no auto-configuration:

| Feature       | Status                 |
| ------------- | ---------------------- |
| Entry Points  | None - utility library |
| Runtime Hooks | None - testing only    |

## Note

This package does not register any entry points as it is intended for development and testing only.
