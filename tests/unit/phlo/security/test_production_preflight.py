"""Tests for the production readiness preflight module.

Covers the closed check vocabulary, per-check pass/fail/unavailable behaviour,
deterministic serialization, secret sanitization, and the read-only contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from phlo.security.production_preflight import (
    ProductionReadinessCheckId,
    ProductionReadinessReasonCode,
    ProductionReadinessState,
    load_effective_environment,
    run_production_readiness,
)


def _write(tmp_path: Path, *files: tuple[str, str]) -> None:
    """Write relative files under a temp project root."""
    for relative, content in files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _plan(tmp_path: Path, service_names: list[str] | None = None) -> SimpleNamespace:
    phlo_dir = tmp_path / ".phlo"
    return SimpleNamespace(
        phlo_dir=phlo_dir,
        compose_file=phlo_dir / "docker-compose.yml",
        service_names=service_names or [],
    )


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A minimal generated project with production posture."""
    _write(
        tmp_path,
        (
            ".phlo/.env",
            "PHLO_ENVIRONMENT=production\n"
            "POSTGRES_USER=lakehouse\n"
            "MINIO_ROOT_USER=object-admin\n"
            # Distinct per-workload credential references (reference-level facts).
            "PHLO_SERVICE_CREDENTIALS_FILE=/run/secrets/workload-credentials.json\n"
            "DAGSTER_MINIO_ACCESS_KEY=dagster-minio-access\n"
            "DAGSTER_MINIO_SECRET_KEY=dagster-minio-secret\n"
            "DAGSTER_TRINO_USER=dagster-trino-user\n"
            "DAGSTER_POSTGRES_USER=dagster-pg-user\n"
            "DAGSTER_POSTGRES_PASSWORD=dagster-pg-password\n"
            "TRINO_QUERY_ACCESS_KEY=query-access\n"
            "TRINO_QUERY_SECRET_KEY=query-secret\n"
            "TRINO_USER=query-user\n"
            "TRINO_ROLE=query_role\n"
            "NESSIE_CATALOG_ACCESS_KEY=catalog-access\n"
            "NESSIE_CATALOG_SECRET_KEY=catalog-secret\n"
            "QUARKUS_DATASOURCE_USERNAME=catalog-pg-user\n"
            "QUARKUS_DATASOURCE_PASSWORD=catalog-pg-password\n"
            "MAINTENANCE_TRINO_USER=maintenance-user\n"
            "MAINTENANCE_TRINO_ROLE=maintenance_role\n"
            "MAINTENANCE_ACCESS_KEY=maintenance-access\n"
            "MAINTENANCE_SECRET_KEY=maintenance-secret\n",
        ),
        (
            ".phlo/.env.local",
            "POSTGRES_PASSWORD=postgres-independent-secret\n"
            "MINIO_ROOT_PASSWORD=minio-independent-secret\n"
            "PHLO_AUTH_JWT_SECRET=jwt-secret-value\n"
            "PHLO_AUTH_JWT_ISSUER=https://issuer.example\n"
            "PHLO_AUTH_JWT_AUDIENCE=phlo-api\n"
            "PHLO_AUDIT_HMAC_KEY=audit-key-value\n"
            "PHLO_SIGNATURE_HMAC_KEY=signature-key-value\n",
        ),
        (".phlo/docker-compose.yml", "# Dev mode: false\nservices:\n  postgres:\n    image: x\n"),
    )
    (tmp_path / ".phlo" / ".env.local").chmod(0o600)
    return tmp_path


