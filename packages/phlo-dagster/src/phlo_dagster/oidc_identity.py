"""Dagster environment adapter for core OIDC identity validation."""

from __future__ import annotations

import os

import httpx

from phlo.security.oidc_identity import (
    OIDCIdentityValidator as CoreOIDCIdentityValidator,
    OIDCVerificationUnavailable,
)

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


class OIDCIdentityValidator(CoreOIDCIdentityValidator):
    """Preserve Dagster's environment configuration over the core validator."""

    def __init__(self) -> None:
        issuer = os.environ.get(OIDC_ISSUER_ENV, "").strip()
        audience = os.environ.get(OIDC_AUDIENCE_ENV, "").strip()
        jwks_url = os.environ.get(OIDC_JWKS_URL_ENV, "").strip()
        ca_file = os.environ.get(OIDC_CA_FILE_ENV, "").strip() or None
        if not any((issuer, audience, jwks_url, ca_file)):
            self.issuer = issuer
            self.audience = audience
            self.jwks_url = jwks_url
            return
        leeway = self._environment_int(OIDC_LEEWAY_ENV, 30)
        cache_ttl = self._environment_int(OIDC_JWKS_CACHE_TTL_ENV, 300)
        refresh_interval = self._environment_int(OIDC_REFRESH_MIN_INTERVAL_ENV, 5)
        self._check_range(OIDC_LEEWAY_ENV, leeway, 0, 300)
        self._check_range(OIDC_JWKS_CACHE_TTL_ENV, cache_ttl, 1, 86_400)
        self._check_range(OIDC_REFRESH_MIN_INTERVAL_ENV, refresh_interval, 1, 300)
        super().__init__(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url,
            ca_file=ca_file,
            groups_claim=os.environ.get(OIDC_GROUPS_CLAIM_ENV, "groups"),
            leeway_seconds=leeway,
            cache_ttl_seconds=cache_ttl,
            refresh_min_interval_seconds=refresh_interval,
            allow_insecure_loopback_http=os.environ.get(OIDC_ALLOW_INSECURE_HTTP_ENV, "").lower()
            == "true",
            stream=httpx.stream,
        )

    @staticmethod
    def _environment_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc

    @staticmethod
    def _check_range(name: str, value: int, minimum: int, maximum: int) -> None:
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")

    @property
    def configured(self) -> bool:
        """Maintain the historical unconfigured Dagster validator state."""
        return bool(self.issuer and self.audience and self.jwks_url)

    def validate(self, token: str):  # noqa: ANN201
        """Reject tokens when the optional Dagster OIDC mode is unconfigured."""
        if not self.configured:
            return None
        try:
            return super().validate(token)
        except OIDCVerificationUnavailable:
            return None

    def readiness(self) -> bool:
        """Report optional OIDC unavailable until its full configuration is present."""
        return self.configured and super().readiness()
