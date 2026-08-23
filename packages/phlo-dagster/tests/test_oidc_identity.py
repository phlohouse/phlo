"""OIDC/JWKS hardening tests for the Dagster boundary.

Covers HTTPS-only JWKS fetches with explicit loopback opt-in, bounded numeric
configuration, one refresh then a global cooldown for unknown key ids, atomic
rejection of malformed or oversized JWKS documents, fail-closed cache-refresh
failures, and claim allowlisting on the resulting principal.
"""

from __future__ import annotations

import time

import httpx
import pytest

from phlo_dagster.oidc_identity import OIDCIdentityValidator

from _oidc_test_helpers import (
    AUDIENCE,
    ISSUER,
    JWKS_URL,
    JWKSResponse,
    jwk_for_private,
    key_and_jwks,
    token,
)


def _configure(monkeypatch, *, allow_http: bool = True) -> None:
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", JWKS_URL)
    if allow_http:
        monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")


def test_http_jwks_requires_explicit_loopback_test_opt_in(monkeypatch) -> None:
    _configure(monkeypatch, allow_http=False)

    with pytest.raises(ValueError, match="incomplete or insecure"):
        OIDCIdentityValidator()

    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", "http://keycloak.example/certs")
    with pytest.raises(ValueError, match="incomplete or insecure"):
        OIDCIdentityValidator()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PHLO_DAGSTER_OIDC_LEEWAY_SECONDS", "-1"),
        ("PHLO_DAGSTER_OIDC_LEEWAY_SECONDS", "301"),
        ("PHLO_DAGSTER_OIDC_JWKS_CACHE_TTL_SECONDS", "0"),
        ("PHLO_DAGSTER_OIDC_JWKS_CACHE_TTL_SECONDS", "86401"),
        ("PHLO_DAGSTER_OIDC_LEEWAY_SECONDS", "not-an-int"),
    ],
)
def test_oidc_numeric_configuration_is_bounded(monkeypatch, name: str, value: str) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        OIDCIdentityValidator()


def test_unknown_kids_refresh_once_then_global_cooldown(monkeypatch) -> None:
    private_key, jwks = key_and_jwks()
    _configure(monkeypatch)
    responses = [JWKSResponse(jwks), JWKSResponse(jwks)]
    fetches = 0

    def fetch(*_args, **_kwargs):
        nonlocal fetches
        fetches += 1
        return responses[min(fetches - 1, len(responses) - 1)]

    monkeypatch.setattr("phlo_dagster.oidc_identity.httpx.stream", fetch)
    validator = OIDCIdentityValidator()

    assert validator.validate(token(private_key, kid="random-a")) is None
    assert validator.validate(token(private_key, kid="random-b")) is None
    assert fetches == 2


def test_unknown_kid_refreshes_for_key_rotation(monkeypatch) -> None:
    private_a, jwks_a = key_and_jwks()
    private_b, _ = key_and_jwks()
    jwks_b = {"keys": [jwk_for_private(private_b, kid="key-2")]}
    _configure(monkeypatch)
    responses = [JWKSResponse(jwks_a), JWKSResponse(jwks_b)]
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: responses.pop(0),
    )
    validator = OIDCIdentityValidator()

    principal = validator.validate(token(private_b, kid="key-2"))

    assert principal is not None
    assert principal.subject == "viewer@example.com"


def test_jwks_preload_ignores_rsa_encryption_keys(monkeypatch) -> None:
    private_key, jwks = key_and_jwks()
    encryption_key = dict(jwks["keys"][0])
    encryption_key.update({"kid": "encryption-key", "alg": "RSA-OAEP", "use": "enc"})
    jwks["keys"].append(encryption_key)
    _configure(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )

    principal = OIDCIdentityValidator().validate(token(private_key))

    assert principal is not None


def test_expired_cache_refresh_failure_fails_closed_and_backs_off(monkeypatch) -> None:
    private_key, jwks = key_and_jwks()
    _configure(monkeypatch)
    fetches = 0

    def fetch(*_args, **_kwargs):
        nonlocal fetches
        fetches += 1
        if fetches == 1:
            return JWKSResponse(jwks)
        raise httpx.HTTPError("identity provider unavailable")

    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_CACHE_TTL_SECONDS", "1")
    monkeypatch.setattr("phlo_dagster.oidc_identity.httpx.stream", fetch)
    validator = OIDCIdentityValidator()
    validator._keys_fetched_at = time.monotonic() - 2

    assert validator.validate(token(private_key)) is None
    assert validator.validate(token(private_key)) is None
    assert fetches == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda key: key.pop("alg"),
        lambda key: key.update({"alg": "HS256"}),
        lambda key: key.update({"use": "enc"}),
        lambda key: key.update({"key_ops": ["sign"]}),
        lambda key: key.update({"n": "bad"}),
    ],
)
def test_malformed_jwks_document_is_rejected_atomically(monkeypatch, mutate) -> None:
    _private_key, jwks = key_and_jwks()
    mutate(jwks["keys"][0])
    _configure(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )

    with pytest.raises(RuntimeError, match="preload failed"):
        OIDCIdentityValidator()


def test_duplicate_kids_and_oversized_documents_are_rejected(monkeypatch) -> None:
    _private_key, jwks = key_and_jwks()
    jwks["keys"].append(dict(jwks["keys"][0]))
    _configure(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )
    with pytest.raises(RuntimeError, match="preload failed"):
        OIDCIdentityValidator()

    private_key, valid_jwks = key_and_jwks()
    del private_key
    oversized = JWKSResponse(valid_jwks)
    oversized.content = b"x" * (1_048_576 + 1)
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: oversized,
    )
    with pytest.raises(RuntimeError, match="preload failed"):
        OIDCIdentityValidator()


def test_oidc_principal_drops_unallowlisted_claims(monkeypatch) -> None:
    private_key, jwks = key_and_jwks()
    _configure(monkeypatch)
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )
    principal = OIDCIdentityValidator().validate(
        token(private_key, extra_claims={"secret": "must-not-survive", "roles": ["admin"]})
    )

    assert principal is not None
    assert "secret" not in principal.claims
    assert "roles" not in principal.claims
