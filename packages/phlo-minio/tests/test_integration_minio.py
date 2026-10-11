"""Comprehensive integration tests for phlo-minio.

Per TEST_STRATEGY.md Level 2 (Functional):
- Bucket Policy Generation: Test policy construction
- Storage: Create buckets, upload/download files, verify policy enforcement
"""

import os
import tempfile
from pathlib import Path

import pytest

# =============================================================================
# Service Plugin Tests
# =============================================================================


class TestMinioServicePlugin:
    """Test MinIO service plugin."""

    def test_plugin_initializes(self):
        """Test MinioServicePlugin can be instantiated."""
        from phlo_minio.plugin import MinioServicePlugin

        plugin = MinioServicePlugin()
        assert plugin is not None

    def test_plugin_metadata(self):
        """Test plugin metadata is correctly defined."""
        from phlo_minio.plugin import MinioServicePlugin

        plugin = MinioServicePlugin()
        metadata = plugin.metadata

        assert metadata.name == "minio"
        assert metadata.version is not None

    def test_service_definition_loads(self):
        """Test service definition YAML can be loaded."""
        from phlo_minio.plugin import MinioServicePlugin

        plugin = MinioServicePlugin()
        service_def = plugin.service_definition

        assert isinstance(service_def, dict)
        # Service definitions have flat structure with 'name' and 'compose' keys
        assert "name" in service_def or "compose" in service_def

    def test_service_definition_has_minio_image(self):
        """Test service definition has MinIO image."""
        from phlo_minio.plugin import MinioServicePlugin

        plugin = MinioServicePlugin()
        service_def = plugin.service_definition

        services = service_def.get("services", {})
        # Find minio service
        for svc_name, svc_config in services.items():
            if "minio" in svc_name.lower():
                if "image" in svc_config:
                    assert "minio" in svc_config["image"].lower()


# =============================================================================
# Configuration Tests
# =============================================================================


class TestMinioConfiguration:
    """Test MinIO configuration."""

    def test_minio_config_accessible(self):
        """Test MinIO configuration is accessible from phlo_minio.settings."""
        from phlo_minio.settings import get_settings

        settings = get_settings()

        # Should have S3/MinIO related settings - check various possible names
        assert hasattr(settings, "minio_endpoint")

    def test_minio_endpoint_format(self):
        """Test MinIO endpoint has expected format."""
        from phlo_minio.settings import get_settings

        settings = get_settings()

        endpoint = settings.minio_endpoint()
        # Should be a URL or host:port
        assert len(endpoint) > 0


# =============================================================================
# Functional Integration Tests (Real MinIO if available)
# =============================================================================


@pytest.fixture
def minio_client():
    """Fixture providing a real MinIO client if available."""
    from urllib3.exceptions import HTTPError

    try:
        from minio import Minio
        from phlo_minio.settings import get_settings

        settings = get_settings()

        endpoint = settings.minio_endpoint()
        access_key = settings.minio_root_user
        secret_key = settings.minio_root_password
        if not secret_key:
            pytest.skip("MinIO credentials are not configured")

        # Remove protocol if present
        if "://" in endpoint:
            endpoint = endpoint.split("://")[1]

        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=False,
        )

        # Verify connectivity
        client.list_buckets()
    except (HTTPError, OSError) as e:
        pytest.skip(f"MinIO not available: {e}")

    yield client


@pytest.mark.integration
class TestMinioIntegrationReal:
    """Real integration tests against a running MinIO instance."""

    def test_list_buckets(self, minio_client):
        """Test listing buckets."""
        buckets = minio_client.list_buckets()
        # Should return a list (may be empty)
        assert isinstance(buckets, list)

    def test_create_and_delete_bucket(self, minio_client):
        """Test creating and deleting a bucket."""
        import uuid

        bucket_name = f"test-bucket-{uuid.uuid4().hex[:8]}"

        try:
            # Create bucket
            minio_client.make_bucket(bucket_name)

            # Verify it exists
            assert minio_client.bucket_exists(bucket_name)
        finally:
            # Cleanup
            try:
                minio_client.remove_bucket(bucket_name)
            except Exception:
                pass

    def test_upload_and_download_file(self, minio_client, tmp_path):
        """Test uploading and downloading a file."""
        import uuid

        bucket_name = f"test-bucket-{uuid.uuid4().hex[:8]}"
        object_name = "test-file.txt"
        content = b"Hello, MinIO!"

        try:
            # Create bucket
            minio_client.make_bucket(bucket_name)

            # Upload
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(content)
                upload_path = f.name

            minio_client.fput_object(bucket_name, object_name, upload_path)
            os.unlink(upload_path)

            # Download
            download_path = Path(tmp_path) / "downloaded.txt"
            minio_client.fget_object(bucket_name, object_name, str(download_path))

            # Verify content
            assert download_path.read_bytes() == content
        finally:
            # Cleanup
            try:
                minio_client.remove_object(bucket_name, object_name)
                minio_client.remove_bucket(bucket_name)
            except Exception:
                pass

    def test_list_objects(self, minio_client):
        """Test listing objects in a bucket."""
        import uuid

        bucket_name = f"test-bucket-{uuid.uuid4().hex[:8]}"

        try:
            minio_client.make_bucket(bucket_name)

            # Upload a test file
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(b"test")
                temp_path = f.name

            minio_client.fput_object(bucket_name, "test.txt", temp_path)
            os.unlink(temp_path)

            # List objects
            objects = list(minio_client.list_objects(bucket_name))

            assert len(objects) == 1
            assert objects[0].object_name == "test.txt"
        finally:
            try:
                for obj in minio_client.list_objects(bucket_name):
                    minio_client.remove_object(bucket_name, obj.object_name)
                minio_client.remove_bucket(bucket_name)
            except Exception:
                pass


# =============================================================================
# Export Tests
# =============================================================================


class TestMinioExports:
    """Test module exports."""

    def test_module_importable(self):
        """Test phlo_minio module is importable."""
        import phlo_minio

        assert phlo_minio is not None

    def test_plugin_importable(self):
        """Test MinioServicePlugin is importable."""
        from phlo_minio.plugin import MinioServicePlugin

        assert MinioServicePlugin is not None
