"""Tests for phlo status command.

Tests the status CLI command for asset and service health monitoring.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from phlo_dagster import cli_status as status_module
from phlo_dagster.cli_status import (
    _check_if_stale,
    _check_service_health,
    _get_asset_status,
    _get_freshness_indicator,
    status,
)


class TestFreshnessIndicator:
    """Tests for freshness indicator logic."""

    def test_fresh_asset(self):
        """Test that recently run assets are marked as fresh."""
        last_run = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=30),
        }
        assert _get_freshness_indicator(last_run) == "fresh"

    def test_okay_asset(self):
        """Test that assets run within 24 hours are okay."""
        last_run = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=12),
        }
        assert _get_freshness_indicator(last_run) == "okay"

    def test_stale_asset(self):
        """Test that assets older than 24 hours are stale."""
        last_run = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=48),
        }
        assert _get_freshness_indicator(last_run) == "stale"

    def test_failed_asset(self):
        """Test that failed assets are marked as failed."""
        last_run = {
            "status": "failure",
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        assert _get_freshness_indicator(last_run) == "failed"

    def test_never_run_asset(self):
        """Test that never-run assets are marked as never_run."""
        assert _get_freshness_indicator(None) == "never_run"

    def test_asset_with_missing_timestamp(self):
        """Test assets with missing timestamp."""
        last_run = {"status": "success"}
        assert _get_freshness_indicator(last_run) == "unknown"


class TestStalenessCheck:
    """Tests for staleness checking."""

    def test_fresh_asset_not_stale(self):
        """Test that recently run assets are not stale."""
        last_run = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=12),
        }
        assert not _check_if_stale(last_run)

    def test_old_asset_is_stale(self):
        """Test that old assets are stale."""
        last_run = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc) - timedelta(days=2),
        }
        assert _check_if_stale(last_run)

    def test_failed_asset_is_stale(self):
        """Test that failed assets are stale."""
        last_run = {
            "status": "failure",
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        assert _check_if_stale(last_run)

    def test_never_run_asset_is_stale(self):
        """Test that never-run assets are stale."""
        assert _check_if_stale(None)

    def test_asset_exactly_24_hours_old(self):
        """Test boundary condition at 24 hours."""
        last_run = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=24),
        }
        # Boundary: should be considered stale
        assert _check_if_stale(last_run)

    def test_asset_23_hours_59_minutes_old(self):
        """Test just under 24 hour boundary."""
        last_run = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=23, minutes=59),
        }
        assert not _check_if_stale(last_run)


class TestServiceHealth:
    """Tests for service health checks."""

    def test_service_health_with_mock(self):
        """Test service health checks work (with or without requests)."""
        # This will test the actual code path
        result = _check_service_health("http://localhost:9999/test", "TestService")

        # Should handle the connection error gracefully
        assert "name" in result
        assert "status" in result
        assert result["name"] == "TestService"

    def test_service_health_returns_required_fields(self):
        """Test that service health returns all required fields."""
        result = _check_service_health("http://localhost:9999/test", "TestService")

        required_fields = {"name", "status"}
        assert required_fields.issubset(result.keys())

    def test_service_health_handles_invalid_url(self):
        """Test that invalid URLs are handled gracefully."""
        result = _check_service_health("not-a-valid-url", "TestService")

        assert result["status"] in ["down", "error", "timeout"]
        assert result["name"] == "TestService"

    def test_all_service_statuses_valid(self):
        """Test that service health returns valid status values."""
        result = _check_service_health("http://localhost:9999/test", "TestService")

        valid_statuses = {"healthy", "down", "timeout", "error", "unhealthy"}
        assert result["status"] in valid_statuses

    def test_service_status_uses_project_port_overrides(self, monkeypatch: pytest.MonkeyPatch):
        """Service health checks should honor project .phlo env port overrides."""
        checked_urls: dict[str, str] = {}

        monkeypatch.setattr(
            status_module,
            "_project_env",
            lambda: {
                "TRINO_PORT": "18080",
                "MINIO_API_PORT": "19000",
                "NESSIE_PORT": "29120",
            },
        )
        monkeypatch.setattr(
            status_module,
            "_dagster_graphql_url",
            lambda: "http://localhost:3300/graphql",
        )
        monkeypatch.setattr(
            status_module,
            "_check_dagster_health",
            lambda url: {"name": "Dagster", "status": "healthy", "url": url},
        )

        def fake_check(url: str, name: str) -> dict[str, str]:
            checked_urls[name.lower()] = url
            return {"name": name, "status": "healthy"}

        monkeypatch.setattr(status_module, "_check_service_health", fake_check)

        services = status_module._get_service_status()

        assert services["dagster"]["url"] == "http://localhost:3300/graphql"
        assert checked_urls == {
            "trino": "http://localhost:18080/v1/info",
            "minio": "http://localhost:19000/minio/health/ready",
            "nessie": "http://localhost:29120/api/v1/config",
        }

    def test_dagster_graphql_url_falls_back_on_invalid_port(self, monkeypatch: pytest.MonkeyPatch):
        """Invalid Dagster port overrides should not produce malformed URLs."""
        monkeypatch.setattr(
            status_module,
            "_project_env",
            lambda: {"DAGSTER_WEBSERVER_PORT": "auto"},
        )
        monkeypatch.setattr(
            status_module, "get_settings", lambda: type("S", (), {"dagster_port": 3000})()
        )

        assert status_module._dagster_graphql_url() == "http://localhost:3000/graphql"

    def test_dagster_health_handles_request_exceptions(self, monkeypatch: pytest.MonkeyPatch):
        """Dagster health should return a structured error for non-connection request errors."""

        def raise_invalid_url(*_args, **_kwargs):
            raise status_module.requests_exceptions.InvalidURL("bad url")

        monkeypatch.setattr(status_module.http_requests, "post", raise_invalid_url)

        result = status_module._check_dagster_health("not-a-url")

        assert result["name"] == "Dagster"
        assert result["status"] == "error"
        assert "bad url" in result["error"]


class TestStatusCLI:
    """Tests for the status CLI command."""

    def test_status_shows_all_by_default(self):
        """Test that status shows both assets and services by default."""
        runner = CliRunner()
        result = runner.invoke(status, [])

        assert result.exit_code == 0
        # With no Dagster running, assets section shows "No assets found"
        # and services section shows service health table
        assert "Service Health" in result.output

    def test_status_assets_only(self):
        """Test filtering to assets only."""
        runner = CliRunner()
        result = runner.invoke(status, ["--assets"])

        assert result.exit_code == 0
        assert "Service Health" not in result.output

    def test_status_services_only(self):
        """Test filtering to services only."""
        runner = CliRunner()
        result = runner.invoke(status, ["--services"])

        assert result.exit_code == 0
        assert "Service Health" in result.output

    def test_status_filter_by_group(self):
        """Test filtering by asset group."""
        runner = CliRunner()
        result = runner.invoke(status, ["--assets", "--group", "nightscout"])

        assert result.exit_code == 0

    def test_status_filter_stale_only(self):
        """Test showing only stale assets."""
        runner = CliRunner()
        result = runner.invoke(status, ["--assets", "--stale"])

        assert result.exit_code == 0

    def test_status_json_output(self):
        """JSON output must parse and carry the required top-level keys."""
        runner = CliRunner()
        result = runner.invoke(status, ["--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "timestamp" in data
        assert "elapsed_seconds" in data

    def test_status_json_assets_only(self):
        """Assets-only JSON must omit services and expose assets."""
        runner = CliRunner()
        result = runner.invoke(status, ["--assets", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "assets" in data
        assert "services" not in data

    def test_status_json_with_group_filter(self):
        """Group-filtered JSON only contains assets from that group."""
        runner = CliRunner()
        result = runner.invoke(status, ["--assets", "--group", "nightscout", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "assets" in data
        for asset in data["assets"]:
            assert asset["group"] == "nightscout"

    def test_status_response_time(self):
        """Test that response time is reasonable."""
        import time

        runner = CliRunner()
        start = time.time()
        result = runner.invoke(status, ["--assets"])
        elapsed = time.time() - start

        assert result.exit_code == 0
        # Should complete in less than 5 seconds
        assert elapsed < 5.0


class TestStatusOutput:
    """Tests for status output formatting."""

    def test_asset_status_empty_when_disconnected(self, monkeypatch: pytest.MonkeyPatch):
        """Test that asset status shows empty state when services disconnected."""

        def disconnected(*_args, **_kwargs):
            raise status_module.requests_exceptions.ConnectionError("Dagster is disconnected")

        monkeypatch.setattr(status_module.http_requests, "post", disconnected)
        runner = CliRunner()
        result = runner.invoke(status, ["--assets"])

        assert result.exit_code == 0
        assert "No assets found" in result.output

    def test_service_status_table_formatting(self):
        """Test that service status table is formatted correctly."""
        runner = CliRunner()
        result = runner.invoke(status, ["--services"])

        assert result.exit_code == 0
        assert "Service" in result.output
        assert "Status" in result.output
        assert "Latency" in result.output

    def test_status_includes_timestamp_in_json(self):
        """JSON output timestamp must be ISO-8601 parseable."""
        runner = CliRunner()
        result = runner.invoke(status, ["--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        datetime.fromisoformat(data["timestamp"])

    def test_group_filter_excludes_other_groups(self):
        """Group filter excludes assets outside the requested group."""
        runner = CliRunner()
        result = runner.invoke(status, ["--assets", "--group", "nightscout", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        for asset in data["assets"]:
            assert asset["group"] == "nightscout"

    def test_stale_filter_shows_only_stale(self):
        """Stale filter only returns stale assets."""
        runner = CliRunner()
        result = runner.invoke(status, ["--assets", "--stale", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        for asset in data["assets"]:
            assert asset["is_stale"]

    def test_combined_filters(self):
        """Combined group and staleness filters intersect."""
        runner = CliRunner()
        result = runner.invoke(
            status,
            ["--assets", "--group", "nightscout", "--stale", "--json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        for asset in data["assets"]:
            assert asset["group"] == "nightscout"
            assert asset["is_stale"]

    def test_non_existent_group_returns_empty(self):
        """Unknown groups return an empty asset list."""
        runner = CliRunner()
        result = runner.invoke(status, ["--assets", "--group", "nonexistent", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["assets"] == []


class TestStatusEdgeCases:
    """Tests for edge cases in status command."""

    def test_asset_status_handles_null_dagster_definition(self, monkeypatch: pytest.MonkeyPatch):
        """Dagster may return asset nodes with null definition during startup."""

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "assetsOrError": {
                            "nodes": [
                                {
                                    "key": {"path": ["dlt_events"]},
                                    "definition": None,
                                }
                            ]
                        }
                    }
                }

        monkeypatch.setattr(status_module, "_dagster_graphql_url", lambda: "http://dagster")
        monkeypatch.setattr(
            status_module.http_requests,
            "post",
            lambda *_args, **_kwargs: FakeResponse(),
        )
        monkeypatch.setattr(
            status_module,
            "_get_asset_last_run",
            lambda _asset_name: status_module.AssetRunEvidence(available=True, last_run=None),
        )

        assets = _get_asset_status()

        assert assets[0]["name"] == "dlt_events"
        assert assets[0]["group"] == ""
        assert assets[0]["status"] == "never_run"
        assert assets[0]["evidence_available"] is True

    def test_unwired_evidence_source_is_declared(self, monkeypatch: pytest.MonkeyPatch):
        """An unwired evidence source declares unavailability, not run data."""
        evidence = status_module._get_asset_last_run("dlt_events")

        assert evidence.available is False
        assert evidence.last_run is None

    def test_unwired_assets_are_excluded_from_stale_filter(self, monkeypatch: pytest.MonkeyPatch):
        """Unknown evidence never satisfies the --stale filter."""

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "assetsOrError": {
                            "nodes": [
                                {"key": {"path": ["dlt_events"]}, "definition": {"groupName": ""}}
                            ]
                        }
                    }
                }

        monkeypatch.setattr(status_module, "_dagster_graphql_url", lambda: "http://dagster")
        monkeypatch.setattr(
            status_module.http_requests,
            "post",
            lambda *_args, **_kwargs: FakeResponse(),
        )

        assets = _get_asset_status(stale=True)

        assert assets == []

    def test_unwired_asset_json_states_are_unknown(self, monkeypatch: pytest.MonkeyPatch):
        """JSON rows for unwired assets carry unknown status/freshness."""

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "assetsOrError": {
                            "nodes": [
                                {"key": {"path": ["dlt_events"]}, "definition": {"groupName": ""}}
                            ]
                        }
                    }
                }

        monkeypatch.setattr(status_module, "_dagster_graphql_url", lambda: "http://dagster")
        monkeypatch.setattr(
            status_module.http_requests,
            "post",
            lambda *_args, **_kwargs: FakeResponse(),
        )

        assets = _get_asset_status()

        assert assets[0]["status"] == "unknown"
        assert assets[0]["freshness"] == "unknown"
        assert assets[0]["is_stale"] is None
        assert assets[0]["evidence_available"] is False

    def test_status_with_all_flags(self):
        """Test status with all filtering flags combined."""
        runner = CliRunner()
        result = runner.invoke(
            status,
            ["--assets", "--services", "--group", "nightscout", "--stale", "--json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "assets" in data
        assert "services" in data

    def test_status_json_is_serializable(self):
        """Test that JSON output is fully serializable."""
        runner = CliRunner()
        result = runner.invoke(status, ["--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert json.dumps(data)

    def test_status_handles_missing_requests_library(self):
        """Test that status handles missing requests library gracefully."""
        runner = CliRunner()
        # Even without requests, should not crash
        result = runner.invoke(status, ["--services"])
        assert result.exit_code == 0
