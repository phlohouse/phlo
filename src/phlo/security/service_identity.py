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

import base64
import hashlib
import hmac
import json
import os
import stat
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from phlo.logging import get_logger

logger = get_logger(__name__)

PHLO_SERVICE_SECRET_ENV = "PHLO_SERVICE_SECRET"
PHLO_INITIATOR_HEADER = "X-Phlo-Initiator"
PHLO_CORRELATION_HEADER = "X-Phlo-Correlation-Id"

DEFAULT_MAX_AGE_SECONDS = 300


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


WORKLOAD_TOKEN_VERSION = "phlo1"
WORKLOAD_TOKEN_MAX_AGE_SECONDS = 300
WORKLOAD_TOKEN_MAX_CLOCK_SKEW_SECONDS = 30
PHLO_SERVICE_CREDENTIALS_FILE_ENV = "PHLO_SERVICE_CREDENTIALS_FILE"


class WorkloadKeyState(StrEnum):
    """Lifecycle state of one workload signing/verification key (ADR 0047 §4.1)."""

    ACTIVE = "active"
    RETIRING = "retiring"
    RETIRED = "retired"


@dataclass(frozen=True)
class WorkloadKey:
    """One key in a caller/audience ring. Secret values never appear in output."""

    kid: str
    secret: str
    state: WorkloadKeyState = WorkloadKeyState.ACTIVE
    activated_at: int = 0
    retiring_until: int | None = None

    def can_sign(self, now: int) -> bool:
        return self.state is WorkloadKeyState.ACTIVE and self.activated_at <= now

    def can_verify(self, now: int) -> bool:
        if self.activated_at > now:
            return False
        if self.state is WorkloadKeyState.RETIRED:
            return False
        return not (
            self.state is WorkloadKeyState.RETIRING
            and self.retiring_until is not None
            and now > self.retiring_until
        )


@dataclass(frozen=True)
class WorkloadKeyRing:
    """Key ring and declared scope set for one (caller, audience) pair."""

    caller: str
    audience: str
    scp: tuple[str, ...]
    keys: Mapping[str, WorkloadKey]

    def active_key(self, now: int) -> WorkloadKey | None:
        candidates = [key for key in self.keys.values() if key.can_sign(now)]
        return max(candidates, key=lambda key: key.activated_at) if candidates else None

    def key_by_kid(self, kid: str) -> WorkloadKey | None:
        return self.keys.get(kid)


@dataclass(frozen=True)
class ServiceIdentityCredentials:
    """Frozen caller/audience workload key rings (ADR 0047 §4)."""

    rings: Mapping[tuple[str, str], WorkloadKeyRing]

    def ring_for(self, caller: str, audience: str) -> WorkloadKeyRing | None:
        return self.rings.get((caller, audience))