@pytest.fixture()
def isolated_preflight(monkeypatch: pytest.MonkeyPatch, project: Path):
    """Isolate ambient config and env so checks are deterministic."""
    for key in (
        "PHLO_ENVIRONMENT",
        "PHLO_AUTH_DEV_MODE",
        "PHLO_AUTH_JWT_SECRET",
        "PHLO_AUTH_JWT_ISSUER",
        "PHLO_AUTH_JWT_AUDIENCE",
        "PHLO_AUTH_JWT_JWKS_URL",
        "PHLO_AUDIT_HMAC_KEY",
        "PHLO_SIGNATURE_HMAC_KEY",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "PHLO_SERVICE_CREDENTIALS_FILE",
        "DAGSTER_MINIO_ACCESS_KEY",
        "DAGSTER_MINIO_SECRET_KEY",
        "DAGSTER_TRINO_USER",
        "DAGSTER_POSTGRES_USER",
        "DAGSTER_POSTGRES_PASSWORD",
        "TRINO_QUERY_ACCESS_KEY",
        "TRINO_QUERY_SECRET_KEY",
        "TRINO_USER",
        "TRINO_ROLE",
        "NESSIE_CATALOG_ACCESS_KEY",
        "NESSIE_CATALOG_SECRET_KEY",
        "QUARKUS_DATASOURCE_USERNAME",
        "QUARKUS_DATASOURCE_PASSWORD",
        "MAINTENANCE_TRINO_USER",
        "MAINTENANCE_TRINO_ROLE",
        "MAINTENANCE_ACCESS_KEY",
        "MAINTENANCE_SECRET_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(
        "phlo.infrastructure.config.get_configured_authorization_backend_name",
        lambda: "canonical",
    )
    monkeypatch.setattr(
        "phlo.capabilities.resolve_capability",
        lambda capability, backend: (
            object() if capability == "authorization_policy_backend" else None
        ),
    )

    # Register passing backend-readiness inspectors for the blessed backends.
    from phlo.security.backend_readiness import (
        REQUIRED_BACKENDS,
        BackendReadinessResult,
        BackendReadinessState,
    )

    def _passing_readiness(capability, backend):
        if capability == "authorization_policy_backend":
            return object()
        if capability == "backend_readiness" and backend in REQUIRED_BACKENDS:
            provider = SimpleNamespace(
                backend_name=backend,
                inspect=lambda b=backend: BackendReadinessResult(
                    backend=b,
                    state=BackendReadinessState.PASSED,
                    reason_code="ok",
                    message=f"{b} readiness evidence present",
                ),
            )
            return SimpleNamespace(name=backend, provider=provider, metadata={})
        return None

    monkeypatch.setattr("phlo.capabilities.resolve_capability", _passing_readiness)

    class _FakeRbacLoader:
        def load(self) -> dict:
            return {"policies": []}

    monkeypatch.setattr(
        "phlo.security.validation._project_rbac_loader",
        lambda: _FakeRbacLoader(),
    )
    return project


def _run(project: Path) -> object:
    return run_production_readiness(
        plan=_plan(project, service_names=["postgres"]),
        project_root=project,
        environment="production",
    )


def test_report_has_closed_vocabulary_and_deterministic_ordering(
    isolated_preflight: Path,
) -> None:
    report = _run(isolated_preflight)
    assert len(report.checks) == len(ProductionReadinessCheckId)
    ids = [check.id for check in report.checks]
    assert set(ids) == set(ProductionReadinessCheckId)
    # Deterministic ordering and output across runs.
    assert ids == [check.id for check in _run(isolated_preflight).checks]
    assert report.to_json() == report.to_json()


def test_report_serializes_stably(isolated_preflight: Path) -> None:
    report = _run(isolated_preflight)
    payload = json.loads(report.to_json())
    assert payload["schema_version"] == "1"
    assert payload["stage"] == "static_preflight"
    assert isinstance(payload["report_id"], str) and payload["report_id"]
    assert payload["environment"] == "production"
    assert isinstance(payload["generated_at"], str)
    assert isinstance(payload["passed"], bool)
    assert payload["services"] == ["postgres"]
    assert set(payload["digests"]) == {"release", "config", "compose", "policy", "services"}
    assert len(payload["checks"]) == len(ProductionReadinessCheckId)
    for check in payload["checks"]:
        assert set(check) == {
            "id",
            "state",
            "message",
            "remediation",
            "source",
            "reason_code",
            "observation_time",
            "details",
        }
        assert check["reason_code"] in {code.value for code in ProductionReadinessReasonCode}
        assert check["observation_time"]


