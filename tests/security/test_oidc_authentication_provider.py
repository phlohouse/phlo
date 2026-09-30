"""Focused tests for the core asymmetric OIDC authentication provider."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from phlo.capabilities import RequestContext
from phlo.capabilities.authentication import JWTAuthenticationProvider


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._content = json.dumps(payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def iter_bytes(self):  # noqa: ANN201
        yield self._content

    def raise_for_status(self) -> None:
        return None


def _b64(value: int) -> str:
    return (
        base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))
        .rstrip(b"=")
        .decode()
    )


def _provider(monkeypatch) -> tuple[JWTAuthenticationProvider, rsa.RSAPrivateKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "one",
                "alg": "RS256",
                "use": "sig",
                "n": _b64(numbers.n),
                "e": _b64(numbers.e),
            }
        ]
    }
    monkeypatch.setattr(
        "phlo.security.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: _Response(jwks),
    )
    provider = JWTAuthenticationProvider(
        secret=None,
        issuer="https://issuer.test",
        audience="phlo-api",
        jwks_url="http://127.0.0.1/jwks",
        allow_insecure_loopback_http=True,
        cache_ttl_seconds=300,
    )
    return provider, private_key


def _token(private_key: rsa.RSAPrivateKey, **claims: Any) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://issuer.test",
        "aud": "phlo-api",
        "sub": "user",
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        **claims,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "one"})


def test_jwt_provider_accepts_only_valid_rs256_claims(monkeypatch) -> None:
    provider, private_key = _provider(monkeypatch)
    token = _token(private_key, groups=["operators"], scope="openid phlo-api")

    result = provider.authenticate(
        RequestContext(
            headers={"authorization": f"Bearer {token}"},
            cookies={},
            query_params={},
        )
    )

    assert result.authenticated is True
    assert result.principal and result.principal.subject == "user"
    assert result.principal.groups == ("operators",)
    assert result.principal.claims["scopes"] == ["openid", "phlo-api"]


def test_jwt_provider_rejects_wrong_audience_and_hs256(monkeypatch) -> None:
    provider, private_key = _provider(monkeypatch)
    wrong_audience = _token(private_key, aud="other")
    hs256 = jwt.encode(
        {
            "iss": "https://issuer.test",
            "aud": "phlo-api",
            "sub": "user",
            "iat": 1,
            "nbf": 1,
            "exp": 2,
        },
        "not-an-oidc-signing-key-which-is-long-enough",
        algorithm="HS256",
        headers={"kid": "one"},
    )

    for token in (wrong_audience, hs256):
        assert provider.validate_token(token) is None
