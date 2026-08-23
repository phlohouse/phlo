"""Tests for Delta Lake settings alias handling.

Credentials and region come from the shared AWS env aliases while the S3
endpoint deliberately stays host-reachable for host-side usage.
"""

from phlo_delta.settings import DeltaSettings


def test_delta_settings_default_to_localhost_for_host_side_usage(tmp_path) -> None:
    """Delta settings should default to a host-reachable MinIO endpoint."""
    settings = DeltaSettings(_project_root=tmp_path)

    assert settings.delta_s3_endpoint == "http://localhost:9000"


def test_delta_settings_use_standard_aws_aliases(monkeypatch, tmp_path) -> None:
    """Delta settings should honor the shared AWS env vars exposed in services."""
    monkeypatch.setenv("AWS_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "example-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "example-secret")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")

    settings = DeltaSettings(_project_root=tmp_path)
    # Only credentials and region come from the shared AWS aliases; the
    # endpoint deliberately stays host-reachable even though AWS_S3_ENDPOINT
    # names the in-network service address.
    assert settings.delta_s3_endpoint == "http://localhost:9000"
    assert settings.delta_s3_access_key == "example-key"
    assert settings.delta_s3_secret_key == "example-secret"
    assert settings.delta_s3_region == "eu-west-2"
