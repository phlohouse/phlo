"""Cryptographically verified OIDC identity for the Dagster boundary.

OIDCIdentityValidator checks RS256 tokens against an explicitly
configured issuer/audience/JWKS triple. All settings come from
environment variables with hard upper bounds; JWKS responses are size-
and key-count-limited and cached under a lock with rate-limited
refresh. Insecure HTTP is accepted only for loopback hosts and only
when explicitly enabled; validation failures return None, never raise.
"""

from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from phlo.capabilities import AuthPrincipal
from phlo.logging import get_logger

logger = get_logger(__name__)

OIDC_ISSUER_ENV = "PHLO_DAGSTER_OIDC_ISSUER"
OIDC_AUDIENCE_ENV = "PHLO_DAGSTER_OIDC_AUDIENCE"
OIDC_JWKS_URL_ENV = "PHLO_DAGSTER_OIDC_JWKS_URL"
OIDC_CA_FILE_ENV = "PHLO_DAGSTER_OIDC_CA_FILE"
OIDC_GROUPS_CLAIM_ENV = "PHLO_DAGSTER_OIDC_GROUPS_CLAIM"
OIDC_LEEWAY_ENV = "PHLO_DAGSTER_OIDC_LEEWAY_SECONDS"
OIDC_JWKS_CACHE_TTL_ENV = "PHLO_DAGSTER_OIDC_JWKS_CACHE_TTL_SECONDS"
OIDC_ALLOW_INSECURE_HTTP_ENV = "PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP"
OIDC_REFRESH_MIN_INTERVAL_ENV = "PHLO_DAGSTER_OIDC_REFRESH_MIN_INTERVAL_SECONDS"
OIDC_REQUIRED_ENV = "PHLO_DAGSTER_OIDC_REQUIRED"
_MAX_JWKS_BYTES = 1_048_576
_MAX_JWKS_KEYS = 32
_MAX_LEEWAY_SECONDS = 300
_MAX_CACHE_TTL_SECONDS = 86_400
_MAX_REFRESH_MIN_INTERVAL_SECONDS = 300


