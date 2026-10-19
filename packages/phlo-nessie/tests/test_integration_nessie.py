"""Comprehensive integration tests for phlo-nessie.

Per TEST_STRATEGY.md Level 2 (Functional):
- Configuration Validation: Test config is accessible
- Branching: Create branches, commit changes, merge branches via Nessie API
"""

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

# =============================================================================
# Service Plugin Tests
# =============================================================================


class TestNessieServicePlugin:
    """Test Nessie service plugin."""

    def test_plugin_initializes(self):
        """Test NessieServicePlugin can be instantiated."""
        from phlo_nessie.plugin import NessieServicePlugin

        plugin = NessieServicePlugin()
        assert plugin is not None

    def test_plugin_metadata(self):
        """Test plugin metadata is correctly defined."""
        from phlo_nessie.plugin import NessieServicePlugin

        plugin = NessieServicePlugin()
        metadata = plugin.metadata

        assert metadata.name == "nessie"
        assert metadata.version is not None

    def test_service_definition_loads(self):
        """Test service definition YAML can be loaded."""
        from phlo_nessie.plugin import NessieServicePlugin

        plugin = NessieServicePlugin()
        service_def = plugin.service_definition

        assert isinstance(service_def, dict)


# =============================================================================
# Configuration Tests
# =============================================================================


class TestNessieConfiguration:
    """Test Nessie configuration."""

    def test_nessie_config_accessible(self):
        """Test Nessie configuration is accessible from phlo_nessie.settings."""
        from phlo_nessie.settings import get_settings

        settings = get_settings()

        assert hasattr(settings, "nessie_host")

    def test_nessie_url_format(self):
        """Test Nessie URL has expected format."""
        from phlo_nessie.settings import get_settings

        settings = get_settings()

        uri = settings.nessie_api_uri()
        # Should contain api version path or be valid URL
        assert len(uri) > 0


# =============================================================================
# NessieResource Tests (Unit)
# =============================================================================


class TestNessieResourceUnit:
    """Unit tests for NessieResource."""

    def test_resource_initialization_default(self):
        """Test NessieResource initializes with defaults."""
        from phlo_nessie.resource import NessieResource

        resource = NessieResource()
        assert resource.base_url is not None
        assert "nessie" in resource.base_url.lower() or "localhost" in resource.base_url.lower()

    def test_resource_initialization_custom_url(self):
        """Test NessieResource initializes with custom URL."""
        from phlo_nessie.resource import NessieResource

        resource = NessieResource(base_url="http://custom-nessie:19120")
        assert resource.base_url == "http://custom-nessie:19120"

    def test_url_construction(self):
        """Test URL construction method."""
        from phlo_nessie.resource import NessieResource

        resource = NessieResource(base_url="http://nessie:19120")
        url = resource._url("/api/v1/trees")

        assert url == "http://nessie:19120/api/v1/trees"


class TestNessieResourceMocked:
    """Test NessieResource with mocked HTTP responses."""

    def test_list_branches_mocked(self):
        """Test listing branches with mocked response."""
        from phlo_nessie.resource import NessieResource, BranchInfo

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "references": [
                {"type": "BRANCH", "name": "main", "hash": "abc123"},
                {"type": "BRANCH", "name": "dev", "hash": "def456"},
                {"type": "TAG", "name": "v1.0", "hash": "tag789"},  # Should be filtered
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            resource = NessieResource(base_url="http://nessie:19120")
            branches = resource.list_branches()

            assert len(branches) == 2
            assert all(isinstance(b, BranchInfo) for b in branches)
            branch_names = [b.name for b in branches]
            assert "main" in branch_names
            assert "dev" in branch_names

    def test_get_branch_hash_mocked(self):
        """Test getting branch hash with mocked response."""
        from phlo_nessie.resource import NessieResource

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"hash": "abc123def456"}

        with patch("requests.get", return_value=mock_response):
            resource = NessieResource(base_url="http://nessie:19120")
            hash_value = resource.get_branch_hash("main")

            assert hash_value == "abc123def456"

    def test_get_branch_hash_not_found(self):
        """Test getting hash for non-existent branch."""
        from phlo_nessie.resource import NessieResource

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("requests.get", return_value=mock_response):
            resource = NessieResource(base_url="http://nessie:19120")
            hash_value = resource.get_branch_hash("nonexistent")

            assert hash_value is None

    def test_delete_branch_mocked(self):
        """Test deleting a branch with mocked response."""
        from phlo_nessie.resource import NessieResource

        # First call returns hash
        mock_get_response = MagicMock()
        mock_get_response.status_code = 200
        mock_get_response.json.return_value = {"hash": "abc123"}

        # Second call is delete
        mock_delete_response = MagicMock()
        mock_delete_response.status_code = 204

        with patch("requests.get", return_value=mock_get_response):
            with patch("requests.delete", return_value=mock_delete_response):
                resource = NessieResource(base_url="http://nessie:19120")
                result = resource.delete_branch("feature-branch")

                assert result is True


