"""Service identity helpers for service-to-service calls.

When phlo-api calls Dagster or Trino, it should identify itself
with a short-lived HMAC service token, not a spoofable header.

Production token format: ``caller:audience:timestamp:nonce:hmac`` where the
signature covers every field and is made with the credential assigned to the
named caller.  Validation consumes the nonce through an injected durable
store, so two receivers cannot both accept the same token.

The older ``PHLO_SERVICE_SECRET`` token helpers remain a development-only
compatibility path until receivers are migrated to the scoped contract.  They
are deliberately unavailable in production and regulated environments.

Header conventions for request chain attribution:
    Authorization: Bearer {service-token}    (service identity)
    X-Phlo-Initiator: alice@example.com     (originating user)
    X-Phlo-Correlation-Id: {request-id}     (audit correlation)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from phlo.logging import get_logger

logger = get_logger(__name__)

PHLO_SERVICE_SECRET_ENV = "PHLO_SERVICE_SECRET"
PHLO_INITIATOR_HEADER = "X-Phlo-Initiator"
PHLO_CORRELATION_HEADER = "X-Phlo-Correlation-Id"

DEFAULT_MAX_AGE_SECONDS = 300


@dataclass(frozen=True)
class ServiceTokenCredential:
    """One caller-audience credential."""

    secret: str


class NonceStore(Protocol):
    """Atomically record a nonce that has been accepted before it expires."""

    def consume(self, nonce: str, *, expires_at: datetime) -> bool:
        """Return whether this is the first successful consumption of ``nonce``."""


class PostgresNonceStore:
    """Durable nonce store backed by a caller-supplied PostgreSQL connection or pool.

    Construction does not read a DSN or create a global connection: the receiver
    owns its existing database resource and injects it here.  The unique nonce
    primary key makes the insert an atomic compare-and-set across processes.
    """

    def __init__(self, connection_or_pool: Any) -> None:
        self._connection_or_pool = connection_or_pool

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        resource = self._connection_or_pool
        if hasattr(resource, "connection"):
            with resource.connection() as connection:
                yield connection
            return
        if hasattr(resource, "getconn"):
            connection = resource.getconn()
            try:
                yield connection
            finally:
                resource.putconn(connection)
            return
        yield resource

    def ensure_schema(self) -> None:
        """Create the small durable nonce table when the receiver starts."""
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS phlo_service_token_nonces (
                            nonce TEXT PRIMARY KEY,
                            expires_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def consume(self, nonce: str, *, expires_at: datetime) -> bool:
        """Atomically claim ``nonce`` for this token's remaining lifetime."""
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO phlo_service_token_nonces (nonce, expires_at)
                        VALUES (%s, %s)
                        ON CONFLICT (nonce) DO NOTHING
                        RETURNING nonce
                        """,
                        (nonce, expires_at),
                    )
                    accepted = cursor.fetchone() is not None
                connection.commit()
                return accepted
            except Exception:
                connection.rollback()
                raise

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete expired records during receiver-owned maintenance."""
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM phlo_service_token_nonces WHERE expires_at <= %s",
                        (now or datetime.now(UTC),),
                    )
                    removed = cursor.rowcount
                connection.commit()
                return removed
            except Exception:
                connection.rollback()
                raise


def create_scoped_service_token(
    caller: str,
    *,
    audience: str,
    credentials: Mapping[tuple[str, str], ServiceTokenCredential],
    now: int | None = None,
) -> str:
    """Create a token signed only by ``caller``'s configured credential."""
    credential = credentials.get((caller, audience))
    if credential is None:
        raise RuntimeError(
            f"No service credential configured for caller {caller!r} and audience {audience!r}"
        )
    if not credential.secret:
        raise RuntimeError(f"Caller {caller!r} has an empty service credential")

    timestamp = str(int(time.time()) if now is None else now)
    nonce = uuid4().hex
    message = f"{caller}:{audience}:{timestamp}:{nonce}"
    signature = hmac.new(credential.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{message}:{signature}"


def validate_scoped_service_token(
    token: str,
    *,
    expected_audience: str,
    credentials: Mapping[tuple[str, str], ServiceTokenCredential],
    nonce_store: NonceStore,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: int | None = None,
) -> str | None:
    """Validate a scoped token and atomically consume its nonce.

    ``None`` means the token must not be authenticated.  Store failures are
    deliberately allowed to propagate so a production receiver fails closed.
    """
    parts = token.split(":", 4)
    if len(parts) != 5:
        return None
    caller, audience, timestamp_str, nonce, provided_hmac = parts
    if audience != expected_audience or not caller or not nonce:
        return None
    credential = credentials.get((caller, expected_audience))
    if credential is None or not credential.secret:
        return None
    try:
        token_time = int(timestamp_str)
    except ValueError:
        return None
    current_time = int(time.time()) if now is None else now
    if token_time > current_time or current_time - token_time > max_age_seconds:
        return None

    message = f"{caller}:{audience}:{timestamp_str}:{nonce}"
    expected = hmac.new(credential.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided_hmac):
        return None

    expires_at = datetime.fromtimestamp(token_time + max_age_seconds, tz=UTC)
    if not nonce_store.consume(nonce, expires_at=expires_at):
        return None
    return caller


def _legacy_service_tokens_allowed() -> bool:
    """Keep shared-secret helpers strictly local while receiver migration lands."""
    # Import lazily to keep this low-level module independent during import.
    from phlo.security.mode import is_regulated

    if is_regulated():
        return False
    environment = os.environ.get("PHLO_ENVIRONMENT", "dev").lower()
    return environment not in {"prod", "production", "staging", "regulated"}


def create_service_token(service_id: str) -> str:
    """Create a short-lived HMAC service token with a nonce.

    Raises: RuntimeError if PHLO_SERVICE_SECRET is not set.
    """
    if not _legacy_service_tokens_allowed():
        raise RuntimeError("Shared service tokens are development-only; use scoped service tokens")
    secret = os.environ.get(PHLO_SERVICE_SECRET_ENV)
    if not secret:
        raise RuntimeError(f"{PHLO_SERVICE_SECRET_ENV} must be set for service-to-service auth")

    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    message = f"{service_id}:{timestamp}:{nonce}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    return f"{service_id}:{timestamp}:{nonce}:{signature}"


def validate_service_token(
    token: str,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> str | None:
    """Validate an HMAC service token."""
    if not _legacy_service_tokens_allowed():
        return None
    secret = os.environ.get(PHLO_SERVICE_SECRET_ENV)
    if not secret:
        return None

    parts = token.split(":", 3)
    if len(parts) != 4:
        # Reject legacy 3-part tokens
        return None

    service_id, timestamp_str, nonce, provided_hmac = parts

    try:
        token_time = int(timestamp_str)
    except ValueError:
        return None

    if abs(time.time() - token_time) > max_age_seconds:
        return None

    message = f"{service_id}:{timestamp_str}:{nonce}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, provided_hmac):
        return None

    return service_id


def build_service_headers(
    service_id: str,
    initiator: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, str]:
    """Build HTTP headers for an authenticated service-to-service call.

    Raises: RuntimeError if PHLO_SERVICE_SECRET is not set.
    """
    token = create_service_token(service_id)
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
    }
    if initiator:
        headers[PHLO_INITIATOR_HEADER] = initiator
    if correlation_id:
        headers[PHLO_CORRELATION_HEADER] = correlation_id
    return headers
