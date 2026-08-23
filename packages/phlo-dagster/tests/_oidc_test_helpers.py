"""Small deterministic OIDC fixtures for Dagster boundary tests.

Generates an RSA keypair plus matching JWKS document, mints RS256 tokens with
controllable claims, and serves the JWKS through a fake response object so
tests never touch a real identity provider.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://keycloak.test/realms/phlo"
AUDIENCE = "phlo-dagster"
JWKS_URL = "http://127.0.0.1/realms/phlo/protocol/openid-connect/certs"


def _base64url(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


def key_and_jwks() -> tuple[rsa.RSAPrivateKey, dict[str, list[dict[str, str]]]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, {"keys": [jwk_for_private(private_key)]}


def jwk_for_private(private_key: rsa.RSAPrivateKey, *, kid: str = "key-1") -> dict[str, str]:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": _base64url(numbers.n),
        "e": _base64url(numbers.e),
    }


def token(
    private_key: rsa.RSAPrivateKey,
    *,
    subject: str = "viewer@example.com",
    groups: list[str] | None = None,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    now: int | None = None,
    expires_in: int = 300,
    not_before: int | None = None,
    algorithm: str = "RS256",
    kid: str = "key-1",
    extra_claims: dict[str, Any] | None = None,
) -> str:
    current = int(time.time()) if now is None else now
    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": current,
        "exp": current + expires_in,
        "groups": groups or ["viewer"],
        "email": subject,
    }
    if not_before is not None:
        payload["nbf"] = not_before
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, private_key, algorithm=algorithm, headers={"kid": kid})


class JWKSResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.status_code = 200
        self.content = json.dumps(payload).encode()

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload

    def __enter__(self) -> "JWKSResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def iter_bytes(self):  # noqa: ANN201
        yield self.content


def stream_response(response: JWKSResponse):  # noqa: ANN201
    return response
