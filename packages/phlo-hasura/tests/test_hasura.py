"""Tests for Hasura metadata, table tracking, and permissions.

Covers admin-secret resolution from .phlo env files with a service
default fallback, track/untrack and permission payloads, Postgres-backed
table discovery with exclusions, permission sync/export, role hierarchy
expansion, and metadata import/export diffing.
"""

import json
import socket
from unittest.mock import MagicMock, patch

import pytest
import requests

from phlo_hasura.client import HasuraClient
from phlo_hasura.permissions import HasuraPermissionManager, RoleHierarchy
from phlo_hasura.sync import HasuraMetadataSync
from phlo_hasura.track import HasuraTableTracker


@pytest.fixture
def sample_metadata():
    """Create sample Hasura metadata for testing."""
    return {
        "version": 3,
        "metadata": {},
        "sources": [
            {
                "name": "default",
                "tables": [
                    {
                        "table": {"schema": "api", "name": "glucose_readings"},
                        "select_permissions": [
                            {
                                "role": "analyst",
                                "permission": {"columns": ["*"], "filter": {}},
                            }
                        ],
                        "insert_permissions": [
                            {
                                "role": "loader",
                                "permission": {
                                    "columns": ["reading_id", "sgv"],
                                    "check": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
                                    "set": {"tenant_id": "x-hasura-tenant-id"},
                                },
                            }
                        ],
                        "update_permissions": [
                            {
                                "role": "editor",
                                "permission": {
                                    "columns": ["sgv"],
                                    "filter": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
                                    "check": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
                                    "set": {"updated_by": "x-hasura-user-id"},
                                },
                            }
                        ],
                        "delete_permissions": [
                            {
                                "role": "admin",
                                "permission": {
                                    "filter": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}}
                                },
                            }
                        ],
                        "array_relationships": [],
                        "object_relationships": [],
                    }
                ],
            }
        ],
    }