def test_failed_check_reason_codes_are_from_the_closed_set(isolated_preflight: Path) -> None:
    report = _run(isolated_preflight)
    for check in report.checks:
        assert check.reason_code in {code.value for code in ProductionReadinessReasonCode}
    env_failed = next(c for c in report.checks if c.id is ProductionReadinessCheckId.ENV_PRODUCTION)
    # A deliberately non-production run yields the not_production reason code.
    non_prod = run_production_readiness(
        plan=_plan(isolated_preflight, service_names=["postgres"]),
        project_root=isolated_preflight,
        environment="dev",
    )
    non_prod_env = next(
        c for c in non_prod.checks if c.id is ProductionReadinessCheckId.ENV_PRODUCTION
    )
    assert non_prod_env.reason_code == ProductionReadinessReasonCode.NOT_PRODUCTION.value
    assert env_failed.reason_code == ProductionReadinessReasonCode.OK.value


def test_report_never_contains_secret_values(isolated_preflight: Path) -> None:
    report = _run(isolated_preflight)
    serialized = report.to_json()
    for secret in (
        "jwt-secret-value",
        "audit-key-value",
        "signature-key-value",
        "postgres-independent-secret",
        "minio-independent-secret",
    ):
        assert secret not in serialized


def test_evaluation_never_mutates_files(project: Path, isolated_preflight: Path) -> None:
    before = {
        path: (path.read_bytes(), path.stat().st_mode & 0o7777)
        for path in (project / ".phlo" / ".env", project / ".phlo" / ".env.local")
    }
    _run(project)
    after = {
        path: (path.read_bytes(), path.stat().st_mode & 0o7777)
        for path in (project / ".phlo" / ".env", project / ".phlo" / ".env.local")
    }
    assert before == after


def test_full_production_posture_passes(isolated_preflight: Path) -> None:
    report = _run(isolated_preflight)
    failing = [c.id.value for c in report.checks if c.state in (ProductionReadinessState.FAILED,)]
    assert failing == []
    # Workload identity checks are now reference-level evaluations.
    for check in report.checks:
        if str(check.id).startswith("identity.workload"):
            assert check.state is ProductionReadinessState.PASSED


def test_missing_workload_identity_references_fail(monkeypatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        (
            ".phlo/.env",
            "PHLO_ENVIRONMENT=production\nPOSTGRES_USER=lakehouse\nMINIO_ROOT_USER=object-admin\n",
        ),
        (".phlo/.env.local", "POSTGRES_PASSWORD=pg-secret-1\nMINIO_ROOT_PASSWORD=minio-secret-1\n"),
    )
    (tmp_path / ".phlo" / ".env.local").chmod(0o600)
    (tmp_path / ".phlo" / "docker-compose.yml").write_text(
        "# Dev mode: false\nservices:\n  postgres:\n    image: x\n"
    )
    report = run_production_readiness(
        plan=_plan(tmp_path, service_names=["postgres"]),
        project_root=tmp_path,
        environment="production",
    )
    identity_checks = [c for c in report.checks if str(c.id).startswith("identity.workload")]
    assert len(identity_checks) == 5
    assert all(c.state is ProductionReadinessState.FAILED for c in identity_checks)
    assert all(
        c.reason_code == ProductionReadinessReasonCode.CREDENTIALS_BUNDLED_OR_SHARED.value
        for c in identity_checks
    )


def test_non_production_environment_fails_env_check(monkeypatch, tmp_path: Path) -> None:
    _write(tmp_path, (".phlo/.env", "PHLO_ENVIRONMENT=dev\n"))
    report = run_production_readiness(
        plan=_plan(tmp_path, service_names=[]),
        project_root=tmp_path,
        environment="dev",
    )
    env_check = next(c for c in report.checks if c.id is ProductionReadinessCheckId.ENV_PRODUCTION)
    assert env_check.state is ProductionReadinessState.FAILED
    assert report.passed is False


