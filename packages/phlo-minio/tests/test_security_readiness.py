"""MinIO readiness reports missing credentials, live evidence, or its absence."""

from __future__ import annotations

import pytest

from phlo.security.backend_readiness import BackendReadinessResult, BackendReadinessState
from phlo_minio import security_readiness
from phlo_minio.security_readiness import MinioReadinessProvider


def test_missing_credentials_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a blank or unset root credential fails and names what is missing."""
    monkeypatch.setenv("MINIO_ROOT_USER", "  ")
    monkeypatch.delenv("MINIO_ROOT_PASSWORD", raising=False)
    result = MinioReadinessProvider().inspect()
    assert result.state is BackendReadinessState.FAILED
    assert result.reason_code == "config_missing"
    assert result.message.endswith("MINIO_ROOT_PASSWORD, MINIO_ROOT_USER")


def test_without_live_evidence_readiness_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify configured credentials alone never pass production readiness."""
    monkeypatch.setenv("MINIO_ROOT_USER", "object-admin")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "object-secret")
    monkeypatch.setattr(security_readiness, "observe_policy_convergence", lambda name: None)
    result = MinioReadinessProvider().inspect()
    assert result.state is BackendReadinessState.UNAVAILABLE
    assert result.reason_code == "evidence_unavailable"
    assert MinioReadinessProvider().plan() is None


def test_live_policy_evidence_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify an observed policy result is what the provider reports."""
    monkeypatch.setenv("MINIO_ROOT_USER", "object-admin")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "object-secret")
    observed = BackendReadinessResult(
        backend="minio",
        state=BackendReadinessState.FAILED,
        reason_code="policy_drift",
        message="minio policies differ from the canonical model",
        evidence_source="live backend",
    )
    monkeypatch.setattr(security_readiness, "observe_policy_convergence", lambda name: observed)
    result = MinioReadinessProvider().inspect()
    assert result.state is BackendReadinessState.FAILED
    assert result.reason_code == "policy_drift"