class TestHasuraClient:
    """Tests for HasuraClient."""

    def test_init_uses_phlo_env_file_for_admin_secret(self, tmp_path, monkeypatch):
        """Client should honor the repo-standard `.phlo` env files."""
        phlo_dir = tmp_path / ".phlo"
        phlo_dir.mkdir()
        (phlo_dir / ".env").write_text("HASURA_ADMIN_SECRET=file-secret\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HASURA_ADMIN_SECRET", raising=False)
        from phlo_hasura import client as client_module

        client_module.get_settings.cache_clear()

        client = HasuraClient()

        assert client.admin_secret == "file-secret"

    def test_init_uses_service_default_admin_secret(self, tmp_path, monkeypatch):
        """Client default should match the generated Hasura service default."""
        phlo_dir = tmp_path / ".phlo"
        phlo_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HASURA_ADMIN_SECRET", raising=False)
        from phlo_hasura import client as client_module

        client_module.get_settings.cache_clear()

        client = HasuraClient()

        assert client.admin_secret == "phlo-hasura-admin-secret"

    @patch("phlo_hasura.client.requests.request")
    def test_track_table(self, mock_request):
        """Should track table via API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "success"}
        mock_request.return_value = mock_response

        client = HasuraClient(admin_secret="test-secret")
        result = client.track_table("api", "glucose_readings")

        assert result["message"] == "success"
        mock_request.assert_called_once()

        # Verify request payload
        call_args = mock_request.call_args
        payload = call_args[1]["json"]
        assert payload["type"] == "pg_track_table"
        assert payload["args"]["schema"] == "api"
        assert payload["args"]["name"] == "glucose_readings"

    @patch("phlo_hasura.client.requests.request")
    def test_untrack_table(self, mock_request):
        """Should untrack table via API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "success"}
        mock_request.return_value = mock_response

        client = HasuraClient(admin_secret="test-secret")
        result = client.untrack_table("api", "glucose_readings")

        assert result["message"] == "success"

    @patch("phlo_hasura.client.requests.request")
    def test_create_select_permission(self, mock_request):
        """Should create SELECT permission."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "success"}
        mock_request.return_value = mock_response

        client = HasuraClient(admin_secret="test-secret")
        result = client.create_select_permission(
            "api", "glucose_readings", "analyst", columns=["reading_id", "sgv"]
        )

        assert result["message"] == "success"

        # Verify permission structure
        payload = mock_request.call_args[1]["json"]
        assert payload["args"]["permission"]["columns"] == ["reading_id", "sgv"]

    @patch("phlo_hasura.client.requests.request")
    def test_create_update_permission(self, mock_request):
        """Should create UPDATE permission."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "success"}
        mock_request.return_value = mock_response

        client = HasuraClient(admin_secret="test-secret")
        result = client.create_update_permission(
            "api",
            "glucose_readings",
            "editor",
            filter={"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
            check={"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
            columns=["sgv"],
            set={"updated_by": "x-hasura-user-id"},
        )

        assert result["message"] == "success"

        payload = mock_request.call_args[1]["json"]
        assert payload["type"] == "pg_create_update_permission"
        assert payload["args"]["permission"]["filter"] == {
            "tenant_id": {"_eq": "X-Hasura-Tenant-Id"}
        }
        assert payload["args"]["permission"]["check"] == {
            "tenant_id": {"_eq": "X-Hasura-Tenant-Id"}
        }
        assert payload["args"]["permission"]["columns"] == ["sgv"]
        assert payload["args"]["permission"]["set"] == {"updated_by": "x-hasura-user-id"}

    @patch("phlo_hasura.client.requests.request")
    def test_create_delete_permission(self, mock_request):
        """Should create DELETE permission."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "success"}
        mock_request.return_value = mock_response

        client = HasuraClient(admin_secret="test-secret")
        result = client.create_delete_permission(
            "api",
            "glucose_readings",
            "admin",
            filter={"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
        )

        assert result["message"] == "success"

        payload = mock_request.call_args[1]["json"]
        assert payload["type"] == "pg_create_delete_permission"
        assert payload["args"]["permission"]["filter"] == {
            "tenant_id": {"_eq": "X-Hasura-Tenant-Id"}
        }

    @patch("phlo_hasura.client.requests.request")
    def test_export_metadata(self, mock_request, sample_metadata):
        """Should export metadata."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_metadata
        mock_request.return_value = mock_response

        client = HasuraClient(admin_secret="test-secret")
        metadata = client.export_metadata()

        assert metadata == sample_metadata

    @patch("phlo_hasura.client.requests.request")
    def test_get_tracked_tables(self, mock_request, sample_metadata):
        """Should get tracked tables by schema."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_metadata
        mock_request.return_value = mock_response

        client = HasuraClient(admin_secret="test-secret")
        tracked = client.get_tracked_tables()

        assert "api" in tracked
        assert "glucose_readings" in tracked["api"]

    @patch("phlo_hasura.client.requests.request")
    def test_request_error_handling(self, mock_request):
        """Should handle API errors."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Invalid request"}
        mock_request.return_value = mock_response

        client = HasuraClient(admin_secret="test-secret")

        with pytest.raises(requests.RequestException):
            client.track_table("api", "nonexistent")


class TestHasuraTableTracker:
    """Tests for HasuraTableTracker."""

    def test_tracker_resolves_unreachable_postgres_host(self, tmp_path, monkeypatch):
        phlo_dir = tmp_path / ".phlo"
        phlo_dir.mkdir()
        (phlo_dir / ".env.local").write_text("POSTGRES_PORT=15432\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("POSTGRES_PORT", raising=False)

        def raise_unresolvable(_host: str) -> str:
            raise socket.gaierror()

        monkeypatch.setattr("phlo.config.network.socket.gethostbyname", raise_unresolvable)

        tracker = HasuraTableTracker(hasura_client=HasuraClient(admin_secret="test-secret"))

        assert (tracker.db_host, tracker.db_port) == ("localhost", 15432)

    @patch("phlo_hasura.track.psycopg2.connect")
    def test_get_tables_in_schema(self, mock_connect):
        """Should get tables from schema."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            ("glucose_readings",),
            ("glucose_metrics",),
        ]

        tracker = HasuraTableTracker(hasura_client=HasuraClient(admin_secret="test-secret"))
        tables = tracker.get_tables_in_schema("api")

        assert tables == ["glucose_readings", "glucose_metrics"]

    @patch("phlo_hasura.track.psycopg2.connect")
    def test_get_foreign_keys(self, mock_connect):
        """Should get foreign keys for table."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            ("user_id", "public", "users", "id"),
        ]

        tracker = HasuraTableTracker(hasura_client=HasuraClient(admin_secret="test-secret"))
        fks = tracker.get_foreign_keys("api", "glucose_readings")

        assert len(fks) == 1
        assert fks[0]["local_column"] == "user_id"
        assert fks[0]["ref_table"] == "users"

    @patch.object(HasuraClient, "track_table")
    @patch("phlo_hasura.track.psycopg2.connect")
    def test_track_tables(self, mock_connect, mock_track):
        """Should track multiple tables."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            ("glucose_readings",),
            ("glucose_metrics",),
        ]
        mock_track.return_value = {"message": "success"}

        tracker = HasuraTableTracker(hasura_client=HasuraClient(admin_secret="test-secret"))
        results = tracker.track_tables("api", verbose=False)

        assert results["glucose_readings"] is True
        assert results["glucose_metrics"] is True
        assert mock_track.call_count == 2

    @patch.object(HasuraClient, "track_table")
    @patch("phlo_hasura.track.psycopg2.connect")
    def test_track_tables_with_exclusion(self, mock_connect, mock_track):
        """Should exclude tables from tracking."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            ("glucose_readings",),
            ("temp_table",),
        ]

        tracker = HasuraTableTracker(hasura_client=HasuraClient(admin_secret="test-secret"))
        results = tracker.track_tables("api", exclude=["temp_table"], verbose=False)

        assert "glucose_readings" in results
        assert "temp_table" not in results