class OIDCIdentityValidator:
    """Validate RS256 OIDC tokens against an explicitly configured JWKS."""

    def __init__(self) -> None:
        self.issuer = os.environ.get(OIDC_ISSUER_ENV, "").strip()
        self.audience = os.environ.get(OIDC_AUDIENCE_ENV, "").strip()
        self.jwks_url = os.environ.get(OIDC_JWKS_URL_ENV, "").strip()
        self.ca_file = os.environ.get(OIDC_CA_FILE_ENV, "").strip() or None
        self.groups_claim = os.environ.get(OIDC_GROUPS_CLAIM_ENV, "groups").strip() or "groups"
        self.leeway = self._bounded_int(OIDC_LEEWAY_ENV, 30, 0, _MAX_LEEWAY_SECONDS)
        self.cache_ttl = self._bounded_int(OIDC_JWKS_CACHE_TTL_ENV, 300, 1, _MAX_CACHE_TTL_SECONDS)
        self.refresh_min_interval = self._bounded_int(
            OIDC_REFRESH_MIN_INTERVAL_ENV,
            5,
            1,
            _MAX_REFRESH_MIN_INTERVAL_SECONDS,
        )
        self._keys: dict[str, dict[str, Any]] = {}
        self._keys_fetched_at = 0.0
        self._last_refresh_attempt = 0.0
        self._refresh_backoff_until = 0.0
        self._negative_kids: dict[str, float] = {}
        self._lock = threading.Lock()
        if self._has_partial_configuration() and not self.configured:
            raise ValueError("Dagster OIDC configuration is incomplete or insecure")
        if self.configured and not self._refresh_keys(force=True):
            raise RuntimeError("Dagster OIDC JWKS preload failed")
        # Startup preload is separate from request-driven unknown-kid refreshes.
        self._last_refresh_attempt = 0.0

    @staticmethod
    def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = os.environ.get(name, str(default)).strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return value

    def _has_partial_configuration(self) -> bool:
        return any((self.issuer, self.audience, self.jwks_url, self.ca_file))

    @property
    def configured(self) -> bool:
        """Return whether issuer, audience, and JWKS URL settings are present and valid."""
        if not (self.issuer and self.audience and self.jwks_url):
            return False
        issuer = urlparse(self.issuer)
        if issuer.scheme != "https" or not issuer.netloc:
            return False
        parsed = urlparse(self.jwks_url)
        if parsed.scheme == "https":
            return bool(self.ca_file)
        if parsed.scheme != "http":
            return False
        # Plain HTTP is test-only and loopback-only. The secure profile never
        # sets this opt-in, so a production endpoint cannot silently downgrade.
        return os.environ.get(
            OIDC_ALLOW_INSECURE_HTTP_ENV, ""
        ).strip().lower() == "true" and self._is_loopback_host(parsed.hostname)

    @staticmethod
    def _is_loopback_host(hostname: str | None) -> bool:
        if hostname == "localhost":
            return True
        try:
            return bool(hostname and ipaddress.ip_address(hostname).is_loopback)
        except ValueError:
            return False

    def validate(self, token: str) -> AuthPrincipal | None:
        """Return a principal only when signature and all OIDC claims validate."""
        if not self.configured:
            return None
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                return None
            key = self._key_for_kid(str(header["kid"]))
            if key is None:
                return None
            public_key = RSAAlgorithm.from_jwk(json.dumps(key))
            if not isinstance(public_key, RSAPublicKey):
                return None
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            )
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject:
                return None
            groups_claim = claims.get(self.groups_claim, ())
            if isinstance(groups_claim, str):
                groups = tuple(value.strip() for value in groups_claim.split(",") if value.strip())
            elif isinstance(groups_claim, list):
                groups = tuple(value for value in groups_claim if isinstance(value, str) and value)
            else:
                groups = ()
            return AuthPrincipal(
                subject=subject,
                principal_type="user",
                email=claims.get("email") if isinstance(claims.get("email"), str) else None,
                groups=groups,
                issuer=self.issuer,
                # Keep only identity material needed by downstream scope and
                # audit code; never retain arbitrary token claims in memory.
                claims={"sub": subject, "groups": list(groups)},
                attributes={"authentication_source": "oidc", "oidc_audience": self.audience},
            )
        except (jwt.PyJWTError, TypeError, ValueError, KeyError):
            logger.debug("dagster_oidc_token_rejected", exc_info=True)
            return None

    def readiness(self) -> bool:
        """Refresh expired keys and report whether verified OIDC is usable."""
        if not self.configured:
            return False
        now = time.monotonic()
        if self._keys and now - self._keys_fetched_at < self.cache_ttl:
            return True
        return self._refresh_keys()

    def _key_for_kid(self, kid: str) -> dict[str, Any] | None:
        now = time.monotonic()
        if kid in self._keys and now - self._keys_fetched_at < self.cache_ttl:
            return self._keys[kid]
        # Cache unknown kids briefly so a flood of forged tokens cannot force
        # one JWKS fetch per request.
        if kid in self._negative_kids and now < self._negative_kids[kid]:
            return None
        refreshed = self._refresh_keys(force=kid not in self._keys)
        if not refreshed:
            return None
        key = self._keys.get(kid)
        if key is None:
            self._negative_kids[kid] = now + self.refresh_min_interval
        return key

    def _refresh_keys(self, *, force: bool = False) -> bool:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._keys_fetched_at < self.cache_ttl and self._keys:
                return True
            # Rate-limit refresh attempts and back off after failures so an
            # unreachable identity provider does not turn every token
            # validation into a network call.
            if now < self._refresh_backoff_until:
                return False
            if (
                self._last_refresh_attempt
                and now - self._last_refresh_attempt < self.refresh_min_interval
            ):
                return False
            self._last_refresh_attempt = now
            try:
                with httpx.stream(
                    "GET",
                    self.jwks_url,
                    verify=self.ca_file or True,
                    timeout=5.0,
                    follow_redirects=False,
                ) as response:
                    status_code = getattr(response, "status_code", 200)
                    if 300 <= status_code < 400:
                        raise ValueError("OIDC JWKS redirects are not allowed")
                    chunks: list[bytes] = []
                    total_bytes = 0
                    for chunk in response.iter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > _MAX_JWKS_BYTES:
                            raise ValueError("OIDC JWKS response is too large")
                        chunks.append(chunk)
                    response.raise_for_status()
                payload = json.loads(b"".join(chunks))
                keys = payload.get("keys") if isinstance(payload, dict) else None
                if not isinstance(keys, list):
                    raise ValueError("OIDC JWKS response does not contain a keys list")
                if len(keys) > _MAX_JWKS_KEYS:
                    raise ValueError("OIDC JWKS contains too many keys")
                parsed_keys: dict[str, dict[str, Any]] = {}
                for key in keys:
                    if not isinstance(key, dict):
                        continue
                    # Providers commonly publish encryption keys alongside
                    # signing keys. They are irrelevant to RS256 signature
                    # verification and must not make a valid signing set
                    # unusable.
                    key_ops = key.get("key_ops")
                    is_signing_candidate = (
                        key.get("alg") == "RS256"
                        and key.get("use") in (None, "sig")
                        and (key_ops is None or (isinstance(key_ops, list) and "verify" in key_ops))
                    )
                    if not is_signing_candidate:
                        continue
                    if not key.get("kid"):
                        raise ValueError("OIDC JWKS contains an invalid signing key")
                    kid = str(key["kid"])
                    if len(kid) > 128:
                        raise ValueError("OIDC JWKS kid is too long")
                    if kid in parsed_keys:
                        raise ValueError("OIDC JWKS contains duplicate signing kid values")
                    if key.get("kty") != "RSA":
                        raise ValueError("OIDC JWKS signing key is not RSA")
                    public_key = RSAAlgorithm.from_jwk(json.dumps(key))
                    if public_key.key_size < 2048:
                        raise ValueError("OIDC JWKS RSA key is smaller than 2048 bits")
                    parsed_keys[kid] = key
                if not parsed_keys:
                    raise ValueError("OIDC JWKS contains no usable signing keys")
                self._keys = parsed_keys
                self._keys_fetched_at = now
                self._refresh_backoff_until = 0.0
                self._negative_kids.clear()
                return True
            except (httpx.HTTPError, OSError, jwt.PyJWTError, ValueError, KeyError, TypeError):
                self._refresh_backoff_until = now + self.refresh_min_interval
                logger.warning("dagster_oidc_jwks_refresh_failed", exc_info=True)
                return False
