"""Tests for compliance feature resolution.

Verifies ComplianceFeatures is frozen with all features defaulting to
false, that regulated mode enables everything unless compliance_config
overrides individual features, and that empty or None config acts as no
override.
"""

from __future__ import annotations

import pytest

from phlo.compliance import ComplianceFeatures, resolve_compliance_features


class TestComplianceFeatures:
    """Tests for ComplianceFeatures dataclass."""

    def test_all_features_default_to_false(self) -> None:
        """Open mode: all compliance features are disabled."""
        features = ComplianceFeatures()

        assert features.tamper_evident_audit is False
        assert features.electronic_signatures is False
        assert features.system_manifest is False
        assert features.access_governance is False

    def test_features_are_immutable(self) -> None:
        """ComplianceFeatures is frozen and cannot be modified."""
        features = ComplianceFeatures(tamper_evident_audit=True)

        with pytest.raises(AttributeError):
            features.tamper_evident_audit = False  # type: ignore[attr-defined]


class TestResolveComplianceFeatures:
    """Tests for resolve_compliance_features()."""

    def test_open_mode_returns_all_false(self) -> None:
        """When regulated=False, all features are disabled."""
        features = resolve_compliance_features(regulated=False)

        assert features.tamper_evident_audit is False
        assert features.electronic_signatures is False
        assert features.system_manifest is False
        assert features.access_governance is False

    def test_regulated_mode_returns_all_true_by_default(self) -> None:
        """When regulated=True with no config, all features are enabled."""
        features = resolve_compliance_features(regulated=True)

        assert features.tamper_evident_audit is True
        assert features.electronic_signatures is True
        assert features.system_manifest is True
        assert features.access_governance is True

    def test_regulated_with_individual_overrides(self) -> None:
        """Individual features can be disabled via compliance_config."""
        config = {
            "tamper_evident_audit": False,
            "electronic_signatures": True,
            "system_manifest": True,
            "access_governance": False,
        }
        features = resolve_compliance_features(regulated=True, compliance_config=config)

        assert features.tamper_evident_audit is False
        assert features.electronic_signatures is True
        assert features.system_manifest is True
        assert features.access_governance is False

    def test_partial_config_enables_remaining(self) -> None:
        """Only specifying some features leaves unspecified ones enabled."""
        config = {
            "tamper_evident_audit": False,
        }
        features = resolve_compliance_features(regulated=True, compliance_config=config)

        assert features.tamper_evident_audit is False
        assert features.electronic_signatures is True
        assert features.system_manifest is True
        assert features.access_governance is True

    def test_empty_config_acts_as_no_override(self) -> None:
        """Empty compliance_config still enables all features in regulated mode."""
        features = resolve_compliance_features(regulated=True, compliance_config={})

        assert features.tamper_evident_audit is True
        assert features.electronic_signatures is True
        assert features.system_manifest is True
        assert features.access_governance is True

    def test_none_config_acts_as_no_override(self) -> None:
        """None compliance_config still enables all features in regulated mode."""
        features = resolve_compliance_features(regulated=True, compliance_config=None)

        assert features.tamper_evident_audit is True
        assert features.electronic_signatures is True
        assert features.system_manifest is True
        assert features.access_governance is True