def test_dev_mode_compose_fails(monkeypatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        (".phlo/.env", "PHLO_ENVIRONMENT=production\n"),
        (".phlo/docker-compose.yml", "# Dev mode: true\nservices: {}\n"),
    )
    report = run_production_readiness(
        plan=_plan(tmp_path, service_names=["postgres"]),
        project_root=tmp_path,
        environment="production",
    )
    check = next(c for c in report.checks if c.id is ProductionReadinessCheckId.COMPOSE_NON_DEV)
    assert check.state is ProductionReadinessState.FAILED


def test_dev_auth_mode_in_production_fails(monkeypatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        (".phlo/.env", "PHLO_ENVIRONMENT=production\n"),
        (".phlo/docker-compose.yml", "# Dev mode: false\nservices: {}\n"),
    )
    monkeypatch.setenv("PHLO_AUTH_DEV_MODE", "1")
    report = run_production_readiness(
        plan=_plan(tmp_path, service_names=[]),
        project_root=tmp_path,
        environment="production",
    )
    check = next(
        c for c in report.checks if c.id is ProductionReadinessCheckId.HTTP_AUTHORIZATION_REQUIRED
    )
    assert check.state is ProductionReadinessState.FAILED


def test_missing_jwt_provider_fails_authn(monkeypatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        (".phlo/.env", "PHLO_ENVIRONMENT=production\n"),
        (".phlo/docker-compose.yml", "# Dev mode: false\nservices: {}\n"),
    )
    report = run_production_readiness(
        plan=_plan(tmp_path, service_names=[]),
        project_root=tmp_path,
        environment="production",
    )
    check = next(c for c in report.checks if c.id is ProductionReadinessCheckId.AUTHN_PROVIDER)
    assert check.state is ProductionReadinessState.FAILED


def test_partial_jwt_provider_fails_authn(monkeypatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        (
            ".phlo/.env",
            "PHLO_ENVIRONMENT=production\nPHLO_AUTH_JWT_ISSUER=https://issuer.example\n",
        ),
        (".phlo/docker-compose.yml", "# Dev mode: false\nservices: {}\n"),
    )
    report = run_production_readiness(
        plan=_plan(tmp_path, service_names=[]),
        project_root=tmp_path,
        environment="production",
    )
    check = next(c for c in report.checks if c.id is ProductionReadinessCheckId.AUTHN_PROVIDER)
    assert check.state is ProductionReadinessState.FAILED


def test_env_local_permissive_mode_fails(project: Path, isolated_preflight: Path) -> None:
    (project / ".phlo" / ".env.local").chmod(0o644)
    report = _run(project)
    check = next(
        c for c in report.checks if c.id is ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600
    )
    assert check.state is ProductionReadinessState.FAILED


def test_env_local_0600_passes(project: Path, isolated_preflight: Path) -> None:
    (project / ".phlo" / ".env.local").chmod(0o600)
    report = _run(project)
    check = next(
        c for c in report.checks if c.id is ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600
    )
    assert check.state is ProductionReadinessState.PASSED


def test_bundled_credentials_fail(project: Path, isolated_preflight: Path) -> None:
    (project / ".phlo" / ".env").write_text(
        "PHLO_ENVIRONMENT=production\nPOSTGRES_USER=phlo\nMINIO_ROOT_USER=minio\n"
    )
    report = _run(project)
    check = next(
        c for c in report.checks if c.id is ProductionReadinessCheckId.SECRETS_NO_BUNDLED_SHARED
    )
    assert check.state is ProductionReadinessState.FAILED


def test_protected_port_exposure_fails(project: Path, isolated_preflight: Path) -> None:
    (project / ".phlo" / "docker-compose.yml").write_text(
        "# Dev mode: false\nservices:\n  postgres:\n    image: x\n    ports:\n      - '5432:5432'\n"
    )
    report = _run(project)
    check = next(
        c for c in report.checks if c.id is ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS
    )
    assert check.state is ProductionReadinessState.FAILED