class TestHasuraPermissionManager:
    """Tests for HasuraPermissionManager."""

    def test_load_json_config(self, tmp_path):
        """Should load JSON config file."""
        config = {
            "tables": {
                "api.glucose_readings": {"select": {"analyst": {"columns": ["*"], "filter": {}}}}
            }
        }

        config_file = tmp_path / "permissions.json"
        with open(config_file, "w") as f:
            json.dump(config, f)

        manager = HasuraPermissionManager(client=HasuraClient(admin_secret="test-secret"))
        loaded = manager.load_config(str(config_file))

        assert loaded["tables"]["api.glucose_readings"] == config["tables"]["api.glucose_readings"]

    @patch.object(HasuraClient, "create_delete_permission")
    @patch.object(HasuraClient, "create_update_permission")
    @patch.object(HasuraClient, "create_insert_permission")
    @patch.object(HasuraClient, "create_select_permission")
    def test_sync_permissions(
        self,
        mock_select,
        mock_insert,
        mock_update,
        mock_delete,
    ):
        """Should sync all supported permission types from config."""
        config = {
            "tables": {
                "api.glucose_readings": {
                    "select": {"analyst": {"columns": ["*"], "filter": {}}},
                    "insert": {
                        "loader": {
                            "columns": ["reading_id", "sgv"],
                            "check": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
                            "set": {"tenant_id": "x-hasura-tenant-id"},
                        }
                    },
                    "update": {
                        "editor": {
                            "columns": ["sgv"],
                            "filter": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
                            "check": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
                            "set": {"updated_by": "x-hasura-user-id"},
                        }
                    },
                    "delete": {"admin": {"filter": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}}}},
                }
            }
        }
        mock_select.return_value = {"message": "success"}
        mock_insert.return_value = {"message": "success"}
        mock_update.return_value = {"message": "success"}
        mock_delete.return_value = {"message": "success"}

        manager = HasuraPermissionManager(client=HasuraClient(admin_secret="test-secret"))
        results = manager.sync_permissions(config, verbose=False)

        assert ("api.glucose_readings", "analyst") in results["select"]
        assert results["select"][("api.glucose_readings", "analyst")] is True
        assert results["insert"][("api.glucose_readings", "loader")] is True
        assert results["update"][("api.glucose_readings", "editor")] is True
        assert results["delete"][("api.glucose_readings", "admin")] is True

    @patch.object(HasuraClient, "export_metadata")
    def test_export_permissions(self, mock_export, sample_metadata):
        """Should export current permissions."""
        mock_export.return_value = sample_metadata

        manager = HasuraPermissionManager(client=HasuraClient(admin_secret="test-secret"))
        exported = manager.export_permissions()

        assert "tables" in exported
        assert "api.glucose_readings" in exported["tables"]
        assert "select" in exported["tables"]["api.glucose_readings"]
        assert exported["tables"]["api.glucose_readings"]["insert"]["loader"] == {
            "columns": ["reading_id", "sgv"],
            "check": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
            "set": {"tenant_id": "x-hasura-tenant-id"},
        }
        assert exported["tables"]["api.glucose_readings"]["update"]["editor"] == {
            "filter": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
            "columns": ["sgv"],
            "check": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}},
            "set": {"updated_by": "x-hasura-user-id"},
        }
        assert exported["tables"]["api.glucose_readings"]["delete"]["admin"] == {
            "filter": {"tenant_id": {"_eq": "X-Hasura-Tenant-Id"}}
        }


