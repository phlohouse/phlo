"""Durable Phlo-owned membership and service-account authority."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from phlo.compliance.signatures.types import SignatureMeaning, SignatureRecord, SignatureRequest
from phlo.plugins.observatory_settings import (
    ObservatorySettingsStorageConfig,
    SettingsScope,
    SettingsStore,
    StorageCorruptionError,
    StorageUnavailableError,
    get_settings_service,
)

_NAMESPACE = "phlo.identity.authority"
_SCHEMA_VERSION = 1
_ROLE_NAMES = frozenset({"admin", "operator", "developer", "analyst", "viewer", "service"})


class IdentityConflictError(ValueError):
    """Raised when an identity mutation conflicts with its current version."""


@dataclass(frozen=True)
class IdentityMember:
    """Phlo's managed role assignment for one authenticated identity."""

    subject: str
    email: str | None
    principal_type: str
    roles: tuple[str, ...]
    active: bool
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class IdentityInvitation:
    """Invitation metadata; the one-time secret is never stored."""

    invitation_id: str
    email: str
    roles: tuple[str, ...]
    status: str
    invited_by: str
    expires_at: str
    created_at: str


@dataclass(frozen=True)
class IdentityServiceAccount:
    """Service-account metadata; its bearer secret is never returned by reads."""

    subject: str
    name: str
    roles: tuple[str, ...]
    active: bool
    version: int
    created_at: str


@dataclass(frozen=True)
class StoredSignature:
    """A persisted signature and its single-use consumption state."""

    record: SignatureRecord
    consumed_at: str | None


def validate_roles(roles: Sequence[str]) -> tuple[str, ...]:
    """Return canonical, de-duplicated role names or reject unknown roles."""
    canonical = tuple(sorted(set(roles)))
    if not canonical or any(role not in _ROLE_NAMES for role in canonical):
        raise ValueError("One or more role names are invalid.")
    return canonical


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp() -> str:
    return _now().isoformat()


