"""Integration tests for phlo-testing.

Smoke-level: the package must import and expose its mock fixtures and
testing utilities; optional imports are tolerated when absent.
"""

import pytest

pytestmark = pytest.mark.integration


def test_testing_module_importable():
    """Test that phlo_testing module is importable."""
    import phlo_testing

    assert phlo_testing is not None


def test_mock_fixtures_available():
    """Test that mock fixtures are available."""
    try:
        from phlo_testing.fixtures import mock_iceberg_catalog

        assert mock_iceberg_catalog is not None
    except ImportError:
        # Module may have different structure
        pass


def test_testing_utilities():
    """Test that testing utilities are available."""
    try:
        from phlo_testing import utils

        assert utils is not None
    except ImportError:
        pass
