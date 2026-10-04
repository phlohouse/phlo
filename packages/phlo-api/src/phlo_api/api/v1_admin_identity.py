"""Phlo-owned durable identity administration and action-bound signatures."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from typing import Annotated, Literal, TypeVar

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import Field, field_validator

from phlo.audit.events import AuditEventType, CanonicalAuditEvent
from phlo.capabilities.interfaces import AuthenticatedSession, AuthPrincipal
from phlo.compliance.audit.sealed import TamperEvidentAuditSink
from phlo.compliance.signatures.service import SignatureService, SignatureServiceConfig
from phlo.compliance.signatures.step_up import RecentMfaClaimsChallenge
from phlo.compliance.signatures.types import SignatureMeaning, SignatureRequest
from phlo.identity.authority import (
    IdentityAuthority,
    IdentityConflictError,
    IdentityInvitation,
    IdentityMember,
    IdentityServiceAccount,
    StoredSignature,
    validate_roles,
)
from phlo.plugins.observatory_settings import (
    SettingsScope,
    StorageUnavailableError,
    get_settings_service,
)
from phlo_api.api.authentication import authenticate_request, get_request_principal
from phlo_api.api.v1_admin_audit import _audit_store
from phlo_api.errors import BackendUnavailableError
from phlo_api.v1_contract import WireModel

router = APIRouter(tags=["v1 admin identity"])

Role = Literal["admin", "operator", "developer", "analyst", "viewer", "service"]
SignedAction = Literal[
    "admin.member.roles.change",
    "admin.invitation.create",
    "admin.service_account.create",
    "admin.service_account.revoke",
    "incident.resolve",
]
SignatureMeaningValue = Literal["approved", "released", "reviewed", "acknowledged", "authored"]
_SETTINGS_KEY = "phlo.identity.admin-settings"
_Result = TypeVar("_Result")


class MemberView(WireModel):
    subject: str
    email: str | None
    principal_type: str
    roles: list[str]
    active: bool
    version: int
    created_at: str
    updated_at: str


class MemberPage(WireModel):
    items: list[MemberView]


class MemberRolesUpdate(WireModel):
    expected_version: int = Field(ge=0)
    roles: list[Role] = Field(min_length=1, max_length=6)
    email: str | None = Field(default=None, max_length=320)
    principal_type: Literal["user", "service", "platform"] = "user"
    active: bool = True
    signature_id: str = Field(min_length=1, max_length=100)


class InvitationView(WireModel):
    invitation_id: str
    email: str
    roles: list[str]
    status: str
    invited_by: str
    expires_at: str
    created_at: str


class InvitationCreated(InvitationView):
    token: str


class InvitationPage(WireModel):
    items: list[InvitationView]


class InvitationCreate(WireModel):
    email: str = Field(min_length=3, max_length=320)
    roles: list[Role] = Field(min_length=1, max_length=6)
    signature_id: str = Field(min_length=1, max_length=100)


class InvitationAccept(WireModel):
    token: str = Field(min_length=20, max_length=200)


class ServiceAccountView(WireModel):
    subject: str
    name: str
    roles: list[str]
    active: bool
    version: int
    created_at: str


class ServiceAccountPage(WireModel):
    items: list[ServiceAccountView]


class ServiceAccountCreate(WireModel):
    name: str = Field(min_length=1, max_length=120)
    roles: list[Role] = Field(min_length=1, max_length=6)
    signature_id: str = Field(min_length=1, max_length=100)


class ServiceAccountCreated(WireModel):
    account: ServiceAccountView
    token: str


class SignatureCreate(WireModel):
    action: SignedAction
    target_type: Literal["member", "invitation", "service_account", "incident"]
    target_id: str = Field(min_length=1, max_length=512)
    target_version: str = Field(min_length=1, max_length=256)
    meaning: SignatureMeaningValue = "approved"
    justification: str = Field(min_length=1, max_length=4000)


class SignatureView(WireModel):
    signature_id: str
    signer_subject: str
    meaning: SignatureMeaning
    action: str
    target_type: str
    target_id: str
    target_version: str
    justification: str | None
    signed_at: str
    authentication_assurance: str
    signature_hash: str
    consumed_at: str | None


class SignaturePage(WireModel):
    items: list[SignatureView]


class AdminSettings(WireModel):
    version: int = Field(ge=0)
    values: dict[str, str | int | float | bool | None] = Field(max_length=100)


class AdminSettingsUpdate(WireModel):
    expected_version: int = Field(ge=0)
    values: dict[str, str | int | float | bool | None] = Field(max_length=100)

    @field_validator("values")
    @classmethod
    def validate_values(
        cls, values: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        blocked = ("token", "password", "secret", "credential", "api_key", "private_key")
        for key, value in values.items():
            normalized = key.casefold()
            if not key.strip() or len(key) > 120:
                raise ValueError("Setting names must be 1 to 120 characters.")
            if any(part in normalized for part in blocked):
                raise ValueError("Credentials cannot be stored in Phlo settings.")
            if isinstance(value, str) and len(value) > 4000:
                raise ValueError("String settings cannot exceed 4000 characters.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Numeric settings must be finite.")
        return values


def _authority() -> IdentityAuthority:
    if os.environ.get("PHLO_IDENTITY_AUTHORITY_ENABLED") != "1":
        raise BackendUnavailableError("Phlo identity authority is disabled.")
    try:
        return IdentityAuthority()
    except StorageUnavailableError as exc:
        raise BackendUnavailableError("Durable identity storage is unavailable.") from exc


def _identity_call(operation: Callable[[], _Result]) -> _Result:
    try:
        return operation()
    except StorageUnavailableError as exc:
        raise BackendUnavailableError("Durable identity storage is unavailable.") from exc


def _actor(request: Request) -> AuthPrincipal:
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return principal


def _audit(event: CanonicalAuditEvent) -> None:
    with _audit_store() as store:
        TamperEvidentAuditSink(store).write(event)


def _view_member(member: IdentityMember) -> MemberView:
    return MemberView(
        subject=member.subject,
        email=member.email,
        principal_type=member.principal_type,
        roles=list(member.roles),
        active=member.active,
        version=member.version,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def _view_invitation(invitation: IdentityInvitation) -> InvitationView:
    return InvitationView(
        invitation_id=invitation.invitation_id,
        email=invitation.email,
        roles=list(invitation.roles),
        status=invitation.status,
        invited_by=invitation.invited_by,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


def _view_service_account(account: IdentityServiceAccount) -> ServiceAccountView:
    return ServiceAccountView(
        subject=account.subject,
        name=account.name,
        roles=list(account.roles),
        active=account.active,
        version=account.version,
        created_at=account.created_at,
    )


def _view_signature(item: StoredSignature) -> SignatureView:
    record = item.record
    return SignatureView(
        signature_id=record.signature_id,
        signer_subject=record.signer_subject,
        meaning=record.meaning,
        action=record.action,
        target_type=record.record_type,
        target_id=record.record_id,
        target_version=record.record_version,
        justification=record.justification,
        signed_at=record.signed_at,
        authentication_assurance=record.authentication_assurance,
        signature_hash=record.signature_hash,
        consumed_at=item.consumed_at,
    )


def _expected_signature(
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    target_version: str,
    meaning: SignatureMeaning = SignatureMeaning.APPROVED,
    justification: str | None = None,
) -> SignatureRequest:
    return SignatureRequest(
        signer_subject=actor,
        meaning=meaning,
        action=action,
        record_type=target_type,
        record_id=target_id,
        record_version=target_version,
        justification=justification,
    )


def _consume_signature(
    authority: IdentityAuthority, signature_id: str, expected: SignatureRequest
) -> None:
    if not _identity_call(lambda: authority.consume_signature(signature_id, expected)):
        _audit(
            CanonicalAuditEvent(
                event_type=AuditEventType.AUTHORIZATION,
                surface="phlo-api",
                actor_subject=expected.signer_subject,
                actor_type="user",
                authentication_source="phlo-authentication",
                action=expected.action,
                resource_type=expected.record_type,
                resource_id=expected.record_id,
                decision="deny",
                reason_code="signature_missing_stale_reused_or_mismatched",
                outcome="failure",
                attributes={
                    "signature_id": signature_id,
                    "expected_version": expected.record_version,
                },
            )
        )
        raise HTTPException(
            status_code=409, detail="Signature is missing, stale, reused, or mismatched."
        )


def _payload_version(expected_version: int, values: dict[str, object]) -> str:
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"{expected_version}:{digest}"


def _action_audit(
    *,
    actor: AuthPrincipal,
    action: str,
    target_type: str,
    target_id: str,
    signature_id: str,
    outcome: str = "success",
) -> None:
    _audit(
        CanonicalAuditEvent(
            event_type=AuditEventType.MUTATION,
            surface="phlo-api",
            actor_subject=actor.subject,
            actor_type=actor.principal_type,
            actor_roles=actor.groups,
            authentication_source=actor.issuer or "phlo-authentication",
            action=action,
            resource_type=target_type,
            resource_id=target_id,
            decision="allow" if outcome == "success" else "skip",
            reason_code="signed_admin_action" if outcome == "success" else "signed_action_attempt",
            outcome=outcome,
            attributes={"signature_id": signature_id},
        )
    )


@router.get("/admin/settings", response_model=AdminSettings)
def v1_admin_settings_get() -> AdminSettings:
    try:
        record = get_settings_service().get(SettingsScope.GLOBAL, _SETTINGS_KEY)
    except StorageUnavailableError as exc:
        raise BackendUnavailableError("Durable settings storage is unavailable.") from exc
    return (
        AdminSettings(version=0, values={})
        if record is None
        else AdminSettings.model_validate(record.settings)
    )


@router.put("/admin/settings", response_model=AdminSettings)
def v1_admin_settings_put(payload: AdminSettingsUpdate, request: Request) -> AdminSettings:
    result: AdminSettings | None = None
    actor = _actor(request)
    _audit(
        CanonicalAuditEvent(
            event_type=AuditEventType.MUTATION,
            surface="phlo-api",
            actor_subject=actor.subject,
            actor_type=actor.principal_type,
            actor_roles=actor.groups,
            authentication_source=actor.issuer or "phlo-authentication",
            action="settings.manage",
            resource_type="settings",
            resource_id=_SETTINGS_KEY,
            decision="skip",
            reason_code="settings_update_attempt",
            outcome="attempted",
            attributes={"expected_version": payload.expected_version},
        )
    )

    def update(current: dict[str, object] | None) -> dict[str, object]:
        nonlocal result
        version = current.get("version", 0) if current else 0
        if isinstance(version, bool) or not isinstance(version, int):
            raise StorageUnavailableError("Stored settings are malformed.")
        if version != payload.expected_version:
            raise HTTPException(status_code=409, detail="Settings have changed.")
        result = AdminSettings(version=version + 1, values=payload.values)
        return result.model_dump()

    try:
        get_settings_service().mutate(SettingsScope.GLOBAL, _SETTINGS_KEY, update)
    except StorageUnavailableError as exc:
        raise BackendUnavailableError("Durable settings storage is unavailable.") from exc
    assert result is not None
    _audit(
        CanonicalAuditEvent(
            event_type=AuditEventType.MUTATION,
            surface="phlo-api",
            actor_subject=actor.subject,
            actor_type=actor.principal_type,
            actor_roles=actor.groups,
            authentication_source=actor.issuer or "phlo-authentication",
            action="settings.manage",
            resource_type="settings",
            resource_id=_SETTINGS_KEY,
            decision="allow",
            reason_code="settings_updated",
            outcome="success",
            attributes={"version": result.version},
        )
    )
    return result


@router.get("/admin/members", response_model=MemberPage)
def v1_admin_members() -> MemberPage:
    members = _identity_call(lambda: _authority().members())
    return MemberPage(items=[_view_member(member) for member in members])


@router.patch("/admin/members/{subject}/roles", response_model=MemberView)
def v1_admin_member_roles(subject: str, payload: MemberRolesUpdate, request: Request) -> MemberView:
    actor = _actor(request)
    roles = list(validate_roles(payload.roles))
    email = payload.email.strip().casefold() if payload.email else None
    version = _payload_version(
        payload.expected_version,
        {
            "roles": roles,
            "active": payload.active,
            "email": email,
            "principal_type": payload.principal_type,
        },
    )
    authority = _authority()
    _consume_signature(
        authority,
        payload.signature_id,
        _expected_signature(
            actor=actor.subject,
            action="admin.member.roles.change",
            target_type="member",
            target_id=subject,
            target_version=version,
        ),
    )
    _action_audit(
        actor=actor,
        action="admin.member.roles.change",
        target_type="member",
        target_id=subject,
        signature_id=payload.signature_id,
        outcome="attempted",
    )
    member = _identity_call(lambda: authority.member(subject))
    if payload.expected_version == 0 and member is not None:
        raise HTTPException(status_code=409, detail="Member already exists.")
    if payload.expected_version > 0 and member is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    if payload.expected_version == 0:
        member_email = email
        member_type = payload.principal_type
    else:
        if member is None:
            raise HTTPException(status_code=404, detail="Member not found.")
        member_email = member.email
        member_type = member.principal_type
    try:
        updated = _identity_call(
            lambda: authority.assign_roles(
                subject=subject,
                email=member_email,
                principal_type=member_type,
                roles=roles,
                expected_version=payload.expected_version,
                active=payload.active,
            )
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _action_audit(
        actor=actor,
        action="admin.member.roles.change",
        target_type="member",
        target_id=subject,
        signature_id=payload.signature_id,
    )
    return _view_member(updated)


@router.get("/admin/invitations", response_model=InvitationPage)
def v1_admin_invitations() -> InvitationPage:
    invitations = _identity_call(lambda: _authority().invitations())
    return InvitationPage(items=[_view_invitation(item) for item in invitations])


@router.post("/admin/invitations", response_model=InvitationCreated, status_code=201)
def v1_admin_invitation_create(payload: InvitationCreate, request: Request) -> InvitationCreated:
    actor = _actor(request)
    email = payload.email.strip().casefold()
    roles = list(validate_roles(payload.roles))
    version = _payload_version(0, {"email": email, "roles": roles})
    authority = _authority()
    _consume_signature(
        authority,
        payload.signature_id,
        _expected_signature(
            actor=actor.subject,
            action="admin.invitation.create",
            target_type="invitation",
            target_id=email,
            target_version=version,
        ),
    )
    _action_audit(
        actor=actor,
        action="admin.invitation.create",
        target_type="invitation",
        target_id=email,
        signature_id=payload.signature_id,
        outcome="attempted",
    )
    try:
        invitation, token = _identity_call(
            lambda: authority.create_invitation(email=email, roles=roles, invited_by=actor.subject)
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _action_audit(
        actor=actor,
        action="admin.invitation.create",
        target_type="invitation",
        target_id=invitation.invitation_id,
        signature_id=payload.signature_id,
    )
    return InvitationCreated(**_view_invitation(invitation).model_dump(), token=token)


@router.post("/invitations/accept", response_model=MemberView)
def v1_invitation_accept(payload: InvitationAccept, request: Request) -> MemberView:
    actor = _actor(request)
    if actor.email is None:
        raise HTTPException(status_code=403, detail="Authenticated email is required.")
    actor_email = actor.email
    _audit(
        CanonicalAuditEvent(
            event_type=AuditEventType.MUTATION,
            surface="phlo-api",
            actor_subject=actor.subject,
            actor_type=actor.principal_type,
            actor_roles=actor.groups,
            authentication_source=actor.issuer or "phlo-authentication",
            action="admin.invitation.accept",
            resource_type="invitation",
            resource_id=actor_email.casefold(),
            decision="skip",
            reason_code="invitation_accept_attempt",
            outcome="attempted",
        )
    )
    try:
        member = _identity_call(
            lambda: _authority().accept_invitation(
                token=payload.token,
                subject=actor.subject,
                email=actor_email,
                principal_type=actor.principal_type,
            )
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        CanonicalAuditEvent(
            event_type=AuditEventType.MUTATION,
            surface="phlo-api",
            actor_subject=actor.subject,
            actor_type=actor.principal_type,
            actor_roles=actor.groups,
            authentication_source=actor.issuer or "phlo-authentication",
            action="admin.invitation.accept",
            resource_type="member",
            resource_id=member.subject,
            decision="allow",
            reason_code="invitation_accepted",
            outcome="success",
        )
    )
    return _view_member(member)


@router.get("/admin/service-accounts", response_model=ServiceAccountPage)
def v1_admin_service_accounts() -> ServiceAccountPage:
    accounts = _identity_call(lambda: _authority().service_accounts())
    return ServiceAccountPage(items=[_view_service_account(item) for item in accounts])


@router.post("/admin/service-accounts", response_model=ServiceAccountCreated, status_code=201)
def v1_admin_service_account_create(
    payload: ServiceAccountCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> ServiceAccountCreated:
    actor = _actor(request)
    name = payload.name.strip()
    roles = list(validate_roles(payload.roles))
    version = _payload_version(0, {"name": name, "roles": roles})
    authority = _authority()
    _consume_signature(
        authority,
        payload.signature_id,
        _expected_signature(
            actor=actor.subject,
            action="admin.service_account.create",
            target_type="service_account",
            target_id=name,
            target_version=version,
        ),
    )
    _action_audit(
        actor=actor,
        action="admin.service_account.create",
        target_type="service_account",
        target_id=name,
        signature_id=payload.signature_id,
        outcome="attempted",
    )
    try:
        account, token = _identity_call(
            lambda: authority.create_service_account(
                name=name,
                roles=roles,
                idempotency_key=idempotency_key,
                actor_subject=actor.subject,
            )
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _action_audit(
        actor=actor,
        action="admin.service_account.create",
        target_type="service_account",
        target_id=account.subject,
        signature_id=payload.signature_id,
    )
    return ServiceAccountCreated(account=_view_service_account(account), token=token)


@router.delete("/admin/service-accounts/{subject}", response_model=ServiceAccountView)
def v1_admin_service_account_revoke(
    subject: str,
    request: Request,
    expected_version: Annotated[int, Header(ge=1)],
    signature_id: Annotated[str, Header(min_length=1, max_length=100)],
) -> ServiceAccountView:
    actor = _actor(request)
    authority = _authority()
    _consume_signature(
        authority,
        signature_id,
        _expected_signature(
            actor=actor.subject,
            action="admin.service_account.revoke",
            target_type="service_account",
            target_id=subject,
            target_version=str(expected_version),
        ),
    )
    _action_audit(
        actor=actor,
        action="admin.service_account.revoke",
        target_type="service_account",
        target_id=subject,
        signature_id=signature_id,
        outcome="attempted",
    )
    try:
        account = _identity_call(
            lambda: authority.revoke_service_account(subject, expected_version)
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _action_audit(
        actor=actor,
        action="admin.service_account.revoke",
        target_type="service_account",
        target_id=subject,
        signature_id=signature_id,
    )
    return _view_service_account(account)


@router.post("/signatures", response_model=SignatureView, status_code=201)
def v1_signature_create(payload: SignatureCreate, request: Request) -> SignatureView:
    result = authenticate_request(request)
    if not result.authenticated or result.principal is None or result.session is None:
        raise HTTPException(status_code=401, detail="Authenticated session is required.")
    if not isinstance(result.session, AuthenticatedSession):
        raise HTTPException(status_code=401, detail="Verified authentication session is required.")
    session = result.session
    authority = _authority()
    _audit(
        CanonicalAuditEvent(
            event_type=AuditEventType.SIGNATURE,
            surface="phlo-api",
            actor_subject=result.principal.subject,
            actor_type=result.principal.principal_type,
            actor_roles=result.principal.groups,
            authentication_source=session.provider_name,
            action="signature.create",
            resource_type=payload.target_type,
            resource_id=payload.target_id,
            decision="skip",
            reason_code="signature_create_attempt",
            outcome="attempted",
            attributes={"action": payload.action, "target_version": payload.target_version},
        )
    )
    signature_request = SignatureRequest(
        signer_subject=result.principal.subject,
        meaning=SignatureMeaning(payload.meaning),
        action=payload.action,
        record_type=payload.target_type,
        record_id=payload.target_id,
        record_version=payload.target_version,
        justification=payload.justification,
    )
    service = SignatureService(
        config=SignatureServiceConfig(step_up_challenge=RecentMfaClaimsChallenge()),
        signature_repository=authority,
    )
    try:
        record = _identity_call(lambda: service.sign(signature_request, session))
    except PermissionError as exc:
        raise HTTPException(
            status_code=403, detail="Recent MFA authentication is required."
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(
        CanonicalAuditEvent(
            event_type=AuditEventType.SIGNATURE,
            surface="phlo-api",
            actor_subject=record.signer_subject,
            actor_type=result.principal.principal_type,
            actor_roles=result.principal.groups,
            authentication_source=session.provider_name,
            action="signature.create",
            resource_type=record.record_type,
            resource_id=record.record_id,
            decision="allow",
            reason_code="signature_recorded",
            outcome="success",
            attributes={
                "signature_id": record.signature_id,
                "meaning": record.meaning,
                "action": record.action,
                "target_version": record.record_version,
                "authentication_assurance": record.authentication_assurance,
                "signature_hash": record.signature_hash,
            },
        )
    )
    stored = next(
        item
        for item in _identity_call(lambda: authority.signatures(record.signer_subject))
        if item.record.signature_id == record.signature_id
    )
    return _view_signature(stored)


@router.get("/signatures", response_model=SignaturePage)
def v1_signatures(request: Request) -> SignaturePage:
    actor = _actor(request)
    signatures = _identity_call(lambda: _authority().signatures(actor.subject))
    return SignaturePage(items=[_view_signature(item) for item in signatures])