class IdentityAuthority:
    """Manage Phlo membership using the shared, transactional SettingsStore.

    The authority occupies one settings record so its mutations use the store's
    atomic `mutate` operation. In PostgreSQL mode that operation serializes
    across replicas with a transaction-scoped advisory lock.
    """

    def __init__(self, store: SettingsStore | None = None) -> None:
        if store is None:
            if ObservatorySettingsStorageConfig().observatory_settings_backend != "postgres":
                raise StorageUnavailableError(
                    "Identity authority requires shared PostgreSQL storage"
                )
            store = get_settings_service()
        self._store = store

    def members(self) -> list[IdentityMember]:
        state = self._read()
        return sorted(
            (_member(subject, item) for subject, item in state["members"].items()),
            key=lambda item: item.subject,
        )

    def member(self, subject: str) -> IdentityMember | None:
        item = self._read()["members"].get(subject)
        return _member(subject, item) if item is not None else None

    def managed_roles(self, subject: str, principal_type: str) -> tuple[str, ...] | None:
        """Return assigned roles, empty for disabled users, or None if unmanaged."""
        state = self._read()
        member = state["members"].get(subject)
        if member is None:
            account = state["service_accounts"].get(subject)
            if account is None or not account["active"]:
                return None
            return tuple(account["roles"])
        if member["principal_type"] != principal_type or not member["active"]:
            return ()
        return tuple(member["roles"])

    def assign_roles(
        self,
        *,
        subject: str,
        email: str | None,
        principal_type: str,
        roles: list[str] | tuple[str, ...],
        expected_version: int,
        active: bool = True,
    ) -> IdentityMember:
        """Replace a member's role set only at the requested current version."""
        if not subject.strip() or principal_type not in {"user", "service", "platform"}:
            raise ValueError("Identity subject or type is invalid.")
        canonical_roles = validate_roles(roles)
        result: IdentityMember | None = None

        def update(state: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            existing = state["members"].get(subject)
            if existing is None and expected_version == 0:
                now = _timestamp()
                item = {
                    "email": email.strip().casefold() if email else None,
                    "principal_type": principal_type,
                    "roles": list(canonical_roles),
                    "active": active,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                state["members"][subject] = item
                result = _member(subject, item)
                return state
            if existing is None or existing["version"] != expected_version:
                raise IdentityConflictError("Member is missing or has changed.")
            now = _timestamp()
            item = {
                **existing,
                "email": email or existing["email"],
                "roles": list(canonical_roles),
                "active": active,
                "version": expected_version + 1,
                "updated_at": now,
            }
            state["members"][subject] = item
            result = _member(subject, item)
            return state

        self._mutate(update)
        assert result is not None
        return result

    def create_invitation(
        self,
        *,
        email: str,
        roles: list[str] | tuple[str, ...],
        invited_by: str,
        ttl: timedelta = timedelta(days=7),
    ) -> tuple[IdentityInvitation, str]:
        """Create an email-bound, one-time invitation and return its secret once."""
        normalized_email = email.strip().casefold()
        if "@" not in normalized_email or ttl <= timedelta(0):
            raise ValueError("Invitation email or expiry is invalid.")
        canonical_roles = validate_roles(roles)
        invitation_id = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        created_at = _now()
        item = {
            "email": normalized_email,
            "roles": list(canonical_roles),
            "status": "pending",
            "invited_by": invited_by,
            "expires_at": (created_at + ttl).isoformat(),
            "created_at": created_at.isoformat(),
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        }

        def insert(state: dict[str, Any]) -> dict[str, Any]:
            if any(
                invite["email"] == normalized_email and invite["status"] == "pending"
                for invite in state["invitations"].values()
            ):
                raise IdentityConflictError("A pending invitation already exists for this email.")
            state["invitations"][invitation_id] = item
            return state

        self._mutate(insert)
        return _invitation(invitation_id, item), token

    def invitations(self) -> list[IdentityInvitation]:
        state = self._read()
        return sorted(
            (_invitation(identity, item) for identity, item in state["invitations"].items()),
            key=lambda item: item.created_at,
            reverse=True,
        )

    def accept_invitation(
        self, *, token: str, subject: str, email: str, principal_type: str = "user"
    ) -> IdentityMember:
        """Accept a live invitation only for its matching authenticated email."""
        if principal_type != "user" or not subject.strip():
            raise ValueError("Only an authenticated user can accept an invitation.")
        normalized_email = email.strip().casefold()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        result: IdentityMember | None = None

        def accept(state: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            matches = [
                (identity, invite)
                for identity, invite in state["invitations"].items()
                if secrets.compare_digest(invite["token_hash"], token_hash)
            ]
            if len(matches) != 1:
                raise IdentityConflictError("Invitation is invalid or already used.")
            invitation_id, invite = matches[0]
            if (
                invite["status"] != "pending"
                or invite["email"] != normalized_email
                or datetime.fromisoformat(invite["expires_at"]) <= _now()
                or subject in state["members"]
            ):
                raise IdentityConflictError("Invitation is invalid, expired, or already used.")
            now = _timestamp()
            member_data = {
                "email": normalized_email,
                "principal_type": principal_type,
                "roles": invite["roles"],
                "active": True,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
            state["members"][subject] = member_data
            state["invitations"][invitation_id] = {**invite, "status": "accepted"}
            result = _member(subject, member_data)
            return state

        self._mutate(accept)
        assert result is not None
        return result

    def create_service_account(
        self,
        *,
        name: str,
        roles: list[str] | tuple[str, ...],
        idempotency_key: str | None = None,
        actor_subject: str | None = None,
    ) -> tuple[IdentityServiceAccount, str]:
        """Create a Phlo service identity and return its random bearer secret once."""
        if not name.strip():
            raise ValueError("Service-account name is required.")
        if idempotency_key is not None and (not idempotency_key.strip() or not actor_subject):
            raise ValueError("An actor is required when using an idempotency key.")
        canonical_roles = validate_roles(roles)
        subject = f"phlo-service:{secrets.token_urlsafe(12)}"
        token = secrets.token_urlsafe(40)
        normalized_name = name.strip()
        request_digest = hashlib.sha256(
            f"{normalized_name}\0{','.join(canonical_roles)}".encode()
        ).hexdigest()
        operation_id = (
            hashlib.sha256(f"{actor_subject}\0{idempotency_key}".encode()).hexdigest()
            if idempotency_key is not None
            else None
        )
        item = {
            "name": normalized_name,
            "roles": list(canonical_roles),
            "active": True,
            "version": 1,
            "created_at": _timestamp(),
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        }

        def insert(state: dict[str, Any]) -> dict[str, Any]:
            if operation_id is not None:
                prior = state["operations"].get(operation_id)
                if prior is not None:
                    if prior["request_digest"] != request_digest:
                        raise IdentityConflictError(
                            "Idempotency key was already used for a different request."
                        )
                    raise IdentityConflictError(
                        "Service account already exists; its one-time token is not replayed."
                    )
            state["service_accounts"][subject] = item
            if operation_id is not None:
                state["operations"][operation_id] = {
                    "request_digest": request_digest,
                    "subject": subject,
                    "completed_at": _timestamp(),
                }
            return state

        self._mutate(insert)
        return _service_account(subject, item), token

    def service_accounts(self) -> list[IdentityServiceAccount]:
        state = self._read()
        return sorted(
            (
                _service_account(subject, item)
                for subject, item in state["service_accounts"].items()
            ),
            key=lambda item: item.subject,
        )

    def authenticate_service_token(self, token: str) -> tuple[str, str] | None:
        """Constant-time match a managed service secret to its active identity."""
        digest = hashlib.sha256(token.encode()).hexdigest()
        for subject, item in self._read()["service_accounts"].items():
            if item["active"] and secrets.compare_digest(item["token_hash"], digest):
                return subject, item["name"]
        return None

    def revoke_service_account(self, subject: str, expected_version: int) -> IdentityServiceAccount:
        """Revoke a service account and invalidate its credential atomically."""
        result: IdentityServiceAccount | None = None

        def revoke(state: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            item = state["service_accounts"].get(subject)
            if item is None or item["version"] != expected_version or not item["active"]:
                raise IdentityConflictError("Service account is missing or has changed.")
            updated = {
                **item,
                "active": False,
                "token_hash": "",
                "version": expected_version + 1,
            }
            state["service_accounts"][subject] = updated
            result = _service_account(subject, updated)
            return state

        self._mutate(revoke)
        assert result is not None
        return result

    def save_signature(self, record: SignatureRecord) -> None:
        """Persist a valid signature bound to its actor, action, target, and version."""
        self.save_signature_idempotent(record, idempotency_key=None, request_digest=None)

    def save_signature_idempotent(
        self,
        record: SignatureRecord,
        *,
        idempotency_key: str | None,
        request_digest: str | None,
    ) -> SignatureRecord:
        """Atomically persist or replay a signature for the same actor, key, and intent."""
        if (
            not record.action
            or not record.record_type
            or not record.record_id
            or not record.record_version
            or not record.has_valid_hash()
        ):
            raise ValueError("Signature is incomplete or invalid.")
        if idempotency_key is not None and (not idempotency_key.strip() or not request_digest):
            raise ValueError("A request digest is required when using an idempotency key.")
        item = _signature_data(record)
        item["consumed_at"] = None
        operation_id = (
            hashlib.sha256(
                f"signature\0{record.signer_subject}\0{idempotency_key}".encode()
            ).hexdigest()
            if idempotency_key is not None
            else None
        )
        result = record

        def insert(state: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            if operation_id is not None:
                prior = state["operations"].get(operation_id)
                if prior is not None:
                    if prior["request_digest"] != request_digest:
                        raise IdentityConflictError(
                            "Idempotency key was already used for a different signature request."
                        )
                    stored = state["signatures"].get(prior["signature_id"])
                    if stored is None:
                        raise StorageCorruptionError("Identity authority state is unavailable")
                    result = _signature_record(stored)
                    return state
            if record.signature_id in state["signatures"]:
                raise IdentityConflictError("Signature already exists.")
            state["signatures"][record.signature_id] = item
            if operation_id is not None:
                state["operations"][operation_id] = {
                    "request_digest": request_digest,
                    "signature_id": record.signature_id,
                    "completed_at": _timestamp(),
                }
            return state

        self._mutate(insert)
        return result

    def consume_signature(
        self,
        signature_id: str,
        expected: SignatureRequest,
        *,
        review: tuple[str, SignatureRequest] | None = None,
    ) -> bool:
        """Consume exact MFA approval and optional independent review atomically."""
        consumed = False

        def consume(state: dict[str, Any]) -> dict[str, Any]:
            nonlocal consumed
            intents = [(signature_id, expected)]
            if review is not None:
                review_id, reviewer = review
                member = state["members"].get(reviewer.signer_subject)
                if (
                    review_id == signature_id
                    or reviewer.signer_subject == expected.signer_subject
                    or reviewer.meaning != SignatureMeaning.REVIEWED
                    or expected.meaning != SignatureMeaning.APPROVED
                    or reviewer.action != expected.action
                    or reviewer.record_type != expected.record_type
                    or reviewer.record_id != expected.record_id
                    or reviewer.record_version != expected.record_version
                    or member is None
                    or not member["active"]
                    or member["principal_type"] != "user"
                    or not {"admin", "operator"}.intersection(member["roles"])
                ):
                    return state
                intents.append(review)
            for identifier, intent in intents:
                item = state["signatures"].get(identifier)
                if item is None or item["consumed_at"] is not None:
                    return state
                record = _signature_record(item)
                if (
                    not record.has_valid_hash()
                    or record.authentication_assurance != "mfa"
                    or record.signer_subject != intent.signer_subject
                    or record.meaning != intent.meaning
                    or record.action != intent.action
                    or record.record_type != intent.record_type
                    or record.record_id != intent.record_id
                    or record.record_version != intent.record_version
                    or (
                        intent.justification is not None
                        and record.justification != intent.justification
                    )
                ):
                    return state
            consumed = True
            for identifier, _ in intents:
                state["signatures"][identifier]["consumed_at"] = _timestamp()
            return state

        self._mutate(consume)
        return consumed

    def signatures(self, signer_subject: str) -> list[StoredSignature]:
        """Return signatures issued by one actor without exposing other actors' records."""
        items = self._read()["signatures"].items()
        return sorted(
            (
                StoredSignature(_signature_record(item), item["consumed_at"])
                for _, item in items
                if item["signer_subject"] == signer_subject
            ),
            key=lambda item: item.record.signed_at,
            reverse=True,
        )

    def _read(self) -> dict[str, Any]:
        record = self._store.get(SettingsScope.GLOBAL, _NAMESPACE)
        if record is None:
            return _empty_state()
        return _validate_state(record.settings)

    def _mutate(self, mutation: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        def apply(current: dict[str, Any] | None) -> dict[str, Any]:
            state = _empty_state() if current is None else _validate_state(current)
            return mutation(state)

        self._store.mutate(SettingsScope.GLOBAL, _NAMESPACE, apply)


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "members": {},
        "invitations": {},
        "service_accounts": {},
        "signatures": {},
        "operations": {},
    }


def _validate_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_version") != _SCHEMA_VERSION or any(
        not isinstance(state.get(key), dict)
        for key in ("members", "invitations", "service_accounts", "signatures")
    ):
        raise StorageCorruptionError("Identity authority state is unavailable")
    state.setdefault("operations", {})
    if not isinstance(state["operations"], dict):
        raise StorageCorruptionError("Identity authority state is unavailable")
    return state


def _member(subject: str, item: dict[str, Any]) -> IdentityMember:
    return IdentityMember(
        subject=subject,
        email=item["email"],
        principal_type=item["principal_type"],
        roles=tuple(item["roles"]),
        active=item["active"],
        version=item["version"],
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def _invitation(identity: str, item: dict[str, Any]) -> IdentityInvitation:
    return IdentityInvitation(
        invitation_id=identity,
        email=item["email"],
        roles=tuple(item["roles"]),
        status=item["status"],
        invited_by=item["invited_by"],
        expires_at=item["expires_at"],
        created_at=item["created_at"],
    )


def _service_account(subject: str, item: dict[str, Any]) -> IdentityServiceAccount:
    return IdentityServiceAccount(
        subject=subject,
        name=item["name"],
        roles=tuple(item["roles"]),
        active=item["active"],
        version=item["version"],
        created_at=item["created_at"],
    )


def _signature_data(record: SignatureRecord) -> dict[str, Any]:
    return {
        "signature_id": record.signature_id,
        "signer_subject": record.signer_subject,
        "meaning": record.meaning.value,
        "record_type": record.record_type,
        "record_id": record.record_id,
        "record_version": record.record_version,
        "action": record.action,
        "justification": record.justification,
        "signed_at": record.signed_at,
        "authentication_assurance": record.authentication_assurance,
        "signature_hash": record.signature_hash,
    }


def _signature_record(item: dict[str, Any]) -> SignatureRecord:
    return SignatureRecord(
        signature_id=item["signature_id"],
        signer_subject=item["signer_subject"],
        meaning=SignatureMeaning(item["meaning"]),
        record_type=item["record_type"],
        record_id=item["record_id"],
        record_version=item["record_version"],
        action=item["action"],
        justification=item["justification"],
        signed_at=item["signed_at"],
        authentication_assurance=item["authentication_assurance"],
        signature_hash=item["signature_hash"],
    )
