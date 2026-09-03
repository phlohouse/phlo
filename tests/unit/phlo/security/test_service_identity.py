"""Tests for service-to-service identity.

Covers HMAC service tokens (creation, expiry, tamper and malformed
rejection, bounded max-age), header construction for initiator and
correlation IDs, and scoped per-audience tokens whose nonces are
consumed exactly once across receiver instances — enforced by the
Postgres nonce store under concurrent consumers. Shared-secret
compatibility is refused in production and regulated mode.
"""

from __future__ import annotations

import json
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
    PHLO_SERVICE_CREDENTIALS_FILE_ENV,
    PostgresNonceStore,
    ServiceIdentityCredentials,
    WorkloadKey,
    WorkloadKeyRing,
    WorkloadKeyState,
    build_scoped_service_headers,
    create_scoped_service_token,
    load_service_identity_credentials,
    validate_scoped_service_token,
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


def _credentials() -> ServiceIdentityCredentials:
    return ServiceIdentityCredentials(
        rings={
            ("phlo-api", "dagster"): WorkloadKeyRing(
                caller="phlo-api",
                audience="dagster",
                scp=("dagster:control",),
                keys={"k1": WorkloadKey(kid="k1", secret="api-dagster-secret")},
            ),
            ("phlo-api", "trino"): WorkloadKeyRing(
                caller="phlo-api",
                audience="trino",
                scp=("trino:query",),
                keys={"k1": WorkloadKey(kid="k1", secret="api-trino-secret")},
            ),
            ("worker", "dagster"): WorkloadKeyRing(
                caller="worker",
                audience="dagster",
                scp=("dagster:control",),
                keys={"k1": WorkloadKey(kid="k1", secret="worker-secret")},
            ),
        }
    )


