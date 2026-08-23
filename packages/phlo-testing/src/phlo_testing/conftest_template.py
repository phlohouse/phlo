"""Conftest template for user projects.

This module provides a ready-to-use conftest.py template that users can copy
to their tests/ directory to get all phlo_testing fixtures automatically.

The template includes:
    - All standard phlo_testing fixtures (mock resources, test data, etc.)
    - Environment reset fixture for test isolation
    - Project root fixture for path resolution

Usage:
    >>> from phlo_testing.conftest_template import CONFTEST_TEMPLATE, get_conftest_template
    >>> from pathlib import Path
    >>> # Write template to tests/conftest.py
    >>> Path("tests/conftest.py").write_text(get_conftest_template())
    >>> # Or use the constant directly
    >>> print(CONFTEST_TEMPLATE)

The fixtures included in the template:
    - mock_iceberg_catalog: Fresh MockIcebergCatalog for each test
    - mock_trino: Fresh MockTrinoResource for each test
    - mock_asset_context: MockAssetContext with logging capture
    - sample_partition_date: Standard test partition date
    - sample_dlt_data: Sample DLT source data
    - temp_staging_dir: Temporary directory for test files
    - And more...
"""

CONFTEST_TEMPLATE = '''"""
Pytest configuration and shared fixtures.

Place this file in tests/ directory to make fixtures available to all tests.
"""

import pytest
from pathlib import Path

# Import fixtures from phlo_testing
from phlo_testing.fixtures import (
    mock_iceberg_catalog,
    mock_trino,
    mock_asset_context,
    mock_resources,
    sample_partition_date,
    sample_partition_range,
    sample_dlt_data,
    sample_dataframe,
    mock_dlt_source_fixture,
    temp_staging_dir,
    test_data_dir,
    setup_test_catalog,
    setup_test_trino,
    load_json_fixture,
    load_csv_fixture,
    test_config,
)


@pytest.fixture(autouse=True)
def reset_test_env(monkeypatch):
    """Reset environment variables before each test.

    Ensures test isolation by setting PHLO_ENV and PHLO_LOG_LEVEL
    before each test execution.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("PHLO_ENV", "test")
    monkeypatch.setenv("PHLO_LOG_LEVEL", "DEBUG")


@pytest.fixture
def project_root() -> Path:
    """Return path to project root.

    Returns:
        Path to the project root directory (where pyproject.toml is located).
    """
    return Path(__file__).parent.parent
'''


def get_conftest_template() -> str:
    """Get the conftest.py template content.

    Returns the string content for conftest.py that can be written to a
    file.

    Example:
        >>> template = get_conftest_template()
        >>> Path("tests/conftest.py").write_text(template)

    """
    return CONFTEST_TEMPLATE