class WorkloadTokenClaims:
    """Canonical JSON claims of a phlo1 token; unknown top-level claims rejected."""

    __slots__ = ("sub", "aud", "scp", "iat", "exp", "jti")

    def __init__(
        self,
        *,
        sub: str,
        aud: str,
        scp: tuple[str, ...],
        iat: int,
        exp: int,
        jti: str,
    ) -> None:
        self.sub = sub
        self.aud = aud
        self.scp = scp
        self.iat = iat
        self.exp = exp
        self.jti = jti

    def to_canonical_json(self) -> str:
        return json.dumps(
            {
                "sub": self.sub,
                "aud": self.aud,
                "scp": list(self.scp),
                "iat": self.iat,
                "exp": self.exp,
                "jti": self.jti,
            },
            sort_keys=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_canonical_json(cls, payload: str) -> WorkloadTokenClaims:
        try:
            raw = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise ValueError("malformed claims JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("claims must be an object")
        allowed = {"sub", "aud", "scp", "iat", "exp", "jti"}
        if set(raw) != allowed:
            raise ValueError("claims must contain exactly the canonical fields")
        sub = raw["sub"]
        aud = raw["aud"]
        scp = raw["scp"]
        iat = raw["iat"]
        exp = raw["exp"]
        jti = raw["jti"]
        if not isinstance(sub, str) or not sub:
            raise ValueError("sub must be a non-empty string")
        if not isinstance(aud, str) or not aud:
            raise ValueError("aud must be a non-empty string")
        if not isinstance(scp, list) or not all(isinstance(x, str) and x for x in scp):
            raise ValueError("scp must be a list of non-empty strings")
        if not all(isinstance(x, int) for x in (iat, exp)):
            raise ValueError("iat and exp must be integers")
        if not isinstance(jti, str) or not jti:
            raise ValueError("jti must be a non-empty string")
        return cls(
            sub=sub,
            aud=aud,
            scp=tuple(scp),
            iat=iat,
            exp=exp,
            jti=jti,
        )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def load_service_identity_credentials() -> ServiceIdentityCredentials:
    """Load caller/audience key rings from the referenced mode-0600 secret file.

    The file is a JSON object keyed by caller then audience; each pair holds
    the declared ``scp`` set and its ``keys`` keyed by ``kid`` (secret, state,
    activated_at, retiring_until). Missing references load as empty; receivers
    fail closed when the ring they need is absent.
    """
    raw_path = os.environ.get(PHLO_SERVICE_CREDENTIALS_FILE_ENV)
    if not raw_path:
        return ServiceIdentityCredentials({})
    path = Path(raw_path)
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"cannot stat service identity credentials: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError("service identity credentials must be a regular file (not a symlink)")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise RuntimeError(
            "service identity credentials must be owner-controlled and not group/world readable"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot load service identity credentials: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("service identity credentials must be a JSON object")

    rings: dict[tuple[str, str], WorkloadKeyRing] = {}
    for caller, audiences in payload.items():
        if not isinstance(audiences, dict):
            continue
        for audience, entry in audiences.items():
            if not isinstance(entry, dict):
                continue
            scp_raw = entry.get("scp", [])
            if not isinstance(scp_raw, list) or not all(isinstance(x, str) and x for x in scp_raw):
                raise RuntimeError(f"invalid scp for caller/audience {caller!r}/{audience!r}")
            keys_raw = entry.get("keys")
            if not isinstance(keys_raw, dict) or not keys_raw:
                raise RuntimeError(f"missing keys for caller/audience {caller!r}/{audience!r}")
            keys: dict[str, WorkloadKey] = {}
            for kid, key_entry in keys_raw.items():
                if not isinstance(key_entry, dict):
                    continue
                secret = key_entry.get("secret")
                if not isinstance(secret, str) or not secret:
                    raise RuntimeError(f"missing secret for kid {kid!r}")
                state = WorkloadKeyState(key_entry.get("state", WorkloadKeyState.ACTIVE.value))
                keys[kid] = WorkloadKey(
                    kid=str(kid),
                    secret=secret,
                    state=state,
                    activated_at=int(key_entry.get("activated_at", 0) or 0),
                    retiring_until=(
                        int(key_entry["retiring_until"])
                        if key_entry.get("retiring_until")
                        else None
                    ),
                )
            rings[(str(caller), str(audience))] = WorkloadKeyRing(
                caller=str(caller),
                audience=str(audience),
                scp=tuple(sorted(scp_raw)),
                keys=keys,
            )
    return ServiceIdentityCredentials(rings)


def create_scoped_service_token(
    caller: str,
    *,
    audience: str,
    scp: tuple[str, ...] | list[str],
    credentials: ServiceIdentityCredentials | Mapping[tuple[str, str], WorkloadKeyRing],
    now: int | None = None,
) -> str:
    """Create a phlo1 workload token signed by the caller's active key."""
    rings = (
        credentials.rings if isinstance(credentials, ServiceIdentityCredentials) else credentials
    )
    ring = rings.get((caller, audience))
    if ring is None:
        raise RuntimeError(
            f"No service credential configured for caller {caller!r} and audience {audience!r}"
        )
    requested = tuple(sorted(scp))
    if requested != tuple(sorted(ring.scp)):
        raise RuntimeError(
            f"Requested scopes {requested!r} for caller {caller!r} and audience {audience!r} "
            f"do not match the declared scope set {tuple(sorted(ring.scp))!r}"
        )
    now_int = int(time.time()) if now is None else now
    key = ring.active_key(now_int)
    if key is None:
        raise RuntimeError(f"No active key for caller {caller!r} and audience {audience!r}")

    claims = WorkloadTokenClaims(
        sub=caller,
        aud=audience,
        scp=tuple(sorted(scp)),
        iat=now_int,
        exp=now_int + WORKLOAD_TOKEN_MAX_AGE_SECONDS,
        jti=uuid4().hex,
    )
    body = (
        f"{WORKLOAD_TOKEN_VERSION}.{key.kid}.{_b64url(claims.to_canonical_json().encode('utf-8'))}"
    )
    signature = _b64url(hmac.new(key.secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def validate_scoped_service_token(
    token: str,
    *,
    expected_audience: str,
    allowed_caller: str,
    expected_scp: tuple[str, ...] | list[str],
    credentials: ServiceIdentityCredentials | Mapping[tuple[str, str], WorkloadKeyRing],
    nonce_store: NonceStore,
    max_age_seconds: int = WORKLOAD_TOKEN_MAX_AGE_SECONDS,
    max_clock_skew_seconds: int = WORKLOAD_TOKEN_MAX_CLOCK_SKEW_SECONDS,
    now: int | None = None,
) -> str | None:
    """Validate a phlo1 workload token and atomically consume its replay state.

    ``None`` means the token must not be authenticated. Store failures are
    deliberately allowed to propagate so a production receiver fails closed
    (503) without invoking the handler.
    """
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != WORKLOAD_TOKEN_VERSION:
        return None
    _, kid, payload_b64, provided_sig = parts

    rings = (
        credentials.rings if isinstance(credentials, ServiceIdentityCredentials) else credentials
    )
    ring = rings.get((allowed_caller, expected_audience))
    if ring is None:
        return None
    now_int = int(time.time()) if now is None else now
    key = ring.key_by_kid(kid)
    if key is None or not key.can_verify(now_int):
        return None

    body = f"{WORKLOAD_TOKEN_VERSION}.{kid}.{payload_b64}"
    expected = _b64url(hmac.new(key.secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, provided_sig):
        return None

    try:
        claims = WorkloadTokenClaims.from_canonical_json(_unb64url(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None

    if claims.sub != allowed_caller:
        return None
    if claims.aud != expected_audience:
        return None
    if tuple(sorted(claims.scp)) != tuple(sorted(expected_scp)):
        return None
    if claims.iat > now_int + max_clock_skew_seconds:
        return None
    if claims.exp < now_int - max_clock_skew_seconds:
        return None
    if claims.exp - claims.iat > max_age_seconds:
        return None

    replay_key = f"{expected_audience}:{kid}:{claims.jti}"
    expires_at = datetime.fromtimestamp(claims.exp + max_clock_skew_seconds, tz=UTC)
    if not nonce_store.consume(replay_key, expires_at=expires_at):
        return None
    return claims.sub


def build_scoped_service_headers(
    caller: str,
    *,
    audience: str,
    scp: tuple[str, ...] | list[str],
    credentials: ServiceIdentityCredentials | Mapping[tuple[str, str], WorkloadKeyRing],
    initiator: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, str]:
    """Build authenticated headers for a phlo1 service-to-service call.

    Raises before any HTTP call when the caller/audience ring is missing or
    has no active key, so production callers fail closed.
    """
    token = create_scoped_service_token(caller, audience=audience, scp=scp, credentials=credentials)
    headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
    if initiator:
        headers[PHLO_INITIATOR_HEADER] = initiator
    if correlation_id:
        headers[PHLO_CORRELATION_HEADER] = correlation_id
    return headers


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