class TestScopedServiceTokens:
    def _token(
        self,
        caller: str = "phlo-api",
        audience: str = "dagster",
        scp: tuple[str, ...] = ("dagster:control",),
        *,
        now: int = 1_000,
        credentials: ServiceIdentityCredentials | None = None,
    ) -> str:
        return create_scoped_service_token(
            caller,
            audience=audience,
            scp=scp,
            credentials=credentials or _credentials(),
            now=now,
        )

    def _validate(
        self,
        token: str,
        *,
        caller: str = "phlo-api",
        audience: str = "dagster",
        scp: tuple[str, ...] = ("dagster:control",),
        store: Any | None = None,
        now: int = 1_001,
        credentials: ServiceIdentityCredentials | None = None,
        max_age_seconds: int | None = None,
    ) -> str | None:
        kwargs = {}
        if max_age_seconds is not None:
            kwargs["max_age_seconds"] = max_age_seconds
        return validate_scoped_service_token(
            token,
            expected_audience=audience,
            allowed_caller=caller,
            expected_scp=scp,
            credentials=credentials or _credentials(),
            nonce_store=store or SharedNonceStore(),
            now=now,
            **kwargs,
        )

    def test_binds_token_to_configured_caller_and_audience(self) -> None:
        assert self._validate(self._token()) == "phlo-api"

    def test_rejects_wrong_audience_before_consuming_nonce(self) -> None:
        store = SharedNonceStore()
        assert self._validate(self._token(), audience="trino", store=store) is None
        assert not store.consumed

    def test_rejects_wrong_caller_before_consuming_nonce(self) -> None:
        store = SharedNonceStore()
        assert self._validate(self._token(caller="worker"), store=store) is None
        assert not store.consumed

    def test_rejects_wrong_scope(self) -> None:
        assert self._validate(self._token(), scp=("api:orchestrate",)) is None

    def test_rejects_undeclared_scope_at_signing(self) -> None:
        with pytest.raises(RuntimeError, match="declared scope"):
            self._token(scp=("api:orchestrate",))

    def test_accepts_exact_declared_scope(self) -> None:
        assert self._validate(self._token(scp=("dagster:control",))) == "phlo-api"

    def test_rejects_subset_of_declared_scopes(self) -> None:
        # Even a strict subset must be refused: only the exact declared set is allowed.
        subset_creds = ServiceIdentityCredentials(
            rings={
                ("phlo-api", "dagster"): WorkloadKeyRing(
                    caller="phlo-api",
                    audience="dagster",
                    scp=("dagster:control", "dagster:monitor"),
                    keys={"k1": WorkloadKey(kid="k1", secret="api-dagster-secret")},
                )
            }
        )
        with pytest.raises(RuntimeError, match="declared scope"):
            self._token(scp=("dagster:control",), credentials=subset_creds)
        # The full declared set (in any order) is accepted.
        assert (
            self._validate(
                self._token(scp=("dagster:monitor", "dagster:control"), credentials=subset_creds),
                scp=("dagster:monitor", "dagster:control"),
                credentials=subset_creds,
            )
            == "phlo-api"
        )

    def test_rejects_missing_ring(self) -> None:
        assert self._validate(self._token(), credentials=ServiceIdentityCredentials({})) is None

    def test_one_caller_has_distinct_rings_for_each_audience(self) -> None:
        dagster_token = self._token(audience="dagster")
        trino_token = self._token(audience="trino", scp=("trino:query",))
        assert self._validate(dagster_token) == "phlo-api"
        assert self._validate(trino_token, audience="trino", scp=("trino:query",)) == "phlo-api"
        assert self._validate(dagster_token, audience="trino", scp=("trino:query",)) is None

    def test_rejects_tampered_signature(self) -> None:
        token = self._token()
        head, _, sig = token.rpartition(".")
        flipped = "a" if sig[0] != "a" else "b"
        assert self._validate(head + "." + flipped + sig[1:]) is None

    def test_rejects_unknown_kid(self) -> None:
        token = self._token()
        # A verifier with no key for this kid must reject before any replay use.
        empty_ring = ServiceIdentityCredentials(
            rings={
                ("phlo-api", "dagster"): WorkloadKeyRing(
                    caller="phlo-api",
                    audience="dagster",
                    scp=("dagster:control",),
                    keys={},
                )
            }
        )
        assert self._validate(token, credentials=empty_ring) is None

    def test_rejects_expired_token(self) -> None:
        # exp = 1300; at now=1400 the token is beyond expiry plus skew.
        assert self._validate(self._token(now=1_000), now=1_400) is None

    def test_rejects_future_token(self) -> None:
        # iat = 1000; at now=900 the token is issued too far in the future.
        assert self._validate(self._token(now=1_000), now=900) is None

    def test_rejects_token_longer_than_the_production_ceiling(self) -> None:
        # exp - iat is 300s; a ceiling below that must reject.
        assert self._validate(self._token(), max_age_seconds=100) is None

    def test_replay_is_rejected_across_receivers_and_restart(self) -> None:
        backing = SharedNonceStore()
        token = self._token()
        assert self._validate(token, store=backing) == "phlo-api"
        restarted = SharedNonceStore(backing.consumed, backing.lock)
        assert self._validate(token, store=restarted) is None

    def test_rotation_accepts_retiring_key_and_rejects_retired_key(self) -> None:
        # A signer ring that only ever had k1 active produces k1-signed tokens.
        signer = ServiceIdentityCredentials(
            rings={
                ("phlo-api", "dagster"): WorkloadKeyRing(
                    caller="phlo-api",
                    audience="dagster",
                    scp=("dagster:control",),
                    keys={
                        "k1": WorkloadKey(
                            kid="k1", secret="old-secret", state=WorkloadKeyState.ACTIVE
                        )
                    },
                )
            }
        )
        # The verifier has rotated: k1 is retiring (until 1200) and k2 is active.
        verifier = ServiceIdentityCredentials(
            rings={
                ("phlo-api", "dagster"): WorkloadKeyRing(
                    caller="phlo-api",
                    audience="dagster",
                    scp=("dagster:control",),
                    keys={
                        "k1": WorkloadKey(
                            kid="k1",
                            secret="old-secret",
                            state=WorkloadKeyState.RETIRING,
                            activated_at=0,
                            retiring_until=1_200,
                        ),
                        "k2": WorkloadKey(
                            kid="k2",
                            secret="new-secret",
                            state=WorkloadKeyState.ACTIVE,
                            activated_at=900,
                        ),
                    },
                )
            }
        )
        old_token = create_scoped_service_token(
            "phlo-api",
            audience="dagster",
            scp=("dagster:control",),
            credentials=signer,
            now=1_000,
        )
        assert old_token.split(".")[1] == "k1"
        # Accepted while the retiring key is still within its retirement interval.
        assert self._validate(old_token, credentials=verifier, now=1_100) == "phlo-api"
        # Rejected after the retirement interval elapses.
        assert self._validate(old_token, credentials=verifier, now=1_300) is None

    def test_retired_key_is_never_used_for_signing(self) -> None:
        retired = ServiceIdentityCredentials(
            rings={
                ("phlo-api", "dagster"): WorkloadKeyRing(
                    caller="phlo-api",
                    audience="dagster",
                    scp=("dagster:control",),
                    keys={
                        "k1": WorkloadKey(
                            kid="k1",
                            secret="old-secret",
                            state=WorkloadKeyState.RETIRED,
                        )
                    },
                )
            }
        )
        import pytest

        with pytest.raises(RuntimeError, match="No active key"):
            self._token(credentials=retired)

    def test_scoped_headers_preserve_initiator_and_correlation(self) -> None:
        headers = build_scoped_service_headers(
            "phlo-api",
            audience="dagster",
            scp=("dagster:control",),
            credentials=_credentials(),
            initiator="alice@co.com",
            correlation_id="req-789",
        )
        assert headers["Authorization"].startswith("Bearer phlo1.")
        assert headers[PHLO_INITIATOR_HEADER] == "alice@co.com"
        assert headers[PHLO_CORRELATION_HEADER] == "req-789"


