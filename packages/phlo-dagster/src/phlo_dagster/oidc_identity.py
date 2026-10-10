"""Dagster environment adapter for core OIDC identity validation."""

from __future__ import annotations


import httpx

from phlo_dagster.settings import DagsterOidcTimingSettings, get_process_settings
from phlo.capabilities import AuthPrincipal
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
        settings = get_process_settings()
        issuer = settings.get(OIDC_ISSUER_ENV, "").strip()
        audience = settings.get(OIDC_AUDIENCE_ENV, "").strip()
        jwks_url = settings.get(OIDC_JWKS_URL_ENV, "").strip()
        ca_file = settings.get(OIDC_CA_FILE_ENV, "").strip() or None
        if not any((issuer, audience, jwks_url, ca_file)):
            self.issuer = issuer
            self.audience = audience
            self.jwks_url = jwks_url
            return
        timing = DagsterOidcTimingSettings()
        super().__init__(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url,
            ca_file=ca_file,
            groups_claim=settings.get(OIDC_GROUPS_CLAIM_ENV, "groups"),
            leeway_seconds=timing.leeway_seconds,
            cache_ttl_seconds=timing.jwks_cache_ttl_seconds,
            refresh_min_interval_seconds=timing.refresh_min_interval_seconds,
            allow_insecure_loopback_http=settings.phlo_dagster_oidc_allow_insecure_http,
            stream=httpx.stream,
        )

    @property
    def configured(self) -> bool:
        """Maintain the historical unconfigured Dagster validator state."""
        return bool(self.issuer and self.audience and self.jwks_url)

    def validate(self, token: str) -> AuthPrincipal | None:
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