def test_no_protected_backends_is_not_applicable(project: Path, isolated_preflight: Path) -> None:
    (project / ".phlo" / "docker-compose.yml").write_text(
        "# Dev mode: false\nservices:\n  traefik:\n    image: x\n"
    )
    report = _run(project)
    check = next(
        c for c in report.checks if c.id is ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS
    )
    assert check.state is ProductionReadinessState.NOT_APPLICABLE


def test_load_effective_environment_precedence(monkeypatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        (".phlo/.env", "A=from-env\n"),
        (".phlo/.env.local", "B=from-local\nA=from-local-wins\n"),
        ("phlo.yaml", "env:\n  C: from-config\n"),
    )
    monkeypatch.setenv("D", "from-process")
    env = load_effective_environment(tmp_path / ".phlo", tmp_path)
    assert env["A"] == "from-local-wins"
    assert env["B"] == "from-local"
    assert env["C"] == "from-config"
    assert env["D"] == "from-process"


def test_backend_readiness_missing_adapter_fails(monkeypatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        (
            ".phlo/.env",
            "PHLO_ENVIRONMENT=production\nPOSTGRES_USER=lakehouse\nMINIO_ROOT_USER=object-admin\n",
        ),
        (".phlo/.env.local", "POSTGRES_PASSWORD=pg-secret-1\nMINIO_ROOT_PASSWORD=minio-secret-1\n"),
    )
    (tmp_path / ".phlo" / ".env.local").chmod(0o600)
    (tmp_path / ".phlo" / "docker-compose.yml").write_text(
        "# Dev mode: false\nservices:\n  postgres:\n    image: x\n"
    )

    # No backend readiness capability registered at all.
    monkeypatch.setattr("phlo.capabilities.resolve_capability", lambda _capability, _backend: None)

    report = run_production_readiness(
        plan=_plan(tmp_path, service_names=["postgres"]),
        project_root=tmp_path,
        environment="production",
    )
    check = next(c for c in report.checks if c.id is ProductionReadinessCheckId.BACKEND_READINESS)
    assert check.state is ProductionReadinessState.FAILED
    assert "missing required backend readiness adapters" in check.message


def test_backend_readiness_unavailable_blocks(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    _write(
        tmp_path,
        (
            ".phlo/.env",
            "PHLO_ENVIRONMENT=production\nPOSTGRES_USER=lakehouse\nMINIO_ROOT_USER=object-admin\n",
        ),
        (".phlo/.env.local", "POSTGRES_PASSWORD=pg-secret-1\nMINIO_ROOT_PASSWORD=minio-secret-1\n"),
    )
    (tmp_path / ".phlo" / ".env.local").chmod(0o600)
    (tmp_path / ".phlo" / "docker-compose.yml").write_text(
        "# Dev mode: false\nservices:\n  postgres:\n    image: x\n"
    )

    from phlo.security.backend_readiness import BackendReadinessResult, BackendReadinessState

    def _resolve(capability, backend):
        if capability != "backend_readiness":
            return None
        return SimpleNamespace(
            name=backend,
            provider=SimpleNamespace(
                backend_name=backend,
                inspect=lambda b=backend: BackendReadinessResult(
                    backend=b,
                    state=BackendReadinessState.UNAVAILABLE,
                    reason_code="evidence_unavailable",
                    message=f"{b} live evidence pending",
                ),
            ),
            metadata={},
        )

    monkeypatch.setattr("phlo.capabilities.resolve_capability", _resolve)
    report = run_production_readiness(
        plan=_plan(tmp_path, service_names=["postgres"]),
        project_root=tmp_path,
        environment="production",
    )
    check = next(c for c in report.checks if c.id is ProductionReadinessCheckId.BACKEND_READINESS)
    assert check.state is ProductionReadinessState.FAILED
    assert check.reason_code == ProductionReadinessReasonCode.BACKEND_READINESS_BLOCKED.value