class TestRoleHierarchy:
    """Tests for RoleHierarchy."""

    def test_get_inherited_roles(self):
        """Should get roles inherited by role."""
        hierarchy = RoleHierarchy()

        admin_roles = hierarchy.get_inherited_roles("admin")
        assert "admin" in admin_roles
        assert "analyst" in admin_roles
        assert "anon" in admin_roles

        analyst_roles = hierarchy.get_inherited_roles("analyst")
        assert "analyst" in analyst_roles
        assert "anon" in analyst_roles
        assert "admin" not in analyst_roles

    def test_expand_permissions(self):
        """Should expand permissions based on hierarchy."""
        config = {"tables": {"api.data": {"select": {"analyst": {"columns": ["*"], "filter": {}}}}}}

        hierarchy = RoleHierarchy()
        expanded = hierarchy.expand_permissions(config)

        # analyst should have permission
        assert "analyst" in expanded["tables"]["api.data"]["select"]
        # anon should inherit analyst's permission
        assert "anon" in expanded["tables"]["api.data"]["select"]


class TestHasuraMetadataSync:
    """Tests for HasuraMetadataSync."""

    @patch.object(HasuraClient, "export_metadata")
    def test_export_metadata(self, mock_export, sample_metadata):
        """Should export metadata to file."""
        mock_export.return_value = sample_metadata

        syncer = HasuraMetadataSync(client=HasuraClient(admin_secret="test-secret"))
        metadata = syncer.export_metadata()

        assert metadata == sample_metadata

    @patch.object(HasuraClient, "apply_metadata")
    def test_import_metadata(self, mock_apply, sample_metadata, tmp_path):
        """Should import metadata from file."""
        metadata_file = tmp_path / "metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(sample_metadata, f)

        mock_apply.return_value = {"message": "success"}

        syncer = HasuraMetadataSync(client=HasuraClient(admin_secret="test-secret"))
        result = syncer.import_metadata(str(metadata_file))

        assert result["message"] == "success"

    def test_get_diff(self, sample_metadata):
        """Should calculate diff between metadata versions."""
        current = sample_metadata
        desired = {
            "version": 3,
            "metadata": {},
            "sources": [
                {
                    "name": "default",
                    "tables": [
                        {
                            "table": {"schema": "api", "name": "new_table"},
                            "select_permissions": [],
                        }
                    ],
                }
            ],
        }

        syncer = HasuraMetadataSync(client=HasuraClient(admin_secret="test-secret"))
        diff = syncer.get_diff(current, desired)

        assert "api.new_table" in diff["tables"]["added"]
        assert "api.glucose_readings" in diff["tables"]["removed"]
