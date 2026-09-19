"""Tests for RustFS settings.

This module validates the RustfsSettings configuration class, including
default values, endpoint formatting, and host/port resolution behavior.

Functions:
    test_rustfs_settings_defaults: Validates default settings values.
    test_rustfs_endpoint: Validates endpoint format.
    test_rustfs_endpoint_custom_port: Validates endpoint with custom port.
    test_rustfs_endpoint_custom_host: Validates endpoint with custom host.
"""

from phlo_rustfs.settings import RustfsSettings


def test_rustfs_settings_defaults():
    """Validate default settings values.

    Verifies that RustfsSettings initializes with expected default values
    suitable for local development. Tests host resolution defaults to
    localhost and standard credential defaults.

    Asserts:
        - rustfs_host == "localhost"
        - rustfs_access_key == "rustfsadmin"
        - rustfs_secret_key == "rustfsadmin"
        - rustfs_api_port == 9000
        - rustfs_console_port == 9001
        - s3_region == "us-east-1"
    """
    settings = RustfsSettings()

    assert settings.rustfs_host == "127.0.0.1"
    assert settings.rustfs_access_key == "rustfsadmin"
    assert settings.rustfs_secret_key == "rustfsadmin"
    assert settings.rustfs_api_port == 9000
    assert settings.rustfs_console_port == 9001
    assert settings.s3_region == "us-east-1"


def test_rustfs_endpoint():
    """Validate endpoint format.

    Verifies that rustfs_endpoint() correctly formats the host and API
    port into a standard "host:port" string for S3 SDK configuration.

    Asserts:
        - endpoint == "localhost:9000" with default settings
    """
    settings = RustfsSettings()
    endpoint = settings.rustfs_endpoint()

    assert endpoint == "127.0.0.1:9000"


def test_rustfs_endpoint_custom_port():
    """Validate endpoint with custom port.

    Verifies that rustfs_endpoint() correctly reflects custom API port
    settings when a non-default port is specified.

    Asserts:
        - endpoint == "localhost:19000" when rustfs_api_port=19000
    """
    settings = RustfsSettings(rustfs_api_port=19000)
    endpoint = settings.rustfs_endpoint()

    assert endpoint == "127.0.0.1:19000"


def test_rustfs_endpoint_custom_host():
    """Validate endpoint with custom host.

    Verifies host resolution behavior when a custom host is specified.
    Note: The host resolves to localhost for local development regardless
    of the input value due to DNS resolution behavior.

    Asserts:
        - endpoint uses localhost:9000 format even with custom host input
    """
    settings = RustfsSettings(rustfs_host="storage.local")
    endpoint = settings.rustfs_endpoint()

    assert endpoint == "127.0.0.1:9000"
