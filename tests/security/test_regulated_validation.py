"""Tests for regulated-stack validation: internal backend boundary
rules enforced over generated docker-compose files."""

from __future__ import annotations

from pathlib import Path

import yaml

from phlo.security.validation import run_regulated_validation


def _write_generated_compose(tmp_path: Path, services: dict[str, object]) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text(yaml.safe_dump({"services": services}))


def test_regulated_backend_boundary_accepts_internal_generated_backends(
    monkeypatch, tmp_path: Path
) -> None:
    from phlo.security.validation import _check_internal_backend_boundary

    _write_generated_compose(
        tmp_path,
        {
            "postgres": {"environment": {"POSTGRES_DB": "phlo"}},
            "minio": {"environment": {"MINIO_ROOT_USER": "configured"}},
            "nessie": {},
            "trino": {},
            "dagster": {
                "ports": ["10006:3000"],
                "labels": {"traefik.enable": "true"},
            },
            "phlo-api": {"ports": ["4000:4000"]},
        },
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    result = _check_internal_backend_boundary()

    assert result.passed is True
    assert "minio, nessie, postgres, trino" in result.message


def test_regulated_backend_boundary_rejects_published_or_routed_backend(
    monkeypatch, tmp_path: Path
) -> None:
    from phlo.security.validation import _check_internal_backend_boundary

    _write_generated_compose(
        tmp_path,
        {
            "postgres": {"ports": ["10000:5432"]},
            "minio": {"labels": ["traefik.enable=true"]},
            "nessie": {"network_mode": "host"},
            "trino": {},
            "dagster": {"ports": ["10006:3000"]},
        },
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    result = _check_internal_backend_boundary()

    assert result.passed is False
    assert "postgres publishes host ports" in result.message
    assert "minio has public Traefik labels" in result.message
    assert "nessie uses host networking" in result.message


def test_regulated_backend_boundary_requires_a_production_render(
    monkeypatch, tmp_path: Path
) -> None:
    from phlo.security.validation import _check_internal_backend_boundary

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    result = _check_internal_backend_boundary()

    assert result.passed is False
    assert "phlo services init --production" in result.message


def test_regulated_backend_boundary_rejects_invalid_utf8_compose(
    monkeypatch, tmp_path: Path
) -> None:
    from phlo.security.validation import _check_internal_backend_boundary

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_bytes(b"\xff")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    result = _check_internal_backend_boundary()

    assert result.passed is False
    assert "Could not read generated Compose configuration" in result.message


def test_regulated_validation_requires_audit_hmac_key(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.delenv("PHLO_AUDIT_HMAC_KEY", raising=False)
    monkeypatch.delenv("PHLO_SIGNATURE_HMAC_KEY", raising=False)

    report = run_regulated_validation(surface_actions=[], surface_resource_types=[])

    check = next(item for item in report.checks if item.name == "compliance_hmac_keys_configured")
    assert check.passed is False
    assert "PHLO_AUDIT_HMAC_KEY is required" in check.message


def test_regulated_validation_requires_signature_hmac_key(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.setenv("PHLO_AUDIT_HMAC_KEY", "test-audit-key")
    monkeypatch.delenv("PHLO_SIGNATURE_HMAC_KEY", raising=False)

    report = run_regulated_validation(surface_actions=[], surface_resource_types=[])

    check = next(item for item in report.checks if item.name == "compliance_hmac_keys_configured")
    assert check.passed is False
    assert "PHLO_SIGNATURE_HMAC_KEY is required" in check.message


def test_regulated_validation_accepts_configured_hmac_keys(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.setenv("PHLO_AUDIT_HMAC_KEY", "test-audit-key")
    monkeypatch.setenv("PHLO_SIGNATURE_HMAC_KEY", "test-signature-key")

    report = run_regulated_validation(surface_actions=[], surface_resource_types=[])

    check = next(item for item in report.checks if item.name == "compliance_hmac_keys_configured")
    assert check.passed is True
    assert "audit and signature HMAC keys are configured" in check.message


def test_regulated_validation_rejects_arbitrary_identity_provider(monkeypatch) -> None:
    from phlo.security.validation import _check_identity_provider

    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "foo")

    result = _check_identity_provider()

    assert result.passed is False
    assert "Unsupported regulated identity provider" in result.message


def test_regulated_validation_rejects_missing_provider_settings(monkeypatch) -> None:
    from phlo.security.validation import _check_identity_provider

    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "proxy")
    monkeypatch.delenv("PHLO_AUTH_PROXY_SHARED_SECRET", raising=False)

    result = _check_identity_provider()

    assert result.passed is False
    assert "PHLO_AUTH_PROXY_SHARED_SECRET" in result.message


def test_regulated_validation_rejects_conflicting_identity_provider_settings(monkeypatch) -> None:
    from phlo.security.validation import _check_identity_provider

    monkeypatch.setenv("PHLO_AUTHENTICATION_METHOD", "proxy")
    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "jwt")

    result = _check_identity_provider()

    assert result.passed is False
    assert "Conflicting authentication settings" in result.message


def test_regulated_validation_rejects_unregistered_provider(monkeypatch) -> None:
    from phlo.security.validation import _check_identity_provider

    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "proxy")
    monkeypatch.setenv("PHLO_AUTH_PROXY_SHARED_SECRET", "proxy-secret")
    monkeypatch.setattr("phlo.capabilities.list_capabilities", lambda family: [])

    result = _check_identity_provider()

    assert result.passed is False
    assert "not registered" in result.message


def test_regulated_validation_rejects_service_token_without_subject(monkeypatch) -> None:
    from phlo.security.validation import _check_identity_provider

    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "service_token")
    monkeypatch.setenv("PHLO_AUTH_SERVICE_TOKENS", '{"secret": {}}')

    result = _check_identity_provider()

    assert result.passed is False
    assert "explicit subject" in result.message


