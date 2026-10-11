"""Bounded RS256 OIDC token validation backed by an explicit JWKS endpoint."""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from phlo.capabilities.interfaces import AuthPrincipal
from phlo.logging_context import get_logger

logger = get_logger(__name__)

_MAX_JWKS_BYTES = 1_048_576
_MAX_JWKS_KEYS = 32
_MAX_GROUPS = 100
_MAX_GROUP_LENGTH = 256
_MAX_SCOPES = 64
_MAX_SCOPE_LENGTH = 128
_MAX_SUBJECT_LENGTH = 512


class OIDCVerificationUnavailable(RuntimeError):
    """Raised when current JWKS verification material cannot be obtained."""


class OIDCIdentityValidator:
    """Validate only RS256 tokens against configured, fresh JWKS material."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        groups_claim: str = "groups",
        leeway_seconds: int = 30,
        cache_ttl_seconds: int = 300,
        refresh_min_interval_seconds: int = 5,
        ca_file: str | None = None,
        allow_insecure_loopback_http: bool = False,
        stream: Callable[..., Any] | None = None,
    ) -> None:
        self.issuer = issuer.strip()
        self.audience = audience.strip()
        self.jwks_url = jwks_url.strip()
        self.groups_claim = groups_claim.strip() or "groups"
        self.leeway = self._bounded("leeway_seconds", leeway_seconds, 0, 300)
        self.cache_ttl = self._bounded("cache_ttl_seconds", cache_ttl_seconds, 1, 86_400)
        self.refresh_min_interval = self._bounded(
            "refresh_min_interval_seconds", refresh_min_interval_seconds, 1, 300
        )
        self.ca_file = ca_file
        self._stream = stream or httpx.stream
        if not self._valid_configuration(allow_insecure_loopback_http):
            raise ValueError("OIDC configuration is incomplete or insecure")
        self._keys: dict[str, dict[str, Any]] = {}
        self._keys_fetched_at = 0.0
        self._last_refresh_attempt = 0.0
        self._refresh_backoff_until = 0.0
        self._negative_kids: dict[str, float] = {}
        self._lock = threading.Lock()
        if not self._refresh_keys(force=True):
            raise RuntimeError("OIDC JWKS preload failed")
        self._last_refresh_attempt = 0.0

    @staticmethod
    def _bounded(name: str, value: int, minimum: int, maximum: int) -> int:
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return value

    def _valid_configuration(self, allow_insecure_loopback_http: bool) -> bool:
        issuer = urlparse(self.issuer)
        if not self.issuer or not self.audience or issuer.scheme != "https" or not issuer.netloc:
            return False
        parsed = urlparse(self.jwks_url)
        if parsed.scheme == "https" and parsed.netloc:
            return True
        return (
            allow_insecure_loopback_http
            and parsed.scheme == "http"
            and self._is_loopback_host(parsed.hostname)
        )

    @staticmethod
    def _is_loopback_host(hostname: str | None) -> bool:
        if hostname == "localhost":
            return True
        try:
            return bool(hostname and ipaddress.ip_address(hostname).is_loopback)
        except ValueError:
            return False

    def validate(self, token: str) -> AuthPrincipal | None:
        """Return a principal only after signature and required claims validate."""
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                return None
            key = self._key_for_kid(header["kid"])
            if key is None:
                if not self._keys or time.monotonic() - self._keys_fetched_at >= self.cache_ttl:
                    raise OIDCVerificationUnavailable("OIDC signing keys are unavailable or stale")
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
                options={"require": ["iss", "aud", "sub", "exp", "iat"]},
            )
            return self._principal_from_claims(claims)
        except (jwt.PyJWTError, TypeError, ValueError, KeyError):
            logger.debug("oidc_token_rejected", exc_info=True)
            return None

    def _principal_from_claims(self, claims: dict[str, Any]) -> AuthPrincipal | None:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > _MAX_SUBJECT_LENGTH:
            return None
        raw_groups = claims.get(self.groups_claim, [])
        if isinstance(raw_groups, str):
            groups = tuple(group.strip() for group in raw_groups.split(",") if group.strip())
        elif isinstance(raw_groups, list) and all(isinstance(group, str) for group in raw_groups):
            groups = tuple(raw_groups)
        elif raw_groups is None:
            groups = ()
        else:
            return None
        if len(groups) > _MAX_GROUPS or any(
            not group or len(group) > _MAX_GROUP_LENGTH for group in groups
        ):
            return None
        raw_scopes = claims.get("scope", claims.get("scp", []))
        if isinstance(raw_scopes, str):
            scopes = tuple(raw_scopes.split())
        elif isinstance(raw_scopes, list) and all(isinstance(scope, str) for scope in raw_scopes):
            scopes = tuple(raw_scopes)
        else:
            return None
        if len(scopes) > _MAX_SCOPES or any(
            not scope or len(scope) > _MAX_SCOPE_LENGTH for scope in scopes
        ):
            return None
        email = claims.get("email")
        return AuthPrincipal(
            subject=subject,
            principal_type="user",
            email=email if isinstance(email, str) else None,
            groups=groups,
            issuer=self.issuer,
            claims={
                "sub": subject,
                "groups": list(groups),
                "scopes": list(scopes),
                **{key: claims[key] for key in ("auth_time", "amr") if key in claims},
            },
            attributes={"authentication_source": "oidc", "oidc_audience": self.audience},
        )

    def readiness(self) -> bool:
        """Return whether an unexpired verification-key cache is available."""
        if self._keys and time.monotonic() - self._keys_fetched_at < self.cache_ttl:
            return True
        return self._refresh_keys()

    def _key_for_kid(self, kid: str) -> dict[str, Any] | None:
        now = time.monotonic()
        if len(kid) > 128 or (kid in self._keys and now - self._keys_fetched_at < self.cache_ttl):
            return self._keys.get(kid)
        if kid in self._negative_kids and now < self._negative_kids[kid]:
            return None
        if not self._refresh_keys(force=kid not in self._keys):
            return None
        key = self._keys.get(kid)
        if key is None:
            self._negative_kids[kid] = now + self.refresh_min_interval
        return key

    def _refresh_keys(self, *, force: bool = False) -> bool:
        with self._lock:
            now = time.monotonic()
            if not force and self._keys and now - self._keys_fetched_at < self.cache_ttl:
                return True
            if now < self._refresh_backoff_until or (
                self._last_refresh_attempt
                and now - self._last_refresh_attempt < self.refresh_min_interval
            ):
                return False
            self._last_refresh_attempt = now
            try:
                parsed = self._fetch_signing_keys()
                self._keys, self._keys_fetched_at = parsed, now
                self._refresh_backoff_until = 0.0
                self._negative_kids.clear()
                return True
            except (httpx.HTTPError, OSError, jwt.PyJWTError, ValueError, KeyError, TypeError):
                self._refresh_backoff_until = now + self.refresh_min_interval
                logger.warning("oidc_jwks_refresh_failed", exc_info=True)
                return False

    def _fetch_signing_keys(self) -> dict[str, dict[str, Any]]:
        with self._stream(
            "GET",
            self.jwks_url,
            verify=self.ca_file or True,
            timeout=5.0,
            follow_redirects=False,
        ) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("OIDC JWKS redirects are not allowed")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > _MAX_JWKS_BYTES:
                    raise ValueError("OIDC JWKS response is too large")
                chunks.append(chunk)
            response.raise_for_status()
        payload = json.loads(b"".join(chunks))
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list) or len(keys) > _MAX_JWKS_KEYS:
            raise ValueError("OIDC JWKS contains an invalid number of keys")
        parsed: dict[str, dict[str, Any]] = {}
        for key in keys:
            self._add_signing_key(parsed, key)
        if not parsed:
            raise ValueError("OIDC JWKS contains no usable signing keys")
        return parsed

    @staticmethod
    def _add_signing_key(parsed: dict[str, dict[str, Any]], key: Any) -> None:
        if not isinstance(key, dict):
            raise ValueError("OIDC JWKS contains an invalid key")
        key_ops = key.get("key_ops")
        if key.get("alg") != "RS256" or key.get("use") not in (None, "sig"):
            return
        if key_ops is not None and (not isinstance(key_ops, list) or "verify" not in key_ops):
            return
        kid = key.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > 128 or kid in parsed:
            raise ValueError("OIDC JWKS has an invalid or duplicate signing kid")
        if key.get("kty") != "RSA":
            raise ValueError("OIDC JWKS signing key is not RSA")
        public_key = RSAAlgorithm.from_jwk(json.dumps(key))
        if not isinstance(public_key, RSAPublicKey) or public_key.key_size < 2048:
            raise ValueError("OIDC JWKS signing key is too weak")
        parsed[kid] = key
