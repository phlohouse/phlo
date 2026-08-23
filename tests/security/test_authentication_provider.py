"""Tests for authentication provider capability.

Covers static/dev-mode, JWT, proxy-header, and service-token providers; the
capability registry and config cache reset around every test.
"""

from __future__ import annotations

import hmac
import os
import time
from unittest.mock import patch

import pytest

from phlo.capabilities import (
    RequestContext,
    clear_all_capabilities,
)
from phlo.capabilities.authentication import (
    JWTAuthenticationProvider,
    ProxyAuthenticationProvider,
    ServiceTokenAuthenticationProvider,
    StaticAuthenticationProvider,
    register_default_capability_providers,
)
from phlo.capabilities.registry import get_capability_registry
from phlo.infrastructure.config import clear_config_cache


@pytest.fixture(autouse=True)
def clear_auth_providers():
    """Clear auth providers before and after each test."""
    clear_all_capabilities()
    clear_config_cache()
    yield
    clear_all_capabilities()
    clear_config_cache()


class TestStaticAuthenticationProvider:
    """Tests for StaticAuthenticationProvider."""

    def test_authenticate_with_dev_mode(self):
        """Test authentication succeeds in dev mode."""
        provider = StaticAuthenticationProvider(dev_mode=True)
        request_context = RequestContext(
            headers={},
            cookies={},
            query_params={},
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is True
        assert result.principal is not None
        assert result.principal.subject == "dev_user"
        assert result.principal.principal_type == "user"

    def test_authenticate_without_credentials_fails(self):
        """Test authentication fails without credentials when not in dev mode."""
        provider = StaticAuthenticationProvider(dev_mode=False)
        request_context = RequestContext(
            headers={},
            cookies={},
            query_params={},
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "missing_credentials"

    def test_authenticate_with_valid_token(self):
        """Test authentication succeeds with valid static token."""
        provider = StaticAuthenticationProvider(
            static_users={"test-token": {"subject": "test-user", "email": "test@example.com"}}
        )
        request_context = RequestContext(
            headers={"authorization": "Bearer test-token"},
            cookies={},
            query_params={},
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is True
        assert result.principal is not None
        assert result.principal.subject == "test-user"
        assert result.principal.email == "test@example.com"

    def test_authenticate_with_invalid_token_fails(self):
        """Test authentication fails with invalid token."""
        provider = StaticAuthenticationProvider(static_users={})
        request_context = RequestContext(
            headers={"authorization": "Bearer invalid-token"},
            cookies={},
            query_params={},
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "missing_credentials"


class TestServiceTokenAuthenticationProvider:
    """Tests for ServiceTokenAuthenticationProvider."""

    def test_service_token_requires_explicit_subject(self):
        with pytest.raises(ValueError, match="explicit subject"):
            ServiceTokenAuthenticationProvider(service_tokens={"secret": {}})

    def test_authenticate_with_valid_service_token(self):
        """Test authentication succeeds with valid service token."""
        provider = ServiceTokenAuthenticationProvider(
            service_tokens={
                "service-token-123": {
                    "subject": "my-service",
                    "principal_type": "service",
                }
            }
        )
        request_context = RequestContext(
            headers={"authorization": "Bearer service-token-123"},
            cookies={},
            query_params={},
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is True
        assert result.principal is not None
        assert result.principal.subject == "my-service"
        assert result.principal.principal_type == "service"

    def test_authenticate_with_invalid_service_token_fails(self):
        """Test authentication fails with invalid service token."""
        provider = ServiceTokenAuthenticationProvider(service_tokens={})
        request_context = RequestContext(
            headers={"authorization": "Bearer invalid"},
            cookies={},
            query_params={},
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "missing_credentials"

    def test_authenticate_without_token_fails(self):
        """Test authentication fails without any token."""
        provider = ServiceTokenAuthenticationProvider(service_tokens={})
        request_context = RequestContext(
            headers={},
            cookies={},
            query_params={},
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "missing_credentials"


class TestProxyAuthenticationProvider:
    """Tests for ProxyAuthenticationProvider."""

    @staticmethod
    def _proxy_signature(
        secret: str,
        timestamp: str,
        remote_addr: str,
        path: str,
        subject: str = "",
        email: str = "",
        groups: str = "",
    ) -> str:
        payload = ":".join([timestamp, remote_addr, path, subject, email, groups])
        return hmac.new(secret.encode(), payload.encode(), "sha256").hexdigest()

    def test_authenticate_from_trusted_proxy(self):
        """Test authentication succeeds from trusted proxy IP."""
        provider = ProxyAuthenticationProvider(trusted_proxies=["127.0.0.1/32", "192.168.1.0/24"])
        request_context = RequestContext(
            headers={
                "x-remote-user": "proxy-user",
                "x-remote-email": "proxy@example.com",
            },
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is True
        assert result.principal is not None
        assert result.principal.subject == "proxy-user"
        assert result.principal.email == "proxy@example.com"

    def test_authenticate_from_untrusted_ip_fails(self):
        """Test authentication fails from untrusted IP."""
        provider = ProxyAuthenticationProvider(trusted_proxies=["10.0.0.1/32"])
        request_context = RequestContext(
            headers={"x-remote-user": "proxy-user"},
            cookies={},
            query_params={},
            remote_addr="192.168.1.100",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "invalid_identity_payload"

    def test_authenticate_without_remote_addr_fails(self):
        """Test authentication fails when remote address is unknown."""
        provider = ProxyAuthenticationProvider(trusted_proxies=["127.0.0.1/32"])
        request_context = RequestContext(
            headers={"x-remote-user": "proxy-user"},
            cookies={},
            query_params={},
            remote_addr=None,
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "invalid_identity_payload"

    def test_authenticate_without_header_fails(self):
        """Test authentication fails when proxy header is missing."""
        provider = ProxyAuthenticationProvider(trusted_proxies=["127.0.0.1/32"])
        request_context = RequestContext(
            headers={},
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "missing_credentials"

    def test_cidr_network_matching(self):
        """Test CIDR network matching works correctly."""
        provider = ProxyAuthenticationProvider(trusted_proxies=["192.168.0.0/16"])

        assert provider._is_from_trusted_proxy(
            RequestContext(headers={}, cookies={}, query_params={}, remote_addr="192.168.1.100")
        )
        assert provider._is_from_trusted_proxy(
            RequestContext(headers={}, cookies={}, query_params={}, remote_addr="192.168.255.255")
        )
        assert not provider._is_from_trusted_proxy(
            RequestContext(headers={}, cookies={}, query_params={}, remote_addr="10.0.0.1")
        )
        assert not provider._is_from_trusted_proxy(
            RequestContext(headers={}, cookies={}, query_params={}, remote_addr="172.16.0.1")
        )

    def test_authenticate_with_shared_secret_succeeds(self):
        """Test authentication succeeds with valid signature (issue #338)."""
        secret = "test-secret-123"
        provider = ProxyAuthenticationProvider(
            trusted_proxies=["127.0.0.1/32"], shared_secret=secret
        )

        timestamp = str(int(time.time()))
        signature = self._proxy_signature(
            secret,
            timestamp,
            "127.0.0.1",
            "/test/path",
            subject="proxy-user",
            email="proxy@example.com",
        )

        request_context = RequestContext(
            headers={
                "x-remote-user": "proxy-user",
                "x-remote-email": "proxy@example.com",
                "x-phlo-proxy-signature": signature,
                "x-phlo-proxy-timestamp": timestamp,
            },
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test/path",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is True
        assert result.principal is not None
        assert result.principal.subject == "proxy-user"

    def test_authenticate_without_signature_fails_when_secret_configured(self):
        """Test authentication fails without signature when shared_secret is set (issue #338)."""
        provider = ProxyAuthenticationProvider(
            trusted_proxies=["127.0.0.1/32"], shared_secret="test-secret"
        )

        request_context = RequestContext(
            headers={
                "x-remote-user": "proxy-user",
            },
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test/path",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "invalid_identity_payload"

    def test_authenticate_with_invalid_signature_fails(self):
        """Test authentication fails with invalid signature (issue #338)."""
        provider = ProxyAuthenticationProvider(
            trusted_proxies=["127.0.0.1/32"], shared_secret="test-secret"
        )

        timestamp = str(int(time.time()))

        request_context = RequestContext(
            headers={
                "x-remote-user": "proxy-user",
                "x-phlo-proxy-signature": "invalid-signature",
                "x-phlo-proxy-timestamp": timestamp,
            },
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test/path",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "invalid_identity_payload"

    def test_authenticate_with_expired_timestamp_fails(self):
        """Test authentication fails with expired timestamp (issue #338)."""
        secret = "test-secret-123"
        provider = ProxyAuthenticationProvider(
            trusted_proxies=["127.0.0.1/32"], shared_secret=secret
        )

        timestamp = str(int(time.time()) - 600)
        signature = self._proxy_signature(secret, timestamp, "127.0.0.1", "/test/path")

        request_context = RequestContext(
            headers={
                "x-remote-user": "proxy-user",
                "x-phlo-proxy-signature": signature,
                "x-phlo-proxy-timestamp": timestamp,
            },
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test/path",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "invalid_identity_payload"

    def test_authenticate_without_secret_allows_unsigned_requests(self):
        """Test authentication allows unsigned requests when no shared_secret configured."""
        provider = ProxyAuthenticationProvider(trusted_proxies=["127.0.0.1/32"])

        request_context = RequestContext(
            headers={
                "x-remote-user": "proxy-user",
                "x-remote-email": "proxy@example.com",
            },
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test/path",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is True
        assert result.principal is not None
        assert result.principal.email == "proxy@example.com"

    def test_authenticate_without_email_header_keeps_email_none(self):
        """Test absent proxy email header stays None on the principal."""
        provider = ProxyAuthenticationProvider(trusted_proxies=["127.0.0.1/32"])

        request_context = RequestContext(
            headers={
                "x-remote-user": "proxy-user",
            },
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test/path",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is True
        assert result.principal is not None
        assert result.principal.email is None

    def test_authenticate_rejects_signed_request_when_identity_headers_change(self):
        """Test signature binds asserted identity fields to the authenticated principal."""
        secret = "test-secret-123"
        provider = ProxyAuthenticationProvider(
            trusted_proxies=["127.0.0.1/32"], shared_secret=secret
        )

        timestamp = str(int(time.time()))
        signature = self._proxy_signature(
            secret,
            timestamp,
            "127.0.0.1",
            "/test/path",
            subject="proxy-user",
            email="proxy@example.com",
            groups="admins,operators",
        )

        request_context = RequestContext(
            headers={
                "x-remote-user": "other-user",
                "x-remote-email": "proxy@example.com",
                "x-remote-groups": "admins,operators",
                "x-phlo-proxy-signature": signature,
                "x-phlo-proxy-timestamp": timestamp,
            },
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test/path",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "invalid_identity_payload"


class TestJWTAuthenticationProvider:
    """Tests for JWTAuthenticationProvider."""

    def _create_jwt(
        self,
        payload: dict,
        secret: str = "test-secret-key-256-bits-long!!",
        algorithm: str = "HS256",
    ) -> str:
        """Create a test JWT token with proper encoding."""
        import base64
        import hashlib
        import json

        def _b64url_encode(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        header = {"alg": algorithm, "typ": "JWT"}
        header_json = json.dumps(header, separators=(",", ":")).encode()
        payload_json = json.dumps(payload, separators=(",", ":")).encode()

        header_encoded = _b64url_encode(header_json)
        payload_encoded = _b64url_encode(payload_json)
        message = f"{header_encoded}.{payload_encoded}".encode()

        signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
        signature_encoded = _b64url_encode(signature)

        return f"{header_encoded}.{payload_encoded}.{signature_encoded}"

    def test_authenticate_rejects_algorithm_confusion(self):
        provider = JWTAuthenticationProvider(secret="test-secret-key-256-bits-long!!")
        now = int(time.time())
        token = self._create_jwt(
            {"sub": "user123", "iat": now - 60, "exp": now + 3600}, algorithm="RS256"
        )

        assert provider.validate_token(token) is None

    def test_validate_claims_requires_subject(self):
        provider = JWTAuthenticationProvider(secret="test-secret")
        now = int(time.time())

        assert provider._validate_claims({"iat": now - 60, "exp": now + 3600}) is False

    def test_authenticate_without_bearer_token(self):
        """Test authentication fails without bearer token."""
        provider = JWTAuthenticationProvider(secret="test-secret")

        request_context = RequestContext(
            headers={},
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "missing_bearer_token"

    def test_authenticate_with_valid_token(self):
        """Test authentication succeeds with a valid signed JWT."""
        provider = JWTAuthenticationProvider(secret="test-secret-key-256-bits-long!!")
        now = int(time.time())
        token = self._create_jwt(
            {
                "sub": "user123",
                "email": "user@example.com",
                "groups": ["developers"],
                "iat": now - 60,
                "exp": now + 3600,
            }
        )

        request_context = RequestContext(
            headers={"authorization": f"Bearer {token}"},
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is True
        assert result.principal is not None
        assert result.principal.subject == "user123"
        assert result.principal.email == "user@example.com"
        assert result.principal.groups == ("developers",)

    def test_decode_payload_accepts_already_padded_base64url(self):
        """Payload decoding should not append redundant padding."""
        provider = JWTAuthenticationProvider(secret="test-secret-key-256-bits-long!!")
        claims = provider._decode_payload("eyJzdWIiOiJ1c2VyMTIzIn0=")

        assert claims == {"sub": "user123"}

    def test_authenticate_with_expired_token(self):
        """Test authentication fails with expired JWT token."""
        provider = JWTAuthenticationProvider(
            secret="test-secret-key-256-bits-long!!", leeway_seconds=60
        )
        now = int(time.time())
        token = self._create_jwt(
            {
                "sub": "user123",
                "iat": now - 7200,
                "exp": now - 3600,
            }
        )

        request_context = RequestContext(
            headers={"authorization": f"Bearer {token}"},
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False
        assert result.reason_code == "invalid_token"

    def test_authenticate_with_invalid_signature(self):
        """Test authentication fails with invalid signature."""
        provider = JWTAuthenticationProvider(secret="different-secret")
        now = int(time.time())
        token = self._create_jwt(
            {
                "sub": "user123",
                "iat": now - 60,
                "exp": now + 3600,
            },
            secret="wrong-secret",
        )

        request_context = RequestContext(
            headers={"authorization": f"Bearer {token}"},
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False

    def test_authenticate_with_mismatched_issuer(self):
        """Test authentication fails with mismatched issuer."""
        provider = JWTAuthenticationProvider(
            secret="test-secret-key-256-bits-long!!",
            issuer="https://expected-issuer.example.com",
        )
        now = int(time.time())
        token = self._create_jwt(
            {
                "sub": "user123",
                "iss": "https://wrong-issuer.example.com",
                "iat": now - 60,
                "exp": now + 3600,
            }
        )

        request_context = RequestContext(
            headers={"authorization": f"Bearer {token}"},
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False

    def test_authenticate_with_mismatched_audience(self):
        """Test authentication fails with mismatched audience."""
        provider = JWTAuthenticationProvider(
            secret="test-secret-key-256-bits-long!!",
            audience="expected-app",
        )
        now = int(time.time())
        token = self._create_jwt(
            {
                "sub": "user123",
                "aud": "wrong-app",
                "iat": now - 60,
                "exp": now + 3600,
            }
        )

        request_context = RequestContext(
            headers={"authorization": f"Bearer {token}"},
            cookies={},
            query_params={},
            remote_addr="127.0.0.1",
            path="/test",
        )
        result = provider.authenticate(request_context)

        assert result.authenticated is False

    def test_validate_token_with_future_iat(self):
        """Test validate_token fails with future iat (clock skew)."""
        provider = JWTAuthenticationProvider(
            secret="test-secret-key-256-bits-long!!", leeway_seconds=60
        )
        now = int(time.time())
        token = self._create_jwt(
            {
                "sub": "user123",
                "iat": now + 300,
                "exp": now + 3600,
            }
        )

        session = provider.validate_token(token)

        assert session is None

    def test_require_secret(self):
        """Test JWTAuthenticationProvider requires a secret."""
        with pytest.raises(ValueError, match="secret is required"):
            JWTAuthenticationProvider(secret="")

    def test_validate_claims_with_valid_claims(self):
        """Test _validate_claims passes with valid claims."""
        provider = JWTAuthenticationProvider(
            secret="test-secret",
            issuer="https://valid.example.com",
            audience="phlo",
        )
        now = int(time.time())
        claims = {
            "sub": "user123",
            "iss": "https://valid.example.com",
            "aud": "phlo",
            "iat": now - 60,
            "exp": now + 3600,
        }

        assert provider._validate_claims(claims) is True

    def test_validate_claims_with_expired_exp(self):
        """Test _validate_claims fails with expired exp."""
        provider = JWTAuthenticationProvider(secret="test-secret", leeway_seconds=60)
        now = int(time.time())
        claims = {
            "sub": "user123",
            "iat": now - 7200,
            "exp": now - 3600,
        }

        assert provider._validate_claims(claims) is False

    def test_validate_claims_with_future_iat(self):
        """Test _validate_claims fails with future iat."""
        provider = JWTAuthenticationProvider(secret="test-secret", leeway_seconds=60)
        now = int(time.time())
        claims = {
            "sub": "user123",
            "iat": now + 300,
            "exp": now + 3600,
        }

        assert provider._validate_claims(claims) is False

    def test_validate_claims_with_wrong_issuer(self):
        """Test _validate_claims fails with wrong issuer."""
        provider = JWTAuthenticationProvider(
            secret="test-secret",
            issuer="https://expected.example.com",
        )
        claims = {
            "sub": "user123",
            "iss": "https://wrong.example.com",
            "iat": int(time.time()) - 60,
            "exp": int(time.time()) + 3600,
        }

        assert provider._validate_claims(claims) is False

    def test_validate_claims_with_wrong_audience(self):
        """Test _validate_claims fails with wrong audience."""
        provider = JWTAuthenticationProvider(
            secret="test-secret",
            audience="expected-app",
        )
        claims = {
            "sub": "user123",
            "aud": "wrong-app",
            "iat": int(time.time()) - 60,
            "exp": int(time.time()) + 3600,
        }

        assert provider._validate_claims(claims) is False

    def test_validate_claims_audience_as_list(self):
        """Test _validate_claims accepts audience as list."""
        provider = JWTAuthenticationProvider(
            secret="test-secret",
            audience="phlo",
        )
        claims = {
            "sub": "user123",
            "aud": ["other-app", "phlo", "another-app"],
            "iat": int(time.time()) - 60,
            "exp": int(time.time()) + 3600,
        }

        assert provider._validate_claims(claims) is True


class TestAuthenticationProviderRegistration:
    """Tests for authentication provider registration."""

    def test_register_default_providers_explicit_env(self):
        """Test providers are registered when explicitly enabled via env."""
        with patch.dict(os.environ, {"PHLO_AUTH_STATIC_ENABLED": "true"}):
            clear_all_capabilities()
            register_default_capability_providers()
            registry = get_capability_registry()
            providers = registry.list("authentication_provider")
            assert len(providers) == 1
            assert providers[0].name == "static"

    def test_no_auto_registration_by_default(self):
        """Test providers are NOT auto-registered without explicit env."""
        env_to_clear = [
            "PHLO_AUTH_STATIC_ENABLED",
            "PHLO_AUTH_PROXY_ENABLED",
            "PHLO_AUTH_SERVICE_ENABLED",
            "PHLO_AUTH_DEV_MODE",
        ]
        with patch.dict(os.environ, dict.fromkeys(env_to_clear, ""), clear=True):
            clear_all_capabilities()
            register_default_capability_providers()
            registry = get_capability_registry()
            providers = registry.list("authentication_provider")
            assert len(providers) == 0

    def test_regulated_mode_disables_dev_admin(self, monkeypatch):
        """Regulated mode cannot silently activate the anonymous dev principal."""
        monkeypatch.setenv("PHLO_REGULATED", "true")
        monkeypatch.setenv("PHLO_AUTH_DEV_MODE", "true")

        register_default_capability_providers()

        assert get_capability_registry().list("authentication_provider") == []

    def test_register_proxy_provider_loads_shared_secret_from_environment(self):
        """Test proxy shared secret is wired through environment config."""
        with patch.dict(
            os.environ,
            {
                "PHLO_AUTH_PROXY_ENABLED": "true",
                "PHLO_AUTH_PROXY_SHARED_SECRET": "env-secret",
                "PHLO_AUTH_PROXY_TRUSTED_PROXIES": "127.0.0.1/32",
                "PHLO_AUTH_PROXY_HEADER_EMAIL": "X-Test-Email",
                "PHLO_AUTH_PROXY_HEADER_GROUPS": "X-Test-Groups",
            },
            clear=True,
        ):
            clear_all_capabilities()
            register_default_capability_providers()
            registry = get_capability_registry()
            providers = registry.list("authentication_provider")

            assert len(providers) == 1
            assert providers[0].name == "proxy"
            provider = providers[0].provider
            assert isinstance(provider, ProxyAuthenticationProvider)
            assert provider._shared_secret == "env-secret"
            assert provider._header_email == "x-test-email"
            assert provider._header_groups == "x-test-groups"

    def test_register_proxy_provider_from_phlo_yaml(self, tmp_path, monkeypatch):
        """Proxy provider should register from root authentication config."""
        (tmp_path / "phlo.yaml").write_text(
            """
authentication:
  provider: proxy
  proxy:
    trusted_proxies:
      - 127.0.0.1/32
    shared_secret: config-secret
    header_email: X-Config-Email
    header_groups: X-Config-Groups
""".lstrip()
        )
        monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
        monkeypatch.delenv("PHLO_AUTH_PROXY_ENABLED", raising=False)
        monkeypatch.delenv("PHLO_AUTH_PROXY_SHARED_SECRET", raising=False)
        monkeypatch.delenv("PHLO_AUTH_PROXY_TRUSTED_PROXIES", raising=False)
        monkeypatch.delenv("PHLO_AUTH_PROXY_HEADER_EMAIL", raising=False)
        monkeypatch.delenv("PHLO_AUTH_PROXY_HEADER_GROUPS", raising=False)

        register_default_capability_providers()
        registry = get_capability_registry()
        providers = registry.list("authentication_provider")

        assert len(providers) == 1
        assert providers[0].name == "proxy"
        provider = providers[0].provider
        assert isinstance(provider, ProxyAuthenticationProvider)
        assert provider._shared_secret == "config-secret"
        assert provider._header_email == "x-config-email"
        assert provider._header_groups == "x-config-groups"

    def test_env_proxy_config_overrides_phlo_yaml(self, tmp_path, monkeypatch):
        """Environment config should override phlo.yaml provider settings."""
        (tmp_path / "phlo.yaml").write_text(
            """
authentication:
  provider: proxy
  proxy:
    trusted_proxies:
      - 127.0.0.1/32
    shared_secret: config-secret
""".lstrip()
        )
        monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
        monkeypatch.setenv("PHLO_AUTH_PROXY_SHARED_SECRET", "env-secret")

        register_default_capability_providers()
        registry = get_capability_registry()
        providers = registry.list("authentication_provider")

        assert len(providers) == 1
        provider = providers[0].provider
        assert isinstance(provider, ProxyAuthenticationProvider)
        assert provider._shared_secret == "env-secret"


class TestProviderSelection:
    """Tests for provider selection logic."""

    def test_multiple_providers_require_explicit_selection(self):
        """Test that multiple providers require explicit selection."""
        with patch.dict(
            os.environ,
            {
                "PHLO_AUTH_STATIC_ENABLED": "true",
                "PHLO_AUTH_PROXY_ENABLED": "true",
            },
        ):
            clear_all_capabilities()
            register_default_capability_providers()
            registry = get_capability_registry()
            providers = registry.list("authentication_provider")
            assert len(providers) == 2
            names = {p.name for p in providers}
            assert "static" in names
            assert "proxy" in names
