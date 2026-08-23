"""Tests for service-to-service identity.

Covers HMAC service tokens (creation, expiry, tamper and malformed
rejection, bounded max-age), header construction for initiator and
correlation IDs, and scoped per-audience tokens whose nonces are
consumed exactly once across receiver instances — enforced by the
Postgres nonce store under concurrent consumers. Shared-secret
compatibility is refused in production and regulated mode.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import pytest

from phlo.security.service_identity import (
    PHLO_CORRELATION_HEADER,
    PHLO_INITIATOR_HEADER,
    PHLO_SERVICE_SECRET_ENV,
    PostgresNonceStore,
    ServiceTokenCredential,
    build_service_headers,
    create_scoped_service_token,
    create_service_token,
    validate_scoped_service_token,
    validate_service_token,
)


@dataclass
class SharedNonceStore:
    """Independent receiver stores sharing the same durable test backing state."""

    consumed: set[str] = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)

    def consume(self, nonce: str, *, expires_at: datetime) -> bool:
        del expires_at
        with self.lock:
            if nonce in self.consumed:
                return False
            self.consumed.add(nonce)
            return True


def _credentials() -> dict[tuple[str, str], ServiceTokenCredential]:
    return {
        ("phlo-api", "dagster"): ServiceTokenCredential(secret="api-dagster-secret"),
        ("phlo-api", "trino"): ServiceTokenCredential(secret="api-trino-secret"),
        ("worker", "dagster"): ServiceTokenCredential(secret="worker-secret"),
    }


class TestCreateServiceToken:
    def test_creates_valid_token(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        token = create_service_token("phlo-api")
        parts = token.split(":", 3)
        assert len(parts) == 4
        assert parts[0] == "phlo-api"

    def test_raises_without_secret(self, monkeypatch):
        monkeypatch.delenv(PHLO_SERVICE_SECRET_ENV, raising=False)
        with pytest.raises(RuntimeError, match=PHLO_SERVICE_SECRET_ENV):
            create_service_token("phlo-api")

    def test_different_services_different_tokens(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        t1 = create_service_token("phlo-api")
        t2 = create_service_token("dagster")
        assert t1 != t2


class TestValidateServiceToken:
    def test_validates_fresh_token(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        token = create_service_token("phlo-api")
        result = validate_service_token(token)
        assert result == "phlo-api"

    def test_rejects_expired_token(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        import hashlib
        import hmac

        old_timestamp = str(int(time.time()) - 600)
        nonce = "deadbeef" * 4
        message = f"phlo-api:{old_timestamp}:{nonce}"
        sig = hmac.new(b"test-secret-key", message.encode(), hashlib.sha256).hexdigest()
        expired_token = f"phlo-api:{old_timestamp}:{nonce}:{sig}"
        assert validate_service_token(expired_token) is None

    def test_rejects_wrong_hmac(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        token = create_service_token("phlo-api")
        parts = token.split(":", 3)
        tampered = f"{parts[0]}:{parts[1]}:{parts[2]}:{'a' * 64}"
        assert validate_service_token(tampered) is None

    def test_rejects_malformed_token(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        assert validate_service_token("not-a-valid-token") is None
        assert validate_service_token("") is None
        assert validate_service_token("a:b") is None
        assert validate_service_token("a:b:c") is None  # old 3-part format rejected

    def test_rejects_non_numeric_timestamp(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        assert validate_service_token("phlo-api:not-a-number:nonce:abc") is None

    def test_returns_none_without_secret(self, monkeypatch):
        monkeypatch.delenv(PHLO_SERVICE_SECRET_ENV, raising=False)
        assert validate_service_token("phlo-api:123:nonce:abc") is None

    def test_custom_max_age(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        token = create_service_token("phlo-api")
        # Valid with large max_age
        assert validate_service_token(token, max_age_seconds=3600) == "phlo-api"
        # Craft a 10-second-old token, validate with 5-second max_age
        import hashlib
        import hmac

        old_ts = str(int(time.time()) - 10)
        nonce = "cafebabe" * 4
        message = f"phlo-api:{old_ts}:{nonce}"
        sig = hmac.new(b"test-secret-key", message.encode(), hashlib.sha256).hexdigest()
        old_token = f"phlo-api:{old_ts}:{nonce}:{sig}"
        assert validate_service_token(old_token, max_age_seconds=5) is None
        assert validate_service_token(old_token, max_age_seconds=30) == "phlo-api"


class TestBuildServiceHeaders:
    def test_basic_headers(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        headers = build_service_headers("phlo-api")
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer phlo-api:")
        assert PHLO_INITIATOR_HEADER not in headers
        assert PHLO_CORRELATION_HEADER not in headers

    def test_with_initiator(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        headers = build_service_headers("phlo-api", initiator="alice@example.com")
        assert headers[PHLO_INITIATOR_HEADER] == "alice@example.com"

    def test_with_correlation_id(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        headers = build_service_headers("phlo-api", correlation_id="req-123")
        assert headers[PHLO_CORRELATION_HEADER] == "req-123"

    def test_with_all_fields(self, monkeypatch):
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "test-secret-key")
        headers = build_service_headers(
            "phlo-api", initiator="alice@co.com", correlation_id="req-456"
        )
        assert headers["Authorization"].startswith("Bearer phlo-api:")
        assert headers[PHLO_INITIATOR_HEADER] == "alice@co.com"
        assert headers[PHLO_CORRELATION_HEADER] == "req-456"


class TestScopedServiceTokens:
    def test_binds_token_to_configured_caller_and_audience(self) -> None:
        token = create_scoped_service_token(
            "phlo-api", audience="dagster", credentials=_credentials(), now=1_000
        )
        assert (
            validate_scoped_service_token(
                token,
                expected_audience="dagster",
                credentials=_credentials(),
                nonce_store=SharedNonceStore(),
                now=1_001,
            )
            == "phlo-api"
        )

    def test_rejects_wrong_audience_before_consuming_nonce(self) -> None:
        token = create_scoped_service_token(
            "phlo-api", audience="dagster", credentials=_credentials(), now=1_000
        )
        store = SharedNonceStore()
        assert (
            validate_scoped_service_token(
                token,
                expected_audience="trino",
                credentials=_credentials(),
                nonce_store=store,
                now=1_001,
            )
            is None
        )
        assert not store.consumed

    def test_rejects_unconfigured_caller(self) -> None:
        credentials = _credentials()
        token = create_scoped_service_token("worker", audience="dagster", credentials=credentials)
        assert (
            validate_scoped_service_token(
                token,
                expected_audience="dagster",
                credentials={("phlo-api", "dagster"): credentials[("phlo-api", "dagster")]},
                nonce_store=SharedNonceStore(),
            )
            is None
        )

    def test_one_caller_has_distinct_credentials_for_each_audience(self) -> None:
        credentials = _credentials()
        dagster_token = create_scoped_service_token(
            "phlo-api", audience="dagster", credentials=credentials, now=1_000
        )
        trino_token = create_scoped_service_token(
            "phlo-api", audience="trino", credentials=credentials, now=1_000
        )

        assert (
            validate_scoped_service_token(
                dagster_token,
                expected_audience="dagster",
                credentials=credentials,
                nonce_store=SharedNonceStore(),
                now=1_001,
            )
            == "phlo-api"
        )
        assert (
            validate_scoped_service_token(
                trino_token,
                expected_audience="trino",
                credentials=credentials,
                nonce_store=SharedNonceStore(),
                now=1_001,
            )
            == "phlo-api"
        )
        assert (
            validate_scoped_service_token(
                dagster_token,
                expected_audience="trino",
                credentials=credentials,
                nonce_store=SharedNonceStore(),
                now=1_001,
            )
            is None
        )

    def test_rejects_service_name_impersonation(self) -> None:
        token = create_scoped_service_token(
            "worker", audience="dagster", credentials=_credentials()
        )
        impersonating = "phlo-api:" + token.split(":", 1)[1]
        assert (
            validate_scoped_service_token(
                impersonating,
                expected_audience="dagster",
                credentials=_credentials(),
                nonce_store=SharedNonceStore(),
            )
            is None
        )

    def test_rejects_expired_token(self) -> None:
        token = create_scoped_service_token(
            "phlo-api", audience="dagster", credentials=_credentials(), now=1_000
        )
        assert (
            validate_scoped_service_token(
                token,
                expected_audience="dagster",
                credentials=_credentials(),
                nonce_store=SharedNonceStore(),
                max_age_seconds=300,
                now=1_301,
            )
            is None
        )

    def test_replay_is_rejected_across_two_receiver_instances_and_restart(self) -> None:
        backing_store = SharedNonceStore()
        token = create_scoped_service_token(
            "phlo-api", audience="dagster", credentials=_credentials(), now=1_000
        )
        assert (
            validate_scoped_service_token(
                token,
                expected_audience="dagster",
                credentials=_credentials(),
                nonce_store=backing_store,
                now=1_001,
            )
            == "phlo-api"
        )
        restarted_receiver = SharedNonceStore(backing_store.consumed, backing_store.lock)
        assert (
            validate_scoped_service_token(
                token,
                expected_audience="dagster",
                credentials=_credentials(),
                nonce_store=restarted_receiver,
                now=1_001,
            )
            is None
        )

    def test_shared_secret_compatibility_is_not_available_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "shared-secret")
        monkeypatch.setenv("PHLO_ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="development-only"):
            create_service_token("phlo-api")
        assert validate_service_token("phlo-api:1:nonce:signature") is None

    def test_shared_secret_compatibility_is_not_available_in_regulated_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PHLO_SERVICE_SECRET_ENV, "shared-secret")
        monkeypatch.setenv("PHLO_ENVIRONMENT", "dev")
        monkeypatch.setenv("PHLO_REGULATED", "true")
        with pytest.raises(RuntimeError, match="development-only"):
            create_service_token("phlo-api")
        assert validate_service_token("phlo-api:1:nonce:signature") is None


def test_postgres_nonce_store_rejects_one_of_two_simultaneous_consumers() -> None:
    dsn = os.environ.get("PHLO_SERVICE_TOKEN_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set PHLO_SERVICE_TOKEN_TEST_POSTGRES_DSN for the live PostgreSQL nonce race")

    import psycopg2

    first_connection = psycopg2.connect(dsn)
    second_connection = psycopg2.connect(dsn)
    cleanup_connection = psycopg2.connect(dsn)
    try:
        first_store = PostgresNonceStore(first_connection)
        first_store.ensure_schema()
        nonce = f"test-{time.time_ns()}"
        expires_at = datetime.fromtimestamp(time.time() + 300, tz=UTC)

        def consume(connection: Any) -> bool:
            return PostgresNonceStore(connection).consume(nonce, expires_at=expires_at)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, (first_connection, second_connection)))
        assert sorted(results) == [False, True]

        # A new connection represents a receiver after process restart.
        restarted_connection = psycopg2.connect(dsn)
        try:
            assert not PostgresNonceStore(restarted_connection).consume(
                nonce, expires_at=expires_at
            )
        finally:
            restarted_connection.close()
    finally:
        with cleanup_connection.cursor() as cursor:
            cursor.execute("DELETE FROM phlo_service_token_nonces WHERE nonce = %s", (nonce,))
        cleanup_connection.commit()
        first_connection.close()
        second_connection.close()
        cleanup_connection.close()
