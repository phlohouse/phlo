"""Tamper-evident audit sealing and sink.

Provides HMAC-keyed hash-chained audit records for tamper-evident audit trails.
The HMAC key prevents an attacker with database access from recalculating the
chain after modifying records.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from phlo.audit.events import CanonicalAuditEvent

GENESIS_HASH = "0" * 64
PHLO_AUDIT_HMAC_KEY_ENV = "PHLO_AUDIT_HMAC_KEY"


def _get_hmac_key() -> bytes:
    """Return the HMAC key from the environment, or a default for dev/test."""
    key = os.environ.get(PHLO_AUDIT_HMAC_KEY_ENV, "")
    if key:
        return key.encode()
    from phlo.security.mode import is_regulated

    if is_regulated():
        raise RuntimeError(f"{PHLO_AUDIT_HMAC_KEY_ENV} is required in regulated mode")
    return b"phlo-dev-audit-key"


def compute_record_hash(
    event: CanonicalAuditEvent,
    sequence_number: int,
    previous_hash: str,
    hmac_key: bytes | None = None,
) -> str:
    """Compute the tamper-evident HMAC for a sealed audit record."""
    if hmac_key is None:
        hmac_key = _get_hmac_key()
    event_dict = event.to_dict()
    payload = f"{sequence_number}:{json.dumps(event_dict, sort_keys=True)}:{previous_hash}"
    return _hmac.new(hmac_key, payload.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class SealedAuditRecord:
    """Sealed audit record with HMAC hash chain.

    Each record contains the original audit event plus chain metadata
    that allows verification of the chain's integrity. The record hash
    is an HMAC-SHA256 keyed with a secret so that an attacker cannot
    recompute the chain without the key.
    """

    sequence_number: int
    """Monotonically increasing sequence number per surface."""

    event: CanonicalAuditEvent
    """The original audit event."""

    previous_hash: str
    """HMAC hash of the previous sealed record. Genesis is all zeros."""

    record_hash: str
    """HMAC-SHA256 of (sequence_number, event.to_dict(), previous_hash)."""

    sealed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    """ISO 8601 timestamp when the record was sealed."""

    @classmethod
    def seal(
        cls,
        event: CanonicalAuditEvent,
        sequence_number: int,
        previous_hash: str,
        hmac_key: bytes | None = None,
    ) -> SealedAuditRecord:
        """Create a sealed audit record.

        Seals ``event`` at ``sequence_number`` over ``previous_hash``
        (GENESIS_HASH for the first record). Uses the env default key when
        ``hmac_key`` is not provided.
        """
        record_hash = compute_record_hash(
            event,
            sequence_number,
            previous_hash,
            hmac_key=hmac_key,
        )

        return cls(
            sequence_number=sequence_number,
            event=event,
            previous_hash=previous_hash,
            record_hash=record_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "sequence_number": self.sequence_number,
            "sealed_at": self.sealed_at,
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
            "event": self.event.to_dict(),
        }


class TamperEvidentAuditSink:
    """Audit sink that seals events with hash chaining.

    Wraps an AuditStore and maintains per-surface sequence counters
    and last hashes for chain integrity.

    Thread-safe via lock per surface.
    """

    def __init__(self, store: AuditStore, hmac_key: bytes | None = None) -> None:
        """Initialize the sink over ``store``, sealing with ``hmac_key`` or
        the env default."""
        self._store = store
        self.is_durable = store.is_durable
        self._hmac_key = hmac_key or _get_hmac_key()
        self._surface_locks: dict[str, threading.Lock] = {}
        self._surface_locks_guard = threading.Lock()
        self._surface_state: dict[str, tuple[int, str]] = {}
        self._state_guard = threading.Lock()

    def _get_surface_lock(self, surface: str) -> threading.Lock:
        """Get or create a lock for a surface."""
        with self._surface_locks_guard:
            if surface not in self._surface_locks:
                self._surface_locks[surface] = threading.Lock()
            return self._surface_locks[surface]

    def _get_surface_state(self, surface: str) -> tuple[int, str]:
        """Return a surface's cached sequence number and last hash,
        initializing from the store on first access."""
        with self._state_guard:
            if surface not in self._surface_state:
                last_record = self._store.get_last(surface)
                if last_record is None:
                    self._surface_state[surface] = (0, GENESIS_HASH)
                else:
                    self._surface_state[surface] = (
                        last_record.sequence_number,
                        last_record.record_hash,
                    )
            return self._surface_state[surface]

    def write(self, event: CanonicalAuditEvent) -> None:
        """Seal and write an audit event under its surface lock."""
        surface = event.surface or "unknown"
        lock = self._get_surface_lock(surface)

        with lock:
            seq, prev_hash = self._get_surface_state(surface)
            new_seq = seq + 1

            sealed = SealedAuditRecord.seal(event, new_seq, prev_hash, hmac_key=self._hmac_key)

            self._store.append(sealed)

            with self._state_guard:
                self._surface_state[surface] = (new_seq, sealed.record_hash)


class AuditStore:
    """Protocol for audit storage backends."""

    is_durable = False

    def append(self, record: SealedAuditRecord) -> None:
        """Append a sealed record to the store; subclasses must implement."""
        raise NotImplementedError

    def get_last(self, surface: str) -> SealedAuditRecord | None:
        """Return the last sealed record for a surface, or None if empty;
        subclasses must implement."""
        raise NotImplementedError

    def query(
        self,
        surface: str,
        after: int | None = None,
        before: int | None = None,
        limit: int = 1000,
    ) -> list[SealedAuditRecord]:
        """Return sealed records for a surface between exclusive sequence
        bounds, capped at ``limit``; subclasses must implement."""
        raise NotImplementedError

    def verify_chain(
        self,
        surface: str,
        hmac_key: bytes | None = None,
    ) -> ChainVerificationResult:
        """Verify chain integrity for a surface and return the outcome;
        subclasses must implement."""
        raise NotImplementedError


@dataclass(frozen=True)
class ChainVerificationResult:
    """Result of a chain verification check."""

    valid: bool
    """Whether the chain is valid."""

    surface: str
    """The surface that was verified."""

    total_records: int
    """Total number of records in the chain."""

    first_invalid_sequence: int | None = None
    """Sequence number of first invalid record, if any."""

    error_message: str | None = None
    """Error message if verification failed."""

    verified_hashes: list[str] = field(default_factory=list)
    """List of verified record hashes in order."""