# =============================================================================
# BranchManagerResource Tests
# =============================================================================


class TestBranchManagerResource:
    """Test BranchManagerResource."""

    def test_get_all_pipeline_branches(self):
        """Test getting pipeline branches (excludes main/dev)."""
        from phlo_nessie.resource import BranchManagerResource, BranchInfo, NessieResource

        mock_nessie = MagicMock(spec=NessieResource)
        mock_nessie.list_branches.return_value = [
            BranchInfo(name="main", hash="abc", created_at=None),
            BranchInfo(name="dev", hash="def", created_at=None),
            BranchInfo(name="feature-123", hash="ghi", created_at=None),
            BranchInfo(name="pipeline-456", hash="jkl", created_at=None),
        ]

        manager = BranchManagerResource(nessie=mock_nessie)
        branches = manager.get_all_pipeline_branches()

        # Should exclude main and dev
        assert len(branches) == 2
        branch_names = [b.name for b in branches]
        assert "main" not in branch_names
        assert "dev" not in branch_names
        assert "feature-123" in branch_names

    def test_cleanup_branch(self):
        """Test cleaning up a branch."""
        from phlo_nessie.resource import BranchManagerResource, NessieResource

        mock_nessie = MagicMock(spec=NessieResource)
        mock_nessie.delete_branch.return_value = True

        manager = BranchManagerResource(nessie=mock_nessie)
        result = manager.cleanup_branch("stale-branch")

        assert result is True
        mock_nessie.delete_branch.assert_called_once_with("stale-branch")


# =============================================================================
# BranchInfo Tests
# =============================================================================


class TestBranchInfo:
    """Test BranchInfo dataclass."""

    def test_branch_info_creation(self):
        """Test BranchInfo can be created."""
        from phlo_nessie.resource import BranchInfo

        branch = BranchInfo(name="feature-branch", hash="abc123", created_at=datetime.now())

        assert branch.name == "feature-branch"
        assert branch.hash == "abc123"
        assert branch.created_at is not None

    def test_branch_info_optional_fields(self):
        """Test BranchInfo with optional fields as None."""
        from phlo_nessie.resource import BranchInfo

        branch = BranchInfo(name="main", hash=None, created_at=None)

        assert branch.name == "main"
        assert branch.hash is None
        assert branch.created_at is None


# =============================================================================
# Functional Integration Tests (Real Nessie if available)
# =============================================================================


@pytest.fixture
def nessie_client():
    """Fixture providing a real Nessie client if available."""
    from phlo_nessie.resource import NessieResource
    import requests

    resource = NessieResource()
    try:
        response = requests.get(f"{resource.base_url}/api/v1/trees", timeout=5)
    except (requests.ConnectionError, requests.Timeout) as e:
        pytest.skip(f"Nessie not available: {e}")

    assert response.status_code == 200, f"Nessie health check failed: {response.status_code}"
    yield resource


