"""Built-in authentication providers for the capability registry.

Provides static, reverse-proxy, service-token, and JWT providers. Each loads
its configuration from environment variables first, falling back to
phlo.yaml; register_default_capability_providers activates a provider only
when it is explicitly enabled in configuration.
Imported by the phlo capabilities layer (phlo.capabilities.discovery) and phlo.security.
Registers default providers into the phlo.capabilities registry at activation time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from phlo.capabilities.interfaces import (
    AuthenticatedSession,
    AuthPrincipal,
    AuthResult,
    BrowserLoginStart,
    LogoutResult,
    RequestContext,
)
from phlo.capabilities.registry import register_capability
from phlo.capabilities.specs import AuthenticationProviderSpec
from phlo.capabilities.support import CapabilitySupport
from phlo.infrastructure.config import (
    get_authentication_config,
)
from phlo.logging import get_logger

logger = get_logger(__name__)


def _authentication_subconfig(name: str) -> dict[str, Any]:
    """Return a provider-specific authentication config block from phlo.yaml."""
    auth_config = get_authentication_config()
    raw = auth_config.get(name)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"phlo.yaml authentication.{name} must be a mapping")
    return raw


def _optional_bool(value: Any, *, path: str) -> bool | None:
    """Validate an optional boolean config value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ValueError(f"{path} must be a boolean")


def _string_dict(value: Any, *, path: str) -> dict[str, dict[str, Any]]:
    """Validate a token-keyed config mapping."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a mapping")

    normalized: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{path} keys must be non-empty strings")
        if not isinstance(item, dict):
            raise ValueError(f"{path}.{key} must be a mapping")
        normalized[key] = item
    return normalized


def _string_list(value: Any, *, path: str) -> list[str] | None:
    """Validate a list of non-empty strings."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{path} entries must be strings")
        stripped = item.strip()
        if not stripped:
            raise ValueError(f"{path} entries cannot be empty")
        normalized.append(stripped)
    return normalized


