"""Tests for the compliance system manifest models.

Covers deployment environments, compliance modes and their regulated
mapping, component versions with optional build metadata, immutable
SystemManifest construction, and capture_manifest id/timestamp
semantics.
"""

from __future__ import annotations

import pytest

from phlo.compliance.manifest import (
    COMPLIANCE_MODE_TO_REGULATED,
    ComplianceMode,
    ComponentVersion,
    DeploymentEnvironment,
    SecurityConfiguration,
    SystemManifest,
    capture_manifest,
)


class TestDeploymentEnvironment:
    """Tests for DeploymentEnvironment enum."""

    def test_all_environments_exist(self) -> None:
        """All expected environments exist."""
        assert DeploymentEnvironment.DEVELOPMENT == "development"
        assert DeploymentEnvironment.STAGING == "staging"
        assert DeploymentEnvironment.PRODUCTION == "production"
        assert DeploymentEnvironment.TEST == "test"


class TestComplianceMode:
    """Tests for ComplianceMode enum."""

    def test_all_modes_exist(self) -> None:
        """Both compliance modes exist."""
        assert ComplianceMode.OPEN == "open"
        assert ComplianceMode.REGULATED == "regulated"

    def test_compliance_mode_to_regulated_mapping(self) -> None:
        """COMPLIANCE_MODE_TO_REGULATED maps correctly."""
        assert COMPLIANCE_MODE_TO_REGULATED[ComplianceMode.OPEN] is False
        assert COMPLIANCE_MODE_TO_REGULATED[ComplianceMode.REGULATED] is True


class TestSecurityConfiguration:
    """Tests for SecurityConfiguration."""

    def test_create_security_configuration(self) -> None:
        """SecurityConfiguration can be created."""
        security = SecurityConfiguration(
            compliance_mode=ComplianceMode.REGULATED,
            regulated=True,
            tamper_evident_audit=True,
            electronic_signatures=True,
            access_governance=True,
        )

        assert security.compliance_mode == ComplianceMode.REGULATED
        assert security.regulated is True
        assert security.tamper_evident_audit is True
        assert security.electronic_signatures is True
        assert security.access_governance is True
        assert security.auth_providers == ()

    def test_security_configuration_with_session_settings(self) -> None:
        """SecurityConfiguration can include session settings."""
        security = SecurityConfiguration(
            compliance_mode=ComplianceMode.OPEN,
            regulated=False,
            tamper_evident_audit=False,
            electronic_signatures=False,
            access_governance=False,
            require_mfa=True,
            session_timeout_seconds=3600,
        )

        assert security.require_mfa is True
        assert security.session_timeout_seconds == 3600


class TestSystemManifest:
    """Tests for SystemManifest."""

    def test_create_system_manifest(self) -> None:
        """SystemManifest can be created."""
        manifest = SystemManifest(
            manifest_id="test-manifest-001",
            captured_at="2024-01-01T00:00:00Z",
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.REGULATED,
                regulated=True,
                tamper_evident_audit=True,
                electronic_signatures=True,
                access_governance=True,
            ),
        )

        assert manifest.manifest_id == "test-manifest-001"
        assert manifest.phlo_version == "1.0.0"
        assert manifest.environment == DeploymentEnvironment.PRODUCTION
        assert manifest.security.compliance_mode == ComplianceMode.REGULATED
        assert manifest.components == ()
        assert manifest.config_snapshot == {}

    def test_system_manifest_with_components(self) -> None:
        """SystemManifest can include components."""
        components = [
            ComponentVersion(name="phlo-api", version="1.0.0"),
            ComponentVersion(name="phlo-dagster", version="1.0.0"),
        ]
        manifest = SystemManifest(
            manifest_id="test-manifest-001",
            captured_at="2024-01-01T00:00:00Z",
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.REGULATED,
                regulated=True,
                tamper_evident_audit=True,
                electronic_signatures=True,
                access_governance=True,
            ),
            components=components,
        )

        assert manifest.components == [components[0], components[1]]
        assert manifest.components[0].name == "phlo-api"
        assert manifest.components[1].name == "phlo-dagster"

    def test_system_manifest_immutable(self) -> None:
        """SystemManifest is immutable (frozen)."""
        manifest = SystemManifest(
            manifest_id="test-manifest-001",
            captured_at="2024-01-01T00:00:00Z",
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.OPEN,
                regulated=False,
                tamper_evident_audit=False,
                electronic_signatures=False,
                access_governance=False,
            ),
        )

        with pytest.raises(AttributeError):
            manifest.manifest_id = "new-id"  # type: ignore[attr-defined]


class TestCaptureManifest:
    """Tests for capture_manifest function."""

    def test_capture_manifest_generates_id(self) -> None:
        """capture_manifest generates a distinct manifest_id per capture."""
        from uuid import UUID

        manifest = capture_manifest(
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.REGULATED,
                regulated=True,
                tamper_evident_audit=True,
                electronic_signatures=True,
                access_governance=True,
            ),
        )
        second = capture_manifest(
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=manifest.security,
        )

        assert UUID(manifest.manifest_id)
        assert manifest.manifest_id != second.manifest_id

    def test_capture_manifest_uses_provided_id(self) -> None:
        """capture_manifest uses provided manifest_id."""
        manifest = capture_manifest(
            manifest_id="custom-id-123",
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.STAGING,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.OPEN,
                regulated=False,
                tamper_evident_audit=False,
                electronic_signatures=False,
                access_governance=False,
            ),
        )

        assert manifest.manifest_id == "custom-id-123"

    def test_capture_manifest_captures_timestamp(self) -> None:
        """capture_manifest stamps a parseable UTC timestamp within the call window."""
        from datetime import UTC, datetime

        before = datetime.now(UTC)
        manifest = capture_manifest(
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.DEVELOPMENT,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.OPEN,
                regulated=False,
                tamper_evident_audit=False,
                electronic_signatures=False,
                access_governance=False,
            ),
        )
        after = datetime.now(UTC)

        captured = datetime.fromisoformat(manifest.captured_at)
        assert captured.tzinfo is not None
        assert before <= captured <= after

    def test_capture_manifest_with_components(self) -> None:
        """capture_manifest accepts component list."""
        components = [
            ComponentVersion(name="phlo-api", version="1.0.0"),
        ]
        manifest = capture_manifest(
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.REGULATED,
                regulated=True,
                tamper_evident_audit=True,
                electronic_signatures=True,
                access_governance=True,
            ),
            components=components,
        )

        assert manifest.components == (components[0],)

    def test_capture_manifest_with_config_snapshot(self) -> None:
        """capture_manifest accepts config snapshot."""
        config = {"log_level": "INFO", "debug": False}
        manifest = capture_manifest(
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.OPEN,
                regulated=False,
                tamper_evident_audit=False,
                electronic_signatures=False,
                access_governance=False,
            ),
            config_snapshot=config,
        )

        assert manifest.config_snapshot == config

    def test_capture_manifest_with_platform_info(self) -> None:
        """capture_manifest accepts platform and region."""
        manifest = capture_manifest(
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=SecurityConfiguration(
                compliance_mode=ComplianceMode.OPEN,
                regulated=False,
                tamper_evident_audit=False,
                electronic_signatures=False,
                access_governance=False,
            ),
            platform="kubernetes",
            region="us-east-1",
        )

        assert manifest.platform == "kubernetes"
        assert manifest.region == "us-east-1"