@pytest.mark.integration
class TestNessieIntegrationReal:
    """Real integration tests against a running Nessie instance."""

    def test_pinned_publication_is_strict_fast_forward(self, nessie_client, monkeypatch):
        """Independent target writes and mutable/recreated sources cannot redirect WAP."""
        import uuid
        import requests

        catalog = nessie_client
        prefix = f"wap-cas-{uuid.uuid4().hex[:8]}"
        target, source = f"{prefix}-qa", f"{prefix}-candidate"
        main_before = catalog.get_branch_hash("main")

        def commit(branch, key):
            response = requests.post(
                catalog._url(f"/api/v1/trees/branch/{branch}/commit"),
                params={"expectedHash": catalog.get_branch_hash(branch)},
                json={
                    "commitMeta": {"message": f"disposable WAP {key}"},
                    "operations": [
                        {
                            "type": "PUT",
                            "key": {"elements": [key]},
                            "content": {"type": "NAMESPACE", "elements": [key], "properties": {}},
                        }
                    ],
                },
                timeout=10,
            )
            response.raise_for_status()
            return catalog.get_branch_hash(branch)

        try:
            assert catalog.create_branch(target)
            assert catalog.create_branch(source, target)
            before = catalog.get_branch_hash(target)
            # Keep the captured target beyond even the server's default page,
            # so an ignored continuation parameter cannot prove ancestry.
            for index in range(251):
                commit(source, f"history-{index}")
            audited = commit(source, "audited")
            later = commit(source, "unaudited")
            first_page = requests.get(
                catalog._url(f"/api/v2/trees/{source}@{audited}/history"),
                params={"max-records": 100},
                timeout=10,
            )
            first_page.raise_for_status()
            history = first_page.json()
            assert history["hasMore"]
            assert all(
                entry["commitMeta"]["hash"] != before and entry.get("parentCommitHash") != before
                for entry in history["logEntries"]
            )
            assert catalog.merge_branch(
                source,
                target,
                expected_target_hash=before,
                expected_source_hash=audited,
            )
            assert catalog.get_branch_hash(target) == audited != later
            assert catalog.get_branch_hash(source) == later
            # Deletion/recreation cannot replace the immutable audited commit.
            assert catalog.delete_branch(source)
            assert catalog.create_branch(source, "main")
            assert not catalog.merge_branch(
                source,
                target,
                expected_target_hash=audited,
                expected_source_hash=later,
            )
            assert catalog.get_branch_hash(target) == audited
            assert catalog.delete_branch(source)
            assert catalog.create_branch(source, target)
            candidate = commit(source, "candidate")
            original_request = catalog._request
            target_movement = []

            def move_target_at_put(method, url, **kwargs):
                if method == "PUT":
                    assert kwargs["params"]["expectedHash"] == audited
                    assert kwargs["json"]["hash"] == candidate
                    target_movement.append(commit(target, "independent-key"))
                return original_request(method, url, **kwargs)

            with monkeypatch.context() as boundary:
                boundary.setattr(catalog, "_request", move_target_at_put)
                assert not catalog.merge_branch(
                    source,
                    target,
                    expected_target_hash=audited,
                    expected_source_hash=candidate,
                )
            assert len(target_movement) == 1
            assert catalog.get_branch_hash(target) == target_movement[0]
            assert catalog.get_branch_hash(source) == candidate
            assert catalog.delete_branch(source)
            assert catalog.create_branch(source, target)
            candidate = commit(source, "lost-response")
            before = catalog.get_branch_hash(target)
            put = requests.put
            publication_requests = []

            def lose_response(*args, **kwargs):
                response = put(*args, **kwargs)
                response.raise_for_status()
                publication_requests.append(response.status_code)
                raise requests.ConnectionError("disposable response lost after CAS")

            with monkeypatch.context() as boundary:
                boundary.setattr(requests, "put", lose_response)
                with pytest.raises(requests.ConnectionError):
                    catalog.merge_branch(
                        source,
                        target,
                        expected_target_hash=before,
                        expected_source_hash=candidate,
                    )
            assert len(publication_requests) == 1
            assert catalog.get_branch_hash(target) == candidate
            assert catalog.get_branch_hash(source) == candidate
            assert catalog.get_branch_hash("main") == main_before
        finally:
            catalog.delete_branch(source)
            catalog.delete_branch(target)

    def test_list_branches(self, nessie_client):
        """Test listing branches from real Nessie."""
        branches = nessie_client.list_branches()

        # Should have at least main branch
        assert len(branches) >= 1
        branch_names = [b.name for b in branches]
        assert "main" in branch_names

    def test_get_main_branch_hash(self, nessie_client):
        """Test getting main branch hash."""
        hash_value = nessie_client.get_branch_hash("main")

        assert hash_value is not None
        assert len(hash_value) > 0

    def test_create_and_delete_branch(self, nessie_client):
        """Test creating and deleting a branch."""
        import uuid
        import requests

        branch_name = f"test-branch-{uuid.uuid4().hex[:8]}"

        try:
            # Get main hash
            main_hash = nessie_client.get_branch_hash("main")

            # Create branch from main
            response = requests.post(
                nessie_client._url("/api/v1/trees/tree"),
                json={
                    "name": branch_name,
                    "type": "BRANCH",
                    "hash": main_hash,
                },
                timeout=10,
            )

            if response.status_code < 300:
                # Verify branch exists
                branches = nessie_client.list_branches()
                branch_names = [b.name for b in branches]
                assert branch_name in branch_names
        finally:
            # Cleanup
            nessie_client.delete_branch(branch_name)


# =============================================================================
# Catalog Scanner Tests
# =============================================================================


class TestCatalogScanner:
    """Test catalog scanner functionality."""

    def test_catalog_scanner_importable(self):
        """Test catalog scanner module is importable."""
        from phlo_nessie import catalog_scanner

        assert catalog_scanner is not None


# =============================================================================
# Export Tests
# =============================================================================


class TestNessieExports:
    """Test module exports."""

    def test_module_importable(self):
        """Test phlo_nessie module is importable."""
        import phlo_nessie

        assert phlo_nessie is not None

    def test_expected_exports(self):
        """Test expected classes are exported."""
        from phlo_nessie import NessieResource, NessieServicePlugin

        assert NessieResource is not None
        assert NessieServicePlugin is not None
