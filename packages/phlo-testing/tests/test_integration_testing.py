"""Public import contracts for phlo-testing.

The package must expose its mock fixtures and utility module.
"""


def test_testing_module_importable():
    """Test that phlo_testing module is importable."""
    import phlo_testing

    assert phlo_testing is not None


def test_mock_fixtures_available():
    """Test that mock fixtures are available."""
    from phlo_testing.fixtures import mock_iceberg_catalog

    assert callable(mock_iceberg_catalog)


def test_testing_utilities():
    """Test that testing utilities are available."""
    from phlo_testing import utils

    assert callable(utils.to_dataframe)
    assert callable(utils.to_records)