def _optional_string(value: Any, *, path: str) -> str | None:
    """Validate an optional non-empty string."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{path} cannot be empty")
    return normalized


def _log_auth_event(
    event_type: str,
    principal: AuthPrincipal | None,
    reason_code: str,
    provider_name: str,
    auth_method: str | None = None,
    **extra: Any,
) -> None:
    """Log authentication event for audit purposes."""
    log_args = {
        "event_type": event_type,
        "reason_code": reason_code,
        "provider": provider_name,
    }
    if principal:
        log_args["subject"] = principal.subject
        log_args["principal_type"] = principal.principal_type
        if principal.issuer:
            log_args["issuer"] = principal.issuer
        if principal.email:
            log_args["email"] = principal.email
    if auth_method:
        log_args["auth_method"] = auth_method
    log_args.update(extra)

    event_name = f"authentication_{event_type}"
    log_fn = logger.info if event_type == "success" else logger.warning
    log_fn(event_name, **log_args)


class StaticAuthenticationProvider:
    """Static/local development authentication provider.

    This provider is intended for development and testing only.
    It validates against configured static users or always succeeds
    when explicitly enabled in development mode.
    """

    def __init__(
        self,
        static_users: dict[str, dict[str, Any]] | None = None,
        dev_mode: bool = False,
    ):
        self._static_users = static_users or {}
        self._dev_mode = dev_mode
        self._sessions: dict[str, AuthenticatedSession] = {}

    def authenticate(self, request_context: RequestContext) -> AuthResult:
        """Authenticate using static credentials or dev mode."""
        auth_header = request_context.headers.get("authorization", "")
        cookie_session = request_context.cookies.get("phlo_session")

        if cookie_session and cookie_session in self._sessions:
            session = self._sessions[cookie_session]
            if self._is_session_valid(session):
                _log_auth_event(
                    "success",
                    session.principal,
                    "authenticated",
                    "static",
                    auth_method="session",
                    path=request_context.path,
                )
                return AuthResult(
                    authenticated=True,
                    principal=session.principal,
                    session=session,
                    reason_code="authenticated",
                )
            del self._sessions[cookie_session]

        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            session = self.validate_token(token)
            if session:
                _log_auth_event(
                    "success",
                    session.principal,
                    "authenticated",
                    "static",
                    auth_method="bearer_token",
                    path=request_context.path,
                )
                return AuthResult(
                    authenticated=True,
                    principal=session.principal,
                    session=session,
                    reason_code="authenticated",
                )
            _log_auth_event(
                "failure",
                None,
                "invalid_token",
                "static",
                auth_method="bearer_token",
                path=request_context.path,
            )

        if self._dev_mode:
            dev_principal = AuthPrincipal(
                subject="dev_user",
                principal_type="user",
                email="dev@localhost",
                groups=("admin",),
                attributes={"mode": "development"},
            )
            session = AuthenticatedSession(
                principal=dev_principal,
                auth_method="static",
                provider_name="static",
                session_id=secrets.token_urlsafe(32),
                attributes={"mode": "development"},
            )
            _log_auth_event(
                "success",
                dev_principal,
                "authenticated",
                "static",
                auth_method="static",
                path=request_context.path,
            )
            return AuthResult(
                authenticated=True,
                principal=dev_principal,
                session=session,
                reason_code="authenticated",
            )

        return AuthResult(
            authenticated=False,
            reason_code="missing_credentials",
        )

    def current_principal(self, request_context: RequestContext) -> AuthPrincipal | None:
        """Get current principal from request context."""
        result = self.authenticate(request_context)
        return result.principal

    def validate_token(self, token: str) -> AuthenticatedSession | None:
        """Validate a bearer token."""
        matched_key: str | None = None
        # Compare every configured key in full rather than dict-lookup by
        # token: comparison time must not reveal whether a prefix matched.
        for key in self._static_users:
            if hmac.compare_digest(key, token):
                matched_key = key
        if matched_key is not None:
            user_data = self._static_users[matched_key]
            principal = AuthPrincipal(
                subject=user_data.get("subject", token),
                principal_type=user_data.get("principal_type", "user"),
                email=user_data.get("email"),
                groups=tuple(user_data.get("groups", [])),
                claims=user_data.get("claims", {}),
                attributes=user_data.get("attributes", {}),
            )
            return AuthenticatedSession(
                principal=principal,
                auth_method="bearer_token",
                provider_name="static",
                session_id=secrets.token_urlsafe(32),
            )
        return None

    def start_login(self) -> BrowserLoginStart:
        """Start login flow (not supported in static provider)."""
        raise NotImplementedError("Static provider does not support browser login")

    def finish_login(self, request_context: RequestContext) -> AuthResult:
        """Finish login flow (not supported in static provider)."""
        raise NotImplementedError("Static provider does not support browser login")

    def logout(self, request_context: RequestContext) -> LogoutResult:
        """Log out the current user."""
        cookie_session = request_context.cookies.get("phlo_session")
        if cookie_session and cookie_session in self._sessions:
            del self._sessions[cookie_session]
        return LogoutResult(success=True)

    def _is_session_valid(self, session: AuthenticatedSession) -> bool:
        """Check if session is still valid."""
        if session.expires_at is None:
            return True
        return datetime.now(UTC) < session.expires_at


class ProxyAuthenticationProvider:
    """Reverse-proxy asserted identity authentication provider.

    This provider validates requests from trusted reverse proxies that
    assert user identity through headers. It uses CIDR notation for
    trusted proxy configuration.
    """

    def __init__(
        self,
        trusted_proxies: list[str] | None = None,
        header_subject: str = "X-Remote-User",
        header_email: str = "X-Remote-Email",
        header_groups: str = "X-Remote-Groups",
        shared_secret: str | None = None,
    ):
        self._trusted_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self._trusted_hosts: set[str] = set()
        for proxy in trusted_proxies or ["127.0.0.1/32", "::1/128"]:
            try:
                if "/" in proxy:
                    network = ipaddress.ip_network(proxy, strict=False)
                    self._trusted_networks.append(network)
                else:
                    self._trusted_hosts.add(proxy)
            except ValueError:
                logger.warning("invalid_trusted_proxy_config", proxy=proxy)
        self._header_subject = header_subject.lower()
        self._header_email = header_email.lower()
        self._header_groups = header_groups.lower()
        self._signature_header = "x-phlo-proxy-signature"
        self._timestamp_header = "x-phlo-proxy-timestamp"
        self._shared_secret = shared_secret

    def _identity_headers(self, request_context: RequestContext) -> tuple[str, str | None, str]:
        """Return asserted identity fields using runtime-facing types."""
        subject = request_context.headers.get(self._header_subject, "")
        email = request_context.headers.get(self._header_email)
        groups_raw = request_context.headers.get(self._header_groups, "")
        return subject, email, groups_raw

    def _identity_payload_parts(self, request_context: RequestContext) -> tuple[str, str, str]:
        """Return the asserted identity fields bound into the proxy signature."""
        subject, email, groups_raw = self._identity_headers(request_context)
        groups = ",".join(g.strip() for g in groups_raw.split(",") if g.strip())
        return subject, email or "", groups

    def _is_from_trusted_proxy(self, request_context: RequestContext) -> bool:
        """Check if request came from a trusted proxy using CIDR matching."""
        remote_addr = request_context.remote_addr
        if remote_addr is None:
            return False
        if remote_addr in self._trusted_hosts:
            return True
        try:
            addr = ipaddress.ip_address(remote_addr)
            for network in self._trusted_networks:
                if addr in network:
                    return True
        except ValueError:
            pass
        return False

    def _verify_proxy_signature(self, request_context: RequestContext) -> bool:
        """Verify that the request was signed by a trusted proxy with the shared secret."""
        if self._shared_secret is None:
            logger.warning(
                "proxy_signature_verification_disabled",
                hint="Set shared_secret on ProxyAuthenticationProvider to enable HMAC verification",
            )
            return True
        signature = request_context.headers.get(self._signature_header)
        timestamp_str = request_context.headers.get(self._timestamp_header)
        if not signature or not timestamp_str:
            logger.debug("missing_proxy_signature", remote_addr=request_context.remote_addr)
            return False
        try:
            timestamp = int(timestamp_str)
        except ValueError:
            logger.debug("invalid_proxy_timestamp", timestamp=timestamp_str)
            return False
        now = datetime.now(UTC).timestamp()
        if abs(now - timestamp) > 300:
            logger.debug("expired_proxy_timestamp", timestamp=timestamp_str)
            return False
        subject, email, groups = self._identity_payload_parts(request_context)
        remote_addr = request_context.remote_addr or ""
        path = request_context.path or ""
        payload_parts: tuple[str, str, str, str, str, str] = (
            str(timestamp),
            remote_addr,
            path,
            subject,
            email,
            groups,
        )
        payload = ":".join(payload_parts)
        expected = hmac.new(self._shared_secret.encode(), payload.encode(), "sha256").hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.debug("invalid_proxy_signature", remote_addr=request_context.remote_addr)
            return False
        return True

    def authenticate(self, request_context: RequestContext) -> AuthResult:
        """Authenticate using proxy-asserted identity."""
        if not self._is_from_trusted_proxy(request_context):
            _log_auth_event(
                "failure",
                None,
                "invalid_identity_payload",
                "proxy",
                auth_method="proxy",
                path=request_context.path,
                remote_addr=request_context.remote_addr,
                reason="untrusted_proxy",
            )
            return AuthResult(
                authenticated=False,
                reason_code="invalid_identity_payload",
            )

        if not self._verify_proxy_signature(request_context):
            _log_auth_event(
                "failure",
                None,
                "invalid_identity_payload",
                "proxy",
                auth_method="proxy",
                path=request_context.path,
                remote_addr=request_context.remote_addr,
                reason="invalid_signature",
            )
            return AuthResult(
                authenticated=False,
                reason_code="invalid_identity_payload",
            )

        subject, email, groups_raw = self._identity_headers(request_context)
        if not subject:
            _log_auth_event(
                "failure",
                None,
                "missing_credentials",
                "proxy",
                auth_method="proxy",
                path=request_context.path,
                remote_addr=request_context.remote_addr,
            )
            return AuthResult(
                authenticated=False,
                reason_code="missing_credentials",
            )

        groups = tuple(g.strip() for g in groups_raw.split(",") if g.strip())

        principal = AuthPrincipal(
            subject=subject,
            principal_type="user",
            email=email,
            groups=groups,
            attributes={"source": "proxy"},
        )

        session = AuthenticatedSession(
            principal=principal,
            auth_method="proxy",
            provider_name="proxy",
            attributes={"remote_addr": request_context.remote_addr or "unknown"},
        )

        _log_auth_event(
            "success",
            principal,
            "authenticated",
            "proxy",
            auth_method="proxy",
            path=request_context.path,
            remote_addr=request_context.remote_addr,
        )

        return AuthResult(
            authenticated=True,
            principal=principal,
            session=session,
            reason_code="authenticated",
        )

    def current_principal(self, request_context: RequestContext) -> AuthPrincipal | None:
        """Get current principal from proxy headers."""
        result = self.authenticate(request_context)
        return result.principal

    def validate_token(self, token: str) -> AuthenticatedSession | None:
        """Validate a bearer token (not supported in proxy provider)."""
        return None

    def authenticate_proxy_identity(self, request_context: RequestContext) -> AuthResult:
        """Authenticate proxy-asserted identity (explicit flow)."""
        return self.authenticate(request_context)


class ServiceTokenAuthenticationProvider:
    """Service principal/token authentication provider.

    This provider validates service accounts used for automation
    and service-to-service authentication.
    """

    def __init__(
        self,
        service_tokens: dict[str, dict[str, Any]] | None = None,
    ):
        self._service_tokens = service_tokens or {}
        for token, service_data in self._service_tokens.items():
            if not isinstance(token, str) or not token.strip():
                raise ValueError("Each service token configuration requires a non-empty token")
            if not isinstance(service_data, dict):
                raise ValueError(f"Service token {token!r} must map to an object")
            subject = service_data.get("subject")
            if not isinstance(subject, str) or not subject.strip():
                raise ValueError(
                    "Each service token configuration requires a non-empty explicit subject"
                )

    def authenticate(self, request_context: RequestContext) -> AuthResult:
        """Authenticate using service token."""
        auth_header = request_context.headers.get("authorization", "")

        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            session = self.validate_token(token)
            if session:
                return AuthResult(
                    authenticated=True,
                    principal=session.principal,
                    session=session,
                    reason_code="authenticated",
                )

        return AuthResult(
            authenticated=False,
            reason_code="missing_credentials",
        )

    def current_principal(self, request_context: RequestContext) -> AuthPrincipal | None:
        """Get current principal from request context."""
        result = self.authenticate(request_context)
        return result.principal

    def validate_token(self, token: str) -> AuthenticatedSession | None:
        """Validate a service token."""
        # Same constant-time scan as the static provider: never leak match
        # progress through comparison timing.
        matched_key: str | None = None
        for key in self._service_tokens:
            if hmac.compare_digest(key, token):
                matched_key = key
        if matched_key is not None:
            service_data = self._service_tokens[matched_key]
            principal = AuthPrincipal(
                subject=service_data["subject"],
                principal_type="service",
                issuer=service_data.get("issuer"),
                email=service_data.get("email"),
                groups=tuple(service_data.get("groups", [])),
                claims=service_data.get("claims", {}),
                attributes=service_data.get("attributes", {}),
            )
            return AuthenticatedSession(
                principal=principal,
                auth_method="bearer_token",
                provider_name="service_token",
                session_id=secrets.token_urlsafe(32),
                attributes={"service": "true"},
            )
        return None


class JWTAuthenticationProvider:
    """JWT Bearer token authentication provider.

    This provider validates JWT tokens signed with HS256 algorithm
    using a shared secret. It extracts standard OIDC-compatible
    claims and maps them to AuthPrincipal for regulated deployments.

    Configuration via phlo.yaml:
        authentication:
          jwt:
            secret: "your-256-bit-secret"
            issuer: "https://issuer.example.com"  # optional
            audience: "phlo"  # optional
    """

    def __init__(
        self,
        secret: str,
        issuer: str | None = None,
        audience: str | None = None,
        leeway_seconds: int = 60,
    ):
        if not secret:
            raise ValueError("JWT secret is required")
        self._secret = secret.encode("utf-8")
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds

    def authenticate(self, request_context: RequestContext) -> AuthResult:
        """Authenticate using JWT bearer token."""
        auth_header = request_context.headers.get("authorization", "")

        if not auth_header.startswith("Bearer "):
            return AuthResult(
                authenticated=False,
                reason_code="missing_bearer_token",
            )

        token = auth_header[7:]
        session = self.validate_token(token)
        if session:
            _log_auth_event(
                "success",
                session.principal,
                "authenticated",
                "jwt",
                auth_method="bearer_token",
                path=request_context.path,
            )
            return AuthResult(
                authenticated=True,
                principal=session.principal,
                session=session,
                reason_code="authenticated",
            )

        _log_auth_event(
            "failure",
            None,
            "invalid_token",
            "jwt",
            auth_method="bearer_token",
            path=request_context.path,
        )
        return AuthResult(
            authenticated=False,
            reason_code="invalid_token",
        )

    def current_principal(self, request_context: RequestContext) -> AuthPrincipal | None:
        """Get current principal from request context."""
        result = self.authenticate(request_context)
        return result.principal

    def validate_token(self, token: str) -> AuthenticatedSession | None:
        """Validate a JWT token and return session if valid."""
        try:
            header, payload, signature = token.split(".")
            if not all([header, payload, signature]):
                return None

            if not self._verify_signature(header, payload, signature):
                logger.warning("jwt_signature_invalid")
                return None

            claims = self._decode_payload(payload)

            if not self._validate_claims(claims):
                return None

            principal = AuthPrincipal(
                subject=claims.get("sub", ""),
                principal_type="user",
                issuer=claims.get("iss"),
                email=claims.get("email"),
                groups=tuple(claims.get("groups", [])),
                claims=claims,
                attributes={
                    "name": claims.get("name", ""),
                    "preferred_username": claims.get("preferred_username", ""),
                },
            )

            session_id = claims.get("jti") or secrets.token_urlsafe(32)

            return AuthenticatedSession(
                principal=principal,
                auth_method="bearer_token",
                provider_name="jwt",
                session_id=session_id,
                issued_at=datetime.fromtimestamp(claims.get("iat", 0), tz=UTC),
                expires_at=datetime.fromtimestamp(claims.get("exp", 0), tz=UTC),
                attributes={
                    "jwt_issuer": claims.get("iss", ""),
                    "jwt_audience": str(claims.get("aud", "")),
                },
            )
        except (ValueError, KeyError) as e:
            logger.warning("jwt_parse_error", error=str(e))
            return None

    def _verify_signature(self, header_b64: str, payload_b64: str, signature_b64: str) -> bool:
        """Verify JWT signature using HS256."""
        try:
            header = self._decode_payload(header_b64)
            if header.get("alg") != "HS256":
                logger.warning("jwt_algorithm_rejected", algorithm=header.get("alg"))
                return False
            message = f"{header_b64}.{payload_b64}".encode()
            expected = hmac.new(self._secret, message, hashlib.sha256).digest()
            padded_signature = signature_b64 + "=" * (-len(signature_b64) % 4)
            actual = base64url_decode(padded_signature)
            return hmac.compare_digest(expected, actual)
        except Exception:
            return False

    def _decode_payload(self, payload_b64: str) -> dict[str, Any]:
        """Decode base64url-encoded JWT payload."""
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        decoded = base64url_decode(padded)
        return json.loads(decoded.decode("utf-8"))

    def _validate_claims(self, claims: dict[str, Any]) -> bool:
        """Validate JWT claims including expiration."""
        now = time.time()

        if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
            logger.warning("jwt_subject_missing")
            return False

        from phlo.security.mode import is_regulated

        if is_regulated() and (not self._issuer or not self._audience):
            logger.warning("jwt_regulated_issuer_audience_missing")
            return False

        exp = claims.get("exp", 0)
        if exp < now - self._leeway:
            logger.warning("jwt_token_expired", exp=exp, now=now)
            return False

        iat = claims.get("iat", 0)
        if iat > now + self._leeway:
            logger.warning("jwt_token_future", iat=iat, now=now)
            return False

        if self._issuer and claims.get("iss") != self._issuer:
            logger.warning("jwt_issuer_mismatch", expected=self._issuer, actual=claims.get("iss"))
            return False

        if self._audience:
            aud = claims.get("aud")
            if aud is None:
                logger.warning("jwt_audience_missing")
                return False
            aud_list = aud if isinstance(aud, list) else [aud]
            if self._audience not in aud_list:
                logger.warning("jwt_audience_mismatch", expected=self._audience, actual=aud)
                return False

        return True


def base64url_decode(data: str | bytes) -> bytes:
    """Decode base64url-encoded string."""
    if isinstance(data, str):
        data = data.encode("ascii")
    return base64.urlsafe_b64decode(data)


def _load_static_config() -> tuple[dict[str, dict[str, Any]], bool]:
    """Load static authentication configuration from env first, then phlo.yaml."""
    static_config = _authentication_subconfig("static")
    static_users = _string_dict(
        static_config.get("users"), path="phlo.yaml authentication.static.users"
    )

    dev_mode_env = os.environ.get("PHLO_AUTH_DEV_MODE", "").lower()
    if dev_mode_env:
        dev_mode = dev_mode_env in ("1", "true", "yes")
    else:
        dev_mode = (
            _optional_bool(
                static_config.get("dev_mode"),
                path="phlo.yaml authentication.static.dev_mode",
            )
            or False
        )

    from phlo.security.mode import is_regulated

    regulated = is_regulated()
    if dev_mode:
        environment = os.environ.get("PHLO_ENVIRONMENT", "dev").lower()
        if regulated or environment in ("production", "prod"):
            logger.error(
                "auth_dev_mode_blocked",
                reason=(
                    "PHLO_AUTH_DEV_MODE is disabled in regulated mode"
                    if regulated
                    else "PHLO_AUTH_DEV_MODE is enabled but PHLO_ENVIRONMENT is production"
                ),
            )
            dev_mode = False
        else:
            logger.warning(
                "auth_dev_mode_active",
                reason="All requests authenticate as dev_user with admin privileges",
            )

    users_json = os.environ.get("PHLO_AUTH_STATIC_USERS")
    if users_json:
        with suppress(json.JSONDecodeError):
            static_users = json.loads(users_json)

    return static_users, dev_mode


def _load_proxy_config() -> dict[str, Any]:
    """Load proxy authentication configuration from env first, then phlo.yaml."""
    config = {}
    proxy_config = _authentication_subconfig("proxy")

    trusted = os.environ.get("PHLO_AUTH_PROXY_TRUSTED_PROXIES")
    if trusted:
        config["trusted_proxies"] = [p.strip() for p in trusted.split(",")]
    else:
        configured = _string_list(
            proxy_config.get("trusted_proxies"),
            path="phlo.yaml authentication.proxy.trusted_proxies",
        )
        if configured:
            config["trusted_proxies"] = configured

    header_subject = os.environ.get("PHLO_AUTH_PROXY_HEADER_SUBJECT")
    if header_subject:
        config["header_subject"] = header_subject
    else:
        configured = _optional_string(
            proxy_config.get("header_subject"),
            path="phlo.yaml authentication.proxy.header_subject",
        )
        if configured:
            config["header_subject"] = configured

    header_email = os.environ.get("PHLO_AUTH_PROXY_HEADER_EMAIL")
    if header_email:
        config["header_email"] = header_email
    else:
        configured = _optional_string(
            proxy_config.get("header_email"),
            path="phlo.yaml authentication.proxy.header_email",
        )
        if configured:
            config["header_email"] = configured

    header_groups = os.environ.get("PHLO_AUTH_PROXY_HEADER_GROUPS")
    if header_groups:
        config["header_groups"] = header_groups
    else:
        configured = _optional_string(
            proxy_config.get("header_groups"),
            path="phlo.yaml authentication.proxy.header_groups",
        )
        if configured:
            config["header_groups"] = configured

    shared_secret = os.environ.get("PHLO_AUTH_PROXY_SHARED_SECRET")
    if shared_secret:
        config["shared_secret"] = shared_secret
    else:
        configured = _optional_string(
            proxy_config.get("shared_secret"),
            path="phlo.yaml authentication.proxy.shared_secret",
        )
        if configured:
            config["shared_secret"] = configured

    return config


def _load_service_token_config() -> dict[str, dict[str, Any]]:
    """Load service-token configuration from env first, then phlo.yaml."""
    service_config = _authentication_subconfig("service_token")
    service_tokens = _string_dict(
        service_config.get("tokens"),
        path="phlo.yaml authentication.service_token.tokens",
    )

    tokens_json = os.environ.get("PHLO_AUTH_SERVICE_TOKENS")
    if tokens_json:
        try:
            service_tokens = json.loads(tokens_json)
        except json.JSONDecodeError as exc:
            raise ValueError("PHLO_AUTH_SERVICE_TOKENS must be valid JSON") from exc

    if not isinstance(service_tokens, dict):
        raise ValueError("PHLO_AUTH_SERVICE_TOKENS must be a JSON object")

    for token, service_data in service_tokens.items():
        if not isinstance(service_data, dict):
            raise ValueError(f"Service token {token!r} must map to an object")
        subject = service_data.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError(f"Service token {token!r} requires a non-empty explicit subject")

    return service_tokens


def _load_jwt_config() -> dict[str, Any]:
    """Load JWT authentication configuration from env first, then phlo.yaml."""
    jwt_config = _authentication_subconfig("jwt")

    secret = os.environ.get("PHLO_AUTH_JWT_SECRET")
    if not secret:
        secret = jwt_config.get("secret", "")

    issuer = os.environ.get("PHLO_AUTH_JWT_ISSUER")
    if issuer is None:
        issuer = jwt_config.get("issuer")

    audience = os.environ.get("PHLO_AUTH_JWT_AUDIENCE")
    if audience is None:
        audience = jwt_config.get("audience")

    env_leeway = os.environ.get("PHLO_AUTH_JWT_LEEWAY")
    if env_leeway is not None:
        leeway = int(env_leeway)
    else:
        leeway_config = jwt_config.get("leeway_seconds")
        leeway = int(leeway_config) if leeway_config is not None else 60

    return {
        "secret": secret,
        "issuer": issuer,
        "audience": audience,
        "leeway_seconds": leeway,
    }


def _provider_enabled(
    provider_name: str,
    *,
    env_enabled: str | None,
    config_block: dict[str, Any],
    selected_provider: str | None,
    configured_payload: Any,
) -> bool:
    """Return whether a built-in provider is explicitly enabled."""
    if env_enabled:
        return True

    enabled = _optional_bool(
        config_block.get("enabled"),
        path=f"phlo.yaml authentication.{provider_name}.enabled",
    )
    if enabled is not None:
        return enabled

    if selected_provider == provider_name:
        return True

    return bool(configured_payload)


def register_default_capability_providers() -> None:
    """Register authentication providers only when explicitly enabled via config.

    Authentication providers are security-sensitive and must be explicitly
    enabled via environment variables, not auto-registered on startup.
    """
    from phlo.infrastructure.config import get_configured_authentication_provider_name

    selected_provider = get_configured_authentication_provider_name()

    static_block = _authentication_subconfig("static")
    static_users, dev_mode = _load_static_config()
    if _provider_enabled(
        "static",
        env_enabled=os.environ.get("PHLO_AUTH_STATIC_ENABLED"),
        config_block=static_block,
        selected_provider=selected_provider,
        configured_payload=static_users or dev_mode,
    ):
        register_capability(
            "authentication_provider",
            AuthenticationProviderSpec(
                name="static",
                provider=StaticAuthenticationProvider(
                    static_users=static_users,
                    dev_mode=dev_mode,
                ),
                metadata={
                    "auth_method": "static",
                    "supports_browser_login": False,
                    "supports_proxy_auth": False,
                    "supports_service_tokens": True,
                    "dev_mode": dev_mode,
                },
                support=CapabilitySupport(
                    supports_permissions=False,
                    supports_attributes=True,
                ),
            ),
        )

    proxy_block = _authentication_subconfig("proxy")
    proxy_config = _load_proxy_config()
    if _provider_enabled(
        "proxy",
        env_enabled=os.environ.get("PHLO_AUTH_PROXY_ENABLED"),
        config_block=proxy_block,
        selected_provider=selected_provider,
        configured_payload=proxy_config,
    ):
        register_capability(
            "authentication_provider",
            AuthenticationProviderSpec(
                name="proxy",
                provider=ProxyAuthenticationProvider(**proxy_config),
                metadata={
                    "auth_method": "proxy",
                    "supports_browser_login": False,
                    "supports_proxy_auth": True,
                    "supports_service_tokens": False,
                },
                support=CapabilitySupport(
                    supports_permissions=False,
                    supports_attributes=True,
                ),
            ),
        )

    service_token_block = _authentication_subconfig("service_token")
    service_tokens = _load_service_token_config()
    if _provider_enabled(
        "service_token",
        env_enabled=os.environ.get("PHLO_AUTH_SERVICE_ENABLED"),
        config_block=service_token_block,
        selected_provider=selected_provider,
        configured_payload=service_tokens,
    ):
        register_capability(
            "authentication_provider",
            AuthenticationProviderSpec(
                name="service_token",
                provider=ServiceTokenAuthenticationProvider(
                    service_tokens=service_tokens,
                ),
                metadata={
                    "auth_method": "bearer_token",
                    "supports_browser_login": False,
                    "supports_proxy_auth": False,
                    "supports_service_tokens": True,
                },
                support=CapabilitySupport(
                    supports_permissions=False,
                    supports_attributes=True,
                ),
            ),
        )

    jwt_block = _authentication_subconfig("jwt")
    jwt_config = _load_jwt_config()
    if _provider_enabled(
        "jwt",
        env_enabled=os.environ.get("PHLO_AUTH_JWT_ENABLED"),
        config_block=jwt_block,
        selected_provider=selected_provider,
        configured_payload=jwt_config.get("secret"),
    ):
        if not jwt_config.get("secret"):
            logger.error("jwt_provider_requires_secret")
        else:
            register_capability(
                "authentication_provider",
                AuthenticationProviderSpec(
                    name="jwt",
                    provider=JWTAuthenticationProvider(
                        secret=jwt_config["secret"],
                        issuer=jwt_config.get("issuer"),
                        audience=jwt_config.get("audience"),
                        leeway_seconds=jwt_config.get("leeway_seconds", 60),
                    ),
                    metadata={
                        "auth_method": "bearer_token",
                        "supports_browser_login": False,
                        "supports_proxy_auth": False,
                        "supports_service_tokens": False,
                        "algorithm": "HS256",
                    },
                    support=CapabilitySupport(
                        supports_permissions=False,
                        supports_attributes=True,
                    ),
                ),
            )