def test_regulated_validation_requires_jwt_issuer_and_audience(monkeypatch) -> None:
    from phlo.security.validation import _check_identity_provider

    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "jwt")
    monkeypatch.setenv("PHLO_AUTH_JWT_SECRET", "secret")
    monkeypatch.delenv("PHLO_AUTH_JWT_ISSUER", raising=False)
    monkeypatch.delenv("PHLO_AUTH_JWT_AUDIENCE", raising=False)

    result = _check_identity_provider()

    assert result.passed is False
    assert "PHLO_AUTH_JWT_ISSUER" in result.message


def test_regulated_validation_rejects_unknown_authorization_backend(monkeypatch) -> None:
    from phlo.security.validation import _check_authorization_backend

    monkeypatch.setenv("PHLO_AUTHORIZATION_BACKEND", "foo")
    monkeypatch.setattr("phlo.capabilities.resolve_capability", lambda *_args, **_kwargs: None)

    result = _check_authorization_backend()

    assert result.passed is False
    assert "not registered" in result.message


def test_regulated_validation_requires_registered_authorization_backend(monkeypatch) -> None:
    from phlo.security.validation import _check_authorization_backend

    monkeypatch.setenv("PHLO_AUTHORIZATION_BACKEND", "opa")
    monkeypatch.setattr(
        "phlo.capabilities.resolve_capability",
        lambda *_args, **_kwargs: object(),
    )

    result = _check_authorization_backend()

    assert result.passed is True
    assert "opa" in result.message


def test_regulated_validation_rejects_conflicting_authorization_backend(
    monkeypatch, tmp_path
) -> None:
    from phlo.security.validation import _check_authorization_backend

    (tmp_path / "phlo.yaml").write_text(
        "services:\n  phlo-api:\n    authorization:\n      backend: default\n"
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_AUTHORIZATION_BACKEND", "opa")

    result = _check_authorization_backend()

    assert result.passed is False
    assert "Conflicting authorization settings" in result.message


def test_regulated_validation_inspects_selected_services(monkeypatch) -> None:
    from phlo.security.gating import validate_service_selection
    from phlo.security.validation import _configured_service_names

    monkeypatch.setenv("PHLO_ENABLED_SERVICES", "phlo-api,pgweb")
    monkeypatch.setattr("phlo.infrastructure.config.load_project_config", lambda _root: {})

    assert _configured_service_names() == ["pgweb", "phlo-api"]
    selection = validate_service_selection(["future-service"], regulated=True)
    assert selection["unknown"] == ["future-service"]
    assert selection["blocked"] == [
        {"service": "future-service", "reason": "Not a known approved regulated entry point"}
    ]
    assert selection["allowed"] == []


def test_regulated_validation_reads_services_from_configured_project_root(monkeypatch) -> None:
    from phlo.security.validation import _configured_service_names

    project_root = Path("/configured/project")
    observed: list[Path] = []

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(project_root))
    monkeypatch.setattr(
        "phlo.infrastructure.config.load_project_config",
        lambda root: observed.append(root) or {"services": {"enabled": ["openmetadata"]}},
    )

    assert _configured_service_names() == ["openmetadata"]
    assert observed == [project_root.resolve()]


# ---------------------------------------------------------------------------
# Observatory settings backend — regulated validation (issue #626)
# ---------------------------------------------------------------------------


def test_regulated_validation_rejects_memory_settings_backend(monkeypatch) -> None:
    """Regulated mode must reject the memory settings backend."""
    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")

    report = run_regulated_validation(surface_actions=[], surface_resource_types=[])

    check = next(item for item in report.checks if item.name == "settings_backend_durable")
    assert check.passed is False
    assert "memory backend is not permitted" in check.message


def test_regulated_validation_accepts_postgres_settings_backend(monkeypatch) -> None:
    """Regulated mode must accept the durable postgres settings backend."""
    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "postgres")

    report = run_regulated_validation(surface_actions=[], surface_resource_types=[])

    check = next(item for item in report.checks if item.name == "settings_backend_durable")
    assert check.passed is True
    assert "durable" in check.message.lower()