class TestWorkloadKeyLifecycle:
    def test_can_sign_requires_active_and_activated(self) -> None:
        key = WorkloadKey(kid="k1", secret="s", state=WorkloadKeyState.ACTIVE, activated_at=100)
        assert key.can_sign(50) is False  # future-active: not yet activated
        assert key.can_sign(100) is True
        assert key.can_sign(200) is True

    def test_can_sign_rejects_retiring_and_retired(self) -> None:
        retiring = WorkloadKey(
            kid="k1", secret="s", state=WorkloadKeyState.RETIRING, activated_at=0
        )
        retired = WorkloadKey(kid="k1", secret="s", state=WorkloadKeyState.RETIRED, activated_at=0)
        assert retiring.can_sign(100) is False
        assert retired.can_sign(100) is False

    def test_can_verify_requires_activation(self) -> None:
        key = WorkloadKey(kid="k1", secret="s", state=WorkloadKeyState.ACTIVE, activated_at=100)
        assert key.can_verify(50) is False  # future-active: not yet activated
        assert key.can_verify(100) is True

    def test_can_verify_retiring_within_interval_and_retired(self) -> None:
        retiring = WorkloadKey(
            kid="k1",
            secret="s",
            state=WorkloadKeyState.RETIRING,
            activated_at=0,
            retiring_until=200,
        )
        assert retiring.can_verify(100) is True
        assert retiring.can_verify(200) is True
        assert retiring.can_verify(201) is False
        retired = WorkloadKey(kid="k1", secret="s", state=WorkloadKeyState.RETIRED, activated_at=0)
        assert retired.can_verify(100) is False

    def test_future_active_key_rejected_on_verify(self) -> None:
        # A token signed by a future-active key is rejected before its
        # activation window opens, matching the can_sign/can_verify contract.
        creds = ServiceIdentityCredentials(
            rings={
                ("phlo-api", "dagster"): WorkloadKeyRing(
                    caller="phlo-api",
                    audience="dagster",
                    scp=("dagster:control",),
                    keys={
                        "k1": WorkloadKey(
                            kid="k1",
                            secret="s",
                            state=WorkloadKeyState.ACTIVE,
                            activated_at=1_000,
                        )
                    },
                )
            }
        )
        token = create_scoped_service_token(
            "phlo-api",
            audience="dagster",
            scp=("dagster:control",),
            credentials=creds,
            now=1_000,
        )
        # Now=900: the key is not yet activated, so verification must reject.
        result = validate_scoped_service_token(
            token,
            expected_audience="dagster",
            allowed_caller="phlo-api",
            expected_scp=("dagster:control",),
            credentials=creds,
            nonce_store=SharedNonceStore(),
            now=900,
        )
        assert result is None
        # Now=1000: activated, so it verifies.
        assert (
            validate_scoped_service_token(
                token,
                expected_audience="dagster",
                allowed_caller="phlo-api",
                expected_scp=("dagster:control",),
                credentials=creds,
                nonce_store=SharedNonceStore(),
                now=1_000,
            )
            == "phlo-api"
        )


class TestCredentialFileLoading:
    def _valid_credentials_json(self) -> str:
        return json.dumps(
            {
                "phlo-api": {
                    "dagster": {
                        "scp": ["dagster:control"],
                        "keys": {
                            "k1": {
                                "secret": "api-dagster-secret",
                                "state": "active",
                                "activated_at": 0,
                            }
                        },
                    }
                }
            }
        )

    def test_world_readable_credential_file_rejected(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "credentials.json"
        path.write_text(self._valid_credentials_json())
        path.chmod(0o644)
        monkeypatch.setenv(PHLO_SERVICE_CREDENTIALS_FILE_ENV, str(path))
        with pytest.raises(RuntimeError, match="not group/world readable"):
            load_service_identity_credentials()

    def test_symlink_credential_file_rejected(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target.json"
        target.write_text(self._valid_credentials_json())
        target.chmod(0o600)
        link = tmp_path / "link.json"
        link.symlink_to(target)
        monkeypatch.setenv(PHLO_SERVICE_CREDENTIALS_FILE_ENV, str(link))
        with pytest.raises(RuntimeError, match="regular file"):
            load_service_identity_credentials()

    def test_owner_only_credential_file_loads(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "credentials.json"
        path.write_text(self._valid_credentials_json())
        path.chmod(0o600)
        monkeypatch.setenv(PHLO_SERVICE_CREDENTIALS_FILE_ENV, str(path))
        credentials = load_service_identity_credentials()
        ring = credentials.ring_for("phlo-api", "dagster")
        assert ring is not None
        assert ring.scp == ("dagster:control",)


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
